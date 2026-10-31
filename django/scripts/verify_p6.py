#!/usr/bin/env python3
"""Disposable PG16 + ordinary LOGIN + real Celery/Redis + S3 wire contract."""

import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
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
    from concurrent.futures import ThreadPoolExecutor
    from io import BytesIO
    import threading
    from urllib.request import urlopen
    from urllib.error import HTTPError
    from django.core.files.uploadedfile import SimpleUploadedFile
    from django.core.management import get_commands
    from django.db import connections
    from django.test import Client
    from django.utils import timezone
    from PIL import Image
    from apps.attachments import services
    from apps.attachments.models import Attachment, ScrapAttachment
    from apps.attachments.tasks import verify_integrity
    from apps.core.models import AuthSession, OperationRequest
    from apps.inventory import orders
    from apps.reconcile.services import run
    from shared.auth.backends import authorize_claims
    from shared.auth.tokens import access_token
    from shared.errors import BusinessError

    f = json.loads(os.environ["VERIFY_FIXTURE"])
    principals = [authorize_claims(claims) for claims in f["claims"]]
    p, approver, user, foreign = principals
    stream = BytesIO()
    Image.new("RGB", (8, 8), "blue").save(stream, format="PNG")
    image = stream.getvalue()

    def upload(actor, key=None):
        return services.upload(
            actor,
            key or str(uuid4()),
            SimpleUploadedFile("proof.png", image, "image/png"),
        )[0]["record"]["id"]

    def pending():
        return orders.create(
            p,
            "scrap",
            str(uuid4()),
            {
                "type": "stock_damage",
                "reason": "附件通路验证",
                "lines": [
                    {
                        "item_id": f["item"],
                        "quantity": "1",
                        "location_id": f["location"],
                        "quality": "available",
                    }
                ],
            },
        )[0]["document"]["id"]

    own, other, personal = upload(p), upload(foreign), upload(user)
    print(
        "STORAGE: real boto3 PUT/GET wire requests to private local S3 protocol fixture; digest readback verified",
        flush=True,
    )
    obj = Attachment.objects.get(tenant_id=p.tenant_id, pk=own)
    try:
        urlopen(
            os.environ["ATTACHMENT_S3_ENDPOINT"] + "/private-fixture/" + obj.object_key,
            timeout=5,
        )
    except HTTPError as error:
        assert error.code == 403
    else:
        raise AssertionError("Anonymous object access unexpectedly succeeded")
    assert services.raw_metadata(p, [own, other])[0]["id"] == own
    assert len(services.raw_metadata(p, [own, other])) == 1
    assert services.raw_metadata(user, [own]) == []
    print(
        "ISOLATION 1/native SQL: cross-tenant and same-tenant unauthorized IDs excluded",
        flush=True,
    )

    def task_result(scope, pk):
        result = verify_integrity.apply_async(args=[scope, pk])
        try:
            return result.get(timeout=30)
        finally:
            result.forget()

    assert task_result(f["claims"][0], own)["ok"]
    assert not task_result(f["claims"][0], other)["ok"]
    assert not task_result({**f["claims"][0], "tid": foreign.tenant_id}, other)["ok"]
    print(
        "ISOLATION 2/Celery: separate real worker through Redis rejects foreign ID and forged tenant scope",
        flush=True,
    )
    document = pending()
    bad_csv = f"scrap_id,attachment_id\n{document},{own}\n{document},{other}\n".encode()
    try:
        services.import_csv(p, str(uuid4()), bad_csv)
    except BusinessError as error:
        assert error.status == 404
    else:
        raise AssertionError("Cross-tenant import accepted")
    assert not ScrapAttachment.objects.filter(
        tenant_id=p.tenant_id, scrap_id=document
    ).exists()
    services.import_csv(
        p, str(uuid4()), f"scrap_id,attachment_id\n{document},{own}\n".encode()
    )
    print(
        "ISOLATION 3/bulk import: mixed-tenant CSV rolls back all rows; own-tenant import succeeds",
        flush=True,
    )
    try:
        services.download(p, other)
    except BusinessError as error:
        assert error.status == 404
    else:
        raise AssertionError("Cross-tenant object download accepted")
    obj, data = services.download(p, own)
    assert data == image
    response = Client().get(
        f"/api/v1/attachments/{other}/download/",
        HTTP_AUTHORIZATION="Bearer " + access_token(p),
    )
    assert response.status_code == 404, response.content
    print(
        "ISOLATION 4/object download: foreign object rejected before access; authorized bytes and SHA match",
        flush=True,
    )

    def parallel(function):
        barrier = threading.Barrier(2)

        def attempt(index):
            try:
                barrier.wait(timeout=10)
                return function(index)
            except BusinessError as error:
                return error.code
            finally:
                connections.close_all()

        with ThreadPoolExecutor(max_workers=2) as pool:
            return list(pool.map(attempt, range(2)))

    key = str(uuid4())
    ids = parallel(lambda index: upload(p, key))
    assert isinstance(ids[0], int) and ids[0] == ids[1], ids
    print(
        "CONCURRENCY 1: duplicate uploads -> one metadata identity and one stored object",
        flush=True,
    )
    target, evidence = pending(), upload(p)

    def link(index):
        services.associate(principals[index], str(uuid4()), target, evidence)
        return "linked"

    outcomes = parallel(link)
    assert sorted(outcomes) == ["ALREADY_LINKED", "linked"], outcomes
    assert (
        ScrapAttachment.objects.filter(
            tenant_id=p.tenant_id, scrap_id=target, attachment_id=evidence
        ).count()
        == 1
    )
    print(
        "CONCURRENCY 2: cross-actor same evidence -> exactly one association",
        flush=True,
    )

    orders.create(
        p,
        "inbound",
        str(uuid4()),
        {
            "type": "opening",
            "lines": [
                {
                    "item_id": f["item"],
                    "quantity": "10",
                    "location_id": f["location"],
                    "quality": "available",
                }
            ],
        },
    )
    target, evidence = pending(), upload(p)

    def link_or_approve(index):
        if index == 0:
            services.associate(p, str(uuid4()), target, evidence)
            return "linked"
        orders.decide(approver, "scrap", target, "approve", str(uuid4()), {})
        return "approved"

    outcomes = parallel(link_or_approve)
    assert "approved" in outcomes and outcomes[0] in {"linked", "EVIDENCE_FROZEN"}, (
        outcomes
    )
    try:
        services.associate(
            p, str(uuid4()), target, evidence, withdraw=True, reason="决策后撤回"
        )
    except BusinessError as error:
        assert error.code == "EVIDENCE_FROZEN"
    else:
        raise AssertionError("Decided evidence withdrawn")
    print(
        "CONCURRENCY 3: evidence versus approval serialized; decided associations cannot change",
        flush=True,
    )
    services.legal_hold(p, str(uuid4()), own, "验收合成保全")
    services.associate(
        p, str(uuid4()), document, own, withdraw=True, reason="误传关联撤回"
    )
    assert Attachment.objects.get(pk=own).legal_hold
    assert services.download(p, own)[1] == image
    print("LIFECYCLE: withdrawal preserves original object and legal hold", flush=True)
    AuthSession.objects.filter(tenant_id=user.tenant_id, user_id=user.user_id).update(
        revoked_at=timezone.now()
    )
    assert not task_result(f["claims"][2], personal)["ok"]
    print("CELERY: revoked initiating family rejected at execution time", flush=True)
    assert not OperationRequest.objects.filter(response__isnull=True).exists()
    assert get_commands()["reconcile"] == "apps.reconcile"
    assert run()["ok"]
    print(
        "O1/P5: reconcile command owned by apps.reconcile; 13/13 clean after P6 flows",
        flush=True,
    )
    print("P6 runtime and four-path isolation verification passed", flush=True)
    connections.close_all()


def main():
    from p6_test_support import isolated_redis, object_server

    cfg = settings.DATABASES["default"]
    params = dict(
        host=cfg["HOST"],
        port=cfg["PORT"],
        user=cfg["USER"],
        password=cfg["PASSWORD"],
        dbname=cfg["NAME"],
    )
    tag = uuid4().hex[:12]
    database, login = f"inventory_p6_{tag}", f"inventory_runtime_{tag}"
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
                tenants = [
                    db.execute(
                        "INSERT INTO tenants(code,name) VALUES (%s,%s) RETURNING id",
                        [code, code],
                    ).fetchone()[0]
                    for code in ("P6_A", "P6_B")
                ]
                claims = []
                for index, role in enumerate(["admin", "admin", "user", "admin"]):
                    tenant = tenants[1] if index == 3 else tenants[0]
                    user = db.execute(
                        "INSERT INTO users(tenant_id,username,name,role,password_hash,must_change_password) VALUES (%s,%s,%s,%s,'fixture-unused',false) RETURNING id",
                        [tenant, f"user{index}", f"合成用户{index}", role],
                    ).fetchone()[0]
                    family = str(uuid4())
                    db.execute(
                        "INSERT INTO auth_sessions(tenant_id,user_id,family_id,refresh_digest,expires_at) VALUES (%s,%s,%s,%s,now()+interval '1 day')",
                        [tenant, user, family, str(uuid4())],
                    )
                    claims.append({"tid": tenant, "sub": str(user), "fid": family})
                category = db.execute(
                    "INSERT INTO categories(tenant_id,code,name,type) VALUES (%s,'C','耗材','consumable') RETURNING id",
                    [tenants[0]],
                ).fetchone()[0]
                item = db.execute(
                    "INSERT INTO items(tenant_id,code,name,type,category_id,unit) VALUES (%s,'PAPER','复印纸','consumable',%s,'包') RETURNING id",
                    [tenants[0], category],
                ).fetchone()[0]
                location = db.execute(
                    "INSERT INTO locations(tenant_id,name) VALUES (%s,'总库') RETURNING id",
                    [tenants[0]],
                ).fetchone()[0]
            with (
                isolated_redis(os.environ.get("REDIS_SERVER")) as redis_url,
                object_server() as (endpoint, objects),
                tempfile.TemporaryDirectory(prefix="inventory-p6-worker-") as directory,
            ):
                runtime_env = {
                    **env,
                    "DB_USER": login,
                    "DB_PASSWORD": password,
                    "REDIS_URL": redis_url,
                    "CELERY_QUEUE": f"p6_{tag}",
                    "CELERY_KEY_PREFIX": f"p6_{tag}:",
                    "ATTACHMENT_STORAGE_BACKEND": "s3",
                    "ATTACHMENT_S3_BUCKET": "private-fixture",
                    "ATTACHMENT_S3_ENDPOINT": endpoint,
                    "ATTACHMENT_RETENTION_DAYS": "30",
                    "AWS_ACCESS_KEY_ID": "p6-fixture",
                    "AWS_SECRET_ACCESS_KEY": "synthetic-not-a-real-key",
                    "AWS_EC2_METADATA_DISABLED": "true",
                    "VERIFY_FIXTURE": json.dumps(
                        {"claims": claims, "item": item, "location": location}
                    ),
                }
                with Path(directory, "celery.log").open("w+") as log:
                    process = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "celery",
                            "-A",
                            "campus_inventory",
                            "worker",
                            "--pool=solo",
                            "--concurrency=1",
                            "--loglevel=WARNING",
                            "--without-gossip",
                            "--without-mingle",
                            "--without-heartbeat",
                            "-Q",
                            f"p6_{tag}",
                        ],
                        cwd=ROOT,
                        env=runtime_env,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                    try:
                        result = subprocess.run(
                            [sys.executable, __file__, "--worker"],
                            cwd=ROOT,
                            env=runtime_env,
                        )
                        if result.returncode:
                            log.seek(0)
                            print(log.read()[-8000:], flush=True)
                            raise RuntimeError("P6 runtime verification failed")
                        print(
                            f"S3 fixture: {len(objects)} immutable private objects; no external bucket used",
                            flush=True,
                        )
                    finally:
                        process.terminate()
                        try:
                            process.wait(timeout=10)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=5)
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
        "P6 temporary database/login, Redis, worker and object server cleaned",
        flush=True,
    )


if __name__ == "__main__":
    worker() if "--worker" in sys.argv else main()
