"""Thirteen diagnostics in one read-only repeatable-read snapshot; never repairs."""

import re
from django.conf import settings
from django.db import connection, transaction
from shared.db.runtime import require_runtime
from shared.errors import BusinessError

CHECKS = (
    "inventory_ledger",
    "asset_latest_event",
    "asset_open_issues",
    "fixed_issued_count",
    "fixed_created_count",
    "excess_consumable_return",
    "quality_transfer_ledger",
    "inbound_ledger",
    "outbound_ledger",
    "scrap_ledger",
    "adjustment_ledger",
    "ledger_balance_after",
    "purchase_return_excess",
)


def statements():
    try:
        source = (settings.SQL_ROOT / "reconcile-v2.sql").read_text()
        body = source.split(
            "BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;\n", 1
        )[1].rsplit("\nCOMMIT;", 1)[0]
    except (OSError, IndexError):
        raise BusinessError(
            "INVALID_RECONCILIATION_SQL", "对账 SQL 制品缺失或格式错误", 503
        ) from None
    parts = re.split(r"^-- check: ([a-z_]+)\n", body, flags=re.MULTILINE)
    checks = list(zip(parts[1::2], parts[2::2]))
    if tuple(name for name, _ in checks) != CHECKS:
        raise BusinessError(
            "INVALID_RECONCILIATION_SQL", "对账检查项缺失或顺序错误", 503
        )
    return checks


def collect(cursor, sample_limit):
    results = []
    for name, statement in statements():
        cursor.execute(statement)
        columns = [c[0] for c in cursor.description]
        total, samples = 0, []
        while rows := cursor.fetchmany(1000):
            total += len(rows)
            samples.extend(
                dict(zip(columns, row))
                for row in rows[: max(0, sample_limit - len(samples))]
            )
        results.append({"name": name, "count": total, "samples": samples})
    return {"ok": not any(r["count"] for r in results), "checks": results}


def run(*, sample_limit=20):
    if not 1 <= sample_limit <= 100:
        raise BusinessError("INVALID_SAMPLE_LIMIT", "样例上限须为1至100", 422)
    if connection.in_atomic_block or not connection.get_autocommit():
        raise BusinessError(
            "RECONCILIATION_REQUIRES_TOP_LEVEL",
            "对账必须单独开启一致快照，不能嵌套业务事务",
            409,
        )
    require_runtime()
    with transaction.atomic():
        with connection.cursor() as cursor:
            cursor.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            cursor.execute(
                "SELECT current_setting('transaction_isolation'),current_setting('transaction_read_only'),now()"
            )
            isolation, read_only, observed = cursor.fetchone()
            result = collect(cursor, sample_limit)
            result.update(
                isolation=isolation,
                read_only=read_only == "on",
                observed_at=observed.isoformat(),
            )
    return result
