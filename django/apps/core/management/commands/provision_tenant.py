"""Controlled provisioning, deliberately outside runtime business HTTP routes."""

import getpass
import os
from django.core.management.base import BaseCommand, CommandError
from django.db import connection, transaction, IntegrityError
from apps.core.models import Tenant, User, OperationRequest, AuditLog
from shared.auth.passwords import hash_password
from shared.errors import BusinessError
from shared.idempotency.service import digest, validate_key


class Command(BaseCommand):
    help = "受控运维开通专用租户及首位管理员；运行身份不可执行"

    def add_arguments(self, parser):
        parser.add_argument("--code", required=True)
        parser.add_argument("--name", required=True)
        parser.add_argument("--admin-username", required=True)
        parser.add_argument("--idempotency-key", required=True)

    def handle(self, *args, **options):
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT has_table_privilege(current_user,'tenants','INSERT')"
            )
            if not cursor.fetchone()[0]:
                raise CommandError(
                    "租户开通须使用独立受控运维身份，不能使用业务运行身份"
                )
        password = os.environ.get("INITIAL_ADMIN_PASSWORD") or getpass.getpass(
            "初始管理员密码（至少12字符）: "
        )
        values = {
            name: options[name].strip() for name in ("code", "name", "admin_username")
        }
        if any(
            not value or len(value) > (120 if name == "name" else 80)
            for name, value in values.items()
        ):
            raise CommandError("学校名须为1–120字符；代码与管理员名须为1–80字符")
        try:
            key = options["idempotency_key"]
            validate_key(key)
            request_digest = digest({**values, "password": password})
            with transaction.atomic():
                tenant = Tenant.objects.filter(code=values["code"]).first()
                if tenant:
                    row = OperationRequest.objects.filter(
                        tenant_id=tenant.pk,
                        operation="ops.provision",
                        idempotency_key=key,
                        request_digest=request_digest,
                    ).first()
                    if row:
                        self.stdout.write("租户开通已处理")
                        return
                    raise CommandError("租户代码已存在；拒绝覆盖已有租户")
                tenant = Tenant.objects.create(code=values["code"], name=values["name"])
                user = User.objects.create(
                    tenant_id=tenant.pk,
                    username=values["admin_username"],
                    name=values["admin_username"],
                    role="admin",
                    password_hash=hash_password(password),
                    must_change_password=True,
                )
                OperationRequest.objects.create(
                    tenant_id=tenant.pk,
                    actor_id=user.pk,
                    operation="ops.provision",
                    idempotency_key=key,
                    request_digest=request_digest,
                    response={"tenant_id": tenant.pk, "user_id": user.pk},
                    business_refs=[{"type": "tenants", "id": tenant.pk}],
                )
                AuditLog.objects.create(
                    tenant_id=tenant.pk,
                    user_id=user.pk,
                    action="tenant.provision",
                    object_type="tenants",
                    object_id=tenant.pk,
                    changes={"initial_admin_created": True},
                    reason="受控运维开通",
                )
        except (BusinessError, IntegrityError) as error:
            raise CommandError(
                error.message
                if isinstance(error, BusinessError)
                else "租户或管理员已存在"
            ) from None
        self.stdout.write(f"已开通租户 {tenant.code}；管理员首次登录须改密")
