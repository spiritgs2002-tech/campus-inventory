-- psql --set=ON_ERROR_STOP=1 -d 专用库存数据库 -f grant-runtime.sql
-- 由DBA执行；创建NOLOGIN权限角色，实际登录身份另行配置密钥并授予该角色。
-- v7 改动：
--   [B3]  tenants 收窄：无 INSERT/DELETE；仅列级 UPDATE（显示信息、编号计数、自批开关、登录策略）。
--         租户 code/status/expire_at 只能由受控运维修改，运行账号不得延期或改状态。
--   [P1-5] ALTER DEFAULT PRIVILEGES：后续迁移新建的表/序列/函数自动继承同款权限，
--         避免"新增触发器函数缺 EXECUTE"导致运行时失败。
BEGIN;
DO $$ BEGIN
 IF NOT EXISTS(SELECT 1 FROM pg_roles WHERE rolname='inventory_runtime') THEN
 CREATE ROLE inventory_runtime NOLOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
 END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE rolname='inventory_runtime' AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls)) THEN
 RAISE EXCEPTION 'existing runtime role has excessive privileges'; END IF;
 IF EXISTS(SELECT 1 FROM pg_auth_members WHERE member=(SELECT oid FROM pg_roles WHERE rolname='inventory_runtime')) THEN
 RAISE EXCEPTION 'runtime role must not inherit other roles'; END IF;
END $$;
REVOKE CREATE ON SCHEMA public FROM PUBLIC;
REVOKE ALL ON SCHEMA public FROM inventory_runtime;
GRANT USAGE ON SCHEMA public TO inventory_runtime;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM inventory_runtime;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT SELECT,INSERT,UPDATE ON ALL TABLES IN SCHEMA public TO inventory_runtime;
REVOKE UPDATE ON audit_logs,stock_ledger,asset_events,inbound_orders,inbound_items FROM inventory_runtime;
GRANT DELETE ON auth_sessions,login_throttle TO inventory_runtime;

-- [B5/N6] scrap_attachments：证据关联只增、只撤，不允许改。
--   撤回误传需要 DELETE（父单 pending 且未决策时由 evidence_line_guard 放行），故单独授予；
--   UPDATE 一律由触发器拒绝，这里同步撤销表级 UPDATE，做到"权限与触发器口径一致"。
--   注意：这使 scrap_attachments 成为"业务表不可 DELETE"规则的唯一例外，
--   verify-runtime.sql 已同步加入豁免名单并注明理由。
REVOKE UPDATE ON scrap_attachments FROM inventory_runtime;
GRANT DELETE ON scrap_attachments TO inventory_runtime;

-- [B3] 租户表不得增删，敏感列不可改
REVOKE INSERT,DELETE ON tenants FROM inventory_runtime;
REVOKE UPDATE ON tenants FROM inventory_runtime;
GRANT UPDATE(name,asset_prefix,next_asset_number,allow_self_approval,login_failure_limit,login_lock_minutes) ON tenants TO inventory_runtime;

-- [P1-5] 默认权限：后续迁移新建对象自动继承
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT,INSERT,UPDATE ON TABLES TO inventory_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE,SELECT ON SEQUENCES TO inventory_runtime;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT EXECUTE ON FUNCTIONS TO inventory_runtime;
-- 默认权限只做"放宽"，不替代"收窄"：若新建表属于不可变/历史类型，此处需显式 REVOKE 并重跑 verify-runtime.sql。

GRANT USAGE,SELECT ON ALL SEQUENCES IN SCHEMA public TO inventory_runtime;
REVOKE ALL ON ALL FUNCTIONS IN SCHEMA public FROM PUBLIC;
GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public TO inventory_runtime;
-- Django 的迁移记账不属于业务数据；只有迁移身份可修改。
DO $$ BEGIN
 IF to_regclass('public.django_migrations') IS NOT NULL THEN
   REVOKE ALL ON django_migrations FROM inventory_runtime;
   REVOKE ALL ON SEQUENCE django_migrations_id_seq FROM inventory_runtime;
 END IF;
END $$;
COMMIT;
