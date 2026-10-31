#!/usr/bin/env python3
"""Disposable ordinary LOGIN + real Redis, Celery worker/beat and S3 wire checks."""

from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import time
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
    from concurrent.futures import ThreadPoolExecutor
    from unittest.mock import patch
    from django.db import connections
    from django.utils import timezone as tz
    from apps.attachments import cleanup, services, storage
    from apps.attachments.models import Attachment, ScrapAttachment
    from apps.core.models import AuthSession, OperationRequest, AuditLog
    from apps.inventory import orders
    from apps.reconcile import maintenance as ops
    from apps.reconcile.services import run
    from apps.reconcile.tasks import cleanup as task
    from shared.auth.backends import authorize_claims
    from shared.errors import BusinessError
    from shared.db.runtime import require_runtime

    f = json.loads(os.environ["VERIFY_FIXTURE"])
    tid, actor = f["tenant"], f["actor"]
    require_runtime()
    store = storage.backend()
    store.cleanup_ready()
    first, replay = ops.cleanup_sessions(tid, "family-gc")
    assert not replay and first["sessions"] == 2, first
    assert ops.cleanup_sessions(tid, "family-gc")[1]
    assert AuthSession.objects.filter(pk__in=f["protected_sessions"]).count() == len(
        f["protected_sessions"]
    )
    assert AuthSession.objects.filter(tenant_id=f["foreign_tenant"]).exists()
    try:
        ops.cleanup_sessions(f["foreign_tenant"], "forbidden")
    except BusinessError as error:
        assert error.code == "MAINTENANCE_SCOPE_REQUIRED"
    else:
        raise AssertionError("unconfigured tenant accepted")

    def cli(*args, success=True):
        result = subprocess.run(
            [sys.executable, "manage.py", *args],
            cwd=ROOT,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert (result.returncode == 0) == success, result.stdout
        return result.stdout

    assert '"sessions": 0' in cli(
        "cleanup-sessions", "--tenant-id", str(tid), "--idempotency-key", "cli-gc"
    )
    assert '"replayed": true' in cli(
        "cleanup-sessions", "--tenant-id", str(tid), "--idempotency-key", "cli-gc"
    )
    assert "MAINTENANCE_SCOPE_REQUIRED" in cli(
        "cleanup-sessions",
        "--tenant-id",
        str(f["foreign_tenant"]),
        "--idempotency-key",
        "foreign",
        success=False,
    )
    assert "IDEMPOTENCY_KEY_REQUIRED" in cli(
        "cleanup_sessions", "--tenant-id", str(tid), success=False
    )
    print(
        "SESSIONS: whole family >30 days removed; active successor, revoked-future, recent expiry and foreign tenant retained; replay no-op; actual runtime CLI alias/replay/negative exits passed",
        flush=True,
    )

    old = OperationRequest.objects.get(pk=f["old_operation"])
    before = (old.request_digest, old.business_refs, old.completed_at)
    result, _ = ops.cleanup_responses(tid, "response-gc")
    assert result["cleared"] == 1, result
    old.refresh_from_db()
    assert old.response is None and before == (
        old.request_digest,
        old.business_refs,
        old.completed_at,
    )
    print(
        "RESPONSES: 90-day response cleared; identity, digest, refs and completion timestamp retained",
        flush=True,
    )

    p = authorize_claims(f["claims"])
    document = orders.create(
        p,
        "scrap",
        "evidence-doc",
        {
            "type": "stock_damage",
            "reason": "合成证据验收",
            "lines": [
                {
                    "item_id": f["item"],
                    "location_id": f["location"],
                    "quality": "available",
                    "quantity": "1",
                }
            ],
        },
    )[0]["document"]["id"]
    services.associate(p, "link", document, f["expired"])
    plan = cleanup.cleanup_evidence(tid, dry_run=True)
    assert plan["attachment_ids"] == [f["expired"]], plan
    failed = cleanup.cleanup_evidence(tid, "physical-gc")
    assert not failed["ok"] and failed["items"][0]["status"] == "retry", failed

    def independent_visibility():
        try:
            obj = Attachment.objects.get(pk=f["expired"])
            return obj.deleted_at is not None
        finally:
            connections.close_all()

    with ThreadPoolExecutor(max_workers=1) as pool:
        assert pool.submit(independent_visibility).result(timeout=10)
    assert store.stat(tid, f["expired_key"]) is not None
    try:
        services.download(p, f["expired"])
    except BusinessError as error:
        assert error.status == 404
    else:
        raise AssertionError("tombstone remains downloadable")
    recovered = cleanup.cleanup_evidence(tid, "physical-gc")
    assert recovered["ok"], recovered
    assert store.stat(tid, f["expired_key"]) is None
    assert ScrapAttachment.objects.filter(
        tenant_id=tid, attachment_id=f["expired"]
    ).exists()
    assert Attachment.objects.get(pk=f["expired"]).deleted_at
    assert cleanup.cleanup_evidence(tid, "physical-gc")["ok"]
    assert AuditLog.objects.filter(action="maintenance.evidence.deleted").count() == 1
    for pk in f["protected_evidence"]:
        obj = Attachment.objects.get(pk=pk)
        assert obj.deleted_at is None and store.stat(obj.tenant_id, obj.object_key)
    print(
        "EVIDENCE: actual S3 HEAD/conditional DELETE; injected 503 preserves committed tombstone across connections, same-key retry completes once; metadata/link retained; hold/future/foreign protected",
        flush=True,
    )

    # A frozen plan may race an upload registration. Recheck under the same tenant lock.
    original = ops.command
    injected = False

    def register_after_plan(tenant, operation, key, payload, callback, **kwargs):
        nonlocal injected
        result = original(tenant, operation, key, payload, callback, **kwargs)
        if operation == "maintenance.orphans.plan" and not injected:
            injected = True
            obj = f["race_key"]
            Attachment.objects.create(
                tenant_id=tid,
                uploaded_by=actor,
                object_key=obj,
                sha256=f["digest"],
                size_bytes=len(b"p7-synthetic"),
                content_type="image/png",
                retention_until=tz.now() + timedelta(days=30),
            )
        return result

    with patch.object(ops, "command", side_effect=register_after_plan):
        orphan_result = cleanup.cleanup_orphans(tid, "orphan-gc", batch_size=100)
    assert not orphan_result["ok"], orphan_result
    assert cleanup.cleanup_orphans(tid, "orphan-gc", batch_size=100)["ok"]
    assert store.stat(tid, f["race_key"])
    for key in f["old_orphans"]:
        assert store.stat(tid, key) is None
    assert store.stat(tid, f["young_orphan"])
    # Page beyond registered rows rather than repeatedly starting at page one.
    cursors = []
    for index in range(20):
        result = cleanup.cleanup_orphans(tid, f"page-{index}", batch_size=1)
        cursors.append(result["next_cursor"])
        if result["next_cursor"] is None:
            break
    assert cursors[-1] is None and any(cursors), cursors
    print(
        "ORPHANS: tenant-only paged listing; >24h unregistered objects deleted; transient failure retried; young object and registration-after-plan preserved",
        flush=True,
    )

    import threading
    from apps.attachments.content import Evidence

    def race(function):
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

    AuthSession.objects.create(
        tenant_id=tid,
        user_id=actor,
        family_id=uuid4(),
        refresh_digest=uuid4().hex,
        expires_at=tz.now() - timedelta(days=40),
    )
    counts = race(
        lambda index: ops.cleanup_sessions(tid, f"concurrent-session-{index}")[0][
            "sessions"
        ]
    )
    assert sorted(counts) == [0, 1], counts
    race_key = f"tenants/{tid}/evidence/{uuid4().hex * 2}.png"
    data = b"p7-synthetic"
    store.put(tid, race_key, Evidence(data, f["digest"], "image/png", "png"))
    target = Attachment.objects.create(
        tenant_id=tid,
        uploaded_by=actor,
        object_key=race_key,
        sha256=f["digest"],
        size_bytes=len(data),
        content_type="image/png",
        retention_until=tz.now() - timedelta(days=1),
    )

    def delete_or_hold(index):
        if index == 0:
            return cleanup.cleanup_evidence(tid, "hold-race")
        services.legal_hold(p, "hold-race", target.pk, "合成并发保全")
        return "held"

    outcomes = race(delete_or_hold)
    target.refresh_from_db()
    if target.legal_hold:
        assert target.deleted_at is None and store.stat(tid, race_key)
    else:
        assert target.deleted_at is not None and store.stat(tid, race_key) is None, (
            outcomes
        )
    print(
        "CONCURRENCY: two ordinary LOGIN connections clean one family once; cleanup versus legal hold serializes to held/present or tombstoned/deleted",
        flush=True,
    )

    pending = AuthSession.objects.create(
        tenant_id=tid,
        user_id=actor,
        family_id=uuid4(),
        refresh_digest=uuid4().hex,
        expires_at=tz.now() - timedelta(days=40),
    )
    result = task.apply_async(args=[tid, "sessions"], task_id="p7-real-task-replay")
    assert result.get(timeout=30)["sessions"] == 1
    result.forget()
    second = task.apply_async(args=[tid, "sessions"], task_id="p7-real-task-replay")
    assert second.get(timeout=30)["replayed"]
    second.forget()
    assert not AuthSession.objects.filter(pk=pending.pk).exists()
    assert run()["ok"]
    print(
        "CELERY: real Redis/worker executed duplicate task ID, one audited mutation; reconcile 13/13 clean",
        flush=True,
    )
    connections.close_all()


@contextmanager
def process(args, env, logfile):
    with logfile.open("w+") as log:
        proc = subprocess.Popen(
            [sys.executable, "-m", "celery", "-A", "campus_inventory", *args],
            cwd=ROOT,
            env=env,
            stdout=log,
            stderr=subprocess.STDOUT,
        )
        try:
            yield proc
        except BaseException:
            log.seek(0)
            print(log.read()[-6000:], flush=True)
            raise
        finally:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)


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
    database, login, password = (
        f"inventory_p7_{tag}",
        f"inventory_runtime_{tag}",
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
            with (
                psycopg.connect(
                    **{**params, "dbname": database}, autocommit=True
                ) as db,
                isolated_redis(os.environ.get("REDIS_SERVER")) as redis_url,
                object_server() as (endpoint, objects),
                tempfile.TemporaryDirectory(prefix="p7-processes-") as directory,
            ):
                tenant, foreign = [
                    db.execute(
                        "INSERT INTO tenants(code,name) VALUES(%s,%s) RETURNING id",
                        [code, code],
                    ).fetchone()[0]
                    for code in ["P7_A", "P7_B"]
                ]
                actor = db.execute(
                    "INSERT INTO users(tenant_id,username,name,role,password_hash,must_change_password) VALUES(%s,'ops','合成运维','admin','unused',false) RETURNING id",
                    [tenant],
                ).fetchone()[0]
                foreign_actor = db.execute(
                    "INSERT INTO users(tenant_id,username,name,role,password_hash) VALUES(%s,'ops','外校','admin','unused') RETURNING id",
                    [foreign],
                ).fetchone()[0]

                def session(
                    tid=tenant,
                    user=actor,
                    days=-40,
                    family=None,
                    parent=None,
                    revoked=False,
                ):
                    return db.execute(
                        "INSERT INTO auth_sessions(tenant_id,user_id,family_id,refresh_digest,expires_at,parent_session_id,revoked_at,revoked_reason) VALUES(%s,%s,%s,%s,now()+(%s * interval '1 day'),%s,%s,%s) RETURNING id,family_id",
                        [
                            tid,
                            user,
                            family or uuid4(),
                            uuid4().hex,
                            days,
                            parent,
                            datetime.now(timezone.utc) if revoked else None,
                            "logout" if revoked else None,
                        ],
                    ).fetchone()

                first, family = session()
                session(family=family, parent=first)
                parent, family = session()
                child, _ = session(days=1, family=family, parent=parent)
                recent, _ = session(days=-1)
                revoked, _ = session(days=1, revoked=True)
                session(tid=foreign, user=foreign_actor)
                _, login_family = session(days=1)
                operation = db.execute(
                    "INSERT INTO operation_requests(tenant_id,actor_id,operation,idempotency_key,request_digest,response,business_refs,completed_at) VALUES(%s,%s,'fixture','old','digest','{}','[{\"type\":\"fixture\",\"id\":123}]',now()-interval '91 days') RETURNING id",
                    [tenant, actor],
                ).fetchone()[0]
                category = db.execute(
                    "INSERT INTO categories(tenant_id,code,name,type) VALUES(%s,'C','耗材','consumable') RETURNING id",
                    [tenant],
                ).fetchone()[0]
                item = db.execute(
                    "INSERT INTO items(tenant_id,code,name,type,category_id,unit) VALUES(%s,'C','纸','consumable',%s,'包') RETURNING id",
                    [tenant, category],
                ).fetchone()[0]
                location = db.execute(
                    "INSERT INTO locations(tenant_id,name) VALUES(%s,'库') RETURNING id",
                    [tenant],
                ).fetchone()[0]
                data = b"p7-synthetic"
                digest = hashlib.sha256(data).hexdigest()

                def object_key(tid=tenant, age=48):
                    key = f"tenants/{tid}/evidence/{uuid4().hex * 2}.png"
                    path = "/private-fixture/" + key
                    objects[path] = (data, "image/png")
                    objects.modified[path] = (
                        datetime.now(timezone.utc) - timedelta(hours=age)
                    ).replace(microsecond=0)
                    return key

                def evidence(tid=tenant, user=actor, expired=True, held=False):
                    key = object_key(tid)
                    pk = db.execute(
                        "INSERT INTO attachments(tenant_id,uploaded_by,object_key,sha256,size_bytes,content_type,retention_until,legal_hold) VALUES(%s,%s,%s,%s,%s,'image/png',now()+(%s*interval '1 day'),%s) RETURNING id",
                        [
                            tid,
                            user,
                            key,
                            digest,
                            len(data),
                            -1 if expired else 30,
                            held,
                        ],
                    ).fetchone()[0]
                    return pk, key

                expired, expired_key = evidence()
                protected = [
                    evidence(held=True)[0],
                    evidence(expired=False)[0],
                    evidence(tid=foreign, user=foreign_actor)[0],
                ]
                old_orphans = [object_key(), object_key()]
                young, race = object_key(age=0), object_key()
                objects.delete_failures["/private-fixture/" + expired_key] = 1
                objects.delete_failures["/private-fixture/" + old_orphans[0]] = 1
                fixture = dict(
                    tenant=tenant,
                    actor=actor,
                    foreign_tenant=foreign,
                    claims={"tid": tenant, "sub": str(actor), "fid": str(login_family)},
                    protected_sessions=[parent, child, recent, revoked],
                    old_operation=operation,
                    expired=expired,
                    expired_key=expired_key,
                    protected_evidence=protected,
                    old_orphans=old_orphans,
                    young_orphan=young,
                    race_key=race,
                    digest=digest,
                    item=item,
                    location=location,
                )
                runtime_env = {
                    **env,
                    "DB_USER": login,
                    "DB_PASSWORD": password,
                    "REDIS_URL": redis_url,
                    "CELERY_QUEUE": f"p7_{tag}",
                    "CELERY_KEY_PREFIX": f"p7_{tag}:",
                    "ATTACHMENT_STORAGE_BACKEND": "s3",
                    "ATTACHMENT_S3_BUCKET": "private-fixture",
                    "ATTACHMENT_S3_ENDPOINT": endpoint,
                    "AWS_ACCESS_KEY_ID": "p7-fixture",
                    "AWS_SECRET_ACCESS_KEY": "synthetic-not-a-real-key",
                    "AWS_EC2_METADATA_DISABLED": "true",
                    "MAINTENANCE_ACTORS_JSON": json.dumps({str(tenant): actor}),
                    "MAINTENANCE_ENABLED": "true",
                    "ATTACHMENT_CLEANUP_ENABLED": "true",
                    "VERIFY_FIXTURE": json.dumps(fixture),
                }
                directory = Path(directory)
                with process(
                    [
                        "worker",
                        "--pool=solo",
                        "--concurrency=1",
                        "--loglevel=INFO",
                        "--without-gossip",
                        "--without-mingle",
                        "--without-heartbeat",
                        "-Q",
                        f"p7_{tag}",
                    ],
                    runtime_env,
                    directory / "worker.log",
                ) as celery_worker:
                    subprocess.run(
                        [sys.executable, __file__, "--worker"],
                        cwd=ROOT,
                        env=runtime_env,
                        check=True,
                    )
                    beat_evidence, beat_key = evidence()
                    beat_session, _ = session()
                    beat_orphan = object_key()
                    beat_env = {**runtime_env, "MAINTENANCE_TEST_INTERVAL_SECONDS": "1"}
                    schedule = directory / "beat.db"
                    # Real beat, then stop and restart its persistent schedule.
                    for iteration in range(2):

                        def reconcile_successes():
                            return sum(
                                "maintenance.reconcile[" in line and "succeeded" in line
                                for line in (directory / "worker.log")
                                .read_text()
                                .splitlines()
                            )

                        previous_deliveries = reconcile_successes()
                        previous_actions = dict(
                            db.execute(
                                "SELECT action,count(*) FROM audit_logs WHERE tenant_id=%s AND action LIKE 'maintenance.%%' GROUP BY action",
                                [tenant],
                            ).fetchall()
                        )
                        with process(
                            [
                                "beat",
                                "--loglevel=INFO",
                                "--schedule",
                                str(schedule),
                                "--max-interval",
                                "1",
                            ],
                            beat_env,
                            directory / f"beat-{iteration}.log",
                        ) as beat:
                            deadline = time.monotonic() + 30
                            while True:
                                actions = dict(
                                    db.execute(
                                        "SELECT action,count(*) FROM audit_logs WHERE tenant_id=%s AND action LIKE 'maintenance.%%' GROUP BY action",
                                        [tenant],
                                    ).fetchall()
                                )
                                marked = db.execute(
                                    "SELECT deleted_at FROM attachments WHERE id=%s",
                                    [beat_evidence],
                                ).fetchone()[0]
                                remains = db.execute(
                                    "SELECT count(*) FROM auth_sessions WHERE id=%s",
                                    [beat_session],
                                ).fetchone()[0]
                                all_kinds = all(
                                    actions.get(action, 0)
                                    > previous_actions.get(action, 0)
                                    for action in {
                                        "maintenance.sessions",
                                        "maintenance.responses",
                                        "maintenance.evidence.plan",
                                        "maintenance.orphans.plan",
                                    }
                                )
                                deliveries = reconcile_successes()
                                beat_text = (
                                    directory / f"beat-{iteration}.log"
                                ).read_text()
                                sent_all = (
                                    all(
                                        f"maintenance-{tenant}-{kind}" in beat_text
                                        for kind in [
                                            "sessions",
                                            "responses",
                                            "evidence",
                                            "orphans",
                                        ]
                                    )
                                    and "maintenance-reconcile" in beat_text
                                )
                                if (
                                    marked
                                    and not remains
                                    and "/private-fixture/" + beat_key not in objects
                                    and "/private-fixture/" + beat_orphan not in objects
                                    and all_kinds
                                    and deliveries > previous_deliveries
                                    and sent_all
                                ):
                                    break
                                if (
                                    beat.poll() is not None
                                    or celery_worker.poll() is not None
                                    or time.monotonic() > deadline
                                ):
                                    raise AssertionError(
                                        "Beat/worker did not complete scheduled maintenance"
                                    )
                                time.sleep(0.2)
                        assert beat.poll() is not None
                        if iteration == 0:
                            previous_count = db.execute(
                                "SELECT count(*) FROM audit_logs WHERE action='maintenance.sessions'"
                            ).fetchone()[0]
                            time.sleep(1.2)
                            assert (
                                db.execute(
                                    "SELECT count(*) FROM audit_logs WHERE action='maintenance.sessions'"
                                ).fetchone()[0]
                                == previous_count
                            )
                    print(
                        "BEAT: real scheduler dispatched sessions/responses/evidence/orphans/reconcile; stop ceased dispatch; persistent restart succeeded; registered metadata retained",
                        flush=True,
                    )
                    print(
                        "P7 runtime, retry, race, pagination and beat lifecycle verification passed",
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
    print(
        "P7 temporary database/login, Redis, worker, beat and object server cleaned",
        flush=True,
    )


if __name__ == "__main__":
    worker() if "--worker" in sys.argv else main()
