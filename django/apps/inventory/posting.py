"""Atomic posting primitives used by order transitions; P5 acceptance stays separate."""

from decimal import Decimal
import secrets
from django.db.models import F, Q, Sum
from django.db import connection
from django.utils import timezone
from apps.core.services import allocate_asset_numbers
from .models import (
    AdjustmentItem,
    AssetCard,
    AssetEvent,
    InboundItem,
    InboundOrder,
    Inventory,
    OutboundAsset,
    OutboundItem,
    OutboundOrder,
    ScrapItem,
    ScrapOrder,
    StockLedger,
)
from shared.errors import BusinessError


def fail(code, message):
    raise BusinessError(code, message, 409)


def fetch(model, tenant_id, pk):
    obj = model.objects.filter(tenant_id=tenant_id, pk=pk).first()
    if obj is None:
        raise BusinessError("NOT_FOUND", "记录不存在或不属于当前学校", 404)
    return obj


def active_user(context, user_id):
    user = context["users"].get(user_id)
    if user is None or not user.active:
        fail("USER_UNAVAILABLE", "领用人不存在或已停用")
    return user


def source_ids(tenant_id, rows):
    mapping = {
        AdjustmentItem: set(),
        InboundItem: set(),
        OutboundAsset: set(),
        OutboundItem: set(),
        ScrapItem: set(),
    }
    fields = {
        "source_inbound_item_id": InboundItem,
        "source_outbound_asset_id": OutboundAsset,
        "source_outbound_item_id": OutboundItem,
        "source_loss_item_id": ScrapItem,
        "adjustment_item_id": AdjustmentItem,
    }
    cards = set()
    for row in rows:
        for name, model in fields.items():
            if row.get(name):
                mapping[model].add(row[name])
        if row.get("asset_card_id"):
            cards.add(row["asset_card_id"])
        cards.update(row.get("asset_ids") or [])
    issues = list(
        OutboundAsset.objects.filter(tenant_id=tenant_id).filter(
            Q(pk__in=mapping[OutboundAsset])
            | Q(asset_card_id__in=cards, closed_at__isnull=True)
        )
    )
    mapping[OutboundAsset].update(issue.pk for issue in issues)
    mapping[OutboundItem].update(issue.outbound_item_id for issue in issues)
    cards.update(issue.asset_card_id for issue in issues)
    return mapping, cards


def discover_users(tenant_id, rows, applicant_id=None):
    _, cards = source_ids(tenant_id, rows)
    ids = {row["recipient_id"] for row in rows if row.get("recipient_id")}
    if applicant_id:
        ids.add(applicant_id)
    ids.update(
        AssetCard.objects.filter(
            tenant_id=tenant_id, pk__in=cards, responsible_user_id__isnull=False
        ).values_list("responsible_user_id", flat=True)
    )
    return ids


def lock_sources(context, rows):
    tenant_id = context["principal"].tenant_id
    mapping, cards = source_ids(tenant_id, rows)
    for model, ids in sorted(mapping.items(), key=lambda pair: pair[0]._meta.db_table):
        query = model.objects.filter(tenant_id=tenant_id, pk__in=ids).order_by("id")
        if model is InboundItem:
            # Runtime has no UPDATE on this immutable source, so PostgreSQL row
            # locking is unavailable. Lock its stable identity before quota reads.
            with connection.cursor() as cursor:
                for pk in sorted(ids):
                    cursor.execute(
                        "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
                        [f"source:{tenant_id}:inbound_items:{pk}"],
                    )
            locked = list(query)
        else:
            locked = list(query.select_for_update(no_key=True))
        if len(locked) != len(ids):
            raise BusinessError("NOT_FOUND", "来源记录不存在或不属于当前学校", 404)
    context["cards"] = {
        card.pk: card
        for card in AssetCard.objects.select_for_update(no_key=True)
        .filter(tenant_id=tenant_id, pk__in=cards)
        .order_by("id")
    }
    if len(context["cards"]) != len(cards):
        raise BusinessError("NOT_FOUND", "资产不存在或不属于当前学校", 404)


def lock_stock(context, keys):
    tenant_id = context["principal"].tenant_id
    context["stocks"] = {}
    for item_id, location_id, quality in sorted(set(keys)):
        row, _ = Inventory.objects.get_or_create(
            tenant_id=tenant_id,
            item_id=item_id,
            location_id=location_id,
            quality=quality,
        )
        row = Inventory.objects.select_for_update(no_key=True).get(
            tenant_id=tenant_id, pk=row.pk
        )
        context["stocks"][(item_id, location_id, quality)] = row


def move_stock(
    context, item_id, location_id, quality, quantity, direction, **reference
):
    p = context["principal"]
    row = context["stocks"][(item_id, location_id, quality)]
    change = quantity if direction == "in" else -quantity
    query = Inventory.objects.filter(tenant_id=p.tenant_id, pk=row.pk)
    if direction == "out":
        query = query.filter(quantity__gte=quantity)
    if query.update(quantity=F("quantity") + change) != 1:
        fail("INSUFFICIENT_STOCK", "库存不足，整张单据未执行")
    row = Inventory.objects.get(tenant_id=p.tenant_id, pk=row.pk)
    context["stocks"][(item_id, location_id, quality)] = row
    StockLedger.objects.create(
        tenant_id=p.tenant_id,
        item_id=item_id,
        location_id=location_id,
        quality=quality,
        direction=direction,
        quantity=quantity,
        balance_after=row.quantity,
        operator_id=p.user_id,
        operation_id=context["operation_id"],
        **reference,
    )


def event(context, card, action, old_status, **references):
    p = context["principal"]
    AssetEvent.objects.create(
        tenant_id=p.tenant_id,
        asset_card_id=card.pk,
        action=action,
        from_status=old_status,
        to_status=card.status,
        location_id=card.location_id,
        responsible_user_id=card.responsible_user_id,
        operator_id=p.user_id,
        operation_id=context["operation_id"],
        **references,
    )


def transition(
    context, card, status, location_id, responsible_id, action, **references
):
    old = card.status
    card.status, card.location_id, card.responsible_user_id = (
        status,
        location_id,
        responsible_id,
    )
    card.save(update_fields=["status", "location_id", "responsible_user_id"])
    event(context, card, action, old, **references)


def new_cards(context, line, action):
    p = context["principal"]
    for number in allocate_asset_numbers(p.tenant_id, int(line.quantity)):
        card = AssetCard.objects.create(
            tenant_id=p.tenant_id,
            asset_no=number,
            item_id=line.item_id,
            source_inbound_item_id=line.pk,
            location_id=line.location_id,
            status="in_stock" if line.quality == "available" else "repair",
            qr_token=secrets.token_urlsafe(32),
            original_value=line.unit_price,
            purchase_date=timezone.localdate(),
        )
        event(
            context,
            card,
            action,
            None,
            inbound_item_id=line.pk,
            adjustment_item_id=line.adjustment_item_id,
        )


def source_issue(context, row):
    p = context["principal"]
    issue = fetch(OutboundAsset, p.tenant_id, row["source_outbound_asset_id"])
    source_line = fetch(OutboundItem, p.tenant_id, issue.outbound_item_id)
    parent = fetch(OutboundOrder, p.tenant_id, source_line.order_id)
    if (
        parent.status != "completed"
        or parent.type not in {"requisition", "transfer"}
        or issue.closed_at
        or issue.item_id != row["item_id"]
    ):
        fail("INVALID_RETURN_SOURCE", "资产发放来源无效或已结案")
    card = context["cards"][issue.asset_card_id]
    if card.status != "in_use" or card.responsible_user_id != source_line.recipient_id:
        fail("STALE_ASSET", "资产责任或状态已变化，请重新确认")
    return issue, card


def check_consumable_return(context, row):
    p = context["principal"]
    source = fetch(OutboundItem, p.tenant_id, row["source_outbound_item_id"])
    parent = fetch(OutboundOrder, p.tenant_id, source.order_id)
    returned = InboundItem.objects.filter(
        tenant_id=p.tenant_id, source_outbound_item_id=source.pk
    ).aggregate(n=Sum("quantity"))["n"] or Decimal(0)
    if (
        source.item_id != row["item_id"]
        or source.item_type != "consumable"
        or parent.status != "completed"
        or parent.type not in {"requisition", "transfer"}
    ):
        fail("INVALID_RETURN_SOURCE", "耗材发放来源无效")
    if returned + row["quantity"] > source.actual_qty:
        fail("RETURN_EXCEEDS_ISSUED", "累计归还数量超过实发数量")


def receive(context, header, rows, *, close_reason="return", event_transfer_id=None):
    p = context["principal"]
    for row in rows:
        if header.type == "return":
            if row["item_type"] == "consumable":
                check_consumable_return(context, row)
            else:
                source_issue(context, row)
        data = {
            k: row.get(k)
            for k in [
                "item_id",
                "item_type",
                "quantity",
                "unit_price",
                "location_id",
                "quality",
                "source_outbound_item_id",
                "source_outbound_asset_id",
                "adjustment_item_id",
            ]
        }
        line = InboundItem.objects.create(
            tenant_id=p.tenant_id, order_id=header.pk, **data
        )
        if line.item_type == "consumable":
            move_stock(
                context,
                line.item_id,
                line.location_id,
                line.quality,
                line.quantity,
                "in",
                inbound_item_id=line.pk,
            )
        elif header.type == "return":
            issue, card = source_issue(context, row)
            transition(
                context,
                card,
                "in_stock" if line.quality == "available" else "repair",
                line.location_id,
                None,
                "return",
                inbound_item_id=line.pk,
                outbound_asset_id=issue.pk,
                transfer_id=event_transfer_id,
            )
            issue.closed_at, issue.close_reason = timezone.now(), close_reason
            issue.save(update_fields=["closed_at", "close_reason"])
        else:
            new_cards(
                context,
                line,
                "purchase"
                if header.type == "purchase"
                else "found_gain"
                if header.type == "adjustment_gain"
                else "opening",
            )
        row["received_line_id"] = line.pk


def issue(context, header, lines, executions):
    p = context["principal"]
    for line, execution in zip(lines, executions):
        quantity = execution["actual_qty"]
        if quantity < line.requested_qty and not execution.get("shortfall_reason"):
            fail("SHORTFALL_REASON_REQUIRED", "实发少于申请时必须填写差额原因")
        if quantity > line.requested_qty:
            fail("EXCESS_ISSUANCE", "实发数量不能超过申请数量")
        if line.item_type == "fixed" and quantity != quantity.to_integral_value():
            fail("FRACTIONAL_ASSET", "固定资产数量必须为整数")
        location = execution.get("location_id") or line.location_id
        recipient = execution.get("recipient_id") or line.recipient_id
        if (
            line.location_id
            and location != line.location_id
            or line.recipient_id
            and recipient != line.recipient_id
        ):
            fail("SUBMITTED_LINE_FROZEN", "不能更改已提交的库位或领用人")
        if quantity and not location:
            fail("LOCATION_REQUIRED", "请选择发放库位")
        if header.type in {"requisition", "transfer"} and quantity:
            active_user(context, recipient)
        line.actual_qty, line.location_id, line.recipient_id = (
            quantity,
            location,
            recipient,
        )
        line.shortfall_reason = execution.get("shortfall_reason") or None
        line.save(
            update_fields=[
                "actual_qty",
                "location_id",
                "recipient_id",
                "shortfall_reason",
            ]
        )
        if not quantity:
            if execution.get("asset_ids"):
                fail("ASSET_COUNT_MISMATCH", "零发放不能选择资产")
            continue
        if header.type == "purchase_return":
            source = fetch(InboundItem, p.tenant_id, line.source_inbound_item_id)
            source_header = fetch(InboundOrder, p.tenant_id, source.order_id)
            total = OutboundItem.objects.filter(
                tenant_id=p.tenant_id, source_inbound_item_id=source.pk
            ).filter(Q(order__status="completed") | Q(order_id=header.pk)).aggregate(
                n=Sum("actual_qty")
            )["n"] or Decimal(0)
            if (
                source_header.type != "purchase"
                or source.item_id != line.item_id
                or total > source.quantity
            ):
                fail("INVALID_PURCHASE_RETURN", "来源非采购或累计退货量超出采购量")
        if line.item_type == "consumable":
            if execution.get("asset_ids"):
                fail("UNEXPECTED_ASSETS", "耗材不能选择资产卡")
            move_stock(
                context,
                line.item_id,
                location,
                "available",
                quantity,
                "out",
                outbound_item_id=line.pk,
            )
        else:
            ids = execution.get("asset_ids") or []
            if len(ids) != int(quantity):
                fail("ASSET_COUNT_MISMATCH", "所选资产件数必须等于实发数量")
            for card_id in sorted(ids):
                card = context["cards"][card_id]
                if (
                    card.item_id != line.item_id
                    or card.status != "in_stock"
                    or card.location_id != location
                ):
                    fail("ASSET_UNAVAILABLE", "资产物资、状态或库位不匹配")
                if OutboundAsset.objects.filter(
                    tenant_id=p.tenant_id, asset_card_id=card.pk, closed_at__isnull=True
                ).exists():
                    fail("ASSET_HAS_OPEN_ISSUE", "资产仍有未结案发放关系")
                if (
                    header.type == "purchase_return"
                    and card.source_inbound_item_id != line.source_inbound_item_id
                ):
                    fail("INVALID_PURCHASE_RETURN", "资产不属于所选采购明细")
                relation = OutboundAsset.objects.create(
                    tenant_id=p.tenant_id,
                    outbound_item_id=line.pk,
                    item_id=line.item_id,
                    asset_card_id=card.pk,
                    closed_at=timezone.now()
                    if header.type == "purchase_return"
                    else None,
                    close_reason="purchase_return"
                    if header.type == "purchase_return"
                    else None,
                )
                transition(
                    context,
                    card,
                    "returned_to_supplier"
                    if header.type == "purchase_return"
                    else "in_use",
                    location,
                    None if header.type == "purchase_return" else recipient,
                    "purchase_return" if header.type == "purchase_return" else "issue",
                    outbound_asset_id=relation.pk,
                    transfer_id=header.transfer_id,
                )
    if not any(row["actual_qty"] for row in executions):
        fail("ZERO_ISSUANCE", "零发放应取消申请")
    header.status, header.operator_id, header.operation_id = (
        "completed",
        p.user_id,
        context["operation_id"],
    )
    header.executed_at, header.self_issued = (
        timezone.now(),
        header.applicant_id == p.user_id,
    )
    header.save(
        update_fields=[
            "status",
            "operator_id",
            "operation_id",
            "executed_at",
            "self_issued",
        ]
    )


def dispose(context, header, lines):
    p = context["principal"]
    for line in lines:
        if header.disposal_type == "stock_damage":
            move_stock(
                context,
                line.item_id,
                line.location_id,
                line.quality,
                line.quantity,
                "out",
                scrap_item_id=line.pk,
            )
            continue
        card = context["cards"][line.asset_card_id]
        if header.disposal_type == "scrap":
            if (
                card.status not in {"in_stock", "repair"}
                or card.responsible_user_id is not None
            ):
                fail("ASSET_MUST_BE_RETURNED", "在用资产须先归还后才能报废")
            transition(
                context,
                card,
                "scrapped",
                card.location_id,
                None,
                "scrap",
                scrap_item_id=line.pk,
            )
        elif header.disposal_type == "loss":
            if card.status not in {"in_stock", "in_use", "repair"}:
                fail("INVALID_ASSET_STATE", "当前资产状态不能核销遗失")
            if line.source_outbound_asset_id:
                relation = fetch(
                    OutboundAsset, p.tenant_id, line.source_outbound_asset_id
                )
                if card.status != "in_use" or relation.closed_at is not None:
                    fail("STALE_ASSET", "遗失来源关系已变化")
                (
                    relation.closed_at,
                    relation.close_reason,
                    relation.loss_scrap_item_id,
                ) = timezone.now(), "loss", line.pk
                relation.save(
                    update_fields=["closed_at", "close_reason", "loss_scrap_item_id"]
                )
            elif card.status == "in_use":
                fail("SOURCE_ISSUE_REQUIRED", "在用资产遗失必须关联未结案发放")
            transition(
                context,
                card,
                "lost",
                card.location_id,
                None,
                "loss",
                scrap_item_id=line.pk,
            )
        else:
            loss = fetch(ScrapItem, p.tenant_id, line.source_loss_item_id)
            loss_header = fetch(ScrapOrder, p.tenant_id, loss.scrap_id)
            if (
                card.status != "lost"
                or loss_header.status != "approved"
                or loss.disposal_type != "loss"
                or loss.asset_card_id != card.pk
            ):
                fail("INVALID_LOSS_SOURCE", "找回来源不是该卡已批准遗失记录")
            if AssetEvent.objects.filter(
                tenant_id=p.tenant_id, action="found", source_loss_item_id=loss.pk
            ).exists():
                fail("ALREADY_FOUND", "该次遗失已找回")
            transition(
                context,
                card,
                "in_stock" if line.recovery_quality == "available" else "repair",
                line.recovery_location_id,
                None,
                "found",
                scrap_item_id=line.pk,
                source_loss_item_id=loss.pk,
            )


def adjust(context, header, lines):
    p = context["principal"]
    gains = []
    for line in lines:
        if line.item_type == "fixed":
            gains.append(
                {
                    "item_id": line.item_id,
                    "item_type": "fixed",
                    "quantity": Decimal(1),
                    "unit_price": line.original_value,
                    "location_id": line.location_id,
                    "quality": line.quality,
                    "adjustment_item_id": line.pk,
                }
            )
            continue
        row = context["stocks"][(line.item_id, line.location_id, line.quality)]
        if row.version != line.inventory_version or row.quantity != line.book_qty:
            fail("STALE_SNAPSHOT", "账面数量或版本已变化，请重新观察并复制申请")
        if header.type == "quality_transfer":
            target = context["stocks"][
                (line.item_id, line.location_id, line.to_quality)
            ]
            if target.version != line.target_inventory_version:
                fail("STALE_SNAPSHOT", "目标质量库存版本已变化")
            move_stock(
                context,
                line.item_id,
                line.location_id,
                line.quality,
                line.quantity,
                "out",
                adjustment_item_id=line.pk,
            )
            move_stock(
                context,
                line.item_id,
                line.location_id,
                line.to_quality,
                line.quantity,
                "in",
                adjustment_item_id=line.pk,
            )
        else:
            move_stock(
                context,
                line.item_id,
                line.location_id,
                line.quality,
                line.quantity,
                line.direction,
                adjustment_item_id=line.pk,
            )
    if gains:
        inbound = InboundOrder.objects.create(
            tenant_id=p.tenant_id,
            order_no="AG-" + secrets.token_hex(16),
            type="adjustment_gain",
            operator_id=p.user_id,
            operation_id=context["operation_id"],
        )
        receive(context, inbound, gains)
