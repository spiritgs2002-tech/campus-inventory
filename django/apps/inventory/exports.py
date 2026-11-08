"""Administrator CSV exports that reuse the authorized, filtered read queries."""

import csv
import io
from django.utils import timezone
from shared.errors import BusinessError
from . import orders, queries

MAX_ROWS = 50000
DIRECTIONS = {"in": "入库", "out": "出库"}
COLUMNS = {
    "stock": [
        ("code", "物资编码"),
        ("item_name", "物资名称"),
        ("unit", "单位"),
        ("location_name", "库位"),
        ("quality_label", "质量"),
        ("quantity", "数量"),
        ("low_stock", "低于安全库存"),
        ("updated_at", "更新时间"),
    ],
    "ledger": [
        ("id", "流水ID"),
        ("created_at", "时间"),
        ("item_name", "物资名称"),
        ("location_name", "库位"),
        ("quality_label", "质量"),
        ("direction", "方向"),
        ("quantity", "数量"),
        ("balance_after", "结存"),
        ("operator_name", "操作人"),
        ("inbound_item_id", "入库明细ID"),
        ("outbound_item_id", "出库明细ID"),
        ("scrap_item_id", "处置明细ID"),
        ("adjustment_item_id", "调整明细ID"),
    ],
    "assets": [
        ("asset_no", "资产号"),
        ("item_name", "物资名称"),
        ("status_label", "状态"),
        ("location_name", "库位"),
        ("responsible_name", "责任人"),
        ("original_value", "原值"),
        ("purchase_date", "购置日期"),
    ],
}
FILENAMES = {"stock": "stock", "ledger": "stock-ledger", "assets": "assets"}


def cell(value):
    """Neutralise spreadsheet formulas without altering plain numbers."""
    if value is None:
        return ""
    if isinstance(value, bool):
        return "是" if value else "否"
    text = str(value)
    if text[:1] in {"=", "+", "-", "@", "\t", "\r"}:
        try:
            float(text)
        except ValueError:
            return "'" + text
    return text


def collect(rows, _number, serialize):
    count = rows.count()
    if count > MAX_ROWS:
        raise BusinessError(
            "EXPORT_TOO_LARGE",
            f"导出结果共{count}行，超过{MAX_ROWS}行上限，请增加筛选条件",
            422,
        )
    return [serialize(row) for row in rows.iterator(chunk_size=2000)]


def export_csv(principal, resource, params):
    p = orders.identity(principal)
    if p.role != "admin":
        raise BusinessError("FORBIDDEN", "导出仅限管理员", 403)
    params = params.copy()
    params.pop("page", None)
    records, _ = getattr(queries, resource)(p, params, paginate=collect)
    buffer = io.StringIO()
    buffer.write("﻿")  # Excel needs the BOM to read UTF-8 Chinese text.
    writer = csv.writer(buffer)
    writer.writerow([label for _, label in COLUMNS[resource]])
    for record in records:
        if resource == "ledger":
            record = {**record, "direction": DIRECTIONS.get(record["direction"], record["direction"])}
        writer.writerow([cell(record.get(key)) for key, _ in COLUMNS[resource]])
    stamp = timezone.localtime().strftime("%Y%m%d-%H%M%S")
    return buffer.getvalue(), f"{FILENAMES[resource]}-{stamp}.csv", len(records)
