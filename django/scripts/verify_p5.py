#!/usr/bin/env python3
"""P5 independent runtime LOGIN: mixed data, concurrency and read-only snapshots."""

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4
from decimal import Decimal

import django
import psycopg
from django.conf import settings
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus_inventory.settings")
django.setup()


def worker():
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch
    from django.db import connection, connections, transaction, DatabaseError
    from django.utils import timezone
    from apps.inventory import assets, orders
    from apps.reconcile import services as reconciliation
    from apps.inventory.models import (
        AssetCard,
        InboundItem,
        OutboundItem,
        OutboundAsset,
    )
    from apps.core.models import OperationRequest, Tenant
    from shared.auth.backends import authorize_claims
    from shared.errors import BusinessError

    f = json.loads(os.environ["VERIFY_FIXTURE"])
    principals = [authorize_claims(c) for c in f["claims"]]
    p = principals[0]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        assert cursor.fetchone() == (False, False, False, False)
    print(
        "IDENTITY: actual ordinary LOGIN, no owner/superuser or bypass privileges",
        flush=True,
    )
    cons, fixed, location, supplier = (
        f[k] for k in ("item", "fixed", "location", "supplier")
    )

    def row(item, quantity=1, **extra):
        return {
            "item_id": item,
            "quantity": str(quantity),
            "location_id": location,
            **extra,
        }

    def create(kind, typ, lines, actor=0, **header):
        return orders.create(
            principals[actor],
            kind,
            str(uuid4()),
            {"type": typ, "lines": lines, **header},
        )[0]["document"]

    def execute(document, quantity, cards=None, actor=0, shortfall=""):
        line = OutboundItem.objects.get(tenant_id=p.tenant_id, order_id=document["id"])
        orders.decide(
            principals[actor],
            "outbound",
            document["id"],
            "execute",
            str(uuid4()),
            {
                "lines": [
                    {
                        "id": line.pk,
                        "actual_qty": str(quantity),
                        "asset_ids": cards or [],
                        "shortfall_reason": shortfall,
                    }
                ]
            },
        )
        return "completed"

    def approve(kind, doc):
        orders.decide(principals[1], kind, doc["id"], "approve", str(uuid4()), {})

    def balanced(label):
        result = reconciliation.run()
        assert len(result["checks"]) == 13 and result["ok"], result
        assert result["read_only"] and result["isolation"] == "repeatable read"
        print(
            f"RECONCILE {label}: 13/13, zero issues; repeatable read / read only",
            flush=True,
        )

    purchase = create(
        "inbound",
        "purchase",
        [row(cons, 100, quality="available")],
        supplier_id=supplier,
    )
    create("inbound", "opening", [row(cons, 10, quality="damaged")])
    fixed_purchase = create(
        "inbound",
        "purchase",
        [row(fixed, 6, quality="available", unit_price="1000.00")],
        supplier_id=supplier,
    )
    cards = list(AssetCard.objects.filter(tenant_id=p.tenant_id).order_by("id"))
    balanced("purchases and opening")

    issue = create("outbound", "requisition", [row(cons, 20)])
    execute(issue, 12, shortfall="分批发放")
    source = OutboundItem.objects.get(order_id=issue["id"], tenant_id=p.tenant_id)
    fixed_issue = create("outbound", "requisition", [row(fixed, 2)])
    execute(fixed_issue, 2, [cards[0].pk, cards[1].pk])
    create(
        "inbound",
        "return",
        [row(cons, 2, quality="available", source_outbound_item_id=source.pk)],
    )
    relation = OutboundAsset.objects.get(
        tenant_id=p.tenant_id, asset_card_id=cards[0].pk, closed_at__isnull=True
    )
    create(
        "inbound",
        "return",
        [row(fixed, quality="damaged", source_outbound_asset_id=relation.pk)],
    )
    assets.repair(p, cards[0].pk, "repair_finish", str(uuid4()), {"reason": "返修完成"})
    relation = OutboundAsset.objects.get(
        tenant_id=p.tenant_id, asset_card_id=cards[1].pk, closed_at__isnull=True
    )
    create(
        "outbound",
        "transfer",
        [
            row(
                fixed,
                recipient_id=principals[1].user_id,
                source_outbound_asset_id=relation.pk,
            )
        ],
    )
    create(
        "outbound",
        "transfer",
        [
            row(
                cons,
                3,
                recipient_id=principals[1].user_id,
                source_outbound_item_id=source.pk,
            )
        ],
    )
    balanced("partial issuance, returns and paired transfers")

    purchase_line = InboundItem.objects.get(
        tenant_id=p.tenant_id, order_id=purchase["id"]
    )
    fixed_line = InboundItem.objects.get(
        tenant_id=p.tenant_id, order_id=fixed_purchase["id"]
    )
    create(
        "outbound",
        "purchase_return",
        [row(cons, 5, source_inbound_item_id=purchase_line.pk)],
    )
    create(
        "outbound",
        "purchase_return",
        [row(fixed, source_inbound_item_id=fixed_line.pk, asset_ids=[cards[2].pk])],
    )
    scrap = create(
        "scrap",
        "scrap",
        [{"item_id": fixed, "asset_card_id": cards[3].pk}],
        reason="无法修复",
    )
    approve("scrap", scrap)
    loss = create(
        "scrap",
        "loss",
        [{"item_id": fixed, "asset_card_id": cards[1].pk}],
        reason="遗失",
    )
    approve("scrap", loss)
    from apps.inventory.models import ScrapItem

    loss_line = ScrapItem.objects.get(tenant_id=p.tenant_id, scrap_id=loss["id"])
    found = create(
        "scrap",
        "recovery",
        [
            {
                "item_id": fixed,
                "asset_card_id": cards[1].pk,
                "source_loss_item_id": loss_line.pk,
                "recovery_location_id": location,
                "recovery_quality": "available",
            }
        ],
        reason="找到",
    )
    approve("scrap", found)
    damage = create(
        "scrap", "stock_damage", [row(cons, 4, quality="available")], reason="耗材破损"
    )
    approve("scrap", damage)
    balanced("purchase returns, loss, recovery and scrap")

    observation = orders.observe(p, cons, location, "available")
    observation.pop("item_type")
    count = create(
        "adjustment",
        "count",
        [
            {
                **observation,
                "quantity": "2",
                "direction": "in",
                "counted_qty": str(Decimal(observation["book_qty"]) + 2),
            }
        ],
        reason="盘点差额",
        reason_category="gain",
    )
    approve("adjustment", count)
    observation = orders.observe(p, cons, location, "available")
    observation.pop("item_type")
    target = orders.observe(p, cons, location, "damaged")
    conversion = create(
        "adjustment",
        "quality_transfer",
        [
            {
                **observation,
                "quantity": "3",
                "direction": "out",
                "to_quality": "damaged",
                "target_inventory_version": target["inventory_version"],
            }
        ],
        reason="质量变化",
        reason_category="quality_change",
    )
    approve("adjustment", conversion)
    gain = create(
        "adjustment",
        "count",
        [
            row(
                fixed,
                quality="available",
                direction="in",
                observed_at=timezone.now().isoformat(),
                original_value="200.00",
            )
        ],
        reason="资产盘盈",
        reason_category="gain",
    )
    approve("adjustment", gain)
    assets.repair(p, cards[4].pk, "repair_start", str(uuid4()), {"reason": "维修"})
    assets.repair(p, cards[4].pk, "repair_finish", str(uuid4()), {"reason": "维修完成"})
    balanced("count, quality conversion, asset gain and repairs")

    for statement in [
        "UPDATE stock_ledger SET quantity=1",
        "DELETE FROM asset_events",
        "TRUNCATE asset_cards",
        "ALTER TABLE asset_cards DISABLE TRIGGER USER",
        "UPDATE asset_cards SET asset_no='forbidden'",
    ]:
        try:
            with transaction.atomic(), connection.cursor() as cursor:
                cursor.execute(statement)
        except DatabaseError:
            pass
        else:
            raise AssertionError(f"Unexpected privilege: {statement}")
    print("IMMUTABILITY: five real LOGIN forbidden operations rejected", flush=True)

    def parallel(function):
        barrier = threading.Barrier(2)

        def run(index):
            try:
                barrier.wait(timeout=10)
                return function(index)
            except BusinessError as error:
                return error.code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(run, range(2)))

    start = Tenant.objects.get(pk=p.tenant_id).next_asset_number
    parallel(
        lambda index: create(
            "inbound", "opening", [row(fixed, 3, quality="available")], actor=index
        )
    )
    newest = list(AssetCard.objects.filter(tenant_id=p.tenant_id).order_by("-id")[:6])
    assert len({c.asset_no for c in newest}) == 6
    assert {int(c.asset_no.rsplit("-", 1)[1]) for c in newest} == set(
        range(start, start + 6)
    )
    assert Tenant.objects.get(pk=p.tenant_id).next_asset_number == start + 6
    print(
        "CONCURRENCY 1: two batches allocate six unique consecutive asset numbers",
        flush=True,
    )

    requests = [
        create("outbound", "requisition", [row(fixed)], actor=i) for i in range(2)
    ]
    results = parallel(lambda i: execute(requests[i], 1, [cards[4].pk], actor=i))
    assert sorted(results) == ["ASSET_UNAVAILABLE", "completed"], results
    assert (
        OutboundAsset.objects.filter(
            tenant_id=p.tenant_id, asset_card_id=cards[4].pk, closed_at__isnull=True
        ).count()
        == 1
    )
    print(
        "CONCURRENCY 2: competing issuances -> exactly one open responsibility",
        flush=True,
    )

    request = create("outbound", "requisition", [row(fixed)])

    def repair_or_issue(index):
        if index:
            return execute(request, 1, [cards[5].pk], actor=1)
        assets.repair(
            p, cards[5].pk, "repair_start", str(uuid4()), {"reason": "并发维修"}
        )
        return "repair"

    results = parallel(repair_or_issue)
    assert sorted(results) in [
        ["ASSET_UNAVAILABLE", "repair"],
        ["INVALID_ASSET_STATE", "completed"],
    ], results
    print(
        "CONCURRENCY 3: repair versus issuance -> one valid state transition",
        flush=True,
    )

    ids = [newest[0].pk, newest[1].pk]
    requests = [
        create("outbound", "requisition", [row(fixed, 2)], actor=i) for i in range(2)
    ]
    results = parallel(
        lambda i: execute(requests[i], 2, ids if i == 0 else ids[::-1], actor=i)
    )
    assert sorted(results) == ["ASSET_UNAVAILABLE", "completed"], results
    print(
        "CONCURRENCY 4: reversed asset selection -> no deadlock or duplicate issue",
        flush=True,
    )
    balanced("after concurrent allocations and transitions")

    snapshot_started, writer_done = threading.Event(), threading.Event()
    original_collect = reconciliation.collect
    repair_card = AssetCard.objects.filter(
        tenant_id=p.tenant_id, status="in_stock"
    ).first()

    def paused_collect(cursor, limit):
        cursor.execute("SELECT count(*) FROM asset_events")
        before = cursor.fetchone()[0]
        snapshot_started.set()
        assert writer_done.wait(timeout=10)
        cursor.execute("SELECT count(*) FROM asset_events")
        assert cursor.fetchone()[0] == before, (
            "snapshot changed after a concurrent commit"
        )
        return original_collect(cursor, limit)

    def snapshot():
        try:
            with patch.object(reconciliation, "collect", side_effect=paused_collect):
                result = reconciliation.run()
                assert result["ok"], result
        finally:
            connections.close_all()

    def writer():
        try:
            assert snapshot_started.wait(timeout=10)
            assets.repair(
                p,
                repair_card.pk,
                "repair_start",
                str(uuid4()),
                {"reason": "一致快照并发验证"},
            )
        finally:
            writer_done.set()
            connections.close_all()

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(snapshot), pool.submit(writer)]
        for future in futures:
            future.result()
    print(
        "CONCURRENCY 5: reconciliation retains one snapshot across a concurrent commit",
        flush=True,
    )
    balanced("fresh snapshot after concurrent writer")
    assert not OperationRequest.objects.filter(response__isnull=True).exists()

    good = subprocess.run(
        [sys.executable, "manage.py", "reconcile", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert good.returncode == 0, good.stderr
    assert json.loads(good.stdout)["ok"]
    print("CLI_JSON: " + good.stdout.strip(), flush=True)
    print("CLI: real runtime reconcile --json exits 0 with 13 clean checks", flush=True)
    with connection.cursor() as cursor:
        cursor.execute(
            (settings.SQL_ROOT / "reconcile-v2.sql").read_text(), params=None
        )
        result_sets = 0
        while True:
            if cursor.description is not None:
                assert cursor.fetchall() == []
                result_sets += 1
            if not cursor.nextset():
                break
    assert result_sets == 13, result_sets
    print(
        "SQL FILE: complete BEGIN/13 queries/COMMIT script -> 13 empty result sets",
        flush=True,
    )
    dirty_card = AssetCard.objects.filter(
        tenant_id=p.tenant_id, status="in_stock"
    ).first()
    AssetCard.objects.filter(tenant_id=p.tenant_id, pk=dirty_card.pk).update(
        status="repair"
    )
    bad = subprocess.run(
        [sys.executable, "manage.py", "reconcile", "--json"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )
    assert bad.returncode != 0 and not json.loads(bad.stdout)["ok"], (
        bad.stdout,
        bad.stderr,
    )
    assert AssetCard.objects.get(pk=dirty_card.pk).status == "repair"
    print(
        "M3/CLI: injected state without event detected; nonzero exit, no automatic repair",
        flush=True,
    )
    connections.close_all()
    print("P5 runtime hard-gate self-check passed", flush=True)


def main():
    cfg = settings.DATABASES["default"]
    params = dict(
        host=cfg["HOST"],
        port=cfg["PORT"],
        user=cfg["USER"],
        password=cfg["PASSWORD"],
        dbname=cfg["NAME"],
    )
    tag = uuid4().hex[:12]
    database, login = f"inventory_p5_{tag}", f"inventory_runtime_{tag}"
    password = secrets.token_urlsafe(32)
    created_db = created_role = False
    with psycopg.connect(**params, autocommit=True) as admin:
        try:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
            )
            created_db = True
            env = {**os.environ, "DB_NAME": database}
            subprocess.run(
                [sys.executable, "manage.py", "apply_schema"],
                cwd=ROOT,
                env=env,
                check=True,
            )
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(login), sql.Literal(password))
            )
            created_role = True
            admin.execute(
                sql.SQL("GRANT inventory_runtime TO {}").format(sql.Identifier(login))
            )
            with psycopg.connect(
                **{**params, "dbname": database}, autocommit=True
            ) as db:
                tenant = db.execute(
                    "INSERT INTO tenants(code,name) VALUES ('PARALLEL_P4','单据并发验收') RETURNING id"
                ).fetchone()[0]
                claims = []
                for name in ("admin1", "admin2"):
                    user = db.execute(
                        "INSERT INTO users(tenant_id,username,name,role,password_hash,must_change_password) VALUES (%s,%s,%s,'admin','fixture-unused',false) RETURNING id",
                        [tenant, name, name],
                    ).fetchone()[0]
                    family = str(uuid4())
                    db.execute(
                        "INSERT INTO auth_sessions(tenant_id,user_id,family_id,refresh_digest,expires_at) VALUES (%s,%s,%s,%s,now()+interval '1 day')",
                        [tenant, user, family, str(uuid4())],
                    )
                    claims.append({"tid": tenant, "sub": str(user), "fid": family})
                category = db.execute(
                    "INSERT INTO categories(tenant_id,code,name,type) VALUES (%s,'CONS','耗材','consumable') RETURNING id",
                    [tenant],
                ).fetchone()[0]
                item = db.execute(
                    "INSERT INTO items(tenant_id,code,name,category_id,type,unit) VALUES (%s,'PAPER','纸',%s,'consumable','包') RETURNING id",
                    [tenant, category],
                ).fetchone()[0]
                location = db.execute(
                    "INSERT INTO locations(tenant_id,name) VALUES (%s,'库位') RETURNING id",
                    [tenant],
                ).fetchone()[0]
                supplier = db.execute(
                    "INSERT INTO suppliers(tenant_id,name) VALUES (%s,'供应商') RETURNING id",
                    [tenant],
                ).fetchone()[0]
                category_fixed = db.execute(
                    "INSERT INTO categories(tenant_id,code,name,type) VALUES (%s,'FIXED','固定资产','fixed') RETURNING id",
                    [tenant],
                ).fetchone()[0]
                fixed = db.execute(
                    "INSERT INTO items(tenant_id,code,name,category_id,type,unit) VALUES (%s,'PC','电脑',%s,'fixed','台') RETURNING id",
                    [tenant, category_fixed],
                ).fetchone()[0]
            subprocess.run(
                [sys.executable, __file__, "--worker"],
                cwd=ROOT,
                check=True,
                env={
                    **env,
                    "DB_USER": login,
                    "DB_PASSWORD": password,
                    "VERIFY_FIXTURE": json.dumps(
                        {
                            "claims": claims,
                            "item": item,
                            "fixed": fixed,
                            "location": location,
                            "supplier": supplier,
                        }
                    ),
                },
            )
        finally:
            if created_db:
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(database)
                    )
                )
            if created_role:
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login)))
    print("P5 disposable database and login cleaned", flush=True)


if __name__ == "__main__":
    worker() if "--worker" in sys.argv else main()
