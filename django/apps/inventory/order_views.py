import json
from uuid import uuid4
from django import forms
from django.core.paginator import Paginator
from django.http import JsonResponse
from django.middleware.csrf import CsrfViewMiddleware
from django.shortcuts import render, redirect
from django.views.decorators.csrf import csrf_exempt
from apps.core.auth_views import error_response
from apps.core import services as core
from apps.catalog import services as catalog
from apps.catalog.models import Item, Location
from shared.errors import BusinessError
from shared.rbac.permission import require_role
from . import orders
from .models import AssetCard, InboundItem, OutboundItem, OutboundAsset, ScrapItem
from .order_forms import HeaderForm, LineForm, ExecuteLineForm, TYPES


def html_record(kind, row):
    return {
        **row,
        "status_label": {
            "pending": "待处理",
            "completed": "已完成",
            "approved": "已批准",
            "rejected": "已拒绝",
            "cancelled": "已取消",
        }.get(row["status"], row["status"]),
        "type_label": dict(TYPES[kind]).get(
            row.get("type", row.get("disposal_type")), "盘盈入库"
        ),
    }


def html_lines(principal, rows):
    tenant_id = principal.tenant_id
    items = dict(
        Item.objects.filter(
            tenant_id=tenant_id, pk__in=[r["item_id"] for r in rows]
        ).values_list("id", "name")
    )
    locations = dict(
        Location.objects.filter(
            tenant_id=tenant_id,
            pk__in=[r.get("location_id") for r in rows if r.get("location_id")],
        ).values_list("id", "name")
    )
    cards = dict(
        AssetCard.objects.filter(
            tenant_id=tenant_id,
            pk__in=[r.get("asset_card_id") for r in rows if r.get("asset_card_id")],
        ).values_list("id", "asset_no")
    )
    return [
        {
            **r,
            "item_label": items.get(r["item_id"], r["item_id"]),
            "location_label": locations.get(r.get("location_id"), "—"),
            "asset_label": cards.get(r.get("asset_card_id"), "—"),
            "quality_label": {"available": "可用", "damaged": "损坏"}.get(
                r.get("quality"), "—"
            ),
        }
        for r in rows
    ]


def choices(principal):
    tenant_id = principal.tenant_id
    cards = AssetCard.objects.filter(tenant_id=tenant_id)
    if principal.role != "admin":
        cards = cards.filter(responsible_user_id=principal.user_id)
    issues = OutboundAsset.objects.filter(tenant_id=tenant_id, closed_at__isnull=True)
    out = OutboundItem.objects.filter(
        tenant_id=tenant_id,
        order__status="completed",
        order__type__in=["requisition", "transfer"],
    )
    losses = ScrapItem.objects.filter(
        tenant_id=tenant_id, disposal_type="loss", scrap__status="approved"
    )
    if principal.role != "admin":
        issues = issues.filter(outbound_item__recipient_id=principal.user_id)
        out = out.filter(recipient_id=principal.user_id)
        losses = losses.filter(scrap__applicant_id=principal.user_id)
    asset_options = [(row.pk, row.asset_no) for row in cards.order_by("id")]
    # Lost cards have no current responsible user; recover through the applicant's loss record.
    if principal.role != "admin":
        asset_options.extend(
            AssetCard.objects.filter(
                tenant_id=tenant_id,
                pk__in=losses.values("asset_card_id"),
                status="lost",
            ).values_list("id", "asset_no")
        )
    return {
        "item_id": catalog.order_choices("items", tenant_id),
        "location_id": catalog.order_choices("locations", tenant_id),
        "recovery_location_id": catalog.order_choices("locations", tenant_id),
        "supplier_id": catalog.order_choices("suppliers", tenant_id),
        "recipient_id": [
            (row["id"], row["name"]) for row in core.user_choices(tenant_id)
        ],
        "asset_card_id": asset_options,
        "asset_ids": asset_options,
        "source_inbound_item_id": [
            (row.pk, f"采购 {row.order.order_no} · 明细 {row.pk}")
            for row in InboundItem.objects.filter(
                tenant_id=tenant_id, order__type="purchase"
            )
            .select_related("order")
            .order_by("id")
        ],
        "source_outbound_item_id": [
            (row.pk, f"发放 {row.order.order_no} · 明细 {row.pk}")
            for row in out.select_related("order").order_by("id")
        ],
        "source_outbound_asset_id": [
            (row.pk, f"资产 {row.asset_card.asset_no} · 发放关系 {row.pk}")
            for row in issues.select_related("asset_card").order_by("id")
        ],
        "source_loss_item_id": [
            (row.pk, f"遗失 {row.scrap.order_no} · 明细 {row.pk}")
            for row in losses.select_related("scrap").order_by("id")
        ],
    }


def configure(form, options):
    for name, values in options.items():
        if name not in form.fields:
            continue
        if name == "asset_ids":
            form.fields[name].widget = forms.SelectMultiple(choices=values)
        else:
            form.fields[name].widget = forms.Select(choices=[("", "请选择"), *values])


@csrf_exempt
@require_role("admin", "user")
def api_orders(request, kind, pk=None, action=None):
    try:
        if request.method == "GET":
            if action == "copy":
                return JsonResponse(
                    {"draft": orders.copy_draft(request.principal, kind, pk)}
                )
            if action:
                return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
            if pk:
                return JsonResponse(orders.detail(request.principal, kind, pk))
            page = Paginator(
                orders.queryset(request.principal, kind).order_by("-id"), 25
            ).get_page(request.GET.get("page"))
            return JsonResponse(
                {
                    "records": [orders.record(obj) for obj in page],
                    "page": page.number,
                    "pages": page.paginator.num_pages,
                }
            )
        if request.method not in {"POST", "DELETE"}:
            return JsonResponse(
                {
                    "error": "METHOD_NOT_ALLOWED",
                    "message": "提交后不能编辑；取消后复制新申请",
                },
                status=405,
            )
        if not request.headers.get("Authorization", "").startswith("Bearer "):
            if CsrfViewMiddleware(lambda r: None).process_view(
                request, lambda r: None, (), {}
            ):
                raise BusinessError("CSRF_REJECTED", "CSRF校验失败", 403)
        if request.content_type != "application/json":
            raise BusinessError("INVALID_CONTENT_TYPE", "请提交JSON请求", 415)
        try:
            payload = json.loads(request.body or b"{}")
        except (ValueError, UnicodeError):
            raise BusinessError("INVALID_JSON", "JSON格式错误") from None
        key = request.headers.get("Idempotency-Key")
        if request.method == "DELETE":
            if not pk or action:
                raise BusinessError("INVALID_ACTION", "只能取消指定单据", 422)
            result, replayed = orders.decide(
                request.principal, kind, pk, "cancel", key, payload
            )
        elif pk:
            if not action:
                raise BusinessError("FROZEN_DOCUMENT", "已提交内容不能编辑", 409)
            result, replayed = orders.decide(
                request.principal, kind, pk, action, key, payload
            )
        else:
            if action:
                raise BusinessError("INVALID_ACTION", "单据操作无效", 422)
            result, replayed = orders.create(request.principal, kind, key, payload)
        response = JsonResponse(result, status=200 if pk or replayed else 201)
        response["Idempotency-Replayed"] = "true" if replayed else "false"
        return response
    except BusinessError as error:
        return error_response(error)


@require_role("admin")
def api_observe(request):
    if request.method != "GET":
        return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
    try:
        item_id = int(request.GET.get("item_id", "0"))
        location_id = int(request.GET.get("location_id", "0"))
        if not 0 < item_id < 2**63 or not 0 < location_id < 2**63:
            raise ValueError()
        return JsonResponse(
            orders.observe(
                request.principal,
                item_id,
                location_id,
                request.GET.get("quality", "available"),
            )
        )
    except ValueError:
        return JsonResponse({"error": "INVALID_DATA"}, status=422)
    except BusinessError as error:
        return error_response(error)


class DecisionForm(forms.Form):
    reason = forms.CharField(
        label="处理原因（拒绝、取消及自批必填）",
        required=False,
        max_length=2000,
        widget=forms.Textarea,
    )


@require_role("admin", "user")
def html_orders(request, kind, pk=None, action=None):
    try:
        p = request.principal
        orders.allowed(p, kind)
        if action and action not in {
            "new",
            "copy",
            "approve",
            "execute",
            "reject",
            "cancel",
        }:
            raise BusinessError("NOT_FOUND", "单据页面不存在", 404)
        if request.method not in {"GET", "POST"}:
            return JsonResponse({"error": "METHOD_NOT_ALLOWED"}, status=405)
        if action in {"approve", "execute", "reject"} and p.role != "admin":
            raise BusinessError("FORBIDDEN", "该操作仅限管理员", 403)
        fragment = request.headers.get("HX-Request") == "true"
        ctx = {
            "kind": kind,
            "label": orders.REGISTRY[kind][3],
            "principal": p,
            "idempotency_key": str(uuid4()),
            "base_url": f"/{kind}/",
        }
        template = "orders/list_fragment.html"
        status = 200
        if action in {"new", "copy"}:
            draft = orders.copy_draft(p, kind, pk) if action == "copy" else {}
            typ = (
                request.POST.get("type")
                if request.method == "POST"
                else draft.get("type") or request.GET.get("type") or TYPES[kind][0][0]
            )
            if typ not in dict(TYPES[kind]):
                raise BusinessError("INVALID_TYPE", "业务类型无效", 422)
            options = choices(p)
            header = HeaderForm(
                request.POST if request.method == "POST" else None,
                kind=kind,
                initial=draft or {"type": typ},
            )
            configure(header, options)
            header.fields["type"].widget = forms.HiddenInput()
            FormSet = forms.formset_factory(
                LineForm,
                extra=0,
                min_num=1,
                max_num=50,
                absolute_max=50,
                validate_min=True,
                validate_max=True,
                can_delete=True,
            )
            formset = FormSet(
                request.POST if request.method == "POST" else None,
                prefix="lines",
                form_kwargs={"kind": kind, "order_type": typ},
                initial=draft.get("lines"),
            )
            empty_form = formset.empty_form
            for form in [*formset, empty_form]:
                configure(form, options)
                for name in ("inventory_version", "target_inventory_version"):
                    if name in form.fields:
                        form.fields[name].widget = forms.HiddenInput()
                for name in ("book_qty", "observed_at"):
                    if name in form.fields:
                        form.fields[name].widget.attrs["readonly"] = True
            if request.method == "POST" and header.is_valid() and formset.is_valid():
                payload = {
                    **header.cleaned_data,
                    "lines": [
                        form.cleaned_data
                        for form in formset
                        if form.cleaned_data and not form.cleaned_data.get("DELETE")
                    ],
                }
                for row in payload["lines"]:
                    row.pop("DELETE", None)
                try:
                    result, _ = orders.create(
                        p, kind, request.POST.get("idempotency_key"), payload
                    )
                    response = redirect(f"/{kind}/{result['document']['id']}/")
                    if fragment:
                        response.status_code = 200
                        response["HX-Redirect"] = response["Location"]
                    return response
                except BusinessError as error:
                    header.add_error(None, error.message)
                    status = error.status
            elif request.method == "POST":
                status = 422
            ctx.update(
                header_form=header,
                formset=formset,
                empty_form=empty_form,
                types=TYPES[kind],
                selected_type=typ,
            )
            template = "orders/new_fragment.html"
        elif action:
            data = orders.detail(p, kind, pk)
            data["lines"] = html_lines(p, data["lines"])
            if data["record"]["status"] != "pending":
                raise BusinessError("FROZEN_DOCUMENT", "单据已终结", 409)
            form = DecisionForm(request.POST if request.method == "POST" else None)
            execution = None
            if action == "execute":
                if kind != "outbound":
                    raise BusinessError("INVALID_ACTION", "此单不支持执行操作", 422)
                FormSet = forms.formset_factory(
                    ExecuteLineForm,
                    extra=0,
                    min_num=1,
                    max_num=50,
                    absolute_max=50,
                    validate_max=True,
                )
                execution = FormSet(
                    request.POST if request.method == "POST" else None,
                    prefix="execute",
                    initial=[
                        {
                            "id": row["id"],
                            "actual_qty": row["requested_qty"],
                            "location_id": row["location_id"],
                            "recipient_id": row["recipient_id"],
                        }
                        for row in data["lines"]
                    ],
                )
                options = choices(p)
                for subform, line in zip(execution, data["lines"]):
                    configure(subform, options)
                    subform.item_label = line["item_label"]
                    subform.requested_qty = line["requested_qty"]
            if (
                request.method == "POST"
                and form.is_valid()
                and (execution is None or execution.is_valid())
            ):
                payload = dict(form.cleaned_data)
                if execution is not None:
                    payload["lines"] = [sub.cleaned_data for sub in execution]
                try:
                    orders.decide(
                        p,
                        kind,
                        pk,
                        action,
                        request.POST.get("idempotency_key"),
                        payload,
                    )
                    return redirect(f"/{kind}/{pk}/")
                except BusinessError as error:
                    form.add_error(None, error.message)
                    status = error.status
            ctx.update(
                form=form, execution=execution, document=data["record"], action=action
            )
            template = "orders/decision_fragment.html"
        elif pk:
            data = orders.detail(p, kind, pk)
            data["record"] = html_record(kind, data["record"])
            data["lines"] = html_lines(p, data["lines"])
            ctx.update(data)
            if kind == "scrap":
                from apps.attachments.services import for_scrap

                ctx["evidence"] = for_scrap(p, pk)
            template = "orders/detail_fragment.html"
        else:
            page = Paginator(orders.queryset(p, kind).order_by("-id"), 25).get_page(
                request.GET.get("page")
            )
            ctx.update(
                page=page,
                records=[html_record(kind, orders.record(row)) for row in page],
            )
        ctx["fragment_template"] = template
        return render(
            request, template if fragment else "orders/page.html", ctx, status=status
        )
    except BusinessError as error:
        return error_response(error)
