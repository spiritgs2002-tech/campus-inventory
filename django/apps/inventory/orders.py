"""SQL-first order commands: immutable submissions and atomic transitions."""

from decimal import Decimal
from datetime import timedelta
from uuid import uuid4
from copy import deepcopy
from django.db import DatabaseError
from django.utils import timezone
from apps.core import services as core
from apps.catalog import services as catalog
from shared.auth.backends import authorize_claims
from shared.errors import BusinessError
from shared.idempotency.service import execute
from . import posting
from .models import (
    InboundOrder,
    InboundItem,
    OutboundOrder,
    OutboundItem,
    ScrapOrder,
    ScrapItem,
    AdjustmentOrder,
    AdjustmentItem,
    OutboundAsset,
    Inventory,
)
from .order_forms import HeaderForm, LineForm, ExecuteLineForm, parse_document, validate

REGISTRY = {
    "inbound": (InboundOrder, InboundItem, "order_id", "入库"),
    "outbound": (OutboundOrder, OutboundItem, "order_id", "出库"),
    "scrap": (ScrapOrder, ScrapItem, "scrap_id", "处置"),
    "adjustment": (AdjustmentOrder, AdjustmentItem, "order_id", "调整"),
}


def identity(principal):
    current = authorize_claims(
        {
            "tid": principal.tenant_id,
            "sub": principal.user_id,
            "fid": principal.family_id,
        }
    )
    if current.must_change_password:
        raise BusinessError("PASSWORD_CHANGE_REQUIRED", "请先修改初始密码", 403)
    return current


def allowed(p, kind):
    if kind not in REGISTRY:
        raise BusinessError("NOT_FOUND", "单据类型不存在", 404)
    if p.role != "admin" and kind in {"inbound", "adjustment"}:
        raise BusinessError("FORBIDDEN", "该业务仅限管理员", 403)


def queryset(principal, kind):
    p = identity(principal)
    allowed(p, kind)
    rows = REGISTRY[kind][0].objects.filter(tenant_id=p.tenant_id)
    return rows if p.role == "admin" else rows.filter(applicant_id=p.user_id)


def header_get(p, kind, pk, *, locked=False):
    rows = queryset(p, kind)
    if locked:
        rows = rows.select_for_update(no_key=True)
    result = rows.filter(pk=pk).first()
    if result is None:
        raise BusinessError("NOT_FOUND", "单据不存在", 404)
    return result


def line_rows(tenant_id, kind, pk):
    return (
        REGISTRY[kind][1]
        .objects.filter(tenant_id=tenant_id, **{REGISTRY[kind][2]: pk})
        .order_by("id")
    )


def record(obj):
    values = {}
    for field in obj._meta.local_concrete_fields:
        if field.name in {"tenant_id", "created_xid"}:
            continue
        value = getattr(obj, field.name)
        if isinstance(value, Decimal):
            value = str(value)
        elif hasattr(value, "isoformat"):
            value = value.isoformat()
        elif value is not None and not isinstance(
            value, (str, int, bool, float, list, dict)
        ):
            value = str(value)
        values[field.name] = value
    if isinstance(obj, InboundOrder):
        values["status"] = "completed"
    return values


def detail(p, kind, pk):
    header = header_get(p, kind, pk)
    return {
        "record": record(header),
        "lines": [record(row) for row in line_rows(p.tenant_id, kind, pk)],
    }


def observe(p, item_id, location_id, quality):
    p = identity(p)
    if p.role != "admin":
        raise BusinessError("FORBIDDEN", "仅管理员可观察库存", 403)
    item = catalog.order_reference("items", p.tenant_id, item_id)
    catalog.order_reference("locations", p.tenant_id, location_id)
    if quality not in {"available", "damaged"}:
        raise BusinessError("INVALID_DATA", "质量无效", 422)
    row = Inventory.objects.filter(
        tenant_id=p.tenant_id, item_id=item_id, location_id=location_id, quality=quality
    ).first()
    return {
        "item_id": item_id,
        "item_type": item.type,
        "location_id": location_id,
        "quality": quality,
        "book_qty": str(row.quantity if row else Decimal(0)),
        "inventory_version": row.version if row else 0,
        "observed_at": timezone.now().isoformat(),
    }


def snapshot_check(tenant_id, row):
    stock = Inventory.objects.filter(
        tenant_id=tenant_id,
        item_id=row["item_id"],
        location_id=row["location_id"],
        quality=row["quality"],
    ).first()
    if (stock.version if stock else 0) != row["inventory_version"] or (
        stock.quantity if stock else Decimal(0)
    ) != row["book_qty"]:
        posting.fail("STALE_SNAPSHOT", "观察快照已过期，请重新观察")
    if row.get("to_quality"):
        target = Inventory.objects.filter(
            tenant_id=tenant_id,
            item_id=row["item_id"],
            location_id=row["location_id"],
            quality=row["to_quality"],
        ).first()
        if (target.version if target else 0) != row["target_inventory_version"]:
            posting.fail("STALE_SNAPSHOT", "目标质量库存版本已变化")


def required(row, *fields):
    if any(row.get(field) is None or row.get(field) == "" for field in fields):
        raise BusinessError("INVALID_DATA", "缺少必要的数量、库位或来源字段", 422)


def prepare_rows(context, kind, header, rows):
    p = context["principal"]
    typ = header["type"]
    for row in rows:
        item = catalog.order_reference(
            "items",
            p.tenant_id,
            row["item_id"],
            active=kind in {"inbound", "outbound"} and typ != "return",
        )
        row["item_type"] = item.type
        if kind in {"inbound", "outbound", "adjustment"} or typ == "stock_damage":
            required(row, "quantity")
        if (
            item.type == "fixed"
            and row.get("quantity") is not None
            and row["quantity"] != row["quantity"].to_integral_value()
        ):
            raise BusinessError("FRACTIONAL_ASSET", "固定资产数量须为整数", 422)
        if row.get("location_id"):
            catalog.order_reference(
                "locations",
                p.tenant_id,
                row["location_id"],
                active=kind in {"inbound", "outbound"},
            )
        if kind == "inbound":
            required(row, "location_id", "quality")
            if typ == "return":
                required(
                    row,
                    "source_outbound_item_id"
                    if item.type == "consumable"
                    else "source_outbound_asset_id",
                )
                if (
                    item.type == "fixed"
                    and (row["quantity"] != 1 or row.get("source_outbound_item_id"))
                ) or (
                    item.type == "consumable" and row.get("source_outbound_asset_id")
                ):
                    raise BusinessError(
                        "INVALID_RETURN_SOURCE", "归还来源与物资类型不一致", 422
                    )
        elif kind == "outbound":
            if typ == "requisition":
                recipient = row.get("recipient_id") or p.user_id
                if p.role != "admin" and recipient != p.user_id:
                    raise BusinessError("FORBIDDEN", "只能为本人提交领用", 403)
                posting.active_user(context, recipient)
                row["recipient_id"] = recipient
            else:
                if p.role != "admin":
                    raise BusinessError("FORBIDDEN", "即时业务仅限管理员", 403)
                required(row, "location_id")
                if typ == "purchase_return":
                    required(row, "source_inbound_item_id")
                else:
                    required(
                        row,
                        "recipient_id",
                        "source_outbound_item_id"
                        if item.type == "consumable"
                        else "source_outbound_asset_id",
                    )
                    posting.active_user(context, row["recipient_id"])
                    if item.type == "fixed" and row["quantity"] != 1:
                        raise BusinessError(
                            "INVALID_DATA", "资产转移须逐件一条明细", 422
                        )
        elif kind == "scrap":
            if typ == "stock_damage":
                if item.type != "consumable" or p.role != "admin":
                    raise BusinessError("FORBIDDEN", "耗材报损仅限管理员", 403)
                required(row, "quantity", "location_id", "quality")
            else:
                if item.type != "fixed" or row.get("quantity") is not None:
                    raise BusinessError(
                        "INVALID_DATA", "固定资产处置按卡办理，不填写数量", 422
                    )
                required(row, "asset_card_id")
                if typ == "recovery":
                    required(
                        row,
                        "source_loss_item_id",
                        "recovery_location_id",
                        "recovery_quality",
                    )
                    catalog.order_reference(
                        "locations",
                        p.tenant_id,
                        row["recovery_location_id"],
                        active=True,
                    )
        else:
            required(row, "location_id", "quality", "observed_at", "direction")
            if row["observed_at"] > timezone.now() + timedelta(minutes=1):
                raise BusinessError("INVALID_OBSERVATION", "观察时间不能在未来", 422)
            if item.type == "fixed":
                if typ != "count" or row["direction"] != "in" or row["quantity"] != 1:
                    raise BusinessError(
                        "INVALID_DATA", "固定资产仅支持逐件盘盈；盘亏请走遗失", 422
                    )
                if any(
                    row.get(f) is not None
                    for f in ("inventory_version", "book_qty", "counted_qty")
                ):
                    raise BusinessError(
                        "INVALID_DATA", "固定资产盘盈不填写耗材快照", 422
                    )
            else:
                required(row, "inventory_version", "book_qty")
                if typ == "quality_transfer":
                    required(row, "to_quality", "target_inventory_version")
                    if (
                        row["direction"] != "out"
                        or row["to_quality"] == row["quality"]
                        or row.get("counted_qty") is not None
                    ):
                        raise BusinessError(
                            "INVALID_DATA",
                            "质量转换须源出、目标质量不同且不填实盘量",
                            422,
                        )
                else:
                    required(row, "counted_qty")
                    change = row["counted_qty"] - row["book_qty"]
                    if (
                        not change
                        or abs(change) != row["quantity"]
                        or row["direction"] != ("in" if change > 0 else "out")
                    ):
                        raise BusinessError(
                            "INVALID_DATA", "盘点差额、方向与数量不一致", 422
                        )
                snapshot_check(p.tenant_id, row)


def check_disposal_ownership(context, header, rows):
    p = context["principal"]
    for row in rows:
        if header["type"] == "stock_damage":
            continue
        card = context["cards"][row["asset_card_id"]]
        if card.item_id != row["item_id"]:
            posting.fail("ASSET_ITEM_MISMATCH", "资产不属于所选物资")
        if header["type"] == "recovery":
            loss = posting.fetch(ScrapItem, p.tenant_id, row["source_loss_item_id"])
            parent = posting.fetch(ScrapOrder, p.tenant_id, loss.scrap_id)
            if (
                loss.disposal_type != "loss"
                or loss.asset_card_id != card.pk
                or parent.status != "approved"
                or card.status != "lost"
            ):
                posting.fail("INVALID_LOSS_SOURCE", "找回来源无效")
            if p.role != "admin" and parent.applicant_id != p.user_id:
                raise BusinessError("FORBIDDEN", "只能提交本人遗失资产的找回", 403)
        else:
            if card.status not in {"in_stock", "repair", "in_use"}:
                posting.fail("INVALID_ASSET_STATE", "当前状态不能发起该处置")
            if p.role != "admin" and card.responsible_user_id != p.user_id:
                raise BusinessError("FORBIDDEN", "只能申请处置本人领用的资产", 403)
            if card.status == "in_use":
                source = OutboundAsset.objects.filter(
                    tenant_id=p.tenant_id, asset_card_id=card.pk, closed_at__isnull=True
                ).first()
                if source is None:
                    posting.fail("SOURCE_ISSUE_REQUIRED", "在用资产缺少有效发放关系")
                row["source_outbound_asset_id"] = source.pk


def command(principal, kind, action, key, payload, callback, discover):
    principal = identity(principal)
    allowed(principal, kind)
    operation = f"orders.{kind}.{action}"

    def locked():
        tenant, users, p = core.lock_order_context(principal, discover)
        allowed(p, kind)
        ctx = {
            "tenant": tenant,
            "users": users,
            "principal": p,
            "operation_id": core.operation_id(p.tenant_id, p.user_id, operation, key),
        }
        return callback(ctx)

    try:
        return execute(
            principal.tenant_id, principal.user_id, operation, key, payload, locked
        )
    except DatabaseError as error:
        state = getattr(error.__cause__, "sqlstate", "")
        if state in {"23503", "23505", "23514", "P0001", "22003"}:
            raise BusinessError(
                "DOCUMENT_CONFLICT", "单据约束、来源额度或状态冲突，操作已回滚", 409
            ) from None
        raise


def result(context, kind, header, action, reason=""):
    p = context["principal"]
    core.audit(
        p,
        kind,
        header.pk,
        {} if action == "create" else {"status": "pending"},
        {"action": action, "status": getattr(header, "status", "completed")},
        reason,
    )
    return {
        "document": record(header),
        "business_refs": [{"type": kind, "id": header.pk}],
    }


def create(principal, kind, key, payload):
    allowed(principal, kind)
    header, rows = parse_document(kind, payload)
    template_rows = rows

    def work(ctx):
        rows = deepcopy(template_rows)
        p = ctx["principal"]
        if kind == "inbound" and header["type"] == "purchase":
            required(header, "supplier_id")
        if header.get("supplier_id"):
            catalog.order_reference(
                "suppliers", p.tenant_id, header["supplier_id"], active=True
            )
        if kind == "adjustment" and (header["type"] == "quality_transfer") != (
            header["reason_category"] == "quality_change"
        ):
            raise BusinessError(
                "INVALID_REASON_CATEGORY", "调整类型与原因分类不一致", 422
            )
        prepare_rows(ctx, kind, header, rows)
        if kind == "scrap":
            ids = [row["asset_card_id"] for row in rows if row.get("asset_card_id")]
            if len(ids) != len(set(ids)):
                raise BusinessError(
                    "DUPLICATE_ASSET", "同张资产卡不能在申请中重复", 422
                )
        if kind == "adjustment":
            keys = [
                (row["item_id"], row["location_id"], quality)
                for row in rows
                if row["item_type"] == "consumable"
                for quality in (
                    [row["quality"], row["to_quality"]]
                    if row.get("to_quality")
                    else [row["quality"]]
                )
            ]
            if len(keys) != len(set(keys)):
                raise BusinessError(
                    "DUPLICATE_STOCK_DIMENSION", "同一库存维度不能重复观察或转换", 422
                )
        number = header.get("order_no") or f"{kind[:2].upper()}-{uuid4().hex}"
        base = {
            "tenant_id": p.tenant_id,
            "order_no": number,
            "operation_id": ctx["operation_id"],
        }
        if kind == "inbound":
            obj = InboundOrder.objects.create(
                **base,
                type=header["type"],
                supplier_id=header.get("supplier_id"),
                operator_id=p.user_id,
                remark=header.get("remark") or None,
            )
            posting.lock_sources(ctx, rows)
            if (
                sum(
                    int(row["quantity"])
                    for row in rows
                    if row["item_type"] == "fixed" and header["type"] != "return"
                )
                > 1000
            ):
                raise BusinessError(
                    "INVALID_ASSET_COUNT", "单次最多创建1000张资产卡", 422
                )
            posting.lock_stock(
                ctx,
                [
                    (r["item_id"], r["location_id"], r["quality"])
                    for r in rows
                    if r["item_type"] == "consumable"
                ],
            )
            posting.receive(ctx, obj, rows)
        elif kind == "outbound":
            if header["type"] != "requisition" and p.role != "admin":
                raise BusinessError("FORBIDDEN", "即时出库仅限管理员", 403)
            obj = OutboundOrder.objects.create(
                **base,
                type=header["type"],
                applicant_id=p.user_id,
                dept_id=ctx["users"][p.user_id].dept_id,
                remark=header.get("remark") or None,
                transfer_id=uuid4() if header["type"] == "transfer" else None,
            )
            posting.lock_sources(ctx, rows)
            received = None
            if header["type"] == "transfer":
                received = InboundOrder.objects.create(
                    tenant_id=p.tenant_id,
                    order_no="TR-" + uuid4().hex,
                    type="return",
                    operator_id=p.user_id,
                    operation_id=ctx["operation_id"],
                    transfer_id=obj.transfer_id,
                )
                for row in rows:
                    row["quality"] = "available"
            posting.lock_stock(
                ctx,
                [
                    (r["item_id"], r["location_id"], "available")
                    for r in rows
                    if r["item_type"] == "consumable"
                    and header["type"] != "requisition"
                ],
            )
            if received:
                posting.receive(
                    ctx,
                    received,
                    rows,
                    close_reason="transfer",
                    event_transfer_id=obj.transfer_id,
                )
                for row in rows:
                    row["source_inbound_item_id"] = row["received_line_id"]
                    if row["item_type"] == "fixed":
                        relation = posting.fetch(
                            OutboundAsset, p.tenant_id, row["source_outbound_asset_id"]
                        )
                        row["asset_ids"] = [relation.asset_card_id]
            lines = []
            for row in rows:
                lines.append(
                    OutboundItem.objects.create(
                        tenant_id=p.tenant_id,
                        order_id=obj.pk,
                        item_id=row["item_id"],
                        item_type=row["item_type"],
                        requested_qty=row["quantity"],
                        source_inbound_item_id=row.get("source_inbound_item_id"),
                        location_id=row.get("location_id"),
                        recipient_id=row.get("recipient_id"),
                    )
                )
            if header["type"] != "requisition":
                posting.issue(
                    ctx,
                    obj,
                    lines,
                    [
                        {
                            "actual_qty": r["quantity"],
                            "asset_ids": r.get("asset_ids", []),
                            "location_id": r["location_id"],
                            "recipient_id": r.get("recipient_id"),
                        }
                        for r in rows
                    ],
                )
        else:
            obj = REGISTRY[kind][0].objects.create(
                **base,
                **(
                    {"disposal_type": header["type"]}
                    if kind == "scrap"
                    else {
                        "type": header["type"],
                        "reason_category": header["reason_category"],
                    }
                ),
                applicant_id=p.user_id,
                reason=header["reason"],
            )
            posting.lock_sources(ctx, rows)
            if kind == "scrap":
                check_disposal_ownership(ctx, header, rows)
            for row in rows:
                if kind == "scrap":
                    names = [
                        "item_id",
                        "item_type",
                        "quantity",
                        "asset_card_id",
                        "location_id",
                        "quality",
                        "source_loss_item_id",
                        "recovery_location_id",
                        "recovery_quality",
                        "source_outbound_asset_id",
                        "residual_value",
                    ]
                    values = {
                        name: (None if row.get(name) == "" else row.get(name))
                        for name in names
                    }
                    ScrapItem.objects.create(
                        tenant_id=p.tenant_id,
                        scrap_id=obj.pk,
                        disposal_type=header["type"],
                        **values,
                    )
                else:
                    names = [
                        "item_id",
                        "item_type",
                        "location_id",
                        "direction",
                        "quality",
                        "to_quality",
                        "quantity",
                        "observed_at",
                        "book_qty",
                        "counted_qty",
                        "inventory_version",
                        "target_inventory_version",
                        "original_value",
                    ]
                    AdjustmentItem.objects.create(
                        tenant_id=p.tenant_id,
                        order_id=obj.pk,
                        adjustment_type=header["type"],
                        **{name: row.get(name) for name in names},
                    )
        return result(
            ctx, kind, obj, "create", header.get("reason", header.get("remark", ""))
        )

    def discover():
        ids = posting.discover_users(principal.tenant_id, rows)
        if kind == "outbound" and header["type"] == "requisition":
            ids.add(principal.user_id)
        return ids

    return command(principal, kind, "create", key, payload, work, discover)


def decide(principal, kind, pk, action, key, payload):
    if kind == "inbound" or action not in {"execute", "approve", "reject", "cancel"}:
        raise BusinessError("FROZEN_DOCUMENT", "单据已执行或操作无效", 409)
    if not isinstance(payload, dict) or set(payload) - {"reason", "lines"}:
        raise BusinessError("INVALID_FIELDS", "含不允许修改的字段", 422)
    reason = payload.get("reason", "")
    if not isinstance(reason, str) or len(reason) > 2000:
        raise BusinessError("INVALID_DATA", "处理原因无效", 422)
    # Validate client data before it is used to discover and lock related users
    # or assets; malformed identifiers must produce 422 rather than ORM errors.
    entries = payload.get("lines", [])
    if not isinstance(entries, list) or len(entries) > 50:
        raise BusinessError("INVALID_LINES", "实发明细无效", 422)
    executions_input = (
        [validate(ExecuteLineForm(data=row), row) for row in entries]
        if kind == "outbound" and action == "execute"
        else []
    )
    if entries and not executions_input:
        raise BusinessError("FROZEN_DOCUMENT", "此操作不能附带或修改明细", 422)

    def rows_for_discovery():
        header = header_get(principal, kind, pk)
        rows = [record(row) for row in line_rows(principal.tenant_id, kind, pk)]
        return posting.discover_users(
            principal.tenant_id, rows + executions_input, header.applicant_id
        )

    def work(ctx):
        p = ctx["principal"]
        header = header_get(p, kind, pk, locked=True)
        if header.status != "pending":
            posting.fail("FROZEN_DOCUMENT", "单据已终结，不能重复执行或修改")
        if p.role != "admin" and (
            action != "cancel" or header.applicant_id != p.user_id
        ):
            raise BusinessError("FORBIDDEN", "无权处理该单据", 403)
        if action in {"reject", "cancel"}:
            if not reason.strip() or payload.get("lines"):
                raise BusinessError(
                    "REASON_REQUIRED", "请填写拒绝或取消原因，不能附带明细", 422
                )
            if kind == "outbound" and action == "reject":
                raise BusinessError("INVALID_ACTION", "出库申请仅支持取消或执行", 422)
            header.status = "rejected" if action == "reject" else "cancelled"
            names = ["status", "operation_id"]
            header.operation_id = ctx["operation_id"]
            if kind != "outbound":
                header.decided_by, header.decided_at, header.decision_reason = (
                    p.user_id,
                    timezone.now(),
                    reason.strip(),
                )
                names.extend(["decided_by", "decided_at", "decision_reason"])
            header.save(update_fields=names)
        else:
            if (
                kind == "outbound"
                and action != "execute"
                or kind != "outbound"
                and action != "approve"
            ):
                raise BusinessError("INVALID_ACTION", "该单据不支持此状态转换", 422)
            lines = list(line_rows(p.tenant_id, kind, pk))
            rows = [
                {
                    f.name: getattr(line, f.name)
                    for f in line._meta.local_concrete_fields
                }
                for line in lines
            ]
            executions = []
            if kind == "outbound":
                if len(executions_input) != len(lines):
                    raise BusinessError(
                        "INVALID_LINES", "必须逐条确认全部实发明细", 422
                    )
                parsed = executions_input
                if len({row["id"] for row in parsed}) != len(lines) or {
                    row["id"] for row in parsed
                } != {line.pk for line in lines}:
                    raise BusinessError(
                        "INVALID_LINES", "实发明细存在重复或不属于此单", 422
                    )
                by_id = {row["id"]: row for row in parsed}
                executions = [by_id[line.pk] for line in lines]
                for line, execution in zip(lines, executions):
                    catalog.order_reference(
                        "items", p.tenant_id, line.item_id, active=True
                    )
                    location = execution.get("location_id") or line.location_id
                    if execution["actual_qty"] > 0 and not location:
                        raise BusinessError("LOCATION_REQUIRED", "请选择发放库位", 422)
                    if location:
                        catalog.order_reference(
                            "locations", p.tenant_id, location, active=True
                        )
                rows += executions
            elif payload.get("lines"):
                raise BusinessError("FROZEN_DOCUMENT", "审批不能修改申请明细", 422)
            posting.lock_sources(ctx, rows)
            keys = []
            if kind == "outbound":
                keys = [
                    (
                        line.item_id,
                        execution.get("location_id") or line.location_id,
                        "available",
                    )
                    for line, execution in zip(lines, executions)
                    if line.item_type == "consumable" and execution["actual_qty"] > 0
                ]
            elif kind == "scrap":
                keys = [
                    (line.item_id, line.location_id, line.quality)
                    for line in lines
                    if line.item_type == "consumable"
                ]
            else:
                keys = [
                    (line.item_id, line.location_id, quality)
                    for line in lines
                    if line.item_type == "consumable"
                    for quality in (
                        [line.quality, line.to_quality]
                        if line.to_quality
                        else [line.quality]
                    )
                ]
            posting.lock_stock(ctx, keys)
            if kind == "outbound":
                posting.issue(ctx, header, lines, executions)
            else:
                self_approval = header.applicant_id == p.user_id
                if self_approval and (
                    not ctx["tenant"].allow_self_approval or not reason.strip()
                ):
                    posting.fail(
                        "SELF_APPROVAL_FORBIDDEN",
                        "禁止自批；单人例外须已开启开关并填写原因",
                    )
                if kind == "scrap":
                    posting.dispose(ctx, header, lines)
                else:
                    posting.adjust(ctx, header, lines)
                header.status, header.approver_id, header.approved_at = (
                    "approved",
                    p.user_id,
                    timezone.now(),
                )
                header.self_approved, header.allow_self_approval_snapshot = (
                    self_approval,
                    ctx["tenant"].allow_self_approval,
                )
                header.operation_id = ctx["operation_id"]
                header.save(
                    update_fields=[
                        "status",
                        "approver_id",
                        "approved_at",
                        "self_approved",
                        "allow_self_approval_snapshot",
                        "operation_id",
                    ]
                )
        return result(ctx, kind, header, action, reason)

    return command(
        principal, kind, f"{pk}.{action}", key, payload, work, rows_for_discovery
    )


def copy_draft(principal, kind, pk):
    header = header_get(principal, kind, pk)
    raw = record(header)
    typ = header.disposal_type if kind == "scrap" else header.type
    form = HeaderForm(kind=kind)
    header_data = {
        field: raw.get(field) for field in form.fields if field != "order_no"
    }
    header_data["type"] = typ
    if kind == "inbound" and typ == "adjustment_gain":
        raise BusinessError("COPY_NOT_SUPPORTED", "盘盈入库请从调整单重新申请", 409)
    rows = []
    for line in line_rows(principal.tenant_id, kind, pk):
        values = record(line)
        if kind == "outbound":
            quantity = (
                line.requested_qty - line.actual_qty
                if header.status == "completed"
                else line.requested_qty
            )
            if quantity <= 0:
                continue
            values["quantity"] = str(quantity)
        fields = LineForm(kind=kind, order_type=typ).fields
        rows.append(
            {
                field: values.get(field)
                for field in fields
                if values.get(field) is not None
            }
        )
    if not rows:
        raise BusinessError("NO_REMAINDER", "没有可复制的未满足量", 409)
    return {**header_data, "lines": rows}
