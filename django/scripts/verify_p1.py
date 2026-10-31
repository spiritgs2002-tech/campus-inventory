#!/usr/bin/env python3
"""Destructive checks run ONLY in fresh uniquely named databases created here.

Requires a dedicated PG16 test cluster and a DBA login with CREATEDB/CREATEROLE.
No existing database or login is dropped. Test objects are removed in finally.
"""

import os
from pathlib import Path
import secrets
import subprocess
import sys
from uuid import uuid4

import django
import psycopg
from django.conf import settings
from django.db import connection
from psycopg import sql

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "campus_inventory.settings")
django.setup()


def manage(*args, env):
    result = subprocess.run(
        [sys.executable, "manage.py", *args],
        cwd=ROOT,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        check=False,
    )
    print(result.stdout, end="", flush=True)
    if result.returncode:
        raise RuntimeError(f"manage.py {args} failed: {result.returncode}")
    return result.stdout


def main():
    cfg = settings.DATABASES["default"]
    admin_params = dict(
        host=cfg["HOST"],
        port=cfg["PORT"],
        user=cfg["USER"],
        password=cfg["PASSWORD"],
        dbname=cfg["NAME"],
    )
    tag = uuid4().hex[:12]
    dbname = f"inventory_p1_{tag}"
    migrator = f"inventory_migrate_{tag}"
    runtime_login = f"inventory_login_{tag}"
    password = secrets.token_urlsafe(32)
    created_databases = []
    created_roles = []
    with psycopg.connect(**admin_params, autocommit=True) as admin:
        version = admin.execute("SHOW server_version").fetchone()[0]
        assert (
            admin.execute("SHOW server_version_num").fetchone()[0].startswith("16")
        ), version
        print(f"PostgreSQL {version}; isolated verification: {dbname}", flush=True)
        try:
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN CREATEROLE NOINHERIT NOSUPERUSER NOCREATEDB NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(migrator), sql.Literal(password))
            )
            created_roles.append(migrator)
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(dbname), sql.Identifier(migrator)
                )
            )
            created_databases.append(dbname)
            env = {
                **os.environ,
                "DB_NAME": dbname,
                "DB_USER": migrator,
                "DB_PASSWORD": password,
            }
            manage("apply_schema", env=env)
            output = manage("apply_schema", env=env)
            assert "No migrations to apply" in output
            manage("makemigrations", "--check", "--dry-run", env=env)

            params = {**admin_params, "dbname": dbname}
            with psycopg.connect(**params, autocommit=True) as db:
                assert (
                    db.execute("SELECT count(*) FROM django_migrations").fetchone()[0]
                    == 6
                )
                notices = []
                db.add_notice_handler(
                    lambda diagnostic: notices.append(diagnostic.message_primary)
                )
                cursor = db.execute(
                    (settings.SQL_ROOT / "test-negative.sql").read_text(), prepare=False
                )
                while cursor.nextset():
                    pass
                passed = [notice for notice in notices if notice.startswith("PASS[")]
                assert len(passed) == 21, notices
                print("SQL positive/negative assertions: 21/21 passed", flush=True)

            # Connect using a real runtime login, not the database owner.
            admin.execute(
                sql.SQL(
                    "CREATE ROLE {} LOGIN INHERIT NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS PASSWORD {}"
                ).format(sql.Identifier(runtime_login), sql.Literal(password))
            )
            created_roles.append(runtime_login)
            admin.execute(
                sql.SQL("GRANT inventory_runtime TO {}").format(
                    sql.Identifier(runtime_login)
                )
            )
            with psycopg.connect(
                **{**params, "user": runtime_login, "password": password},
                autocommit=True,
            ) as runtime:
                runtime.execute("SELECT count(*) FROM items")
                for statement in (
                    "UPDATE tenants SET status='disabled'",
                    "TRUNCATE locations",
                    "CREATE TABLE public.forbidden_p1(id bigint)",
                    "INSERT INTO django_migrations(app,name,applied) VALUES ('bad','bad',now())",
                ):
                    try:
                        runtime.execute(statement)
                    except psycopg.errors.InsufficientPrivilege:
                        pass
                    else:
                        raise AssertionError(
                            f"Runtime login unexpectedly allowed: {statement}"
                        )
            print(
                "Actual runtime login: read allowed; 4 forbidden operations rejected",
                flush=True,
            )

            # A committed but unrecorded baseline must never be silently adopted.
            with psycopg.connect(**params, autocommit=True) as db:
                before = db.execute("SELECT count(*) FROM users").fetchone()[0]
                db.execute(
                    "DELETE FROM django_migrations WHERE app='core' AND name='0001_initial'"
                )
                result = subprocess.run(
                    [sys.executable, "manage.py", "apply_schema"],
                    cwd=ROOT,
                    env=env,
                    text=True,
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                )
                assert (
                    result.returncode != 0
                    and "Unrecorded schema exists" in result.stdout
                )
                assert db.execute("SELECT count(*) FROM users").fetchone()[0] == before
            print(
                "Unrecorded baseline: adoption refused, business rows unchanged",
                flush=True,
            )

            # A failure immediately before the original COMMIT must leave no tables.
            rollback_db = f"inventory_rollback_{tag}"
            admin.execute(
                sql.SQL("CREATE DATABASE {} OWNER {}").format(
                    sql.Identifier(rollback_db), sql.Identifier(migrator)
                )
            )
            created_databases.append(rollback_db)
            rollback_env = {**env, "DB_NAME": rollback_db}
            child = """
import hashlib, os, tempfile
from pathlib import Path
from unittest.mock import patch
import django
django.setup()
from django.db import connection, DatabaseError
from shared.db.operations import RunSQLFile, SQL_DIR
original = (SQL_DIR / 'schema-v7.sql').read_bytes()
assert original.rstrip().endswith(b'COMMIT;')
broken = original.rstrip()[:-len(b'COMMIT;')] + b'SELECT 1/0;\\nCOMMIT;'
with tempfile.TemporaryDirectory() as directory:
    Path(directory, 'fault.sql').write_bytes(broken)
    with patch('shared.db.operations.SQL_DIR', Path(directory)):
        try:
            with connection.schema_editor(atomic=False) as editor:
                RunSQLFile('fault.sql', hashlib.sha256(broken).hexdigest()).database_forwards('core', editor, None, None)
        except DatabaseError:
            pass
        else:
            raise AssertionError('Expected injected SQL failure')
with connection.cursor() as cursor:
    cursor.execute("SELECT count(*) FROM pg_tables WHERE schemaname='public'")
    assert cursor.fetchone()[0] == 0
    cursor.execute("SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname='public'")
    assert cursor.fetchone()[0] == 0
connection.close()
print('Injected pre-COMMIT failure: 0 residual tables / 0 residual functions')
"""
            subprocess.run(
                [sys.executable, "-c", child], cwd=ROOT, env=rollback_env, check=True
            )

            # The normal suite creates another fresh database using DBA credentials.
            suite_env = {**os.environ, "DB_NAME": dbname}
            manage("test", "tests", "--noinput", "-v", "2", env=suite_env)
            print("P1 verification passed", flush=True)
        finally:
            connection.close()
            for name in reversed(created_databases):
                admin.execute(
                    sql.SQL("DROP DATABASE {} WITH (FORCE)").format(
                        sql.Identifier(name)
                    )
                )
            for role in reversed(created_roles):
                admin.execute(sql.SQL("DROP ROLE {}").format(sql.Identifier(role)))


if __name__ == "__main__":
    main()
