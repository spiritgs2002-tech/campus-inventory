from unittest.mock import patch
from django.db import OperationalError
from django.test import SimpleTestCase
from shared.errors import BusinessError
from shared.idempotency.service import execute


class CommandRetryTests(SimpleTestCase):
    def failure(self, code):
        cause = Exception("fixture database failure")
        cause.sqlstate = code
        error = OperationalError("fixture database failure")
        error.__cause__ = cause
        return error

    def test_deadlock_retry_reuses_same_key_and_has_finite_limit(self):
        with (
            patch(
                "shared.idempotency.service._execute", side_effect=self.failure("40P01")
            ) as operation,
            patch("shared.idempotency.service.time.sleep") as sleep,
        ):
            with self.assertRaises(BusinessError) as caught:
                execute(1, 2, "fixture", "same-key", {}, lambda: None)
        self.assertEqual(caught.exception.code, "RETRYABLE_LOCK_CONFLICT")
        self.assertEqual(operation.call_count, 3)
        self.assertEqual(sleep.call_count, 2)
        self.assertEqual(
            {call.args[3] for call in operation.call_args_list}, {"same-key"}
        )

    def test_network_failures_are_never_automatically_retried(self):
        with patch(
            "shared.idempotency.service._execute", side_effect=self.failure("08006")
        ) as operation:
            with self.assertRaises(OperationalError):
                execute(1, 2, "fixture", "same-key", {}, lambda: None)
        self.assertEqual(operation.call_count, 1)
