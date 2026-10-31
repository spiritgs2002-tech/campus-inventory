from io import StringIO
import json
import logging
from unittest.mock import patch
from django.db import OperationalError
from django.test import SimpleTestCase
from django.urls import reverse
import redis
from apps.core import views
from shared.logging import JsonFormatter


class ReadinessTests(SimpleTestCase):
    def setUp(self):
        db = patch("apps.core.views.connection")
        self.db = db.start()
        self.addCleanup(db.stop)
        broker = patch("apps.core.views.redis.Redis.from_url")
        self.factory = broker.start()
        self.addCleanup(broker.stop)
        self.redis = self.factory.return_value.__enter__.return_value
        self.redis.ping.return_value = True

    def test_healthy_dependencies_and_head_without_auth(self):
        response = self.client.get(
            reverse("health-ready"), HTTP_AUTHORIZATION="invalid"
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"status": "ok"})
        self.assertEqual(response["Cache-Control"], "no-store")
        self.db.cursor.return_value.__enter__.return_value.execute.assert_called_once_with(
            "SELECT 1"
        )
        response = self.client.head("/health/ready/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
        self.assertIn("default", views.ready._non_atomic_requests)

    def test_database_failure_is_503_and_does_not_leak_exception(self):
        self.db.cursor.side_effect = OperationalError("sensitive-connection-data")
        with self.assertLogs("apps.core.views", level="WARNING") as captured:
            response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["checks"], {"database": "error", "redis": "ok"}
        )
        self.assertNotIn("sensitive-connection-data", response.content.decode())
        self.assertNotIn("sensitive-connection-data", "".join(captured.output))
        self.redis.ping.assert_called_once()

    def test_redis_failure_is_503_and_client_is_closed(self):
        self.redis.ping.side_effect = redis.ConnectionError("sensitive-connection-data")
        with self.assertLogs("apps.core.views", level="WARNING"):
            response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json()["checks"], {"database": "ok", "redis": "error"}
        )
        self.assertNotIn("sensitive-connection-data", response.content.decode())
        self.factory.return_value.__exit__.assert_called_once()

    def test_redis_timeout_or_negative_ping_is_not_ready(self):
        for failure in [redis.TimeoutError("read timed out"), False]:
            with self.subTest(failure=type(failure).__name__):
                self.redis.ping.side_effect = (
                    failure if isinstance(failure, Exception) else None
                )
                self.redis.ping.return_value = False
                with self.assertLogs("apps.core.views", level="WARNING"):
                    response = self.client.get("/health/ready/")
                self.assertEqual(response.status_code, 503)
        self.assertEqual(self.factory.call_args.kwargs["socket_timeout"], 2)
        self.assertEqual(self.factory.call_args.kwargs["socket_connect_timeout"], 2)

    def test_both_dependencies_failed_are_reported_without_internal_details(self):
        self.db.cursor.side_effect = OperationalError("database unavailable")
        self.factory.side_effect = ValueError("malformed credential URL")
        with self.assertLogs("apps.core.views", level="WARNING"):
            response = self.client.get("/health/ready/")
        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {"status": "error", "checks": {"database": "error", "redis": "error"}},
        )

    def test_unsafe_methods_rejected_without_dependency_access(self):
        for method in ["post", "put", "patch", "delete"]:
            self.assertEqual(
                getattr(self.client, method)("/health/ready/").status_code, 405
            )
        self.db.cursor.assert_not_called()
        self.factory.assert_not_called()

    def test_liveness_stays_independent_when_dependencies_fail(self):
        self.db.cursor.side_effect = AssertionError("liveness must not access DB")
        self.factory.side_effect = AssertionError("liveness must not access Redis")
        self.assertEqual(self.client.get("/health/live/").status_code, 200)


class StructuredLoggingTests(SimpleTestCase):
    def test_info_is_one_json_line_with_unicode_and_dependency_fields(self):
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.Logger("test-readiness", level=logging.INFO)
        logger.addHandler(handler)
        logger.info(
            "就绪检查\n第二行",
            extra={"dependency": "redis", "error_type": "TimeoutError"},
        )
        lines = stream.getvalue().splitlines()
        self.assertEqual(len(lines), 1)
        value = json.loads(lines[0])
        self.assertEqual(value["level"], "INFO")
        self.assertEqual(value["message"], "就绪检查\n第二行")
        self.assertEqual(value["dependency"], "redis")
        self.assertEqual(value["error_type"], "TimeoutError")
        self.assertTrue(value["timestamp"].endswith("+00:00"))

    def test_exception_traceback_is_json_encoded(self):
        stream = StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(JsonFormatter())
        logger = logging.Logger("test-error")
        logger.addHandler(handler)
        try:
            raise RuntimeError("fixture failure")
        except RuntimeError:
            logger.exception("Application error")
        value = json.loads(stream.getvalue())
        self.assertEqual(value["level"], "ERROR")
        self.assertIn("RuntimeError", value["exception"])
