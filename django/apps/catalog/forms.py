from django import forms
from django.db.models import Q
from .models import Category


class BaseForm(forms.Form):
    name = forms.CharField(label="名称", max_length=120)
    active = forms.BooleanField(label="启用", required=False, initial=True)
    reason = forms.CharField(label="操作原因", max_length=1000, required=False)


class LocationForm(BaseForm):
    pass


class SupplierForm(BaseForm):
    contact = forms.CharField(
        label="联系信息（加密保存）", max_length=500, required=False
    )


class CategoryForm(BaseForm):
    code = forms.CharField(label="分类编码", max_length=80)
    type = forms.ChoiceField(
        label="类型", choices=[("consumable", "耗材"), ("fixed", "固定资产")]
    )
    parent_id = forms.TypedChoiceField(
        label="上级分类", required=False, coerce=int, empty_value=None
    )

    def __init__(self, *args, tenant_id, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = Category.objects.filter(tenant_id=tenant_id, active=True)
        if instance:
            choices = (
                Category.objects.filter(tenant_id=tenant_id)
                .filter(Q(active=True) | Q(pk=instance.parent_id))
                .exclude(pk=instance.pk)
            )
        self.fields["parent_id"].choices = [
            ("", "无"),
            *choices.order_by("id").values_list("id", "name"),
        ]


class ItemForm(BaseForm):
    code = forms.CharField(label="物资编码", max_length=80)
    type = forms.ChoiceField(
        label="类型", choices=[("consumable", "耗材"), ("fixed", "固定资产")]
    )
    category_id = forms.TypedChoiceField(label="分类", coerce=int)
    unit = forms.CharField(label="单位", max_length=20)
    spec = forms.CharField(label="规格", max_length=500, required=False)
    reference_price = forms.DecimalField(
        label="参考单价", max_digits=14, decimal_places=2, min_value=0, required=False
    )
    safety_stock = forms.DecimalField(
        label="安全库存", max_digits=14, decimal_places=3, min_value=0, required=False
    )

    def __init__(self, *args, tenant_id, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = Category.objects.filter(tenant_id=tenant_id, active=True)
        if instance:
            choices = Category.objects.filter(tenant_id=tenant_id).filter(
                Q(active=True) | Q(pk=instance.category_id)
            )
        self.fields["category_id"].choices = list(
            choices.order_by("id").values_list("id", "name")
        )
