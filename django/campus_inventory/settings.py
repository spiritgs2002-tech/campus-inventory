"""P0 configuration; SQL schema ownership remains outside the ORM."""

import os
import json
from pathlib import Path
from celery.schedules import crontab
from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent
SQL_ROOT = BASE_DIR / "sql"
DEBUG = os.environ.get("DEBUG", "false").lower() == "true"
SECRET_KEY = os.environ.get("SECRET_KEY", "")
if not SECRET_KEY:
    raise ImproperlyConfigured("SECRET_KEY must be set in the environment")
ALLOWED_HOSTS = [
    s.strip()
    for s in os.environ.get(
        "ALLOWED_HOSTS", "localhost,127.0.0.1,[::1]" if DEBUG else ""
    ).split(",")
    if s.strip()
]
if not DEBUG and not ALLOWED_HOSTS:
    raise ImproperlyConfigured("Production ALLOWED_HOSTS must be explicitly configured")
INSTALLED_APPS = [
    "django.contrib.staticfiles",
    "csp",
    *[
        f"apps.{name}"
        for name in (
            "core",
            "catalog",
            "inventory",
            "attachments",
            "reconcile",
            "procurement",
            "repair",
            "notifications",
        )
    ],
]
MIDDLEWARE = [
    "django.middleware.security.SecurityMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "shared.tenant.middleware.IdentityMiddleware",
    "csp.middleware.CSPMiddleware",
    "django.middleware.common.CommonMiddleware",
    "apps.attachments.middleware.EvidenceUploadLimitMiddleware",
    "apps.repair.middleware.RepairUploadLimitMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]
ROOT_URLCONF = "campus_inventory.urls"
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [BASE_DIR / "templates"],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "apps.notifications.context_processors.unread_count",
                "django.template.context_processors.csrf",
            ]
        },
    }
]
WSGI_APPLICATION = "campus_inventory.wsgi.application"
ASGI_APPLICATION = "campus_inventory.asgi.application"
DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.environ.get("DB_NAME", "campus_inventory"),
        "USER": os.environ.get("DB_USER", "inventory_app"),
        "PASSWORD": os.environ.get("DB_PASSWORD", ""),
        "HOST": os.environ.get("DB_HOST", "localhost"),
        "PORT": os.environ.get("DB_PORT", "5432"),
        "CONN_MAX_AGE": 60,
        "ATOMIC_REQUESTS": True,
        "OPTIONS": {
            "connect_timeout": 5,
            "options": "-c lock_timeout=3000 -c statement_timeout=15000",
        },
    }
}
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_BROKER_URL = REDIS_URL
CELERY_RESULT_BACKEND = REDIS_URL
LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {"json": {"()": "shared.logging.JsonFormatter"}},
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "json",
        }
    },
    "root": {"handlers": ["console"], "level": "INFO"},
    # Override Django's default mail/console chain so each request is emitted once.
    "loggers": {"django": {"handlers": [], "level": "INFO", "propagate": True}},
}
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TIMEZONE = "Asia/Shanghai"
CONTENT_SECURITY_POLICY = {
    "DIRECTIVES": {
        "default-src": ["'self'"],
        "script-src": ["'self'"],
        "style-src": ["'self'"],
        "img-src": ["'self'", "data:"],
        "connect-src": ["'self'"],
        "object-src": ["'none'"],
        "base-uri": ["'self'"],
        "frame-ancestors": ["'none'"],
        "form-action": ["'self'"],
    }
}
CSRF_TRUSTED_ORIGINS = [
    s.strip()
    for s in os.environ.get("CSRF_TRUSTED_ORIGINS", "").split(",")
    if s.strip()
]
CSRF_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_SECURE = not DEBUG
SESSION_COOKIE_HTTPONLY = True
SESSION_COOKIE_SAMESITE = "Strict"
SECURE_SSL_REDIRECT = not DEBUG
# 反向代理信任由 TRUST_X_FORWARDED_PROTO 显式开启，见下方配置。
SECURE_HSTS_SECONDS = 31536000 if not DEBUG else 0
SECURE_HSTS_INCLUDE_SUBDOMAINS = not DEBUG
SECURE_HSTS_PRELOAD = not DEBUG
SECURE_CONTENT_TYPE_NOSNIFF = True
X_FRAME_OPTIONS = "DENY"
LANGUAGE_CODE = "zh-hans"
TIME_ZONE = "Asia/Shanghai"
USE_TZ = True
STATIC_URL = "/static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STATICFILES_DIRS = [BASE_DIR / "static"]
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
SESSION_ENGINE = "django.contrib.sessions.backends.signed_cookies"
SESSION_COOKIE_NAME = "inventory_session"
SESSION_COOKIE_AGE = 604800
AUTH_SIGNING_KEY = os.environ.get("AUTH_SIGNING_KEY") or SECRET_KEY
ACCESS_TOKEN_SECONDS = 300
REFRESH_TOKEN_SECONDS = 604800
PII_ENCRYPTION_KEYS = [
    key.strip()
    for key in os.environ.get("PII_ENCRYPTION_KEYS", "").split(",")
    if key.strip()
]
DATA_UPLOAD_MAX_MEMORY_SIZE = 1024 * 1024
# Evidence storage is private. Retention must be explicitly set by operations.
ATTACHMENT_STORAGE_BACKEND = os.environ.get("ATTACHMENT_STORAGE_BACKEND", "s3")
ATTACHMENT_STORAGE_ROOT = os.environ.get("ATTACHMENT_STORAGE_ROOT", "")
ATTACHMENT_MAX_BYTES = 10 * 1024 * 1024
ATTACHMENT_RETENTION_DAYS = os.environ.get("ATTACHMENT_RETENTION_DAYS", "")
ATTACHMENT_S3_BUCKET = os.environ.get("ATTACHMENT_S3_BUCKET", "")
ATTACHMENT_S3_REGION = os.environ.get("ATTACHMENT_S3_REGION", "us-east-1")
ATTACHMENT_S3_ENDPOINT = os.environ.get("ATTACHMENT_S3_ENDPOINT", "") or None
CELERY_RESULT_EXPIRES = 3600
CELERY_TASK_DEFAULT_QUEUE = os.environ.get("CELERY_QUEUE", "celery")
CELERY_BROKER_TRANSPORT_OPTIONS = {
    "global_keyprefix": os.environ.get("CELERY_KEY_PREFIX", ""),
    "visibility_timeout": 60,
}
CELERY_RESULT_BACKEND_TRANSPORT_OPTIONS = {
    "global_keyprefix": os.environ.get("CELERY_KEY_PREFIX", "")
}
# Explicit operations actor allowlist; no public endpoint can select this scope.
try:
    _maintenance_actors = json.loads(os.environ.get("MAINTENANCE_ACTORS_JSON", "{}"))
    if not isinstance(_maintenance_actors, dict) or any(
        not str(k).isdigit()
        or not 0 < int(k) < 2**63
        or type(v) is not int
        or not 0 < v < 2**63
        for k, v in _maintenance_actors.items()
    ):
        raise ValueError()
    MAINTENANCE_ACTORS = {int(k): v for k, v in _maintenance_actors.items()}
except (ValueError, TypeError):
    raise ImproperlyConfigured(
        "MAINTENANCE_ACTORS_JSON must map tenant IDs to operations admin IDs"
    ) from None
MAINTENANCE_ENABLED = os.environ.get("MAINTENANCE_ENABLED", "false").lower() == "true"
ATTACHMENT_CLEANUP_ENABLED = (
    os.environ.get("ATTACHMENT_CLEANUP_ENABLED", "false").lower() == "true"
)
ORPHAN_GRACE_HOURS = 24
try:
    PICKUP_ANONYMOUS_PER_MINUTE = int(
        os.environ.get("PICKUP_ANONYMOUS_PER_MINUTE", "60")
    )
except ValueError:
    raise ImproperlyConfigured("PICKUP_ANONYMOUS_PER_MINUTE must be 1-10000") from None
if not 1 <= PICKUP_ANONYMOUS_PER_MINUTE <= 10000:
    raise ImproperlyConfigured("PICKUP_ANONYMOUS_PER_MINUTE must be 1-10000")
# Anonymous QR flows (pickup / repair / requisition) with self-declared identity.
# "*" = all tenants, "" or "none" = disabled, "1,2" = tenant ID allowlist.
_anon_tenants = os.environ.get("ANONYMOUS_SCAN_TENANTS", "*").strip()
if _anon_tenants == "*":
    ANONYMOUS_SCAN_TENANTS = "*"
elif _anon_tenants.lower() in {"", "none"}:
    ANONYMOUS_SCAN_TENANTS = frozenset()
else:
    try:
        ANONYMOUS_SCAN_TENANTS = frozenset(
            int(part) for part in _anon_tenants.split(",") if part.strip()
        )
        if any(not 0 < tid < 2**63 for tid in ANONYMOUS_SCAN_TENANTS):
            raise ValueError()
    except ValueError:
        raise ImproperlyConfigured(
            'ANONYMOUS_SCAN_TENANTS must be "*", empty/none, or tenant IDs'
        ) from None


def _bounded_int(name, default, low, high):
    try:
        value = int(os.environ.get(name, str(default)))
    except ValueError:
        raise ImproperlyConfigured(f"{name} must be {low}-{high}") from None
    if not low <= value <= high:
        raise ImproperlyConfigured(f"{name} must be {low}-{high}")
    return value


# Per-request cap for an unverified consumable pickup (fixed assets are always 1).
PICKUP_ANONYMOUS_MAX_QTY = _bounded_int("PICKUP_ANONYMOUS_MAX_QTY", 20, 1, 1000000)
# Per employee number, per anonymous operation, rolling hour.
ANONYMOUS_PER_EMPLOYEE_PER_HOUR = _bounded_int(
    "ANONYMOUS_PER_EMPLOYEE_PER_HOUR", 10, 1, 10000
)
# New unverified identities a school may register per rolling hour.
ANONYMOUS_NEW_IDENTITIES_PER_HOUR = _bounded_int(
    "ANONYMOUS_NEW_IDENTITIES_PER_HOUR", 30, 1, 100000
)
# Behind a trusted reverse proxy only; never enable when the app is exposed directly.
if os.environ.get("TRUST_X_FORWARDED_PROTO", "false").lower() == "true":
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
# One beat instance per deployment; overlapping deliveries remain idempotent.
_test_interval = os.environ.get("MAINTENANCE_TEST_INTERVAL_SECONDS")
if _test_interval and (not DEBUG or not 1 <= int(_test_interval) <= 60):
    raise ImproperlyConfigured(
        "Maintenance test interval requires DEBUG and 1-60 seconds"
    )
CELERY_BEAT_SCHEDULE = {}
if MAINTENANCE_ENABLED:
    for _tid in sorted(MAINTENANCE_ACTORS):
        for _kind, _minute in [
            ("sessions", 0),
            ("responses", 10),
            ("evidence", 20),
            ("orphans", 30),
        ]:
            if _kind in {"evidence", "orphans"} and not ATTACHMENT_CLEANUP_ENABLED:
                continue
            CELERY_BEAT_SCHEDULE[f"maintenance-{_tid}-{_kind}"] = {
                "task": "maintenance.cleanup",
                "schedule": float(_test_interval)
                if _test_interval
                else crontab(hour=2, minute=_minute),
                "args": [_tid, _kind, 25],
                "options": {"expires": 3600},
            }
    CELERY_BEAT_SCHEDULE["maintenance-reconcile"] = {
        "task": "maintenance.reconcile",
        "schedule": float(_test_interval) if _test_interval else crontab(minute=45),
        "options": {"expires": 1800},
    }
