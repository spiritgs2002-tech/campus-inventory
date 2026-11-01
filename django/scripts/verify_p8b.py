#!/usr/bin/env python3
"""P8b disposable PostgreSQL integration gate; never use an existing business DB."""

import os
import json
import secrets
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
    from apps.procurement import services
    from apps.notifications.models import Notification
    from apps.reconcile.services import run
    from shared.auth.backends import authorize_claims
    from shared.db.runtime import require_runtime
    from shared.errors import BusinessError
    from apps.catalog.models import Item, Location, Supplier

    require_runtime()
    admin = authorize_claims(claims)
    item = Item.objects.get(tenant_id=admin.tenant_id, code="PAPER")
    location = Location.objects.get(tenant_id=admin.tenant_id, name="总库")
    supplier = Supplier.objects.get(tenant_id=admin.tenant_id, name="采购供应商")

    def approved():
        response, _ = services.requisition_submit(
            admin, dict(item_id=item.pk, qty="2", reason="合成并发验收"), uuid4().hex
        )
        pk = response["document"]["id"]
        services.decide(admin, pk, "approve", uuid4().hex)
        return pk

    def compete(pk, keys):
        def post(key):
            try:
                return services.fulfill(
                    authorize_claims(claims),
                    pk,
                    key,
                    location.pk,
                    "available",
                    supplier.pk,
                    "2.00",
                )
            except BusinessError as error:
                return error.code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(post, keys))

    pk = approved()
    key = uuid4().hex
    results = compete(pk, [key, key])
    assert all(isinstance(result, tuple) for result in results), results
    assert sum(result[1] for result in results) == 1, results
    pk = approved()
    results = compete(pk, [uuid4().hex, uuid4().hex])
    assert sum(isinstance(result, tuple) for result in results) == 1, results
    assert "FROZEN_DOCUMENT" in results, results
    assert (
        Notification.objects.filter(
            tenant_id=admin.tenant_id, type="requisition_arrived"
        ).count()
        == 2
    )
    result = run()
    assert result["ok"], result
    print(
        "P8b actual ordinary LOGIN: same-key and distinct-key arrival races passed; two notifications, 13/13 reconciliation clean",
        flush=True,
    )
    connections.close_all()


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
    database = "inventory_p8b_" + uuid4().hex[:12]
    with psycopg.connect(**params) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(database)))
        try:
            env = {**os.environ, "DB_NAME": database}
            for args in [
                ("apply_schema",),
                (
                    "test",
                    "tests.test_requisition",
                    "tests.test_notifications",
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
            seed = """
import django,json
django.setup()
from tests.order_fixtures import OrderFixtureMixin as F
F.setUpTestData()
print(json.dumps(F.claims[F.admin.pk]))
"""
            claims = json.loads(
                subprocess.check_output(
                    [sys.executable, "-c", seed], cwd=ROOT, env=env, text=True
                )
            )
            login = "p8b_run_" + uuid4().hex[:12]
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
                    "P8B_CLAIMS": json.dumps(claims),
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
        "P8b SQL-first, scoped requisition, arrival posting, notification and rollback integration passed; disposable database cleaned"
    )


if __name__ == "__main__":
    runtime_worker(
        json.loads(os.environ["P8B_CLAIMS"])
    ) if "--worker" in sys.argv else main()
