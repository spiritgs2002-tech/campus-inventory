import csv
from datetime import timedelta
from io import StringIO
from pathlib import Path
import tempfile
from unittest.mock import patch

from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection, DatabaseError, IntegrityError
from django.test import TestCase
from django.utils import timezone

from apps.catalog.models import Item
from apps.core import services as core
from apps.core.models import AuditLog, AuthSession, Tenant, User
from apps.inventory.models import AssetCard
from apps.inventory.pickup import ensure_lightweight_user
from apps.repair.models import RepairTicket
from .order_fixtures import OrderFixtureMixin

FIELDS = [
    "reporter_name",
    "reporter_no",
    "description",
    "target_type",
    "target_id",
    "target_name",
    "priority",
    "status",
    "assigned_to_no",
    "reject_reason",
    "note",
]


class ImportRepairTests(OrderFixtureMixin, TestCase):
    def setUp(self):
        super().setUp()
        directory = tempfile.TemporaryDirectory(prefix="import-repairs-", dir="/tmp")
        self.addCleanup(directory.cleanup)
        self.csv_path = Path(directory.name) / "repairs.csv"

    def row(self, **changes):
        return {
            "reporter_name": "收集表教师",
            "reporter_no": "CSV001",
            "description": "门锁损坏",
            **changes,
        }

    def write(self, rows, *, headers=FIELDS, delimiter=",", encoding="utf-8-sig"):
        with self.csv_path.open("w", encoding=encoding, newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=headers, delimiter=delimiter)
            writer.writeheader()
            writer.writerows(rows)

    def run_import(self, *, code=0, **options):
        output = StringIO()
        error = None
        try:
            arguments = {
                "csv": str(self.csv_path),
                "tenant_code": self.tenant.code,
                "batch_id": "20261101-form",
                "stdout": output,
                **options,
            }
            call_command("import_repairs", **arguments)
        except CommandError as caught:
            error = caught
        if code:
            self.assertIsNotNone(error, output.getvalue())
            self.assertEqual(error.returncode, code, str(error))
        elif error:
            self.fail(f"Unexpected command error: {error}\n{output.getvalue()}")
        return output.getvalue()

    def test_normal_import_has_audit_and_generated_no(self):
        self.write([self.row()])
        before = timezone.now()
        sessions = AuthSession.objects.count()
        output = self.run_import()
        row = RepairTicket.objects.get()
        self.assertEqual(row.ticket_no, "RP-20261101-form-00001")
        self.assertEqual(
            (row.status, row.target_type, row.priority),
            ("pending", "location", "normal"),
        )
        self.assertIsNone(row.target_id)
        self.assertIsNone(row.photo_attachment_id)
        self.assertGreaterEqual(row.created_at, before)
        user = User.objects.get(tenant_id=self.tenant.pk, username="CSV001")
        self.assertEqual(row.reporter_user_id, user.pk)
        self.assertTrue(user.password_hash.startswith("!pickup-unverified:"))
        self.assertEqual(AuthSession.objects.count(), sessions)
        audit = AuditLog.objects.get(object_type="repair", object_id=row.pk)
        self.assertEqual(audit.action, "repair.create")
        self.assertEqual(audit.user_id, user.pk)
        self.assertEqual(audit.changes["after"]["source"], "csv")
        self.assertIn("成功导入      : 1", output)

    def test_chinese_aliases_whitespace_case_and_bom(self):
        self.write(
            [
                {
                    " 姓 名 ": " 教师 ",
                    "Employee_NO": "CSV002",
                    "描 述": " 门损坏 ",
                    "地点": "教学楼一层",
                    "收集时间": "2020-01-01",
                }
            ],
            headers=[" 姓 名 ", "Employee_NO", "描 述", "地点", "收集时间"],
        )
        self.run_import()
        row = RepairTicket.objects.get()
        self.assertEqual(row.description, "门损坏\n备注：教学楼一层")
        self.assertEqual(row.reporter_name, "教师")

    def test_item_name_code_priority_and_location_name(self):
        other = Item.objects.create(
            tenant_id=self.tenant.pk,
            code="OTHER-CODE",
            name=self.item.code,
            type="consumable",
            category_id=self.item.category_id,
            unit="件",
        )
        self.write(
            [
                self.row(target_type="item", target_name=self.item.code),
                self.row(target_type="item", target_name=self.item.name),
                self.row(target_type="location", target_name=self.location.name),
            ]
        )
        self.run_import()
        self.assertEqual(
            list(
                RepairTicket.objects.order_by("id").values_list("target_id", flat=True)
            ),
            [self.item.pk, self.item.pk, self.location.pk],
        )
        self.assertNotEqual(other.pk, self.item.pk)

    def test_asset_number_and_explicit_id(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        self.write(
            [
                self.row(target_type="asset", target_name=card.asset_no),
                self.row(
                    target_type="asset",
                    target_id=str(card.pk),
                    target_name="不存在但编号优先",
                ),
            ]
        )
        self.run_import()
        for row in RepairTicket.objects.all():
            self.assertEqual(row.asset_target_id, card.pk)
            self.assertIsNone(row.item_target_id)

    def test_ambiguous_item_name_is_error_and_next_row_imports(self):
        Item.objects.create(
            tenant_id=self.tenant.pk,
            code="DUP",
            name=self.item.name,
            type="consumable",
            category_id=self.item.category_id,
            unit="件",
        )
        self.write(
            [self.row(target_type="item", target_name=self.item.name), self.row()]
        )
        output = self.run_import(code=1)
        self.assertEqual(RepairTicket.objects.get().ticket_no, "RP-20261101-form-00002")
        self.assertIn("行错误        : 1", output)

    def test_explicit_invalid_id_never_falls_back_to_valid_name(self):
        for value in ["-1", "0", "x", "1.0", "9223372036854775808"]:
            self.write(
                [
                    self.row(
                        target_type="item", target_id=value, target_name=self.item.code
                    )
                ]
            )
            self.run_import(code=1)
        self.assertFalse(RepairTicket.objects.exists())

    def test_asset_missing_or_inactive_item_rejected(self):
        self.receive(1, fixed=True)
        card = AssetCard.objects.get(tenant_id=self.tenant.pk)
        Item.objects.filter(pk=self.fixed.pk).update(active=False)
        self.write([self.row(target_type="asset", target_name=card.asset_no)])
        self.run_import(code=1)
        self.assertFalse(RepairTicket.objects.exists())

    def test_duplicate_replay_has_no_user_or_audit_side_effect(self):
        self.write([self.row(), self.row()])
        self.run_import()
        counts = User.objects.count(), AuditLog.objects.count()
        output = self.run_import()
        self.assertIn("跳过(重复)    : 2", output)
        self.assertEqual(RepairTicket.objects.count(), 2)
        self.assertEqual((User.objects.count(), AuditLog.objects.count()), counts)

    def test_rows_reuse_one_lightweight_identity(self):
        self.write([self.row(), self.row(description="其他问题")])
        self.run_import()
        self.assertEqual(
            User.objects.filter(tenant_id=self.tenant.pk, username="CSV001").count(), 1
        )
        self.assertEqual(
            RepairTicket.objects.values("reporter_user_id").distinct().count(), 1
        )

    def test_mixed_six_rows_and_replay(self):
        self.write(
            [
                self.row(),
                self.row(target_type="location", target_name=self.location.name),
                self.row(target_type="item", target_name=self.item.code),
                self.row(reporter_no="错误工号"),
                self.row(priority="HIGH"),
                self.row(status="done", assigned_to_no=self.user.username),
            ]
        )
        self.run_import(code=1)
        self.assertEqual(RepairTicket.objects.count(), 5)
        output = self.run_import(code=1)
        self.assertIn("跳过(重复)    : 5", output)
        self.assertIn("成功导入      : 0", output)
        self.assertEqual(RepairTicket.objects.count(), 5)

    def test_bad_row_does_not_pollute_identity_for_later_row(self):
        self.write(
            [
                self.row(target_type="item", target_name="不存在"),
                self.row(reporter_name="另一人"),
            ]
        )
        self.run_import(code=1)
        self.assertEqual(User.objects.get(username="CSV001").name, "另一人")

    def test_formal_disabled_and_renamed_identities_rejected(self):
        p = ensure_lightweight_user(self.tenant.pk, "轻量教师", "DISABLED")
        User.objects.filter(pk=p.user_id).update(active=False)
        self.write(
            [
                self.row(reporter_name=self.user.name, reporter_no=self.user.username),
                self.row(reporter_name="轻量教师", reporter_no="DISABLED"),
                self.row(reporter_name="原名", reporter_no="RENAME"),
                self.row(reporter_name="改名", reporter_no="RENAME"),
            ]
        )
        self.run_import(code=1)
        self.assertEqual(RepairTicket.objects.count(), 1)
        self.assertEqual(RepairTicket.objects.get().reporter_name, "原名")

    def test_assignee_status_rules(self):
        rows = [
            self.row(status=status, assigned_to_no=self.user.username)
            for status in ["pending", "dispatched", "repairing", "done", "cancelled"]
        ]
        self.write(rows)
        self.run_import()
        self.assertEqual(RepairTicket.objects.count(), 5)
        self.write(
            [
                self.row(status="done"),
                self.row(status="dispatched", assigned_to_no="unknown"),
                self.row(status="repairing", assigned_to_no=self.other.username),
            ]
        )
        self.run_import(code=1, batch_id="invalid-assignees")
        self.assertEqual(RepairTicket.objects.count(), 5)

    def test_disabled_assignee_rejected(self):
        User.objects.filter(pk=self.user.pk).update(active=False)
        self.write([self.row(status="done", assigned_to_no=self.user.username)])
        self.run_import(code=1)
        self.assertFalse(RepairTicket.objects.exists())

    def test_text_lengths_and_note_not_truncated(self):
        self.write(
            [
                self.row(description="x" * 4000, note="再加地点"),
                self.row(reporter_name="x" * 121),
                self.row(reject_reason="x" * 2001),
                self.row(description="x" * 4000),
            ]
        )
        self.run_import(code=1)
        self.assertEqual(len(RepairTicket.objects.get().description), 4000)

    def test_invalid_values_and_missing_targets_are_row_errors(self):
        self.write(
            [
                self.row(description=" "),
                self.row(target_type="asset"),
                self.row(target_type="item"),
                self.row(target_type="other"),
                self.row(priority="urgent"),
                self.row(status="lost"),
                self.row(description="NUL\x00"),
            ]
        )
        output = self.run_import(code=1)
        self.assertIn("行错误        : 7", output)
        self.assertFalse(RepairTicket.objects.exists())

    def test_strict_rolls_back_successes_and_stops(self):
        self.write(
            [
                self.row(),
                self.row(reporter_no="bad!"),
                self.row(reporter_no="UNPROCESSED"),
            ]
        )
        output = self.run_import(code=1, strict=True)
        self.assertIn("成功导入      : 0", output)
        self.assertIn("strict整批回滚: 1", output)
        self.assertIn("未处理        : 1", output)
        self.assertFalse(RepairTicket.objects.exists())
        self.assertFalse(
            User.objects.filter(username__in=["CSV001", "UNPROCESSED"]).exists()
        )
        self.assertFalse(AuditLog.objects.filter(object_type="repair").exists())

    def test_dry_run_does_not_allocate_records_or_sequences(self):
        self.write([self.row(), self.row()])
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT last_value,is_called FROM repair_repairticket_id_seq"
            )
            sequence = cursor.fetchone()
            cursor.execute("SELECT last_value,is_called FROM users_id_seq")
            user_sequence = cursor.fetchone()
        counts = User.objects.count(), AuditLog.objects.count()
        output = self.run_import(dry_run=True)
        self.assertIn("dry-run可导入 : 2", output)
        self.assertFalse(RepairTicket.objects.exists())
        self.assertEqual((User.objects.count(), AuditLog.objects.count()), counts)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT last_value,is_called FROM repair_repairticket_id_seq"
            )
            self.assertEqual(cursor.fetchone(), sequence)
            cursor.execute("SELECT last_value,is_called FROM users_id_seq")
            self.assertEqual(cursor.fetchone(), user_sequence)

    def test_dry_run_matches_identity_conflicts_and_duplicates(self):
        self.write([self.row()])
        self.run_import()
        self.write(
            [
                self.row(),
                self.row(reporter_name="改名"),
                self.row(reporter_no="NEW", reporter_name="一"),
                self.row(reporter_no="NEW", reporter_name="二"),
            ]
        )
        output = self.run_import(code=1, dry_run=True)
        self.assertIn("跳过(重复)    : 1", output)
        self.assertIn("行错误        : 2", output)
        self.assertIn("dry-run可导入 : 1", output)
        self.assertFalse(User.objects.filter(username="NEW").exists())

    def test_tenant_isolation_and_same_ticket_no_in_two_schools(self):
        self.write(
            [self.row(target_type="location", target_id=str(self.other_location.pk))]
        )
        self.run_import(code=1)
        self.write([self.row()])
        self.run_import()
        row = RepairTicket.objects.get()
        self.assertEqual(
            self.request(
                "get", f"/api/v1/repair/{row.pk}/", user=self.other
            ).status_code,
            403,
        )
        self.assertContains(self.request("get", "/manage/repairs/"), row.ticket_no)
        self.assertNotContains(
            self.request("get", "/manage/repairs/", user=self.other), row.ticket_no
        )
        with self.csv_path.open(encoding="utf-8-sig") as source:
            self.assertIn("reporter_name", source.readline())
        call_command(
            "import_repairs",
            csv=str(self.csv_path),
            tenant_code=self.other_tenant.code,
            batch_id="20261101-form",
            stdout=StringIO(),
        )
        self.assertEqual(
            RepairTicket.objects.filter(ticket_no=row.ticket_no).count(), 2
        )

    def test_disabled_or_expired_tenant_and_missing_tenant_are_fatal(self):
        self.write([self.row()])
        for changes in [
            {"status": "disabled"},
            {"status": "active", "expire_at": timezone.localdate() - timedelta(days=1)},
        ]:
            with connection.cursor() as cursor:
                cursor.execute("RESET ROLE")
                Tenant.objects.filter(pk=self.tenant.pk).update(**changes)
                cursor.execute("SET LOCAL ROLE inventory_runtime")
            self.run_import(code=2)
        self.assertFalse(RepairTicket.objects.exists())

    def test_duplicate_headers_missing_headers_and_empty_file(self):
        for content in [
            "reporter_name,姓名,reporter_no,description\nA,A,B,C\n",
            "姓名,描述\nA,B\n",
            "",
        ]:
            self.csv_path.write_text(content)
            self.run_import(code=2)
        self.assertFalse(RepairTicket.objects.exists())

    def test_row_column_count_and_multiline_physical_lines(self):
        self.csv_path.write_text(
            '姓名,工号,描述\nA,CSV1,"第一行\n第二行"\nB,CSV2\nC,CSV3,正常,额外\nD,CSV4,正常\n'
        )
        output = self.run_import(code=1)
        self.assertIn("行 4（数据序号2）", output)
        self.assertEqual(
            list(
                RepairTicket.objects.order_by("id").values_list("ticket_no", flat=True)
            ),
            ["RP-20261101-form-00001", "RP-20261101-form-00004"],
        )

    def test_bad_trailing_encoding_or_csv_is_fatal_before_writes(self):
        for raw in [
            b"name,employee_no,description\nA,CSV1,OK\n\xff",
            b'name,employee_no,description\nA,CSV1,OK\n"unterminated',
        ]:
            self.csv_path.write_bytes(raw)
            self.run_import(code=2)
            self.assertFalse(RepairTicket.objects.exists())

    def test_decoded_surrogate_is_a_row_error_not_a_broken_batch(self):
        self.csv_path.write_text(
            "name,employee_no,description\nTeacher,ESC,"
            + chr(92)
            + "ud800\nTeacher,GOOD,normal\n",
            encoding="ascii",
        )
        output = self.run_import(code=1, encoding="unicode_escape")
        self.assertIn("Unicode", output)
        self.assertEqual(RepairTicket.objects.get().reporter_no, "GOOD")

    def test_delimiters_encoding_and_default_type(self):
        for index, delimiter in enumerate([";", "\t"]):
            self.write(
                [self.row(target_name=self.item.code)],
                delimiter=delimiter,
                encoding="gb18030",
            )
            self.run_import(
                delimiter=r"\t" if delimiter == "\t" else delimiter,
                encoding="gb18030",
                default_target_type="item",
                batch_id=f"separated-{index}",
            )
        self.assertEqual(RepairTicket.objects.count(), 2)

    def test_bad_parameters_and_file_are_exit_2(self):
        self.write([self.row()])
        for options in [
            {"batch_id": ""},
            {"batch_id": "bad/batch"},
            {"delimiter": "|"},
            {"encoding": "not-a-codec"},
        ]:
            self.run_import(code=2, **options)
        self.csv_path.unlink()
        self.run_import(code=2)

    def test_non_duplicate_integrity_failure_is_not_skipped(self):
        self.write([self.row(), self.row(reporter_no="CSV002")])
        original = RepairTicket.objects.create
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise IntegrityError("not a duplicate ticket key")
            return original(**kwargs)

        with patch.object(RepairTicket.objects, "create", side_effect=create):
            output = self.run_import(code=1)
        self.assertIn("跳过(重复)    : 0", output)
        self.assertEqual(RepairTicket.objects.count(), 1)
        self.assertFalse(User.objects.filter(username="CSV001").exists())

    def test_audit_failure_rolls_back_that_row_and_continues(self):
        self.write([self.row(), self.row(reporter_no="CSV002")])
        original = core.audit

        def audit(principal, *args, **kwargs):
            if principal.username == "CSV001":
                raise DatabaseError("injected audit failure")
            return original(principal, *args, **kwargs)

        with patch(
            "apps.repair.management.commands.import_repairs.core.audit",
            side_effect=audit,
        ):
            self.run_import(code=1)
        self.assertEqual(RepairTicket.objects.count(), 1)
        self.assertEqual(AuditLog.objects.filter(object_type="repair").count(), 1)
        self.assertFalse(User.objects.filter(username="CSV001").exists())

    def test_database_check_failure_savepoint_recovers(self):
        self.write([self.row(), self.row()])
        original = RepairTicket.objects.create
        calls = []

        def create(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                kwargs["status"] = "invalid-database-state"
            return original(**kwargs)

        with patch.object(RepairTicket.objects, "create", side_effect=create):
            output = self.run_import(code=1)
        self.assertIn("CHECK", output)
        self.assertEqual(RepairTicket.objects.get().ticket_no, "RP-20261101-form-00002")

    def test_superuser_is_rejected_before_reading_csv(self):
        with connection.cursor() as cursor:
            cursor.execute("RESET ROLE")
        self.run_import(code=2)
        self.assertFalse(RepairTicket.objects.exists())
