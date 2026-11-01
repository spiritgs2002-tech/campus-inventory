"""Import collection-form CSV under a runtime login, without changing schema."""

import codecs
import csv
from dataclasses import dataclass, field
from pathlib import Path
import re

from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, IntegrityError, transaction

from apps.catalog.models import Item, Location
from apps.core import services as core
from apps.core.models import Tenant, User
from apps.inventory.models import AssetCard
from apps.inventory.pickup import UNVERIFIED, ensure_lightweight_user, tenant_available
from apps.repair.models import RepairTicket
from shared.db.runtime import require_runtime
from shared.errors import BusinessError

TARGETS = {"asset", "item", "location"}
PRIORITIES = {"low", "normal", "high"}
STATUSES = {"pending", "dispatched", "repairing", "done", "cancelled"}
EMPLOYEE_NO = re.compile(r"[A-Za-z0-9_.-]{1,80}\Z", re.ASCII)
ID_MAX = 2**63 - 1
ALIASES = {
    "reporter_name": {"姓名", "报修人", "name"},
    "reporter_no": {"工号", "学号", "工号或学号", "employee_no"},
    "target_type": {"对象类型", "报修对象类型"},
    "target_id": {"对象编号", "编号", "id"},
    "target_name": {"对象名称", "名称"},
    "description": {"故障说明", "说明", "描述"},
    "priority": {"紧急程度", "优先级"},
    "status": {"状态"},
    "assigned_to_no": {"处理人工号", "处理人"},
    "reject_reason": {"驳回原因"},
    "note": {"备注", "地点"},
}


class RowError(ValueError):
    pass


@dataclass
class Summary:
    total: int
    processed: int = 0
    created: int = 0
    preview: int = 0
    skipped: int = 0
    errors: list = field(default_factory=list)
    rolled_back: int = 0


def normalize_header(value):
    return "".join(value.lstrip("\ufeff").split()).casefold()


def header_map(header):
    lookup = {
        normalize_header(alias): canonical
        for canonical, aliases in ALIASES.items()
        for alias in {canonical, canonical.replace("_", ""), *aliases}
    }
    result = {}
    for index, raw in enumerate(header):
        canonical = lookup.get(normalize_header(raw))
        if canonical is None:
            continue  # Collection forms may contain unrelated timestamp columns.
        if canonical in result.values():
            raise CommandError(f"表头重复映射到 {canonical}", returncode=2)
        result[index] = canonical
    required = {"reporter_name", "reporter_no", "description"}
    missing = required - set(result.values())
    if missing:
        raise CommandError("缺少必填列: " + ", ".join(sorted(missing)), returncode=2)
    return result


def read_csv(path, encoding, delimiter):
    """Decode and parse completely before any write, including trailing bad input."""
    try:
        with path.open(encoding=encoding, newline="") as source:
            reader = csv.reader(source, delimiter=delimiter, strict=True)
            header = next(reader, None)
            if not header:
                raise CommandError("CSV为空或没有表头", returncode=2)
            mapping = header_map(header)
            rows = []
            for raw in reader:
                if not raw:
                    continue
                # seq numbers logical records; line_num identifies physical CSV lines.
                rows.append((reader.line_num, raw))
            return header, mapping, rows
    except (OSError, UnicodeError, csv.Error, LookupError, ValueError) as error:
        raise CommandError(f"CSV文件读取或解析失败: {error}", returncode=2) from None


def one_id(rows, label):
    ids = list(rows.values_list("id", flat=True)[:2])
    if len(ids) != 1:
        raise RowError(f"{label}须在本校精确匹配一条启用记录（实际{len(ids)}条或更多）")
    return ids[0]


def resolve_target(tenant_id, kind, explicit_id, name):
    if kind == "asset":
        # AssetCard has no active flag; the associated catalog item owns it.
        rows = AssetCard.objects.filter(tenant_id=tenant_id, item__active=True)
    else:
        model = Item if kind == "item" else Location
        rows = model.objects.filter(tenant_id=tenant_id, active=True)
    if explicit_id:
        if not re.fullmatch(r"[0-9]+", explicit_id, re.ASCII):
            raise RowError("target_id须为正整数；不会忽略无效编号改用名称")
        # Compare length first, avoiding arbitrary-size integer conversion.
        digits = explicit_id.lstrip("0") or "0"
        if len(digits) > 19 or not 1 <= int(digits) <= ID_MAX:
            raise RowError("target_id超出有效编号范围")
        return one_id(rows.filter(pk=int(digits)), "target_id")
    if not name:
        if kind == "location":
            return None
        raise RowError("资产或物品须填写target_id或target_name")
    if kind == "asset":
        return one_id(rows.filter(asset_no=name), "资产编号")
    if kind == "item":
        codes = list(rows.filter(code=name).values_list("id", flat=True)[:2])
        if codes:
            if len(codes) != 1:
                raise RowError("物品code匹配到多条")
            return codes[0]
    return one_id(rows.filter(name=name), "对象名称")


def prepare(tenant_id, row, default_type):
    values = {name: "" for name in ALIASES}
    values.update({name: value.strip() for name, value in row.items()})
    if any("\x00" in value for value in values.values()):
        raise RowError("字段包含无效NUL字符")
    try:
        for value in values.values():
            value.encode("utf-8")
    except UnicodeError:
        raise RowError("字段包含不能写入数据库的Unicode字符") from None
    name, number = values["reporter_name"], values["reporter_no"]
    if not 1 <= len(name) <= 120:
        raise RowError("reporter_name须为1–120字符")
    if not EMPLOYEE_NO.fullmatch(number):
        raise RowError("reporter_no须为1–80位字母、数字、下划线、点或短横线")
    description = values["description"]
    if not description:
        raise RowError("description不能为空")
    if values["note"]:
        description += "\n备注：" + values["note"]
    if len(description) > 4000:
        raise RowError("description合并备注后不能超过4000字符")
    kind = (values["target_type"] or default_type).casefold()
    priority = (values["priority"] or "normal").casefold()
    status = (values["status"] or "pending").casefold()
    if kind not in TARGETS or priority not in PRIORITIES or status not in STATUSES:
        raise RowError("target_type、priority或status取值无效")
    target_id = resolve_target(
        tenant_id, kind, values["target_id"], values["target_name"]
    )
    assigned_to = None
    if values["assigned_to_no"]:
        if not EMPLOYEE_NO.fullmatch(values["assigned_to_no"]):
            raise RowError("assigned_to_no工号无效")
        assigned_to = one_id(
            User.objects.filter(
                tenant_id=tenant_id, username=values["assigned_to_no"], active=True
            ),
            "处理人工号",
        )
    if status in {"dispatched", "repairing", "done"} and assigned_to is None:
        raise RowError("此状态须填写本校启用处理人的assigned_to_no")
    if len(values["reject_reason"]) > 2000:
        raise RowError("reject_reason不能超过2000字符")
    return {
        "reporter_name": name,
        "reporter_no": number,
        "target_type": kind,
        "target_id": target_id,
        "description": description,
        "priority": priority,
        "status": status,
        "assigned_to": assigned_to,
        "reject_reason": values["reject_reason"] or None,
        "photo_attachment_id": None,
    }


def preview_identity(tenant_id, name, number, seen):
    """Read-only preview: do not allocate users, tickets, audits or sequence IDs."""
    user = User.objects.filter(tenant_id=tenant_id, username=number).first()
    if user is not None:
        if (
            user.role != "user"
            or not user.active
            or user.must_change_password
            or not user.password_hash.startswith(UNVERIFIED)
            or user.name != name
        ):
            raise RowError("该工号不能用于轻量登记，须核验现有账号")
    if number in seen and seen[number] != name:
        raise RowError("同批次同工号姓名不一致")


def database_error(error):
    state = getattr(error.__cause__, "sqlstate", "")
    return {
        "23503": "数据库外键校验失败（对象或用户已变化）",
        "23514": "数据库CHECK约束校验失败",
        "23505": "数据库唯一约束冲突（非工单重复键）",
        "22001": "字段超过数据库长度限制",
        "22003": "编号或数字超出数据库范围",
        "55P03": "记录繁忙，请以相同batch-id重试",
        "40P01": "事务锁冲突，请以相同batch-id重试",
    }.get(state, "数据库操作失败，本行已回滚")


class Command(BaseCommand):
    help = "从收集表CSV导入报修工单（普通运行身份、租户隔离、确定性批次幂等）"
    requires_system_checks = []

    def add_arguments(self, parser):
        parser.add_argument("--csv", required=True)
        parser.add_argument("--tenant-code", required=True)
        parser.add_argument("--batch-id", required=True)
        parser.add_argument("--encoding", default="utf-8-sig")
        parser.add_argument("--delimiter", default=",")
        parser.add_argument("--dry-run", action="store_true")
        parser.add_argument("--strict", action="store_true")
        parser.add_argument(
            "--default-target-type", choices=sorted(TARGETS), default="location"
        )

    def handle(self, *args, **options):
        try:
            require_runtime()
            self.import_file(options)
        except BusinessError as error:
            raise CommandError(f"{error.code}: {error.message}", returncode=2) from None
        except DatabaseError:
            raise CommandError(
                "数据库不可用或批次事务失败，整批未提交", returncode=2
            ) from None

    def import_file(self, options):
        if options["default_target_type"] not in TARGETS:
            raise CommandError("default-target-type取值无效", returncode=2)
        batch = options["batch_id"]
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,79}", batch, re.ASCII):
            raise CommandError(
                "batch-id须为1–80位字母、数字、点、下划线或短横线，且以字母或数字开头",
                returncode=2,
            )
        delimiter = "\t" if options["delimiter"] == r"\t" else options["delimiter"]
        if delimiter not in {",", ";", "\t"}:
            raise CommandError("delimiter仅支持逗号、分号或制表符", returncode=2)
        try:
            codecs.lookup(options["encoding"])
        except LookupError:
            raise CommandError("encoding不是有效的编码名称", returncode=2) from None
        try:
            tenant = Tenant.objects.get(code=options["tenant_code"].strip())
        except Tenant.DoesNotExist:
            raise CommandError("租户代码不存在", returncode=2) from None
        tenant_available(tenant)
        path = Path(options["csv"]).expanduser()
        header, mapping, rows = read_csv(path, options["encoding"], delimiter)
        stats = Summary(total=len(rows))
        dry, strict = options["dry_run"], options["strict"]
        seen = {}
        with transaction.atomic():
            tenant = Tenant.objects.select_for_update(no_key=True).get(pk=tenant.pk)
            tenant_available(tenant)
            for seq, (line, raw) in enumerate(rows, start=1):
                stats.processed += 1
                ticket_no = f"RP-{batch}-{seq:05d}"
                try:
                    with transaction.atomic():
                        if RepairTicket.objects.filter(
                            tenant_id=tenant.pk, ticket_no=ticket_no
                        ).exists():
                            stats.skipped += 1
                            continue
                        if len(raw) != len(header):
                            raise RowError("列数与表头不一致")
                        data = prepare(
                            tenant.pk,
                            {
                                canonical: raw[index]
                                for index, canonical in mapping.items()
                            },
                            options["default_target_type"],
                        )
                        if dry:
                            preview_identity(
                                tenant.pk,
                                data["reporter_name"],
                                data["reporter_no"],
                                seen,
                            )
                            seen[data["reporter_no"]] = data["reporter_name"]
                            stats.preview += 1
                            continue
                        p = ensure_lightweight_user(
                            tenant.pk, data["reporter_name"], data["reporter_no"]
                        )
                        ticket = RepairTicket.objects.create(
                            tenant_id=tenant.pk,
                            ticket_no=ticket_no,
                            reporter_user_id=p.user_id,
                            **data,
                        )
                        core.audit(
                            p,
                            "repair",
                            ticket.pk,
                            None,
                            {
                                "action": "create",
                                "identity": "unverified",
                                "source": "csv",
                                "batch_id": batch,
                                "seq": seq,
                                "status": ticket.status,
                                "photo_attachment_id": None,
                            },
                            "CSV批量报修导入",
                        )
                    stats.created += 1
                except IntegrityError as error:
                    constraint = getattr(
                        getattr(error.__cause__, "diag", None), "constraint_name", ""
                    )
                    if (
                        constraint == "uq_repair_tenant_no"
                        and RepairTicket.objects.filter(
                            tenant_id=tenant.pk, ticket_no=ticket_no
                        ).exists()
                    ):
                        stats.skipped += 1
                        continue
                    stats.errors.append((line, seq, database_error(error)))
                except (RowError, BusinessError) as error:
                    stats.errors.append((line, seq, str(error)))
                except DatabaseError as error:
                    stats.errors.append((line, seq, database_error(error)))
                if strict and stats.errors:
                    stats.rolled_back, stats.created = stats.created, 0
                    transaction.set_rollback(True)
                    break
        self.stdout.write(
            f"[import_repairs] tenant={tenant.code} batch={batch} csv={path.name}"
        )
        self.stdout.write(
            f"  总行数        : {stats.total}\n  成功导入      : {stats.created}\n  跳过(重复)    : {stats.skipped}\n  行错误        : {len(stats.errors)}"
        )
        if dry:
            self.stdout.write(f"  dry-run可导入 : {stats.preview}（未写入任何记录）")
        if strict and stats.errors:
            self.stdout.write(
                f"  strict整批回滚: {stats.rolled_back}\n  未处理        : {stats.total - stats.processed}"
            )
        if stats.errors:
            self.stdout.write("  错误明细:")
            for line, seq, message in stats.errors:
                self.stdout.write(f"    行 {line}（数据序号{seq}）: {message}")
            raise CommandError(
                f"导入存在{len(stats.errors)}行错误，请修正后以同批次重跑", returncode=1
            )
