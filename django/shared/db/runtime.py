from django.db import connection
from shared.errors import BusinessError


def require_runtime():
    """Business commands must not silently run with the migration owner's rights."""
    with connection.cursor() as cursor:
        cursor.execute("""
            SELECT pg_has_role(current_user,'inventory_runtime','USAGE')
               AND NOT (r.rolsuper OR r.rolcreatedb OR r.rolcreaterole OR r.rolreplication OR r.rolbypassrls)
               AND NOT has_schema_privilege(current_user,'public','CREATE')
               AND NOT EXISTS (
                   SELECT 1 FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
                   WHERE n.nspname='public' AND c.relkind='r' AND
                     (c.relowner=r.oid OR has_table_privilege(current_user,c.oid,'TRIGGER')
                        OR has_table_privilege(current_user,c.oid,'TRUNCATE')))
            FROM pg_roles r WHERE r.rolname=current_user
        """)
        if not cursor.fetchone()[0]:
            raise BusinessError(
                "UNSAFE_DATABASE_ROLE", "业务服务必须使用普通运行身份", 503
            )
