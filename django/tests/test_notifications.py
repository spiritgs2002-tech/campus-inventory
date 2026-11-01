from django.test import TestCase
from shared.auth.backends import authorize_claims
from shared.errors import BusinessError
from apps.notifications import services
from .order_fixtures import OrderFixtureMixin


class NotificationTests(OrderFixtureMixin, TestCase):
    def p(self, user):
        return authorize_claims(self.claims[user.pk])

    def make(self):
        return services.create_notification(
            self.tenant.pk,
            self.user.pk,
            self.user.name,
            "requisition_arrived",
            "到货",
            "请领取",
            "requisition",
            1,
        )

    def test_owner_reads_and_repeats(self):
        n = self.make()
        services.mark_read(self.p(self.user), n.pk)
        services.mark_read(self.p(self.user), n.pk)
        n.refresh_from_db()
        self.assertTrue(n.is_read)

    def test_other_user_cannot_read(self):
        n = self.make()
        with self.assertRaises(BusinessError):
            services.mark_read(self.p(self.admin), n.pk)

    def test_foreign_school_cannot_read(self):
        n = self.make()
        with self.assertRaises(BusinessError):
            services.mark_read(self.p(self.other), n.pk)

    def test_same_name_is_not_identity(self):
        self.make()
        self.admin.name = self.user.name
        self.admin.save(update_fields=["name"])
        self.assertFalse(services.owned(self.p(self.admin)).exists())

    def test_list_and_badge(self):
        self.make()
        r = self.request("get", "/s/notifications/", user=self.user)
        self.assertContains(r, "到货")
        self.assertEqual(r.context["unread_notification_count"], 1)

    def test_read_api(self):
        n = self.make()
        r = self.request(
            "post", f"/api/v1/notifications/{n.pk}/read/", {}, user=self.user
        )
        self.assertEqual(r.status_code, 200, r.content)

    def test_bearer_read_with_csrf_enforced(self):
        from django.test import Client
        from shared.auth.tokens import access_token

        row = self.make()
        response = Client(enforce_csrf_checks=True).post(
            f"/api/v1/notifications/{row.pk}/read/",
            data="{}",
            content_type="application/json",
            HTTP_AUTHORIZATION="Bearer " + access_token(self.p(self.user)),
        )
        self.assertEqual(response.status_code, 200, response.content)
