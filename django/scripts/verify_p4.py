#!/usr/bin/env python3
"""P4 concurrency checks using a real runtime LOGIN in a disposable PG16 DB."""

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4

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
    from django.db import connection, connections
    from apps.inventory import orders
    from apps.inventory.models import (
        InboundItem,
        OutboundItem,
        OutboundOrder,
        Inventory,
        StockLedger,
    )
    from apps.core.models import OperationRequest
    from shared.auth.backends import authorize_claims
    from shared.errors import BusinessError

    fixture = json.loads(os.environ["VERIFY_FIXTURE"])
    principals = [authorize_claims(claims) for claims in fixture["claims"]]
    p = principals[0]
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT rolsuper,rolcreatedb,rolcreaterole,rolbypassrls FROM pg_roles WHERE rolname=current_user"
        )
        assert cursor.fetchone() == (False, False, False, False)
    print(
        "IDENTITY: ordinary LOGIN; not owner/superuser, no CREATEDB/CREATEROLE/BYPASSRLS",
        flush=True,
    )
    item, location, supplier = (fixture[k] for k in ("item", "location", "supplier"))

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

    def create(kind, typ, rows, **header):
        return orders.create(
            p, kind, str(uuid4()), {"type": typ, "lines": rows, **header}
        )[0]["document"]

    def requisition(quantity):
        return create(
            "outbound",
            "requisition",
            [{"item_id": item, "quantity": str(quantity), "location_id": location}],
        )

    def execute(index, document, quantity):
        line = OutboundItem.objects.get(tenant_id=p.tenant_id, order_id=document["id"])
        orders.decide(
            principals[index],
            "outbound",
            document["id"],
            "execute",
            str(uuid4()),
            {"lines": [{"id": line.pk, "actual_qty": str(quantity)}]},
        )
        return "completed"

    def quantity():
        return Inventory.objects.get(
            tenant_id=p.tenant_id,
            item_id=item,
            location_id=location,
            quality="available",
        ).quantity

    create(
        "inbound",
        "opening",
        [
            {
                "item_id": item,
                "quantity": "50",
                "location_id": location,
                "quality": "available",
            }
        ],
    )
    key = str(uuid4())
    payload = {
        "type": "requisition",
        "lines": [{"item_id": item, "quantity": "10", "location_id": location}],
    }
    ids = parallel(
        lambda i: orders.create(p, "outbound", key, payload)[0]["document"]["id"]
    )
    assert isinstance(ids[0], int) and ids[0] == ids[1], ids
    assert OutboundOrder.objects.filter(tenant_id=p.tenant_id).count() == 1
    print(
        "CONCURRENCY 1: same key -> one submitted order and identical response",
        flush=True,
    )

    document = {"id": ids[0]}
    results = parallel(lambda i: execute(i, document, 10))
    assert sorted(results) == ["FROZEN_DOCUMENT", "completed"], results
    assert quantity() == 40
    assert (
        StockLedger.objects.filter(outbound_item__order_id=document["id"]).count() == 1
    )
    print(
        "CONCURRENCY 2: two executors -> one completion, one FROZEN_DOCUMENT, one ledger",
        flush=True,
    )

    requests = [requisition(30), requisition(30)]
    results = parallel(lambda i: execute(i, requests[i], 30))
    assert sorted(results) == ["INSUFFICIENT_STOCK", "completed"], results
    assert quantity() == 10
    print(
        "CONCURRENCY 3: competing orders -> no oversell, failed order remains pending",
        flush=True,
    )
    assert (
        OutboundOrder.objects.filter(
            tenant_id=p.tenant_id,
            pk__in=[row["id"] for row in requests],
            status="pending",
        ).count()
        == 1
    )

    source = OutboundItem.objects.get(tenant_id=p.tenant_id, order_id=document["id"])
    row = {
        "item_id": item,
        "quantity": "6",
        "location_id": location,
        "quality": "available",
        "source_outbound_item_id": source.pk,
    }

    def return_items(index):
        orders.create(
            principals[index],
            "inbound",
            str(uuid4()),
            {"type": "return", "lines": [row]},
        )
        return "returned"

    results = parallel(return_items)
    assert sorted(results) == ["RETURN_EXCEEDS_ISSUED", "returned"], results
    assert quantity() == 16
    print("CONCURRENCY 4: cumulative return quota -> exactly one accepted", flush=True)

    snapshot = orders.observe(p, item, location, "available")
    snapshot.pop("item_type")
    snapshot.update(quantity="1", direction="in", counted_qty="17")
    adjustments = [
        create(
            "adjustment", "count", [snapshot], reason="并发盘点", reason_category="gain"
        )
        for _ in range(2)
    ]

    def approve(index):
        orders.decide(
            principals[1],
            "adjustment",
            adjustments[index]["id"],
            "approve",
            str(uuid4()),
            {},
        )
        return "approved"

    results = parallel(approve)
    assert sorted(results) == ["STALE_SNAPSHOT", "approved"], results
    assert quantity() == 17
    print(
        "CONCURRENCY 5: identical observations -> one approval, one STALE_SNAPSHOT",
        flush=True,
    )

    purchase = create(
        "inbound",
        "purchase",
        [
            {
                "item_id": item,
                "quantity": "5",
                "location_id": location,
                "quality": "available",
            }
        ],
        supplier_id=supplier,
    )
    source = InboundItem.objects.get(tenant_id=p.tenant_id, order_id=purchase["id"])
    row = {
        "item_id": item,
        "quantity": "4",
        "location_id": location,
        "source_inbound_item_id": source.pk,
    }

    def purchase_return(index):
        orders.create(
            principals[index],
            "outbound",
            str(uuid4()),
            {"type": "purchase_return", "lines": [row]},
        )
        return "returned"

    results = parallel(purchase_return)
    assert sorted(results) == ["INVALID_PURCHASE_RETURN", "returned"], results
    assert quantity() == 18
    print(
        "CONCURRENCY 6: immutable purchase source quota -> exactly one accepted without UPDATE grant",
        flush=True,
    )
    assert not OperationRequest.objects.filter(response__isnull=True).exists()
    print(
        "P4: all six concurrency cases passed; no incomplete operation placeholders",
        flush=True,
    )
    connections.close_all()


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
    database, login = f"inventory_p4_{tag}", f"inventory_runtime_{tag}"
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
    print("P4 disposable database and login cleaned", flush=True)


if __name__ == "__main__":
    worker() if "--worker" in sys.argv else main()
