from django import forms
from shared.errors import BusinessError

ID_MAX = 2**63 - 1
TARGETS = [("asset", "资产"), ("item", "物品"), ("location", "场地")]
PRIORITIES = [("low", "低"), ("normal", "普通"), ("high", "紧急")]
STATUSES = [
    ("pending", "待派单"),
    ("dispatched", "已派单"),
    ("repairing", "处理中"),
    ("done", "已完成"),
    ("cancelled", "已取消"),
]


class TicketForm(forms.Form):
    target_type = forms.ChoiceField(label="报修对象", choices=TARGETS)
    target_id = forms.IntegerField(
        label="对象编号（场地可留空）", min_value=1, max_value=ID_MAX, required=False
    )
    description = forms.CharField(
        label="故障说明", max_length=4000, widget=forms.Textarea
    )
    priority = forms.ChoiceField(label="紧急程度", choices=PRIORITIES, required=False)
    photo_attachment_id = forms.IntegerField(
        min_value=1, max_value=ID_MAX, required=False, widget=forms.HiddenInput
    )
    name = forms.CharField(label="姓名", max_length=120, required=False)
    employee_no = forms.RegexField(
        r"^[A-Za-z0-9_.-]{1,80}$", label="工号", required=False
    )
    token = forms.CharField(max_length=128, required=False, widget=forms.HiddenInput)

    def clean(self):
        data = super().clean()
        if data.get("target_type") in {"asset", "item"} and not data.get("target_id"):
            self.add_error("target_id", "请选择具体资产或物品")
        return data


class DispatchForm(forms.Form):
    assigned_to = forms.IntegerField(min_value=1, max_value=ID_MAX)


class TransitionForm(forms.Form):
    action = forms.ChoiceField(
        choices=[("start", "开始处理"), ("done", "完成"), ("cancel", "取消")]
    )
    reason = forms.CharField(max_length=2000, required=False)


def clean_payload(form_class, payload):
    if not isinstance(payload, dict) or set(payload) - set(form_class.base_fields):
        raise BusinessError("INVALID_FIELDS", "请求包含未知字段或不是对象", 422)
    if any(
        not isinstance(value, (str, int, type(None))) or isinstance(value, bool)
        for value in payload.values()
    ):
        raise BusinessError("INVALID_FIELDS", "参数类型无效", 422)
    form = form_class(payload)
    if not form.is_valid():
        raise BusinessError("INVALID_REPAIR", "请填写有效的报修参数", 422)
    try:
        for value in form.cleaned_data.values():
            if isinstance(value, str):
                value.encode("utf-8")
    except UnicodeError:
        raise BusinessError("INVALID_REPAIR", "文本包含无效字符", 422) from None
    return form.cleaned_data
