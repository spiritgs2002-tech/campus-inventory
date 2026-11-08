"""Deployment reminders surfaced by `manage.py check` (and migrate/apply_schema)."""

import os
from django.conf import settings
from django.core.checks import Warning, register


@register()
def proxy_trust_is_explicit(app_configs, **kwargs):
    # Upgrades from builds that trusted X-Forwarded-Proto unconditionally must now
    # opt in; an unset value behind nginx/CLB causes an HTTPS redirect loop.
    if settings.DEBUG or "TRUST_X_FORWARDED_PROTO" in os.environ:
        return []
    return [
        Warning(
            "TRUST_X_FORWARDED_PROTO 未设置：如在 nginx/CLB 等反向代理后运行，"
            "请设为 true，否则 HTTPS 跳转会死循环；应用直接暴露时请显式设为 false。",
            id="campus.W001",
        )
    ]
