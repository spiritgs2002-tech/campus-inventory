#!/usr/bin/env python3
"""P7->P8a upgrade/rollback and ordinary LOGIN self-pickup concurrency."""

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import threading
from unittest.mock import patch
from uuid import uuid4
import django
import psycopg
from psycopg import sql
from django.conf import settings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus_inventory.settings")
django.setup()


def artifact_directory():
    # Use the process archive; allow a separate directory for each acceptance run.
    directory = Path(
        os.environ.get(
            "P8A_ARTIFACT_DIR", ROOT.parent / "docs/过程记录/P8a/复验制品"
        )
    ).resolve()
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def worker():
    from django.db import connections
    from django.test import Client
    from apps.catalog.models import Item
    from apps.core.models import OperationRequest, User
    from apps.inventory import orders, pickup, posting, qr
    from apps.inventory.models import (
        AssetCard,
        AssetEvent,
        Inventory,
        OutboundAsset,
        OutboundOrder,
    )
    from apps.reconcile.services import run
    from shared.auth.backends import authorize_claims
    from shared.auth.tokens import access_token
    from shared.db.runtime import require_runtime
    from shared.errors import BusinessError

    f = json.loads(os.environ["VERIFY_FIXTURE"])
    p, user, foreign = [authorize_claims(c) for c in f["claims"]]
    require_runtime()
    item = Item.objects.get(tenant_id=p.tenant_id, pk=f["item"])
    assert item.qr_token is None
    qr.generate_item_tokens(p, "backfill")
    assert qr.generate_item_tokens(p, "backfill")[1]
    item.refresh_from_db()
    token = item.qr_token

    def receive(quantity=1, fixed=False):
        return orders.create(
            p,
            "inbound",
            uuid4().hex,
            {
                "type": "opening",
                "lines": [
                    {
                        "item_id": f["fixed"] if fixed else item.pk,
                        "quantity": str(quantity),
                        "location_id": f["location"],
                        "quality": "available",
                        "unit_price": "12",
                    }
                ],
            },
        )

    def stock():
        return Inventory.objects.get(
            tenant_id=p.tenant_id,
            item_id=item.pk,
            location_id=f["location"],
            quality="available",
        ).quantity

    def race(function):
        barrier = threading.Barrier(2)

        def call(index):
            try:
                barrier.wait(timeout=10)
                return function(index)
            except BusinessError as error:
                return error.code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(call, range(2)))

    receive(3)
    key = uuid4().hex
    results = race(lambda index: pickup.submit(user, token, {"quantity": "1"}, key))
    assert all(isinstance(result, tuple) for result in results), results
    assert sorted(result[1] for result in results) == [False, True]
    assert results[0][0]["document"]["id"] == results[1][0]["document"]["id"]
    assert stock() == 2
    print(
        "CONCURRENCY 1: identical key -> one document, one stock movement, same response identity",
        flush=True,
    )
    results = race(
        lambda index: pickup.submit(
            [p, user][index], token, {"quantity": "2"}, uuid4().hex
        )
    )
    assert (
        sum(isinstance(r, tuple) for r in results) == 1
        and "INSUFFICIENT_STOCK" in results
    ), results
    assert stock() == 0
    print(
        "CONCURRENCY 2: consumable competition -> one completion, one shortage, no oversell",
        flush=True,
    )
    receive(fixed=True)
    card = AssetCard.objects.get(tenant_id=p.tenant_id)
    result = race(
        lambda index: pickup.submit([p, user][index], card.qr_token, {}, uuid4().hex)
    )
    assert (
        sum(isinstance(r, tuple) for r in result) == 1 and "ASSET_UNAVAILABLE" in result
    ), result
    card.refresh_from_db()
    assert card.status == "in_use"
    assert (
        OutboundAsset.objects.filter(
            asset_card_id=card.pk, closed_at__isnull=True
        ).count()
        == 1
    )
    assert AssetEvent.objects.filter(asset_card_id=card.pk, action="issue").count() == 1
    print(
        "CONCURRENCY 3: same asset -> exactly one open relation and issue event",
        flush=True,
    )
    receive(2)
    key = uuid4().hex
    trace = {"quantity": "1", "name": "合成免密教师", "employee_no": "SCAN-1"}
    results = race(lambda index: pickup.submit(None, token, trace, key))
    assert all(isinstance(result, tuple) for result in results), results
    assert sorted(result[1] for result in results) == [False, True]
    assert User.objects.filter(tenant_id=p.tenant_id, username="SCAN-1").count() == 1
    assert stock() == 1
    print(
        "CONCURRENCY 4: anonymous same employee/key -> one trace user and one document",
        flush=True,
    )
    results = race(
        lambda index: pickup.submit(
            None,
            token,
            {
                "quantity": "1",
                "name": f"合成教师{index}",
                "employee_no": f"SCAN-{index + 2}",
            },
            uuid4().hex,
        )
    )
    assert (
        sum(isinstance(r, tuple) for r in results) == 1
        and "INSUFFICIENT_STOCK" in results
    ), results
    assert (
        User.objects.filter(
            tenant_id=p.tenant_id, username__in=["SCAN-2", "SCAN-3"]
        ).count()
        == 1
    )
    assert stock() == 0
    print(
        "CONCURRENCY 5: anonymous shortage rolls back failed trace user and operation placeholder",
        flush=True,
    )
    for principal, data, expected in [
        (
            None,
            {"quantity": "1", "name": p.name, "employee_no": p.username},
            "LOGIN_REQUIRED_FOR_EMPLOYEE",
        ),
        (foreign, {"quantity": "1"}, "NOT_FOUND"),
        (user, {"quantity": "1", "recipient_id": p.user_id}, "INVALID_FIELDS"),
    ]:
        try:
            pickup.submit(principal, token, data, uuid4().hex)
        except BusinessError as error:
            assert error.code == expected, (error.code, expected)
        else:
            raise AssertionError("Unsafe identity/scope accepted")
    print(
        "AUTHORIZATION: formal identity impersonation, cross-school token and recipient override rejected",
        flush=True,
    )
    receive()
    before = OutboundOrder.objects.count()
    original = posting.issue

    def fail_after_issue(*args):
        original(*args)
        raise BusinessError("INJECTED", "post-issue failure", 409)

    with patch("apps.inventory.pickup.posting.issue", side_effect=fail_after_issue):
        try:
            pickup.submit(
                None,
                token,
                {"quantity": "1", "name": "应回滚", "employee_no": "ROLLBACK"},
                uuid4().hex,
            )
        except BusinessError as error:
            assert error.code == "INJECTED"
        else:
            raise AssertionError("Injected failure committed")
    assert stock() == 1 and OutboundOrder.objects.count() == before
    assert not User.objects.filter(username="ROLLBACK").exists()
    assert not OperationRequest.objects.filter(response__isnull=True).exists()
    print(
        "ATOMICITY: post-issue failure rolls back stock, document, audit, operation and identity",
        flush=True,
    )
    browser = Client(enforce_csrf_checks=True)
    page = browser.get(f"/s/pickup/{token}/")
    assert page.status_code == 200
    csrf = page.cookies["csrftoken"].value
    key = uuid4().hex
    fields = {
        "quantity": "1",
        "name": "表单登记",
        "employee_no": "HTML",
        "idempotency_key": key,
        "csrfmiddlewaretoken": csrf,
    }
    success = browser.post(f"/s/pickup/{token}/", fields)
    replay = browser.post(f"/s/pickup/{token}/", fields)
    assert success.status_code == 200 and "领用成功" in success.content.decode()
    assert "没有重复出库" in replay.content.decode() and stock() == 0
    png = browser.get(
        f"/api/v1/qr/{token}/png", HTTP_AUTHORIZATION="Bearer " + access_token(p)
    )
    assert png.status_code == 200 and png.content.startswith(b"\x89PNG\r\n\x1a\n")
    artifacts = artifact_directory()
    (artifacts / "P8a示例二维码.png").write_bytes(png.content)
    mobile = browser.get(
        "/s/inventory/", HTTP_AUTHORIZATION="Bearer " + access_token(user)
    )
    assert mobile.status_code == 200 and item.name in mobile.content.decode()
    print(
        "HTTP: enforced-CSRF no-JS form/replay, QR PNG and mobile inventory passed",
        flush=True,
    )
    result = run()
    assert result["ok"], result
    (artifacts / "P8a对账结果.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print(
        "RECONCILE: 13/13 clean after authenticated/anonymous pickup and competition",
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
    database, login, password = (
        f"inventory_p8a_{tag}",
        f"inventory_run_{tag}",
        secrets.token_urlsafe(32),
    )
    created_db = created_role = False
    with psycopg.connect(**params, autocommit=True) as admin:
        try:
            admin.execute(
                sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database))
            )
            created_db = True
            env = {**os.environ, "DB_NAME": database}
            legacy = """
import django
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
MigrationExecutor(connection).migrate([('inventory','0001_initial'),('attachments','0001_initial'),('core','0003_runtime_access_guards')])
with connection.cursor() as c:
    c.execute('SELECT count(*) FROM django_migrations')
    assert c.fetchone()[0]==6
connection.close()
print('P7 baseline: six published migrations applied')
"""
            subprocess.run(
                [sys.executable, "-c", legacy], cwd=ROOT, env=env, check=True
            )
            with psycopg.connect(
                **{**params, "dbname": database}, autocommit=True
            ) as db:
                tenants = [
                    db.execute(
                        "INSERT INTO tenants(code,name) VALUES(%s,%s) RETURNING id",
                        [code, code],
                    ).fetchone()[0]
                    for code in ["P8_A", "P8_B"]
                ]
                claims = []
                for index, role in enumerate(["admin", "user", "admin"]):
                    tid = tenants[1] if index == 2 else tenants[0]
                    uid = db.execute(
                        "INSERT INTO users(tenant_id,username,name,password_hash,role,must_change_password) VALUES(%s,%s,%s,'fixture-unused',%s,false) RETURNING id",
                        [tid, f"u{index}", f"合成用户{index}", role],
                    ).fetchone()[0]
                    family = str(uuid4())
                    db.execute(
                        "INSERT INTO auth_sessions(tenant_id,user_id,family_id,refresh_digest,expires_at) VALUES(%s,%s,%s,%s,now()+interval '1 day')",
                        [tid, uid, family, uuid4().hex],
                    )
                    claims.append({"tid": tid, "sub": str(uid), "fid": family})
                ids = []
                for typ, code in [("consumable", "PAPER"), ("fixed", "PC")]:
                    category = db.execute(
                        "INSERT INTO categories(tenant_id,code,name,type) VALUES(%s,%s,%s,%s) RETURNING id",
                        [tenants[0], code, code, typ],
                    ).fetchone()[0]
                    ids.append(
                        db.execute(
                            "INSERT INTO items(tenant_id,code,name,type,category_id,unit) VALUES(%s,%s,%s,%s,%s,'件') RETURNING id",
                            [tenants[0], code, code, typ, category],
                        ).fetchone()[0]
                    )
                location = db.execute(
                    "INSERT INTO locations(tenant_id,name) VALUES(%s,'合成总库') RETURNING id",
                    [tenants[0]],
                ).fetchone()[0]
            fault = """
import hashlib,tempfile
from pathlib import Path
from unittest.mock import patch
import django
django.setup()
from django.db import connection,DatabaseError
from shared.db.operations import RunSQLFile,SQL_DIR
original=(SQL_DIR/'p8a-item-qr.sql').read_bytes()
broken=original.rstrip()[:-len(b'COMMIT;')]+b'SELECT 1/0;\\nCOMMIT;'
with tempfile.TemporaryDirectory() as directory:
    Path(directory,'fault.sql').write_bytes(broken)
    with patch('shared.db.operations.SQL_DIR',Path(directory)):
        try:
            with connection.schema_editor(atomic=False) as editor:
                RunSQLFile('fault.sql',hashlib.sha256(broken).hexdigest()).database_forwards('catalog',editor,None,None)
        except DatabaseError: pass
        else: raise AssertionError('Missing injected migration failure')
with connection.cursor() as c:
    c.execute("SELECT count(*) FROM information_schema.columns WHERE table_name='items' AND column_name='qr_token'")
    assert c.fetchone()[0]==0
connection.close()
print('UPGRADE FAULT: pre-COMMIT delta failure leaves QR column absent and P7 data intact')
"""
            subprocess.run([sys.executable, "-c", fault], cwd=ROOT, env=env, check=True)
            for _ in range(2):
                subprocess.run(
                    [sys.executable, "manage.py", "apply_schema"],
                    cwd=ROOT,
                    env=env,
                    check=True,
                )
            with psycopg.connect(
                **{**params, "dbname": database}, autocommit=True
            ) as db:
                assert (
                    db.execute("SELECT count(*) FROM django_migrations").fetchone()[0]
                    == 10
                )
                assert db.execute(
                    "SELECT name,qr_token FROM items WHERE id=%s", [ids[0]]
                ).fetchone() == ("PAPER", None)
            print(
                "UPGRADE: P7 data preserved; QR expansion applied once; repeated migration no-op",
                flush=True,
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
            runtime = {
                **env,
                "DB_USER": login,
                "DB_PASSWORD": password,
                "VERIFY_FIXTURE": json.dumps(
                    {
                        "claims": claims,
                        "item": ids[0],
                        "fixed": ids[1],
                        "location": location,
                    }
                ),
            }
            subprocess.run(
                [sys.executable, __file__, "--worker"],
                cwd=ROOT,
                env=runtime,
                check=True,
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
    print(
        "P8a upgrade/runtime/concurrency/HTTP verification passed; temporary resources cleaned",
        flush=True,
    )


if __name__ == "__main__":
    worker() if "--worker" in sys.argv else main()
