from django import forms
from decimal import Decimal
from shared.errors import BusinessError

TYPES = {
    "inbound": [("purchase", "采购入库"), ("opening", "期初入库"), ("return", "归还")],
    "outbound": [
        ("requisition", "领用申请"),
        ("purchase_return", "采购退货"),
        ("transfer", "责任转移"),
    ],
    "scrap": [
        ("scrap", "固定资产报废"),
        ("loss", "固定资产遗失"),
        ("recovery", "遗失找回"),
        ("stock_damage", "耗材报损"),
    ],
    "adjustment": [("count", "库存盘点调整"), ("quality_transfer", "库存质量转换")],
}
QUALITY = [("available", "可用"), ("damaged", "损坏")]
ID_MAX = 2**63 - 1


class AssetIDsField(forms.Field):
    widget = forms.SelectMultiple

    def clean(self, value):
        if value in (None, ""):
            return []
        values = value if isinstance(value, list) else str(value).split(",")
        try:
            ids = [int(v) for v in values]
        except (ValueError, TypeError):
            raise forms.ValidationError("请选择有效资产") from None
        if (
            len(ids) > 1000
            or len(set(ids)) != len(ids)
            or any(v < 1 or v > ID_MAX for v in ids)
        ):
            raise forms.ValidationError("资产不能重复且须为有效编号")
        return ids


class HeaderForm(forms.Form):
    order_no = forms.CharField(
        label="单号（留空自动生成）", max_length=80, required=False
    )
    type = forms.ChoiceField(label="业务类型")
    supplier_id = forms.IntegerField(
        label="供应商", min_value=1, max_value=ID_MAX, required=False
    )
    reason = forms.CharField(
        label="申请原因", max_length=2000, required=False, widget=forms.Textarea
    )
    reason_category = forms.ChoiceField(
        label="调整原因",
        required=False,
        choices=[
            ("", "请选择"),
            ("gain", "盘盈"),
            ("loss", "盘亏"),
            ("entry_error", "录入差错"),
            ("quality_change", "质量变化"),
            ("other", "其他"),
        ],
    )
    remark = forms.CharField(
        label="备注", max_length=2000, required=False, widget=forms.Textarea
    )

    def __init__(self, *args, kind, **kwargs):
        super().__init__(*args, **kwargs)
        self.fields["type"].choices = TYPES[kind]
        keep = {"order_no", "type"}
        keep |= (
            {"supplier_id", "remark"}
            if kind == "inbound"
            else {"remark"}
            if kind == "outbound"
            else {"reason", "reason_category"}
            if kind == "adjustment"
            else {"reason"}
        )
        self.fields = {k: v for k, v in self.fields.items() if k in keep}
        if "reason" in self.fields:
            self.fields["reason"].required = True
        if "reason_category" in self.fields:
            self.fields["reason_category"].required = True


class LineForm(forms.Form):
    item_id = forms.IntegerField(label="物资", min_value=1, max_value=ID_MAX)
    quantity = forms.DecimalField(
        label="数量",
        max_digits=14,
        decimal_places=3,
        min_value=Decimal(".001"),
        required=False,
    )
    unit_price = forms.DecimalField(
        label="单价", max_digits=14, decimal_places=2, min_value=0, required=False
    )
    location_id = forms.IntegerField(
        label="库位", min_value=1, max_value=ID_MAX, required=False
    )
    quality = forms.ChoiceField(
        label="质量", choices=[("", "请选择"), *QUALITY], required=False
    )
    recipient_id = forms.IntegerField(
        label="领用人", min_value=1, max_value=ID_MAX, required=False
    )
    source_outbound_item_id = forms.IntegerField(
        label="来源发放明细", min_value=1, max_value=ID_MAX, required=False
    )
    source_outbound_asset_id = forms.IntegerField(
        label="来源资产发放关系", min_value=1, max_value=ID_MAX, required=False
    )
    source_inbound_item_id = forms.IntegerField(
        label="来源采购明细", min_value=1, max_value=ID_MAX, required=False
    )
    asset_card_id = forms.IntegerField(
        label="资产卡", min_value=1, max_value=ID_MAX, required=False
    )
    asset_ids = AssetIDsField(label="选择资产", required=False)
    source_loss_item_id = forms.IntegerField(
        label="原遗失明细", min_value=1, max_value=ID_MAX, required=False
    )
    recovery_location_id = forms.IntegerField(
        label="找回后库位", min_value=1, max_value=ID_MAX, required=False
    )
    recovery_quality = forms.ChoiceField(
        label="找回后质量", choices=[("", "请选择"), *QUALITY], required=False
    )
    residual_value = forms.DecimalField(
        label="残值", max_digits=14, decimal_places=2, min_value=0, required=False
    )
    direction = forms.ChoiceField(
        label="调整方向",
        choices=[("", "请选择"), ("in", "增加"), ("out", "减少")],
        required=False,
    )
    to_quality = forms.ChoiceField(
        label="转换后质量", choices=[("", "请选择"), *QUALITY], required=False
    )
    observed_at = forms.DateTimeField(label="观察时间", required=False)
    book_qty = forms.DecimalField(
        label="观察账面数量",
        max_digits=14,
        decimal_places=3,
        min_value=0,
        required=False,
    )
    counted_qty = forms.DecimalField(
        label="实盘数量", max_digits=14, decimal_places=3, min_value=0, required=False
    )
    inventory_version = forms.IntegerField(
        label="观察版本", min_value=0, max_value=ID_MAX, required=False
    )
    target_inventory_version = forms.IntegerField(
        label="目标质量版本", min_value=0, max_value=ID_MAX, required=False
    )
    original_value = forms.DecimalField(
        label="原值", max_digits=14, decimal_places=2, min_value=0, required=False
    )

    def __init__(self, *args, kind, order_type, **kwargs):
        super().__init__(*args, **kwargs)
        keep = {"item_id", "quantity"}
        if kind == "inbound":
            keep |= {"location_id", "quality", "unit_price"}
            if order_type == "return":
                keep |= {"source_outbound_item_id", "source_outbound_asset_id"}
        elif kind == "outbound":
            keep |= {"location_id", "recipient_id"}
            if order_type == "purchase_return":
                keep |= {"source_inbound_item_id", "asset_ids"}
            if order_type == "transfer":
                keep |= {"source_outbound_item_id", "source_outbound_asset_id"}
        elif kind == "scrap":
            keep |= (
                {"location_id", "quality"}
                if order_type == "stock_damage"
                else {"asset_card_id", "residual_value"}
            )
            if order_type == "recovery":
                keep |= {
                    "source_loss_item_id",
                    "recovery_location_id",
                    "recovery_quality",
                }
        else:
            keep |= {
                "location_id",
                "quality",
                "direction",
                "observed_at",
                "book_qty",
                "counted_qty",
                "inventory_version",
                "original_value",
            }
            if order_type == "quality_transfer":
                keep |= {"to_quality", "target_inventory_version"}
        self.fields = {k: v for k, v in self.fields.items() if k in keep}


class ExecuteLineForm(forms.Form):
    id = forms.IntegerField(widget=forms.HiddenInput, min_value=1, max_value=ID_MAX)
    actual_qty = forms.DecimalField(
        label="实发数量", max_digits=14, decimal_places=3, min_value=0
    )
    location_id = forms.IntegerField(
        label="发放库位", min_value=1, max_value=ID_MAX, required=False
    )
    recipient_id = forms.IntegerField(
        label="领用人", min_value=1, max_value=ID_MAX, required=False
    )
    shortfall_reason = forms.CharField(
        label="差额原因", max_length=1000, required=False
    )
    asset_ids = AssetIDsField(label="发放资产", required=False)


def validate(form, data):
    if not isinstance(data, dict) or set(data) - set(form.fields):
        raise BusinessError("INVALID_FIELDS", "含不允许的字段", 422)
    if not form.is_valid():
        error = BusinessError("INVALID_DATA", "请检查单据字段", 422)
        error.fields = form.errors.get_json_data()
        raise error
    return form.cleaned_data.copy()


def parse_document(kind, payload):
    if not isinstance(payload, dict):
        raise BusinessError("INVALID_DATA", "单据必须为对象", 422)
    header = validate(
        HeaderForm(data={k: v for k, v in payload.items() if k != "lines"}, kind=kind),
        {k: v for k, v in payload.items() if k != "lines"},
    )
    rows = payload.get("lines")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 50:
        raise BusinessError("INVALID_LINES", "每张单据须有1至50条明细", 422)
    lines = [
        validate(LineForm(data=row, kind=kind, order_type=header["type"]), row)
        for row in rows
    ]
    return header, lines
