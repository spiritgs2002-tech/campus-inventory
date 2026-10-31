from django import forms
from .models import Attachment
from apps.inventory.models import ScrapOrder


class UploadForm(forms.Form):
    file = forms.FileField(
        label="证据文件",
        max_length=200,
        widget=forms.ClearableFileInput(attrs={"accept": ".png,.jpg,.jpeg,.pdf"}),
    )
    sha256 = forms.RegexField(
        label="SHA-256（可选校验）", regex=r"^[0-9a-f]{64}$", required=False
    )


class AssociationForm(forms.Form):
    scrap_id = forms.TypedChoiceField(label="处置单", coerce=int)
    attachment_id = forms.TypedChoiceField(label="附件", coerce=int)
    reason = forms.CharField(label="说明", max_length=2000, required=False)

    def __init__(self, *args, principal, **kwargs):
        super().__init__(*args, **kwargs)
        orders = ScrapOrder.objects.filter(
            tenant_id=principal.tenant_id, status="pending", decided_by__isnull=True
        )
        attachments = Attachment.objects.filter(
            tenant_id=principal.tenant_id, deleted_at__isnull=True
        )
        if principal.role != "admin":
            orders = orders.filter(applicant_id=principal.user_id)
            attachments = attachments.filter(uploaded_by=principal.user_id)
        self.fields["scrap_id"].choices = [
            (row.pk, row.order_no) for row in orders.order_by("-id")[:100]
        ]
        self.fields["attachment_id"].choices = [
            (row.pk, f"附件 {row.pk} · {row.content_type} · {row.size_bytes}字节")
            for row in attachments.order_by("-id")[:100]
        ]


class ReasonForm(forms.Form):
    reason = forms.CharField(label="处理原因", max_length=2000, widget=forms.Textarea)


class CSVForm(forms.Form):
    file = forms.FileField(label="证据关联 CSV 清单")
