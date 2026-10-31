"""Minimal runtime support; immutable headers remain non-updatable by the app."""

from django.db import migrations


class Migration(migrations.Migration):
    dependencies = [("core", "0002_model_state")]
    operations = [
        migrations.RunSQL(
            "GRANT SELECT ON TABLE public.django_migrations TO inventory_runtime;"
        ),
        # PostgreSQL requires UPDATE privilege for SELECT FOR UPDATE. The immutable
        # inbound header deliberately has none. This trigger-only function may
        # lock its tenant-scoped parent as the migration owner instead. Its pinned
        # search_path is pg_catalog,public; runtime cannot create schema objects.
        migrations.RunSQL(
            "ALTER FUNCTION public.inbound_insert_guard() SECURITY DEFINER;"
        ),
    ]
