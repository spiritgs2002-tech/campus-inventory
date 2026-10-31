from django import forms
from django.db.models import Q as forms_models_q
from .models import Department


class ReasonForm(forms.Form):
    reason = forms.CharField(label="操作原因", required=False, max_length=1000)


class DepartmentForm(ReasonForm):
    name = forms.CharField(label="部门名称", max_length=120)
    active = forms.BooleanField(label="启用", required=False, initial=True)


class UserForm(ReasonForm):
    username = forms.CharField(label="用户名", max_length=80)
    name = forms.CharField(label="姓名", max_length=120)
    role = forms.ChoiceField(
        label="角色", choices=[("user", "领用人"), ("admin", "管理员")]
    )
    dept_id = forms.TypedChoiceField(
        label="部门", coerce=int, empty_value=None, required=False
    )
    active = forms.BooleanField(label="启用", required=False, initial=True)
    password = forms.CharField(
        label="初始/重置密码", required=False, widget=forms.PasswordInput, max_length=72
    )

    def __init__(self, *args, tenant_id, instance=None, **kwargs):
        super().__init__(*args, **kwargs)
        choices = Department.objects.filter(tenant_id=tenant_id, active=True)
        if instance and instance.dept_id:
            choices = Department.objects.filter(tenant_id=tenant_id).filter(
                forms_models_q(active=True) | forms_models_q(pk=instance.dept_id)
            )
        self.fields["dept_id"].choices = [
            ("", "未分配"),
            *choices.order_by("id").values_list("id", "name"),
        ]
        if instance is None:
            self.fields["password"].required = True


class TenantForm(ReasonForm):
    name = forms.CharField(label="学校名称", max_length=120)
    asset_prefix = forms.RegexField(
        label="资产编号前缀", regex=r"^[A-Za-z0-9_-]{1,20}$", max_length=20
    )
    login_failure_limit = forms.IntegerField(
        label="失败锁定次数", min_value=1, max_value=20
    )
    login_lock_minutes = forms.IntegerField(
        label="锁定分钟数", min_value=1, max_value=1440
    )
    allow_self_approval = forms.BooleanField(label="允许自批", required=False)
