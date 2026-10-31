import time
from datetime import timedelta
from uuid import uuid4

from django.conf import settings
from django.db import connection, transaction
from django.utils import timezone

from .models import AuthSession, LoginThrottle, Tenant, User
from shared.auth.backends import authorize_claims
from shared.auth.passwords import hash_password, verify_password
from shared.auth.tokens import access_token, refresh_digest, refresh_secret
from shared.db.runtime import require_runtime
from shared.errors import BusinessError
from shared.idempotency.service import execute, validate_key


def lock_family(tenant_id, family_id):
    # All rotation/revocation paths acquire this lock before reading family rows.
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            [f"auth:{tenant_id}:{family_id}"],
        )


def revoke_family(tenant_id, family_id, reason):
    lock_family(tenant_id, family_id)
    AuthSession.objects.filter(tenant_id=tenant_id, family_id=family_id).update(
        revoked_at=timezone.now(), revoked_reason=reason, parent_session_id=None
    )


def revoke_user(tenant_id, user_id, reason):
    families = sorted(
        set(
            AuthSession.objects.filter(
                tenant_id=tenant_id, user_id=user_id
            ).values_list("family_id", flat=True)
        ),
        key=str,
    )
    for family in families:
        revoke_family(tenant_id, family, reason)


def _user_view(principal):
    return {
        "id": principal.user_id,
        "name": principal.name,
        "username": principal.username,
        "role": principal.role,
        "tenant": principal.tenant_id,
    }


def _credentials(result):
    if result["issued_at"] + settings.ACCESS_TOKEN_SECONDS <= int(time.time()):
        raise BusinessError(
            "LOGIN_ALREADY_PROCESSED", "原登录响应已过期，请使用新的幂等键重新登录", 409
        )
    principal = authorize_claims(result["claims"])
    session = AuthSession.objects.get(
        tenant_id=principal.tenant_id, id=result["session_id"]
    )
    if session.rotated_at is not None:
        raise BusinessError(
            "LOGIN_ALREADY_PROCESSED", "登录已处理，请使用新的幂等键重新登录", 409
        )
    return (
        {
            "access_token": access_token(principal, result["issued_at"]),
            "user": _user_view(principal),
            "force_change_password": principal.must_change_password,
        },
        refresh_secret(session.id, session.family_id),
        result["claims"],
    )


def login(tenant_code, username, password, key):
    require_runtime()
    validate_key(key)
    user = (
        User.objects.filter(tenant__code=tenant_code, username=username)
        .select_related("tenant")
        .first()
    )
    valid = verify_password(password, user.password_hash if user else None)
    error = None
    if user:
        # Throttling has its own lock; failures never lock the business user row.
        with transaction.atomic():
            throttle, _ = LoginThrottle.objects.select_for_update().get_or_create(
                tenant_id=user.tenant_id, user_id=user.pk
            )
            now = timezone.now()
            if throttle.locked_until and throttle.locked_until > now:
                error = BusinessError(
                    "INVALID_CREDENTIALS", "登录失败，请检查凭证或稍后再试", 401
                )
            elif (
                not valid
                or not user.active
                or user.tenant.status != "active"
                or (
                    user.tenant.expire_at
                    and user.tenant.expire_at < timezone.localdate()
                )
            ):
                if throttle.locked_until:
                    throttle.failed_login_count = 0
                throttle.failed_login_count += 1
                throttle.locked_until = (
                    now + timedelta(minutes=user.tenant.login_lock_minutes)
                    if throttle.failed_login_count >= user.tenant.login_failure_limit
                    else None
                )
                throttle.save(update_fields=["failed_login_count", "locked_until"])
                error = BusinessError(
                    "INVALID_CREDENTIALS", "登录失败，请检查凭证或稍后再试", 401
                )
            else:
                throttle.failed_login_count = 0
                throttle.locked_until = None
                throttle.save(update_fields=["failed_login_count", "locked_until"])
    if not user or error:
        raise error or BusinessError(
            "INVALID_CREDENTIALS", "登录失败，请检查凭证或稍后再试", 401
        )

    def create_session():
        Tenant.objects.select_for_update(no_key=True).get(pk=user.tenant_id)
        current = (
            User.objects.select_for_update(no_key=True)
            .select_related("tenant")
            .get(pk=user.pk, tenant_id=user.tenant_id)
        )
        if (
            not current.active
            or current.tenant.status != "active"
            or (
                current.tenant.expire_at
                and current.tenant.expire_at < timezone.localdate()
            )
            or not verify_password(password, current.password_hash)
        ):
            raise BusinessError(
                "INVALID_CREDENTIALS", "登录失败，请检查凭证或稍后再试", 401
            )
        family = uuid4()
        session = AuthSession.objects.create(
            tenant_id=user.tenant_id,
            user_id=user.pk,
            family_id=family,
            refresh_digest=f"pending:{uuid4()}",
            expires_at=timezone.now()
            + timedelta(seconds=settings.REFRESH_TOKEN_SECONDS),
        )
        session.refresh_digest = refresh_digest(refresh_secret(session.pk, family))
        session.save(update_fields=["refresh_digest"])
        return {
            "session_id": session.pk,
            "claims": {"tid": user.tenant_id, "sub": str(user.pk), "fid": str(family)},
            "issued_at": int(time.time()),
        }

    result, _ = execute(
        user.tenant_id,
        user.pk,
        "auth.login",
        key,
        {"tenant": tenant_code, "username": username, "password": password},
        create_session,
    )
    return _credentials(result)


def refresh(raw, key):
    require_runtime()
    validate_key(key)
    old = AuthSession.objects.filter(refresh_digest=refresh_digest(raw)).first()
    if old is None:
        raise BusinessError("UNAUTHENTICATED", "登录已失效", 401)
    error = None
    output = None
    with transaction.atomic():
        # Serialize each command key before family locks, including retries.
        def rotate():
            nonlocal error
            lock_family(old.tenant_id, old.family_id)
            current = AuthSession.objects.get(pk=old.pk, tenant_id=old.tenant_id)
            family = AuthSession.objects.filter(
                tenant_id=old.tenant_id, family_id=old.family_id
            )
            if family.filter(revoked_at__isnull=False).exists():
                raise BusinessError("UNAUTHENTICATED", "登录已失效", 401)
            if current.rotated_at is not None:
                if timezone.now() - current.rotated_at <= timedelta(seconds=10):
                    error = BusinessError(
                        "REFRESH_ALREADY_ROTATED", "刷新凭证已轮换", 409
                    )
                else:
                    revoke_family(old.tenant_id, old.family_id, "reuse")
                    error = BusinessError(
                        "REFRESH_REUSE_DETECTED",
                        "检测到旧刷新凭证重用，请重新登录",
                        401,
                    )
                return {"rejected": error.code}
            if current.expires_at <= timezone.now():
                raise BusinessError("UNAUTHENTICATED", "登录已失效", 401)
            claims = {
                "tid": old.tenant_id,
                "sub": str(old.user_id),
                "fid": str(old.family_id),
            }
            authorize_claims(claims)
            new = AuthSession.objects.create(
                tenant_id=old.tenant_id,
                user_id=old.user_id,
                family_id=old.family_id,
                parent_session_id=old.pk,
                refresh_digest=f"pending:{uuid4()}",
                expires_at=timezone.now()
                + timedelta(seconds=settings.REFRESH_TOKEN_SECONDS),
            )
            new.refresh_digest = refresh_digest(refresh_secret(new.pk, new.family_id))
            new.save(update_fields=["refresh_digest"])
            current.rotated_at = timezone.now()
            current.save(update_fields=["rotated_at"])
            return {
                "session_id": new.pk,
                "claims": claims,
                "issued_at": int(time.time()),
            }

        # Old-token reuse detection always precedes replay of the first refresh response.
        lock_free_payload = {"refresh_digest": old.refresh_digest}
        result, replayed = execute(
            old.tenant_id, old.user_id, "auth.refresh", key, lock_free_payload, rotate
        )
        if replayed:
            lock_family(old.tenant_id, old.family_id)
            current = AuthSession.objects.get(pk=old.pk, tenant_id=old.tenant_id)
            if current.rotated_at:
                if timezone.now() - current.rotated_at > timedelta(seconds=10):
                    revoke_family(old.tenant_id, old.family_id, "reuse")
                    error = BusinessError(
                        "REFRESH_REUSE_DETECTED", "旧凭证重用，请重新登录", 401
                    )
                else:
                    error = BusinessError(
                        "REFRESH_ALREADY_ROTATED", "刷新凭证已轮换", 409
                    )
            elif "rejected" in result:
                error = BusinessError(result["rejected"], "刷新凭证已失效", 401)
        output = result
    if error:
        raise error
    return _credentials(output)


def logout(claims, key):
    validate_key(key)
    # Signed cookie/JWT claims are verified by the view. Permit only a recorded
    # replay when the family was already revoked by this exact logout command.
    try:
        principal = authorize_claims(claims)
    except BusinessError:
        from .models import OperationRequest
        from shared.idempotency.service import digest

        row = OperationRequest.objects.filter(
            tenant_id=claims["tid"],
            actor_id=int(claims["sub"]),
            operation="auth.logout",
            idempotency_key=key,
            request_digest=digest({"family": claims["fid"]}),
        ).first()
        if row and row.response is not None:
            require_runtime()
            return row.response
        raise

    def revoke():
        revoke_family(principal.tenant_id, principal.family_id, "logout")
        return {"logged_out": True}

    return execute(
        principal.tenant_id,
        principal.user_id,
        "auth.logout",
        key,
        {"family": principal.family_id},
        revoke,
    )[0]


def change_password(claims, old_password, new_password, key):
    principal = authorize_claims(claims)
    encoded = hash_password(new_password)

    def change():
        Tenant.objects.select_for_update(no_key=True).get(pk=principal.tenant_id)
        user = User.objects.select_for_update(no_key=True).get(
            tenant_id=principal.tenant_id, pk=principal.user_id
        )
        authorize_claims(claims)
        if not verify_password(old_password, user.password_hash):
            raise BusinessError("INVALID_CREDENTIALS", "当前密码不正确", 401)
        user.password_hash, user.must_change_password = encoded, False
        user.save(update_fields=["password_hash", "must_change_password"])
        revoke_user(user.tenant_id, user.pk, "password_change")
        return {"password_changed": True}

    return execute(
        principal.tenant_id,
        principal.user_id,
        "auth.change_password",
        key,
        {"old": old_password, "new": new_password},
        change,
    )[0]
