import hashlib
import hmac
import time
import jwt
from django.conf import settings
from shared.errors import BusinessError


def refresh_secret(session_id, family_id):
    value = f"refresh:{session_id}:{family_id}".encode()
    return hmac.new(
        settings.AUTH_SIGNING_KEY.encode(), value, hashlib.sha256
    ).hexdigest()


def refresh_digest(raw):
    return hashlib.sha256(raw.encode()).hexdigest()


def access_token(principal, issued_at=None):
    issued = int(issued_at or time.time())
    return jwt.encode(
        {
            "sub": str(principal.user_id),
            "tid": principal.tenant_id,
            "fid": principal.family_id,
            "iat": issued,
            "exp": issued + settings.ACCESS_TOKEN_SECONDS,
            "iss": "campus-inventory",
            "aud": "inventory-api",
            "type": "access",
        },
        settings.AUTH_SIGNING_KEY,
        algorithm="HS256",
    )


def decode_access(raw):
    try:
        claims = jwt.decode(
            raw,
            settings.AUTH_SIGNING_KEY,
            algorithms=["HS256"],
            issuer="campus-inventory",
            audience="inventory-api",
            options={"require": ["sub", "tid", "fid", "iat", "exp", "type"]},
        )
        if claims["type"] != "access":
            raise ValueError()
        return claims
    except (jwt.InvalidTokenError, ValueError, TypeError):
        raise BusinessError("UNAUTHENTICATED", "登录已失效", 401) from None
