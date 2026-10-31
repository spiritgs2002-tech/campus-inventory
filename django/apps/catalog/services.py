from decimal import Decimal
from . import forms
from .models import Category, Item, Location, Supplier
from apps.core.services import audit, run_command, validated
from shared.crypto.service import decrypt_text, encrypt_text
from shared.errors import BusinessError

RESOURCES = {
    "locations": (Location, forms.LocationForm, "库位"),
    "categories": (Category, forms.CategoryForm, "分类"),
    "items": (Item, forms.ItemForm, "物资"),
    "suppliers": (Supplier, forms.SupplierForm, "供应商"),
}


def order_reference(resource, tenant_id, pk, *, active=False):
    obj = get_instance(resource, tenant_id, pk)
    if active and not obj.active:
        raise BusinessError("REFERENCE_DISABLED", "所选主数据已停用", 409)
    return obj


def order_choices(resource, tenant_id):
    return list(
        queryset(resource, tenant_id)
        .filter(active=True)
        .order_by("id")
        .values_list("id", "name")
    )


def queryset(resource, tenant_id):
    return RESOURCES[resource][0].objects.filter(tenant_id=tenant_id)


def get_instance(resource, tenant_id, pk):
    obj = queryset(resource, tenant_id).filter(pk=pk).first()
    if obj is None:
        raise BusinessError("NOT_FOUND", "记录不存在", 404)
    return obj


def serialize(resource, obj):
    names = {
        "locations": ["id", "name", "active"],
        "categories": ["id", "code", "name", "type", "parent_id", "active"],
        "items": [
            "id",
            "code",
            "name",
            "type",
            "category_id",
            "unit",
            "spec",
            "reference_price",
            "safety_stock",
            "active",
        ],
        "suppliers": ["id", "name", "active"],
    }[resource]
    return {
        name: (
            str(getattr(obj, name))
            if isinstance(getattr(obj, name), Decimal)
            else getattr(obj, name)
        )
        for name in names
    }


def form_for(resource, tenant_id, instance=None, data=None):
    cls = RESOURCES[resource][1]
    kwargs = (
        {"tenant_id": tenant_id, "instance": instance}
        if resource in {"categories", "items"}
        else {}
    )
    initial = (
        {
            name: getattr(instance, name, "")
            for name in cls.base_fields
            if name != "reason"
        }
        if instance
        else {}
    )
    if resource == "suppliers" and instance:
        initial["contact"] = decrypt_text(instance.contact)
    return cls(data=data, initial=initial, **kwargs)


def save(principal, resource, pk, key, payload):
    def change(current):
        obj = get_instance(resource, current.tenant_id, pk) if pk else None
        before = serialize(resource, obj) if obj else None
        data = validated(form_for(resource, current.tenant_id, obj, payload), payload)
        reason = data.pop("reason", "")
        if resource in {"categories", "items"} and obj and obj.type != data["type"]:
            raise BusinessError("IMMUTABLE_TYPE", "已创建的分类或物资类型不可更改", 409)
        if resource == "categories" and data["parent_id"]:
            parent = get_instance("categories", current.tenant_id, data["parent_id"])
            if parent.type != data["type"]:
                raise BusinessError("CATEGORY_TYPE_MISMATCH", "上级分类类型不一致", 422)
            visited = {obj.pk} if obj else set()
            while parent:
                if parent.pk in visited:
                    raise BusinessError("CATEGORY_CYCLE", "分类不能形成循环层级", 422)
                visited.add(parent.pk)
                parent = (
                    get_instance("categories", current.tenant_id, parent.parent_id)
                    if parent.parent_id
                    else None
                )
        if resource == "items":
            category = get_instance(
                "categories", current.tenant_id, data["category_id"]
            )
            if category.type != data["type"]:
                raise BusinessError(
                    "CATEGORY_TYPE_MISMATCH", "物资与分类类型不一致", 422
                )
        if resource == "suppliers":
            contact = data.pop("contact", "")
            data["contact"] = encrypt_text(contact)
        obj = obj or RESOURCES[resource][0](tenant_id=current.tenant_id)
        for field, value in data.items():
            setattr(obj, field, value)
        if obj.pk:
            obj.save(update_fields=list(data))
        else:
            obj.save(force_insert=True)
        after = serialize(resource, obj)
        audit_after = dict(after)
        if resource == "suppliers":
            audit_after["contact_changed"] = True
        audit(current, resource, obj.pk, before, audit_after, reason)
        return {"record": after, "business_refs": [{"type": resource, "id": obj.pk}]}

    return run_command(principal, resource, pk, key, payload, change)
