#!/usr/bin/env python3
"""Real-login concurrency and startup checks in a newly created disposable PG16 DB."""

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
    database = f"inventory_p2p3_{tag}"
    login = f"inventory_runtime_{tag}"
    password = secrets.token_urlsafe(32)
    created_db = False
    created_role = False
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
                    "INSERT INTO tenants(code,name) VALUES ('PARALLEL','并发验收学校') RETURNING id"
                ).fetchone()[0]
                users = []
                for name in ("admin1", "admin2"):
                    user = db.execute(
                        "INSERT INTO users(tenant_id,username,name,role,password_hash,must_change_password) VALUES (%s,%s,%s,'admin','fixture-unused',false) RETURNING id",
                        [tenant, name, name],
                    ).fetchone()[0]
                    family = str(uuid4())
                    session = db.execute(
                        "INSERT INTO auth_sessions(tenant_id,user_id,family_id,refresh_digest,expires_at) VALUES (%s,%s,%s,%s,now()+interval '1 day') RETURNING id",
                        [tenant, user, family, str(uuid4())],
                    ).fetchone()[0]
                    from shared.auth.tokens import refresh_digest, refresh_secret

                    raw = refresh_secret(session, family)
                    db.execute(
                        "UPDATE auth_sessions SET refresh_digest=%s WHERE id=%s",
                        [refresh_digest(raw), session],
                    )
                    users.append(
                        {
                            "claims": {"tid": tenant, "sub": str(user), "fid": family},
                            "refresh": raw,
                        }
                    )
            runtime_env = {
                **env,
                "DB_USER": login,
                "DB_PASSWORD": password,
                "VERIFY_FIXTURE": json.dumps(users),
            }
            child = r"""
import os,json,threading
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4
import django
django.setup()
from django.db import connection,connections
from apps.core import services as core
from apps.catalog import services as catalog
from apps.core import auth_service
from apps.core.models import User,AuthSession,OperationRequest
from apps.catalog.models import Location
from shared.auth.backends import authorize_claims
from shared.errors import BusinessError
fixture=json.loads(os.environ['VERIFY_FIXTURE'])
principals=[authorize_claims(row['claims']) for row in fixture]

def parallel(function):
    barrier=threading.Barrier(2)
    def worker(index):
        try:
            barrier.wait(timeout=10)
            return function(index)
        except BusinessError as error:
            return error.code
        finally:
            connections.close_all()
    with ThreadPoolExecutor(max_workers=2) as executor:
        return list(executor.map(worker,range(2)))

key=str(uuid4())
def duplicate(index):
    return catalog.save(principals[0],'locations',None,key,{'name':'并发同键库位','active':True})[0]['record']['id']
ids=parallel(duplicate)
assert ids[0]==ids[1] and isinstance(ids[0],int),ids
assert Location.objects.filter(tenant_id=principals[0].tenant_id,name='并发同键库位').count()==1
print('CONCURRENCY: duplicate key -> one location, one response identity',flush=True)

def rotation(index):
    auth_service.refresh(fixture[0]['refresh'],str(uuid4()))
    return 'rotated'
rotations=parallel(rotation)
assert sorted(rotations)==['REFRESH_ALREADY_ROTATED','rotated'],rotations
print('CONCURRENCY: two refresh requests -> one rotation, one explicit retry response',flush=True)

def disable(index):
    principal=principals[index]
    user=User.objects.get(pk=principal.user_id,tenant_id=principal.tenant_id)
    core.save(principal,'users',user.pk,str(uuid4()),{'username':user.username,'name':user.name,'role':'admin','active':False,'reason':'并发离岗'})
    return 'disabled'
disabled=parallel(disable)
assert sorted(disabled)==['LAST_ADMIN','disabled'],disabled
assert User.objects.filter(tenant_id=principals[0].tenant_id,role='admin',active=True).count()==1
assert not OperationRequest.objects.filter(response__isnull=True).exists()
print('CONCURRENCY: two admin deactivations -> one accepted, one LAST_ADMIN; no empty placeholders',flush=True)
connections.close_all()
"""
            subprocess.run(
                [sys.executable, "-c", child], cwd=ROOT, env=runtime_env, check=True
            )
            subprocess.run(
                [sys.executable, "manage.py", "showmigrations", "--plan"],
                cwd=ROOT,
                env=runtime_env,
                check=True,
            )
            print(
                "P2/P3 actual runtime LOGIN and concurrency verification passed",
                flush=True,
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


if __name__ == "__main__":
    main()
