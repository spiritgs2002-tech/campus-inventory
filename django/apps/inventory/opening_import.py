"""Opening-balance CSV import: validated rows become ordinary opening inbound orders.

Every row is checked before anything is written. Execution reuses orders.create
(locks, ledger, asset cards, audit, idempotency) in chunks the order service
accepts, all inside one transaction: any failure rolls back the whole file.
"""

import csv
import hashlib
import io
from decimal import Decimal, InvalidOperation
from django.db import transaction
from apps.catalog.models import Item, Location
from apps.core.models import OperationRequest
from shared.errors import BusinessError
from shared.idempotency.service import validate_key
from . import orders
from .models import InboundOrder

MAX_BYTES = 1024 * 1024
MAX_ROWS = 1000  # ~8s locally; larger files must be split (gunicorn timeout 60s)
LINES_PER_ORDER = 50
FIXED_PER_ORDER = 1000
MAX_REPORTED_ERRORS = 50
ALIASES = {
    "code": {"物资编码", "编码", "code", "item_code"},
    "location": {"库位", "库位名称", "location"},
    "quantity": {"数量", "期初数量", "quantity"},
    "quality": {"质量", "quality"},
    "unit_price": {"单价", "unit_price", "price"},
}
QUALITY = {
    "": "available",
    "可用": "available",
    "available": "available",
    "损坏": "damaged",
    "damaged": "damaged",
}
TEMPLATE = "物资编码,库位,数量,质量,单价\r\n"


class _Preview(Exception):
    pass


def _header(row):
    mapping = {}
    for index, raw in enumerate(row):
        name = "".join(raw.lstrip("﻿").split()).casefold()
        for key, names in ALIASES.items():
            if name in {n.casefold() for n in names}:
                if key in mapping:
                    raise BusinessError("INVALID_HEADER", f"表头重复：{raw}", 422)
                mapping[key] = index
    missing = {"code", "location", "quantity"} - set(mapping)
    if missing:
        raise BusinessError(
            "INVALID_HEADER", "表头须包含：物资编码、库位、数量（质量、单价可选）", 422
        )
    return mapping


def _decode(content):
    for encoding in ("utf-8-sig", "gb18030"):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise BusinessError("INVALID_ENCODING", "文件须为 UTF-8 或 GBK 编码的 CSV", 422)


def parse(tenant_id, content):
    if len(content) > MAX_BYTES:
        raise BusinessError("FILE_TOO_LARGE", "期初文件不能超过1MiB", 422)
    reader = csv.reader(io.StringIO(_decode(content), newline=""))
    rows = [row for row in reader if any(cell.strip() for cell in row)]
    if not rows:
        raise BusinessError("EMPTY_FILE", "文件为空", 422)
    mapping, body = _header(rows[0]), rows[1:]
    if not 1 <= len(body) <= MAX_ROWS:
        raise BusinessError(
            "INVALID_ROW_COUNT", f"期初数据须为1至{MAX_ROWS}行", 422
        )
    items = {
        row.code: row
        for row in Item.objects.filter(tenant_id=tenant_id, active=True)
    }
    locations = {
        row.name: row
        for row in Location.objects.filter(tenant_id=tenant_id, active=True)
    }
    lines, errors, seen = [], [], {}

    def value(row, key):
        index = mapping.get(key)
        return row[index].strip() if index is not None and index < len(row) else ""

    for number, row in enumerate(body, start=2):
        problems = []
        item = items.get(value(row, "code"))
        if item is None:
            problems.append(f"物资编码“{value(row, 'code')}”不存在或已停用")
        location = locations.get(value(row, "location"))
        if location is None:
            problems.append(f"库位“{value(row, 'location')}”不存在或已停用")
        quality = QUALITY.get(value(row, "quality").casefold())
        if quality is None:
            problems.append("质量只能填 可用 或 损坏")
        try:
            quantity = Decimal(value(row, "quantity"))
            if not quantity.is_finite() or quantity <= 0 or quantity != round(quantity, 3):
                raise InvalidOperation
        except InvalidOperation:
            quantity = None
            problems.append("数量须为大于0、最多3位小数的数字")
        price = None
        if value(row, "unit_price"):
            try:
                price = Decimal(value(row, "unit_price"))
                if not price.is_finite() or price < 0 or price != round(price, 2):
                    raise InvalidOperation
            except InvalidOperation:
                problems.append("单价须为不小于0、最多2位小数的数字")
        if item is not None and item.type == "fixed" and quantity is not None:
            if quantity != quantity.to_integral_value():
                problems.append("固定资产数量须为整数（每件生成一张资产卡）")
        if item is not None and location is not None and quality is not None:
            dimension = (item.pk, location.pk, quality)
            if dimension in seen:
                problems.append(f"与第{seen[dimension]}行的物资/库位/质量重复")
            seen.setdefault(dimension, number)
        if problems:
            errors.append({"line": number, "errors": problems})
            continue
        line = {
            "item_id": item.pk,
            "quantity": format(quantity, "f"),
            "location_id": location.pk,
            "quality": quality,
        }
        if price is not None:
            line["unit_price"] = format(price, "f")
        lines.append((line, item.type, quantity))
    return lines, errors


def chunks(lines):
    current, fixed = [], 0
    for line, item_type, quantity in lines:
        cards = int(quantity) if item_type == "fixed" else 0
        if current and (
            len(current) == LINES_PER_ORDER or fixed + cards > FIXED_PER_ORDER
        ):
            yield current
            current, fixed = [], 0
        if cards > FIXED_PER_ORDER:
            raise BusinessError(
                "TOO_MANY_ASSETS", f"单行固定资产不能超过{FIXED_PER_ORDER}件", 422
            )
        current.append(line)
        fixed += cards
    if current:
        yield current


def run(principal, content, key, *, preview=False):
    p = orders.identity(principal)
    orders.allowed(p, "inbound")
    if p.role != "admin":
        raise BusinessError("FORBIDDEN", "期初导入仅限管理员", 403)
    validate_key(key)
    if len(key) > 100:
        raise BusinessError("INVALID_IDEMPOTENCY_KEY", "幂等键过长", 422)
    lines, errors = parse(p.tenant_id, content)
    summary = {
        "rows": len(lines) + len(errors),
        "valid_rows": len(lines),
        "errors": errors[:MAX_REPORTED_ERRORS],
        "error_rows": len(errors),
        "orders": [],
        "preview": preview,
        "replayed": False,
    }
    if errors:
        return summary
    digest = hashlib.sha256(content).hexdigest()[:16]
    batches = list(chunks(lines))
    # A different key for the same file would double the opening balance.
    replay = OperationRequest.objects.filter(
        tenant_id=p.tenant_id,
        actor_id=p.user_id,
        operation="orders.inbound.create",
        idempotency_key=f"{key}:1",
    ).exists()
    if (
        not replay
        and InboundOrder.objects.filter(
            tenant_id=p.tenant_id,
            type="opening",
            remark__startswith=f"期初导入 {digest} ",
        ).exists()
    ):
        raise BusinessError(
            "OPENING_ALREADY_IMPORTED", "该文件已正式导入过，不能重复导入", 409
        )
    try:
        with transaction.atomic():
            for index, batch in enumerate(batches, start=1):
                result, replayed = orders.create(
                    p,
                    "inbound",
                    f"{key}:{index}",
                    {
                        "type": "opening",
                        "remark": f"期初导入 {digest} 第{index}/{len(batches)}批",
                        "lines": batch,
                    },
                )
                summary["replayed"] = summary["replayed"] or replayed
                summary["orders"].append(
                    {"id": result["document"]["id"], "lines": len(batch)}
                )
            if preview:
                raise _Preview()
    except _Preview:
        summary["orders"] = [{"id": None, "lines": o["lines"]} for o in summary["orders"]]
    return summary
