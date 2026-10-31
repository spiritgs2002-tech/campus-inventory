from django.db import IntegrityError, connection

from . import forms
from .auth_service import revoke_user
from .models import AuditLog, Department, Tenant, User
from .models import OperationRequest
from shared.auth.backends import authorize_claims
from shared.auth.backends import require_admin
from shared.auth.passwords import hash_password
from shared.errors import BusinessError
from shared.idempotency.service import execute

RESOURCES = {
    "departments": (Department, forms.DepartmentForm, "部门"),
    "users": (User, forms.UserForm, "用户"),
    "tenant": (Tenant, forms.TenantForm, "学校设置"),
}


def lock_order_context(principal, discover_users):
    """Order key is already locked; serialize tenant, then relevant users by ID."""
    tenant = Tenant.objects.select_for_update(no_key=True).get(pk=principal.tenant_id)
    ids = {principal.user_id, *discover_users()}
    users = {
        user.pk: user
        for user in User.objects.select_for_update(no_key=True)
        .filter(tenant_id=principal.tenant_id, pk__in=ids)
        .order_by("id")
    }
    current = authorize_claims(
        {
            "tid": principal.tenant_id,
            "sub": principal.user_id,
            "fid": principal.family_id,
        }
    )
    if current.must_change_password:
        raise BusinessError("PASSWORD_CHANGE_REQUIRED", "请先修改初始密码", 403)
    return tenant, users, current


def operation_id(tenant_id, actor_id, operation, key):
    return OperationRequest.objects.get(
        tenant_id=tenant_id, actor_id=actor_id, operation=operation, idempotency_key=key
    ).pk


def allocate_asset_numbers(tenant_id, count):
    if not 1 <= count <= 1000:
        raise BusinessError("INVALID_ASSET_COUNT", "单次建卡数量须为1至1000")
    with connection.cursor() as cursor:
        cursor.execute(
            "UPDATE tenants SET next_asset_number=next_asset_number+%s WHERE id=%s RETURNING asset_prefix,next_asset_number-%s",
            [count, tenant_id, count],
        )
        prefix, start = cursor.fetchone()
    return [f"{prefix}-{number:08d}" for number in range(start, start + count)]


def queryset(resource, tenant_id):
    model = RESOURCES[resource][0]
    return (
        model.objects.filter(pk=tenant_id)
        if resource == "tenant"
        else model.objects.filter(tenant_id=tenant_id)
    )


def get_instance(resource, tenant_id, pk):
    obj = queryset(resource, tenant_id).filter(pk=pk).first()
    if obj is None:
        raise BusinessError("NOT_FOUND", "记录不存在", 404)
    return obj


def form_for(resource, tenant_id, instance=None, data=None):
    cls = RESOURCES[resource][1]
    kwargs = (
        {"tenant_id": tenant_id, "instance": instance} if resource == "users" else {}
    )
    initial = (
        {
            name: getattr(instance, name, "")
            for name in cls.base_fields
            if name not in {"reason", "password"}
        }
        if instance
        else {}
    )
    return cls(data=data, initial=initial, **kwargs)


def serialize(resource, obj):
    names = {
        "departments": ["id", "name", "active"],
        "users": [
            "id",
            "username",
            "name",
            "role",
            "dept_id",
            "active",
            "must_change_password",
        ],
        "tenant": [
            "id",
            "code",
            "name",
            "status",
            "expire_at",
            "asset_prefix",
            "allow_self_approval",
            "login_failure_limit",
            "login_lock_minutes",
        ],
    }[resource]
    return {
        field: (
            getattr(obj, field).isoformat()
            if hasattr(getattr(obj, field), "isoformat")
            else getattr(obj, field)
        )
        for field in names
    }


def user_choices(tenant_id):
    return list(
        User.objects.filter(tenant_id=tenant_id, active=True).values("id", "name")
    )


def run_command(principal, resource, pk, key, payload, callback):
    require_admin(principal)

    def locked():
        # Idempotency INSERT already holds FK KEY SHARE locks. NO KEY UPDATE
        # serializes mutations without a lock-upgrade deadlock against those FKs.
        Tenant.objects.select_for_update(no_key=True).get(pk=principal.tenant_id)
        ids = {principal.user_id}
        if resource == "users" and pk:
            ids.add(pk)
        list(
            User.objects.select_for_update(no_key=True)
            .filter(tenant_id=principal.tenant_id, id__in=ids)
            .order_by("id")
        )
        current = require_admin(principal)
        return callback(current)

    try:
        return execute(
            principal.tenant_id,
            principal.user_id,
            f"master.{resource}.{'update' if pk else 'create'}",
            key,
            {"id": pk, "data": payload},
            locked,
        )
    except IntegrityError:
        raise BusinessError(
            "CONFLICT", "名称或编码已存在，或关联数据不允许此修改", 409
        ) from None


def audit(principal, resource, pk, before, after, reason):
    AuditLog.objects.create(
        tenant_id=principal.tenant_id,
        user_id=principal.user_id,
        action=f"{resource}.update" if before else f"{resource}.create",
        object_type=resource,
        object_id=pk,
        changes={"before": before, "after": after},
        reason=reason or None,
    )


def validated(form, payload):
    unexpected = set(payload) - set(form.fields)
    if unexpected:
        raise BusinessError("INVALID_FIELDS", "含不允许修改的字段", 422)
    if not form.is_valid():
        error = BusinessError("INVALID_DATA", "请检查输入字段", 422)
        error.fields = form.errors.get_json_data()
        raise error
    return form.cleaned_data.copy()


def save(principal, resource, pk, key, payload):
    if resource == "tenant" and pk != principal.tenant_id:
        raise BusinessError(
            "FORBIDDEN", "租户开通、停用和期限变更只能由受控运维执行", 403
        )

    def change(current):
        obj = get_instance(resource, current.tenant_id, pk) if pk else None
        before = serialize(resource, obj) if obj else None
        data = validated(form_for(resource, current.tenant_id, obj, payload), payload)
        reason = data.pop("reason", "")
        password = data.pop("password", "")
        if (
            resource == "tenant"
            and data["allow_self_approval"] != obj.allow_self_approval
            and not reason
        ):
            raise BusinessError("REASON_REQUIRED", "修改自批开关必须说明原因", 422)
        if resource == "users" and obj:
            removing_admin = (
                obj.active
                and obj.role == "admin"
                and (not data["active"] or data["role"] != "admin")
            )
            if (
                removing_admin
                and not User.objects.filter(
                    tenant_id=current.tenant_id, active=True, role="admin"
                )
                .exclude(pk=obj.pk)
                .exists()
            ):
                raise BusinessError("LAST_ADMIN", "不能停用或降级最后一名管理员", 409)
            if obj.active and (not data["active"] or data["role"] != obj.role):
                from apps.inventory.services import user_has_open_work

                if user_has_open_work(current.tenant_id, obj.pk):
                    raise BusinessError(
                        "USER_HAS_OPEN_WORK",
                        "用户仍有在用资产或待处理申请，请先完成交接",
                        409,
                    )
        if obj is None:
            obj = RESOURCES[resource][0](tenant_id=current.tenant_id)
        revoke = False
        if resource == "users":
            revoke = password or (
                obj.pk and (obj.active != data["active"] or obj.role != data["role"])
            )
            if password:
                data.update(
                    password_hash=hash_password(password), must_change_password=True
                )
        for field, value in data.items():
            setattr(obj, field, value)
        if obj.pk:
            obj.save(update_fields=list(data))
        else:
            obj.save(force_insert=True)
        if resource == "users" and revoke:
            revoke_user(
                current.tenant_id, obj.pk, "password_change" if password else "disabled"
            )
        after = serialize(resource, obj)
        audit_before = dict(before) if before else None
        audit_after = dict(after)
        if password:
            audit_after["password_changed"] = True
        audit(current, resource, obj.pk, audit_before, audit_after, reason)
        return {"record": after, "business_refs": [{"type": resource, "id": obj.pk}]}

    return run_command(principal, resource, pk, key, payload, change)
