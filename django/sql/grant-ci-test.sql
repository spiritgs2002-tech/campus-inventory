-- sql/grant-ci-test.sql
-- 仅用于 CI / 本地测试数据库；严禁对生产库执行。
--
-- 背景：grant-runtime.sql 按项目宪法刻意从运行身份(inventory_runtime)回收了
--   * django_migrations 的读权限（迁移记账只归迁移身份）
--   * tenants 的 INSERT / UPDATE（租户只能由受控运维开通/改状态）
-- 但 Django 测试运行器以“普通运行身份”连库建 fixture 时，需要：
--   1) 读取 django_migrations 以构建迁移图；
--   2) 在 tenants 上 INSERT（建测试租户）与 UPDATE（模拟停用/过期）。
-- 因此在“测试库”上把这几项补回给 inventory_runtime（其成员即测试运行身份）。
-- 该脚本只作用于被显式指向的测试库，不影响生产库权限模型。

BEGIN;

-- 测试运行器需要读迁移记账表
GRANT SELECT ON django_migrations TO inventory_runtime;
GRANT SELECT ON SEQUENCE django_migrations_id_seq TO inventory_runtime;

-- 测试 fixture 需要在 tenants 上建/改测试租户
GRANT INSERT, UPDATE ON tenants TO inventory_runtime;
GRANT USAGE, SELECT ON SEQUENCE tenants_id_seq TO inventory_runtime;

COMMIT;
