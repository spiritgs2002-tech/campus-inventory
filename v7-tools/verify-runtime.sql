-- 以DBA或可读取目录的身份执行。也必须对实际登录身份做等价检查。
-- v7 改动：
--   [P1-1] 补负向断言：业务表无 DELETE（仅会话/限流表豁免）；不可变表无 UPDATE；每表需 SELECT+INSERT。
--   [B3]   tenants 断言：无 INSERT/DELETE；code/status/expire_at 不可 UPDATE；编号计数仍可 UPDATE。
DO $$ DECLARE r oid; t record; col TEXT; BEGIN
 SELECT oid INTO r FROM pg_roles WHERE rolname='inventory_runtime';
 IF r IS NULL THEN RAISE EXCEPTION 'runtime role missing'; END IF;
 IF EXISTS(SELECT 1 FROM pg_roles WHERE oid=r AND (rolcanlogin OR rolsuper OR rolcreatedb OR rolcreaterole OR rolreplication OR rolbypassrls))
 OR EXISTS(SELECT 1 FROM pg_auth_members WHERE member=r)
 OR has_schema_privilege(r,'public','CREATE')
 OR EXISTS(SELECT 1 FROM pg_database WHERE datname=current_database() AND datdba=r) THEN
 RAISE EXCEPTION 'runtime role/schema/database privilege invalid'; END IF;

 -- [B3] 租户开通/期限/状态只能由受控运维处理
 IF has_table_privilege(r,'tenants','INSERT') OR has_table_privilege(r,'tenants','DELETE') THEN
   RAISE EXCEPTION 'tenants must not be insertable/deletable by runtime'; END IF;
 FOREACH col IN ARRAY ARRAY['code','status','expire_at'] LOOP
   IF has_column_privilege(r,'tenants',col,'UPDATE') THEN
     RAISE EXCEPTION 'tenants column % must not be updatable by runtime',col; END IF;
 END LOOP;
 IF NOT has_column_privilege(r,'tenants','next_asset_number','UPDATE') THEN
   RAISE EXCEPTION 'tenants numbering counter must remain updatable by runtime'; END IF;

 FOR t IN SELECT c.oid,c.relname,c.relowner FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace
 WHERE n.nspname='public' AND c.relkind='r' LOOP
 IF t.relname='django_migrations' THEN
   IF has_table_privilege(r,t.oid,'INSERT') OR has_table_privilege(r,t.oid,'UPDATE')
      OR has_table_privilege(r,t.oid,'DELETE') OR has_table_privilege(r,t.oid,'TRUNCATE')
      OR has_table_privilege(r,t.oid,'TRIGGER') OR t.relowner=r THEN
     RAISE EXCEPTION 'runtime must not mutate Django migration records'; END IF;
   CONTINUE;
 END IF;
 IF t.relowner=r OR has_table_privilege(r,t.oid,'TRIGGER') OR has_table_privilege(r,t.oid,'TRUNCATE') THEN
   RAISE EXCEPTION 'excessive table privilege: %',t.relname; END IF;
 -- [B3] tenants 是有意例外：租户开通走受控运维，运行账号不得建租户，故只要求 SELECT。
 IF t.relname='tenants' THEN
   IF NOT has_table_privilege(r,t.oid,'SELECT') THEN
     RAISE EXCEPTION 'runtime needs SELECT on tenants'; END IF;
 ELSIF NOT has_table_privilege(r,t.oid,'SELECT') OR NOT has_table_privilege(r,t.oid,'INSERT') THEN
   RAISE EXCEPTION 'runtime needs SELECT+INSERT: %',t.relname; END IF;
 -- [P1-1] 业务数据一律不可物理删除（会话与限流表由生命周期管理，豁免）
 IF t.relname NOT IN ('auth_sessions','login_throttle','scrap_attachments') AND has_table_privilege(r,t.oid,'DELETE') THEN
   RAISE EXCEPTION 'business tables must not grant DELETE: %',t.relname; END IF;
 -- [B5/N6] scrap_attachments 是"业务表不可 DELETE"的唯一例外：误传证据必须可撤回。
 -- 豁免成立的前提是撤销窗口被数据库收窄：仅父单 pending 且未决策，见 evidence_line_guard。
 IF t.relname='scrap_attachments' THEN
   IF NOT has_table_privilege(r,t.oid,'DELETE') THEN
     RAISE EXCEPTION 'scrap_attachments needs DELETE for withdrawing mis-uploaded evidence'; END IF;
   IF has_table_privilege(r,t.oid,'UPDATE') THEN
     RAISE EXCEPTION 'scrap_attachments must not grant UPDATE (delete and re-add instead)'; END IF;
 END IF;
 -- [P1-1] 一次成型/不可变表不可 UPDATE
 IF t.relname IN ('inbound_orders','inbound_items','stock_ledger','asset_events','audit_logs','scrap_attachments')
    AND has_table_privilege(r,t.oid,'UPDATE') THEN
   RAISE EXCEPTION 'immutable tables must not grant UPDATE: %',t.relname; END IF;
 IF t.relname IN ('audit_logs','stock_ledger','asset_events') THEN
   IF has_table_privilege(r,t.oid,'UPDATE') OR has_table_privilege(r,t.oid,'DELETE') THEN
     RAISE EXCEPTION 'history ACL invalid: %',t.relname; END IF;
 END IF;
 END LOOP;
END $$;
SELECT 'runtime ACL assertions passed; actual login memberships must also be checked' AS result;
