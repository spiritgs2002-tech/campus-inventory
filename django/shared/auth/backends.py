from uuid import UUID
from django.db import connection
from django.utils import timezone
from shared.auth.principal import Principal
from shared.errors import BusinessError


def authorize_claims(claims):
    """B9: exactly one session/user/tenant join, including whole-family revocation."""
    try:
        tenant_id, user_id = int(claims["tid"]), int(claims["sub"])
        family_id = str(UUID(str(claims["fid"])))
    except (KeyError, ValueError, TypeError, OverflowError):
        raise BusinessError("UNAUTHENTICATED", "登录已失效", 401) from None
    if not (0 < tenant_id < 2**63 and 0 < user_id < 2**63):
        raise BusinessError("UNAUTHENTICATED", "登录已失效", 401)
    with connection.cursor() as cursor:
        cursor.execute(
            """
            SELECT u.tenant_id,u.id,u.role,u.username,u.name,u.must_change_password
            FROM auth_sessions s
            JOIN users u ON u.tenant_id=s.tenant_id AND u.id=s.user_id
            JOIN tenants t ON t.id=u.tenant_id
            WHERE s.tenant_id=%s AND s.user_id=%s AND s.family_id=%s::uuid
              AND u.active AND t.status='active' AND (t.expire_at IS NULL OR t.expire_at >= %s)
            GROUP BY u.tenant_id,u.id,u.role,u.username,u.name,u.must_change_password
            HAVING NOT bool_or(s.revoked_at IS NOT NULL) AND bool_or(s.expires_at > now())
        """,
            [tenant_id, user_id, family_id, timezone.localdate()],
        )
        row = cursor.fetchone()
    if row is None:
        raise BusinessError("UNAUTHENTICATED", "登录已失效", 401)
    return Principal(row[0], row[1], family_id, *row[2:])


def require_admin(principal, *, allow_password_change=False):
    principal = authorize_claims(
        {
            "tid": principal.tenant_id,
            "sub": principal.user_id,
            "fid": principal.family_id,
        }
    )
    if principal.role != "admin":
        raise BusinessError("FORBIDDEN", "仅管理员可操作", 403)
    if principal.must_change_password and not allow_password_change:
        raise BusinessError("PASSWORD_CHANGE_REQUIRED", "请先修改初始密码", 403)
    return principal
