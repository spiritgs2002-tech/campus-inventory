import json
from datetime import timedelta
from uuid import uuid4
from io import StringIO
from unittest.mock import patch

from cryptography.fernet import Fernet
from django.core.management import call_command, CommandError
from django.db import connection
from django.test import Client, TestCase, override_settings
from django.test.utils import CaptureQueriesContext
from django.utils import timezone

from apps.catalog.models import Category, Item, Location, Supplier
from apps.core.models import (
    AuditLog,
    AuthSession,
    LoginThrottle,
    OperationRequest,
    Tenant,
    User,
)
from apps.inventory.models import OutboundOrder, InboundOrder, InboundItem, AssetCard
from shared.auth.backends import authorize_claims
from shared.auth.passwords import hash_password, verify_password
from shared.auth.tokens import access_token
from shared.errors import BusinessError


@override_settings(
    CSRF_TRUSTED_ORIGINS=["https://inventory.test"],
    PII_ENCRYPTION_KEYS=[Fernet.generate_key().decode()],
)
class AuthMasterDataTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.password = "Test-password-12345"
        encoded = hash_password(cls.password)
        cls.tenant = Tenant.objects.create(code="SCHOOL", name="学校一")
        cls.other_tenant = Tenant.objects.create(code="OTHER", name="学校二")
        cls.admin = User.objects.create(
            tenant_id=cls.tenant.id,
            username="admin",
            name="管理员",
            role="admin",
            password_hash=encoded,
            must_change_password=False,
        )
        cls.user = User.objects.create(
            tenant_id=cls.tenant.id,
            username="user",
            name="普通用户",
            role="user",
            password_hash=encoded,
            must_change_password=False,
        )
        cls.other = User.objects.create(
            tenant_id=cls.other_tenant.id,
            username="admin",
            name="另一校管理员",
            role="admin",
            password_hash=encoded,
            must_change_password=False,
        )
        cls.new_user = User.objects.create(
            tenant_id=cls.tenant.id,
            username="new",
            name="初始用户",
            role="user",
            password_hash=encoded,
            must_change_password=True,
        )
        cls.claims = {}
        for user in [cls.admin, cls.user, cls.other, cls.new_user]:
            family = uuid4()
            AuthSession.objects.create(
                tenant_id=user.tenant_id,
                user_id=user.pk,
                refresh_digest=str(uuid4()),
                family_id=family,
                expires_at=timezone.now() + timedelta(days=1),
            )
            cls.claims[user.pk] = {
                "tid": user.tenant_id,
                "sub": str(user.pk),
                "fid": str(family),
            }

    def setUp(self):
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE inventory_runtime")

    def token(self, user=None):
        return access_token(authorize_claims(self.claims[(user or self.admin).pk]))

    def api(self, method, path, data=None, user=None, key=None, client=None, **headers):
        return getattr(client or self.client, method)(
            path,
            data=(data or {}) if method == "get" else json.dumps(data or {}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + self.token(user),
            HTTP_IDEMPOTENCY_KEY=key or str(uuid4()),
            **headers,
        )

    def auth(self, action, data=None, key=None, client=None, **headers):
        return (client or self.client).post(
            f"/api/v1/auth/{action}/",
            data=json.dumps(data or {}),
            content_type="application/json",
            HTTP_ORIGIN="https://inventory.test",
            HTTP_X_INVENTORY_REQUEST="1",
            HTTP_IDEMPOTENCY_KEY=key or str(uuid4()),
            **headers,
        )

    def login(self, client=None, key=None, username="admin", password=None):
        return self.auth(
            "login",
            {
                "tenant_code": "SCHOOL",
                "username": username,
                "password": password or self.password,
            },
            client=client,
            key=key,
        )

    def test_login_cookie_and_no_plaintext_tokens_in_operations(self):
        response = self.login()
        self.assertEqual(response.status_code, 200, response.content)
        cookie = response.cookies["inventory_refresh"]
        self.assertTrue(cookie["httponly"])
        self.assertEqual(cookie["samesite"], "Strict")
        self.assertEqual(cookie["path"], "/api/v1/auth")
        stored = json.dumps(
            list(
                OperationRequest.objects.values(
                    "response", "request_digest", "business_refs"
                )
            )
        )
        self.assertNotIn(response.json()["access_token"], stored)
        self.assertNotIn(cookie.value, stored)
        self.assertNotIn(self.password, stored)

    def test_login_key_replay_does_not_create_another_family(self):
        key = str(uuid4())
        first = self.login(key=key)
        count = AuthSession.objects.count()
        second = self.login(key=key)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(first.json()["access_token"], second.json()["access_token"])
        self.assertEqual(AuthSession.objects.count(), count)

    def test_failed_login_locks_throttle_without_deactivating_user(self):
        for _ in range(5):
            self.assertEqual(self.login(password="incorrect-password").status_code, 401)
        self.assertEqual(self.login().status_code, 401)
        throttle = LoginThrottle.objects.get(
            tenant_id=self.tenant.pk, user_id=self.admin.pk
        )
        self.assertEqual(throttle.failed_login_count, 5)
        self.assertGreater(throttle.locked_until, timezone.now())
        self.admin.refresh_from_db()
        self.assertTrue(self.admin.active)

    def test_origin_header_and_idempotency_key_required(self):
        path = "/api/v1/auth/login/"
        data = json.dumps(
            {"tenant_code": "SCHOOL", "username": "admin", "password": self.password}
        )
        self.assertEqual(
            self.client.post(path, data, content_type="application/json").status_code,
            403,
        )
        response = self.client.post(
            path,
            data,
            content_type="application/json",
            HTTP_ORIGIN="https://inventory.test",
            HTTP_X_INVENTORY_REQUEST="1",
        )
        self.assertEqual(response.status_code, 400)

    def test_b9_is_one_query_and_checks_entire_family(self):
        with CaptureQueriesContext(connection) as queries:
            principal = authorize_claims(self.claims[self.admin.pk])
        self.assertEqual(len(queries), 1)
        self.assertIn("JOIN users", queries[0]["sql"])
        self.assertIn("JOIN tenants", queries[0]["sql"])
        AuthSession.objects.create(
            tenant_id=self.tenant.pk,
            user_id=self.admin.pk,
            refresh_digest=str(uuid4()),
            family_id=principal.family_id,
            expires_at=timezone.now() + timedelta(days=1),
            revoked_at=timezone.now(),
            revoked_reason="logout",
        )
        with self.assertRaises(BusinessError):
            authorize_claims(self.claims[self.admin.pk])

    def test_refresh_rotation_short_retry_and_late_reuse_revoke_family(self):
        logged = self.login()
        raw = logged.cookies["inventory_refresh"].value
        response = self.auth("refresh")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertNotEqual(response.cookies["inventory_refresh"].value, raw)
        self.client.cookies["inventory_refresh"] = raw
        self.assertEqual(
            self.auth("refresh").json()["error"]["code"], "REFRESH_ALREADY_ROTATED"
        )
        family = AuthSession.objects.get(
            refresh_digest=__import__("hashlib").sha256(raw.encode()).hexdigest()
        ).family_id
        AuthSession.objects.filter(
            tenant_id=self.tenant.pk, family_id=family, rotated_at__isnull=False
        ).update(
            rotated_at=timezone.now() - timedelta(seconds=11),
            expires_at=timezone.now() - timedelta(seconds=1),
        )
        self.client.cookies["inventory_refresh"] = raw
        reused = self.auth("refresh")
        self.assertEqual(reused.status_code, 401, reused.content)
        self.assertFalse(
            AuthSession.objects.filter(
                tenant_id=self.tenant.pk, family_id=family, revoked_at__isnull=True
            ).exists()
        )

    def test_logout_clears_family_parent_links_and_access(self):
        self.login()
        rotated = self.auth("refresh")
        token = rotated.json()["access_token"]
        response = self.auth("logout")
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self.client.get(
                "/api/v1/catalog/items/", HTTP_AUTHORIZATION="Bearer " + token
            ).status_code,
            401,
        )
        self.assertFalse(
            AuthSession.objects.filter(
                user_id=self.admin.pk,
                revoked_reason="logout",
                parent_session_id__isnull=False,
            ).exists()
        )

    def test_change_password_revokes_all_families(self):
        self.login()
        result = self.auth(
            "change-password",
            {"old_password": self.password, "new_password": "New-password-12345"},
        )
        self.assertEqual(result.status_code, 200, result.content)
        self.assertFalse(
            AuthSession.objects.filter(
                user_id=self.admin.pk, revoked_at__isnull=True
            ).exists()
        )
        self.admin.refresh_from_db()
        self.assertTrue(verify_password("New-password-12345", self.admin.password_hash))
        self.assertFalse(self.admin.must_change_password)

    def test_forced_change_blocks_masterdata(self):
        result = self.api("get", "/api/v1/catalog/items/", user=self.new_user)
        self.assertEqual(result.status_code, 403)
        self.assertEqual(result.json()["error"]["code"], "PASSWORD_CHANGE_REQUIRED")

    def test_anonymous_and_operator_cannot_write(self):
        self.assertEqual(self.client.get("/api/v1/catalog/items/").status_code, 401)
        result = self.api(
            "post", "/api/v1/catalog/locations/", {"name": "坏请求"}, user=self.user
        )
        self.assertEqual(result.status_code, 403)
        self.assertEqual(
            self.api("get", "/api/v1/master/users/", user=self.user).status_code, 403
        )

    def test_create_replay_conflict_and_trimmed_uniqueness(self):
        key = str(uuid4())
        path = "/api/v1/catalog/locations/"
        first = self.api("post", path, {"name": "  仓库A  "}, key=key)
        self.assertEqual(first.status_code, 201, first.content)
        second = self.api("post", path, {"name": "  仓库A  "}, key=key)
        self.assertEqual(second.status_code, 200, second.content)
        self.assertEqual(second["Idempotency-Replayed"], "true")
        self.assertEqual(Location.objects.filter(tenant_id=self.tenant.pk).count(), 1)
        self.assertEqual(
            self.api("post", path, {"name": "另一个"}, key=key).status_code, 409
        )
        self.assertEqual(self.api("post", path, {"name": "仓库A"}).status_code, 409)
        self.assertEqual(AuditLog.objects.filter(object_type="locations").count(), 1)

    def test_cross_tenant_reads_writes_and_injected_tenant_rejected(self):
        other_location = self.api(
            "post",
            "/api/v1/catalog/locations/",
            {"name": "另一校库位"},
            user=self.other,
        ).json()["record"]
        path = f"/api/v1/catalog/locations/{other_location['id']}/"
        self.assertEqual(self.api("get", path).status_code, 404)
        self.assertEqual(
            self.api("put", path, {"name": "越权", "active": True}).status_code, 404
        )
        self.assertEqual(
            self.api(
                "post",
                "/api/v1/catalog/locations/",
                {"name": "注入", "tenant_id": self.other_tenant.pk},
            ).status_code,
            422,
        )
        self.assertNotContains(
            self.api("get", "/api/v1/catalog/locations/"), "另一校库位"
        )

    def test_category_type_cycle_and_item_type_validation(self):
        path = "/api/v1/catalog/categories/"
        root = self.api(
            "post", path, {"name": "根", "code": "R", "type": "fixed"}
        ).json()["record"]
        child = self.api(
            "post",
            path,
            {"name": "子", "code": "C", "type": "fixed", "parent_id": root["id"]},
        ).json()["record"]
        response = self.api(
            "put",
            f"{path}{root['id']}/",
            {
                "name": "根",
                "code": "R",
                "type": "fixed",
                "parent_id": child["id"],
                "active": True,
            },
        )
        self.assertEqual(response.status_code, 422, response.content)
        bad = self.api(
            "post",
            "/api/v1/catalog/items/",
            {
                "name": "不一致",
                "code": "I",
                "type": "consumable",
                "category_id": root["id"],
                "unit": "台",
            },
        )
        self.assertEqual(bad.status_code, 422, bad.content)
        self.assertFalse(Item.objects.exists())

    def test_user_creation_hashes_password_and_scopes_department(self):
        dept = self.api("post", "/api/v1/master/departments/", {"name": "总务"}).json()[
            "record"
        ]
        created = self.api(
            "post",
            "/api/v1/master/users/",
            {
                "username": "created",
                "name": "新增",
                "role": "user",
                "dept_id": dept["id"],
                "password": "Initial-password-12345",
            },
        )
        self.assertEqual(created.status_code, 201, created.content)
        user = User.objects.get(pk=created.json()["record"]["id"])
        self.assertTrue(user.must_change_password)
        self.assertTrue(verify_password("Initial-password-12345", user.password_hash))
        self.assertNotIn("password_hash", created.json()["record"])
        self.assertNotIn(
            "Initial-password",
            json.dumps(list(AuditLog.objects.values_list("changes", flat=True))),
        )
        other_dept = self.api(
            "post",
            "/api/v1/master/departments/",
            {"name": "其他校部门"},
            user=self.other,
        ).json()["record"]
        invalid = self.api(
            "post",
            "/api/v1/master/users/",
            {
                "username": "cross",
                "name": "跨租户",
                "role": "user",
                "dept_id": other_dept["id"],
                "password": "Initial-password-12345",
            },
        )
        self.assertEqual(invalid.status_code, 422)

    def test_last_admin_cannot_be_disabled(self):
        response = self.api(
            "delete", f"/api/v1/master/users/{self.admin.pk}/", {"reason": "测试"}
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "LAST_ADMIN")

    def test_disable_user_revokes_family_and_preserves_row(self):
        old_token = self.token(self.user)
        response = self.api(
            "delete", f"/api/v1/master/users/{self.user.pk}/", {"reason": "离岗"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.user.refresh_from_db()
        self.assertFalse(self.user.active)
        self.assertFalse(
            AuthSession.objects.filter(
                user_id=self.user.pk, revoked_at__isnull=True
            ).exists()
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/catalog/items/", HTTP_AUTHORIZATION="Bearer " + old_token
            ).status_code,
            401,
        )

    def test_pending_order_blocks_user_disabling(self):
        OutboundOrder.objects.create(
            tenant_id=self.tenant.pk,
            order_no="PENDING",
            type="requisition",
            applicant_id=self.user.pk,
        )
        response = self.api(
            "delete", f"/api/v1/master/users/{self.user.pk}/", {"reason": "离岗"}
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "USER_HAS_OPEN_WORK")

    def test_tenant_restricted_fields_and_self_approval_reason(self):
        data = {
            "name": "新校名",
            "asset_prefix": "NEW",
            "login_failure_limit": 5,
            "login_lock_minutes": 15,
            "allow_self_approval": True,
        }
        self.assertEqual(
            self.api("put", "/api/v1/master/tenant/", data).status_code, 422
        )
        response = self.api(
            "put", "/api/v1/master/tenant/", {**data, "reason": "已确认的单人例外"}
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.assertEqual(
            self.api(
                "put", "/api/v1/master/tenant/", {**data, "status": "disabled"}
            ).status_code,
            422,
        )
        self.assertEqual(
            self.api(
                "get", f"/api/v1/master/tenant/{self.other_tenant.pk}/"
            ).status_code,
            404,
        )

    def test_supplier_contact_encrypted_and_no_secret_in_audit_or_idempotency(self):
        response = self.api(
            "post",
            "/api/v1/catalog/suppliers/",
            {"name": "供应商", "contact": "contact-sensitive"},
        )
        self.assertEqual(response.status_code, 201, response.content)
        supplier = Supplier.objects.get(pk=response.json()["record"]["id"])
        self.assertTrue(supplier.contact.startswith("fernet:"))
        self.assertNotIn("contact-sensitive", supplier.contact)
        self.assertNotIn(
            "contact-sensitive",
            json.dumps(list(AuditLog.objects.values_list("changes", flat=True))),
        )
        self.assertNotIn(
            "contact-sensitive",
            json.dumps(
                list(OperationRequest.objects.values_list("response", flat=True))
            ),
        )

    def test_htmx_form_list_save_validation_and_escaped_input(self):
        token = self.token()
        headers = {"HTTP_AUTHORIZATION": "Bearer " + token, "HTTP_HX_REQUEST": "true"}
        response = self.client.get("/catalog/locations/new/", **headers)
        self.assertEqual(response.status_code, 200, response.content)
        saved = self.client.post(
            "/catalog/locations/new/",
            {
                "name": "<script>alert(1)</script>",
                "active": "on",
                "idempotency_key": str(uuid4()),
            },
            **headers,
        )
        self.assertEqual(saved.status_code, 200, saved.content)
        self.assertContains(saved, "&lt;script&gt;")
        self.assertNotContains(saved, "<script>alert(1)</script>")
        self.assertContains(saved, 'hx-get="/catalog/locations/new/"')
        invalid = self.client.post(
            "/catalog/locations/new/",
            {"name": "", "idempotency_key": str(uuid4())},
            **headers,
        )
        self.assertEqual(invalid.status_code, 422, invalid.content)

    def test_browser_login_signed_session_and_csrf_protection(self):
        client = Client(enforce_csrf_checks=True)
        page = client.get("/auth/login/")
        self.assertEqual(page.status_code, 200)
        token = client.cookies["csrftoken"].value
        payload = {
            "tenant_code": "SCHOOL",
            "username": "admin",
            "password": self.password,
            "idempotency_key": str(uuid4()),
            "csrfmiddlewaretoken": token,
        }
        logged = client.post("/auth/login/", payload)
        self.assertEqual(logged.status_code, 302, logged.content)
        self.assertEqual(client.get("/catalog/items/").status_code, 200)
        denied = client.post(
            "/catalog/locations/new/",
            {"name": "无CSRF", "idempotency_key": str(uuid4())},
        )
        self.assertEqual(denied.status_code, 403)

    def test_expired_tenant_is_denied_immediately(self):
        token = self.token()
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        Tenant.objects.filter(pk=self.tenant.pk).update(
            expire_at=timezone.localdate() - timedelta(days=1)
        )
        with connection.cursor() as cursor:
            cursor.execute("SET LOCAL ROLE inventory_runtime")
        self.assertEqual(
            self.client.get(
                "/api/v1/catalog/items/", HTTP_AUTHORIZATION="Bearer " + token
            ).status_code,
            401,
        )

    def test_migration_identity_cannot_execute_business_requests(self):
        token = self.token()
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        denied = self.client.get(
            "/api/v1/catalog/items/", HTTP_AUTHORIZATION="Bearer " + token
        )
        self.assertEqual(denied.status_code, 503, denied.content)

    def test_runtime_can_read_migration_status_but_not_provision_tenants(self):
        with connection.cursor() as cursor:
            cursor.execute("SELECT count(*) FROM django_migrations")
            self.assertEqual(cursor.fetchone()[0], 10)
        with self.assertRaises(CommandError):
            call_command(
                "provision_tenant",
                code="BAD",
                name="无权限开通",
                admin_username="admin",
                idempotency_key=str(uuid4()),
                stdout=StringIO(),
            )

    def test_controlled_provisioning_is_idempotent_and_requires_password_change(self):
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        key = str(uuid4())
        with patch.dict(
            "os.environ", {"INITIAL_ADMIN_PASSWORD": "Provision-password-12345"}
        ):
            for _ in range(2):
                call_command(
                    "provision_tenant",
                    code="PROVISION",
                    name="开通学校",
                    admin_username="first",
                    idempotency_key=key,
                    stdout=StringIO(),
                )
        tenant = Tenant.objects.get(code="PROVISION")
        self.assertEqual(User.objects.filter(tenant_id=tenant.pk).count(), 1)
        self.assertTrue(User.objects.get(tenant_id=tenant.pk).must_change_password)

    def test_logout_original_key_replay_after_revocation(self):
        logged = self.login()
        token = logged.json()["access_token"]
        key = str(uuid4())
        for _ in range(2):
            response = self.auth(
                "logout", key=key, HTTP_AUTHORIZATION="Bearer " + token
            )
            self.assertEqual(response.status_code, 200, response.content)

    def test_reset_password_revokes_all_target_user_families(self):
        response = self.api(
            "put",
            f"/api/v1/master/users/{self.user.pk}/",
            {
                "username": "user",
                "name": "普通用户",
                "role": "user",
                "active": True,
                "password": "Reset-password-12345",
            },
        )
        self.assertEqual(response.status_code, 200, response.content)
        self.user.refresh_from_db()
        self.assertTrue(self.user.must_change_password)
        self.assertFalse(
            AuthSession.objects.filter(
                user_id=self.user.pk, revoked_at__isnull=True
            ).exists()
        )

    def test_invalid_signature_and_expired_jwt_are_rejected(self):
        token = self.token()
        self.assertEqual(
            self.client.get(
                "/api/v1/catalog/items/",
                HTTP_AUTHORIZATION="Bearer " + token + "broken",
            ).status_code,
            401,
        )
        expired = access_token(
            authorize_claims(self.claims[self.admin.pk]),
            int(timezone.now().timestamp()) - 600,
        )
        self.assertEqual(
            self.client.get(
                "/api/v1/catalog/items/", HTTP_AUTHORIZATION="Bearer " + expired
            ).status_code,
            401,
        )

    def test_cookie_json_write_requires_csrf(self):
        client = Client(enforce_csrf_checks=True)
        logged = self.login(client=client)
        self.assertEqual(logged.status_code, 200)
        denied = client.post(
            "/api/v1/catalog/locations/",
            json.dumps({"name": "无CSRF"}),
            content_type="application/json",
            HTTP_IDEMPOTENCY_KEY=str(uuid4()),
        )
        self.assertEqual(denied.status_code, 403, denied.content)

    def test_all_tenant_local_unique_master_keys(self):
        for resource, data in [
            ("departments", {"name": "唯一部门"}),
            ("suppliers", {"name": "唯一供应商"}),
            (
                "categories",
                {"code": "UNIQUE", "name": "唯一分类", "type": "consumable"},
            ),
            (
                "users",
                {
                    "username": "unique",
                    "name": "唯一用户",
                    "role": "user",
                    "password": "Unique-password-12345",
                },
            ),
        ]:
            path = f"/api/v1/{'master' if resource in {'departments', 'users'} else 'catalog'}/{resource}/"
            with self.subTest(resource=resource):
                first = self.api("post", path, data)
                self.assertEqual(first.status_code, 201, first.content)
                self.assertEqual(self.api("post", path, data).status_code, 409)
                self.assertEqual(
                    self.api("post", path, data, user=self.other).status_code, 201
                )

    def test_missing_command_key_and_inactive_item_category_are_rejected(self):
        response = self.client.post(
            "/api/v1/catalog/locations/",
            json.dumps({"name": "无幂等键"}),
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + self.token(),
        )
        self.assertEqual(response.status_code, 400, response.content)
        category = self.api(
            "post",
            "/api/v1/catalog/categories/",
            {"code": "OFF", "name": "停用分类", "type": "fixed"},
        ).json()["record"]
        self.assertEqual(
            self.api(
                "delete", f"/api/v1/catalog/categories/{category['id']}/", {}
            ).status_code,
            200,
        )
        response = self.api(
            "post",
            "/api/v1/catalog/items/",
            {
                "code": "BAD",
                "name": "物资",
                "type": "fixed",
                "category_id": category["id"],
                "unit": "台",
            },
        )
        self.assertEqual(response.status_code, 422, response.content)

    def test_encryption_missing_key_fails_closed_and_admin_form_can_decrypt(self):
        with override_settings(PII_ENCRYPTION_KEYS=[]):
            response = self.api(
                "post",
                "/api/v1/catalog/suppliers/",
                {"name": "无密钥", "contact": "sensitive"},
            )
            self.assertEqual(response.status_code, 503, response.content)
            self.assertFalse(Supplier.objects.filter(name="无密钥").exists())
        response = self.api(
            "post",
            "/api/v1/catalog/suppliers/",
            {"name": "有密钥", "contact": "encrypted-contact"},
        )
        record = response.json()["record"]
        page = self.client.get(
            f"/catalog/suppliers/{record['id']}/edit/",
            HTTP_AUTHORIZATION="Bearer " + self.token(),
        )
        self.assertContains(page, "encrypted-contact")
        self.assertEqual(page["Cache-Control"], "private, no-store")

    def test_in_use_asset_blocks_user_deactivation(self):
        operation = OperationRequest.objects.create(
            tenant_id=self.tenant.pk,
            actor_id=self.admin.pk,
            operation="fixture",
            idempotency_key=str(uuid4()),
            request_digest="fixture",
            response={"fixture": True},
        )
        location = Location.objects.create(tenant_id=self.tenant.pk, name="库位")
        category = Category.objects.create(
            tenant_id=self.tenant.pk, code="FIX", name="固定资产", type="fixed"
        )
        item = Item.objects.create(
            tenant_id=self.tenant.pk,
            category_id=category.pk,
            code="ASSET",
            name="设备",
            type="fixed",
            unit="台",
        )
        order = InboundOrder.objects.create(
            tenant_id=self.tenant.pk,
            order_no="IN-ASSET",
            type="opening",
            operator_id=self.admin.pk,
            operation_id=operation.pk,
        )
        line = InboundItem.objects.create(
            tenant_id=self.tenant.pk,
            order_id=order.pk,
            item_id=item.pk,
            item_type="fixed",
            quantity=1,
            location_id=location.pk,
            quality="available",
        )
        AssetCard.objects.create(
            tenant_id=self.tenant.pk,
            asset_no="AS-TEST",
            item_id=item.pk,
            source_inbound_item_id=line.pk,
            location_id=location.pk,
            status="in_use",
            responsible_user_id=self.user.pk,
            qr_token=str(uuid4()),
        )
        response = self.api(
            "delete", f"/api/v1/master/users/{self.user.pk}/", {"reason": "离岗"}
        )
        self.assertEqual(response.status_code, 409, response.content)
        self.assertEqual(response.json()["error"]["code"], "USER_HAS_OPEN_WORK")
