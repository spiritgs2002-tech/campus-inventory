#!/usr/bin/env python3
"""P8c disposable PostgreSQL integration gate; never use an existing business DB."""

import os
import json
import secrets
import tempfile
from pathlib import Path
import subprocess
import sys
from uuid import uuid4
import django
import psycopg
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus_inventory.settings")
django.setup()


def runtime_worker(claims):
    from concurrent.futures import ThreadPoolExecutor
    from django.db import connections
    from unittest.mock import patch
    from apps.core.models import AuthSession, User
    from apps.repair import services
    from apps.repair.models import RepairTicket
    from apps.reconcile.services import run
    from shared.auth.backends import authorize_claims
    from shared.db.runtime import require_runtime
    from shared.errors import BusinessError

    require_runtime()
    from apps.procurement.models import Requisition

    assert Requisition.objects.filter(
        order_no="RQ-P8B-LEGACY", reason="存量升级验收"
    ).exists()
    print("P8b stored requisition preserved after P8c upgrade", flush=True)
    admin = authorize_claims(claims)
    user = User.objects.get(tenant_id=admin.tenant_id, username="u")
    session = AuthSession.objects.get(tenant_id=admin.tenant_id, user_id=user.pk)
    user_claims = {
        "tid": user.tenant_id,
        "sub": str(user.pk),
        "fid": str(session.family_id),
    }
    teacher = authorize_claims(user_claims)
    payload = {
        "target_type": "location",
        "description": "合成场地报修",
        "priority": "high",
    }

    def race(callback):
        import threading

        barrier = threading.Barrier(2)

        def go(index):
            try:
                barrier.wait(timeout=10)
                return callback(index)
            except BusinessError as error:
                return error.code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(go, [0, 1]))

    key = uuid4().hex
    results = race(
        lambda i: services.repair_submit(authorize_claims(user_claims), payload, key)
    )
    assert all(isinstance(row, tuple) for row in results), results
    assert sum(row[1] for row in results) == 1, results
    pk = results[0][0]["document"]["id"]
    assert RepairTicket.objects.filter(tenant_id=admin.tenant_id).count() == 1
    key = uuid4().hex
    results = race(
        lambda i: services.repair_transition(
            authorize_claims(claims), pk, "dispatch", key, assigned_to=user.pk
        )
    )
    assert (
        all(isinstance(row, tuple) for row in results)
        and sum(row[1] for row in results) == 1
    ), results
    results = race(
        lambda i: services.repair_transition(
            authorize_claims(claims), pk, "start", uuid4().hex
        )
    )
    assert (
        sum(isinstance(row, tuple) for row in results) == 1
        and "FROZEN_DOCUMENT" in results
    ), results
    results = race(
        lambda i: services.repair_transition(
            authorize_claims(claims), pk, "done" if i else "cancel", uuid4().hex
        )
    )
    assert (
        sum(isinstance(row, tuple) for row in results) == 1
        and "FROZEN_DOCUMENT" in results
    ), results
    assert RepairTicket.objects.get(pk=pk).status in ["done", "cancelled"]
    print(
        "P8c ordinary LOGIN: concurrent create/dispatch replay, distinct-key start, done/cancel terminal race passed",
        flush=True,
    )
    result, _ = services.repair_submit(teacher, payload, uuid4().hex)
    pk = result["document"]["id"]
    with patch("apps.repair.services.core.audit", side_effect=RuntimeError("injected")):
        try:
            services.repair_transition(
                admin, pk, "dispatch", uuid4().hex, assigned_to=user.pk
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("Expected transition fault")
    assert RepairTicket.objects.get(pk=pk).status == "pending"
    from tests.test_repair import photo
    from apps.attachments import services as attachments

    attachment, _ = attachments.upload(teacher, uuid4().hex, photo())
    result, _ = services.repair_submit(
        teacher,
        {**payload, "photo_attachment_id": attachment["record"]["id"]},
        uuid4().hex,
    )
    obj, data = attachments.download(teacher, result["document"]["photo_attachment_id"])
    assert obj.content_type == "image/png" and data.startswith(b"\x89PNG")
    print(
        "P8c ordinary LOGIN: canonical attachment upload/repair association/download and transition rollback passed",
        flush=True,
    )
    result = run()
    assert result["ok"], result
    print(
        "P8c independent reconciliation: 13/13 clean; asset repair hook remains disabled",
        flush=True,
    )
    connections.close_all()


def prepare_p8b(env):
    seed = """
import django,json
django.setup()
from django.db import connection
from django.db.migrations.executor import MigrationExecutor
from tests.order_fixtures import OrderFixtureMixin as F
from apps.procurement.models import Requisition
MigrationExecutor(connection).migrate([('procurement','0001_initial'),('notifications','0001_initial'),('inventory','0001_initial'),('attachments','0001_initial')])
with connection.cursor() as c:
    c.execute('SELECT count(*) FROM django_migrations')
    assert c.fetchone()[0]==9
F.setUpTestData()
Requisition.objects.create(tenant_id=F.tenant.pk,order_no='RQ-P8B-LEGACY',applicant_id=F.user.pk,applicant_name=F.user.name,applicant_no=F.user.username,item_id=F.item.pk,qty='2',reason='存量升级验收')
print(json.dumps(F.claims[F.admin.pk]))
"""
    claims = json.loads(
        subprocess.check_output(
            [sys.executable, "-c", seed], cwd=ROOT, env=env, text=True
        )
    )
    fault = """
import django,hashlib,tempfile
django.setup()
from pathlib import Path
from unittest.mock import patch
from django.db import connection,DatabaseError
from shared.db.operations import RunSQLFile,SQL_DIR
original=(SQL_DIR/'p8c-repair.sql').read_bytes()
broken=original.rstrip()[:-len(b'COMMIT;')]+b'SELECT 1/0;\\nCOMMIT;'
with tempfile.TemporaryDirectory() as directory:
    Path(directory,'fault.sql').write_bytes(broken)
    with patch('shared.db.operations.SQL_DIR',Path(directory)):
        try:
            with connection.schema_editor(atomic=False) as editor:
                RunSQLFile('fault.sql',hashlib.sha256(broken).hexdigest()).database_forwards('repair',editor,None,None)
        except DatabaseError:pass
        else:raise AssertionError('Expected SQL fault')
with connection.cursor() as c:
    c.execute("SELECT to_regclass('public.repair_repairticket')")
    assert c.fetchone()[0] is None
    c.execute("SELECT reason FROM procurement_requisition WHERE order_no='RQ-P8B-LEGACY'")
    assert c.fetchone()[0]=='存量升级验收'
connection.close()
"""
    subprocess.run([sys.executable, "-c", fault], cwd=ROOT, env=env, check=True)
    print(
        "P8b baseline: nine migrations and stored requisition; P8c pre-COMMIT fault leaves table absent and legacy data intact",
        flush=True,
    )
    return claims


def main():
    from django.conf import settings

    cfg = settings.DATABASES["default"]
    params = dict(
        host=cfg["HOST"],
        port=cfg["PORT"],
        user=cfg["USER"],
        password=cfg["PASSWORD"],
        dbname=cfg["NAME"],
        autocommit=True,
    )
    database = "inventory_p8c_" + uuid4().hex[:12]
    with (
        tempfile.TemporaryDirectory(prefix="p8c-photos-", dir="/tmp") as photos,
        psycopg.connect(**params) as admin,
    ):
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            env = {
                **os.environ,
                "DB_NAME": database,
                "ATTACHMENT_STORAGE_BACKEND": "filesystem",
                "ATTACHMENT_STORAGE_ROOT": photos,
                "ATTACHMENT_RETENTION_DAYS": "30",
            }
            claims = prepare_p8b(env)
            for args in [
                ("apply_schema",),
                ("apply_schema",),
                (
                    "test",
                    "tests.test_repair",
                    "--no-input",
                    "--verbosity",
                    "2",
                ),
                ("verify_schema",),
                ("verify_runtime",),
            ]:
                subprocess.run(
                    [sys.executable, "manage.py", *args], cwd=ROOT, env=env, check=True
                )
            login = "p8c_run_" + uuid4().hex[:12]
            password = secrets.token_urlsafe(32)
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE PASSWORD {}"
                ).format(sql.Identifier(login), sql.Literal(password))
            )
            try:
                admin.execute(
                    sql.SQL("GRANT inventory_runtime TO {}").format(
                        sql.Identifier(login)
                    )
                )
                runtime = {
                    **env,
                    "DB_USER": login,
                    "DB_PASSWORD": password,
                    "P8C_CLAIMS": json.dumps(claims),
                }
                subprocess.run(
                    [sys.executable, __file__, "--worker"],
                    cwd=ROOT,
                    env=runtime,
                    check=True,
                )
            finally:
                # Close worker sessions before dropping the temporary login.
                admin.execute(
                    "SELECT pg_terminate_backend(pid) FROM pg_stat_activity WHERE usename=%s",
                    [login],
                )
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(login)))
        finally:
            admin.execute(
                sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                    sql.Identifier(database)
                )
            )
    print(
        "P8c SQL-first upgrade, repair workflow, attachment, concurrency and rollback integration passed; disposable database cleaned"
    )


if __name__ == "__main__":
    runtime_worker(
        json.loads(os.environ["P8C_CLAIMS"])
    ) if "--worker" in sys.argv else main()
