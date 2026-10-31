#!/usr/bin/env python3
"""Real DB/Redis readiness checks using a disposable ordinary LOGIN."""

import json
import logging
import os
from pathlib import Path
import secrets
import socket
import subprocess
import sys
from uuid import uuid4
import django
import psycopg
from psycopg import sql
from django.conf import settings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus_inventory.settings")
django.setup()


def worker():
    from django.db import connection
    from django.test import Client

    expected = int(os.environ["VERIFY_EXPECT_STATUS"])
    seen = []

    def execute(execute, statement, params, many, context):
        assert not connection.in_atomic_block, "Readiness is inside ATOMIC_REQUESTS"
        seen.append(statement)
        return execute(statement, params, many, context)

    with connection.execute_wrapper(execute):
        response = Client().get("/health/ready/")
    assert response.status_code == expected, response.content
    payload = response.json()
    if expected == 200:
        assert payload == {"status": "ok"} and seen == ["SELECT 1"], (payload, seen)
    else:
        assert (
            payload["status"] == "error"
            and payload["checks"][os.environ["VERIFY_FAILED_DEPENDENCY"]] == "error"
        )
    assert Client().get("/health/live/").status_code == 200
    logging.getLogger("readiness.verify").info("Readiness INFO log smoke test")
    print(
        json.dumps({"expected_status": expected, "result": payload, "non_atomic": True})
    )
    connection.close()


def main():
    from p6_test_support import isolated_redis

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
        f"inventory_ready_{tag}",
        f"inventory_probe_{tag}",
        secrets.token_urlsafe(32),
    )
    created_db = created_role = False

    def check(env, status, dependency=""):
        result = subprocess.run(
            [sys.executable, __file__, "--worker"],
            cwd=ROOT,
            env={
                **env,
                "VERIFY_EXPECT_STATUS": str(status),
                "VERIFY_FAILED_DEPENDENCY": dependency,
            },
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert result.returncode == 0, result.stdout
        entries = [json.loads(line) for line in result.stdout.splitlines()]
        assert any(
            e.get("level") == "INFO"
            and e.get("message") == "Readiness INFO log smoke test"
            for e in entries
        ), entries
        assert password not in result.stdout
        print(result.stdout, end="", flush=True)

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
            env = {**env, "DB_USER": login, "DB_PASSWORD": password}
            with isolated_redis(os.environ.get("REDIS_SERVER")) as redis_url:
                env["REDIS_URL"] = redis_url
                check(env, 200)
                with socket.socket() as port:
                    port.bind(("127.0.0.1", 0))
                    blocked_port = port.getsockname()[1]
                    check(
                        {**env, "DB_HOST": "127.0.0.1", "DB_PORT": str(blocked_port)},
                        503,
                        "database",
                    )
            check(env, 503, "redis")
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
        "Readiness: real DB/Redis 200; disconnected DB/Redis 503; non-atomic and JSON INFO logging verified; all temporary resources cleaned",
        flush=True,
    )


if __name__ == "__main__":
    worker() if "--worker" in sys.argv else main()
