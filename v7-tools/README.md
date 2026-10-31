# v7 验证工具

P5 起的对账入口为 **`reconcile-v2.sql`** 或 `django/` 下的 `manage.py reconcile`。旧 `reconcile.sql` 保留作历史证据：其四项单据流水检查未限定耗材，会误报固定资产。v2 为只读诊断修订，不修改 schema、grant 或已发布迁移；后文旧版对账命令仅供历史查阅。P5 新版本的来源、字节校验与实测结果见 `django/sql/README.md` 和 `P5开发交付及整改报告.md`。

基线为 **`schema-v7.sql`**（取代 schema-v6.sql；v6 的 4 处美元引号错误会导致整份 DDL 无法执行）。

## 前置条件

- **PostgreSQL 16**（支持下限与测试基线统一为 PG16；`pg_current_xact_id()` 的技术下限为 PG13，但 PG13 已结束官方支持，不作部署基线）。
- **psql 16**（与服务端同大版本，避免 `\d` 元命令与 `ON_ERROR_STOP` 行为差异）。
- **可建库权限**：执行者须能 `createdb`，或由 DBA 预先建立专用库。
- **本项目专用数据库**：`grant-runtime.sql` 会改写目标库 public schema 与表权限，**不要对共享业务数据库执行**。
- 不需要超级用户，但需能对 `public` schema 授权（通常由库 owner 或 DBA 执行）。真实登录角色的额外成员关系也须由 DBA 验证。

## 执行顺序

```sh
# 0) 静态预检（CI 第 1 步，不需要数据库）
python3 precheck-sql.py ../schema-v7.sql

createdb inventory_v7_test
psql --set=ON_ERROR_STOP=1 -d inventory_v7_test -f ../schema-v7.sql   # G0
psql --set=ON_ERROR_STOP=1 -d inventory_v7_test -f grant-runtime.sql
psql --set=ON_ERROR_STOP=1 -d inventory_v7_test -f verify-runtime.sql
psql --set=ON_ERROR_STOP=1 -d inventory_v7_test -f test-negative.sql   # 逐项正负用例
psql --set=ON_ERROR_STOP=1 -d inventory_v7_test -f cleanup-sessions.sql
psql --set=ON_ERROR_STOP=1 -d inventory_v7_test -f reconcile.sql
```

可选：会话族清理（P1-2，建议每日调度一次）

```sh
psql --set=ON_ERROR_STOP=1 -d inventory_v7_test -c "SELECT cleanup_expired_session_families();"
```

## G0 断言（执行 schema-v7.sql 后立即判定）

```sql
SELECT count(*) FROM information_schema.tables
 WHERE table_schema='public' AND table_type='BASE TABLE';   -- = 27
SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace
 WHERE n.nspname='public';                                  -- = 14
SELECT count(*) FROM pg_trigger WHERE NOT tgisinternal;     -- = 32（23 静态 + 9 个 DO 块动态）
```

三值全等且无报错才算 G0 通过；任一不符则整事务回滚，不得进入 M0 后续项。

**实测记录（2026-10-31）**：PostgreSQL 16.15（Homebrew，aarch64-apple-darwin）空库执行 `schema-v7.sql`：单事务 COMMIT、0 报错，27/14/32 全等；`test-negative.sql` 21 项正负用例全 PASS，退出码 0。

## 脚本职责

- **precheck-sql.py**：静态预检，CI 第 1 步。检查美元引号成对、闭合 `$$` 后必须有 `;`、开引号不得写成单个 `$`。v6 与 v7.1 各有一处这类错误会让整份 DDL 无法执行，纯靠阅读发现不了。**它不能替代真实执行。**
- **grant-runtime.sql**：创建 `inventory_runtime` NOLOGIN 权限角色并授权。运行角色必须非表 owner、非超级用户、无角色继承、无 TRIGGER/TRUNCATE、无 schema CREATE。`tenants` 仅给列级 UPDATE（显示信息、编号计数、自批开关、登录策略），**不给 INSERT/DELETE，也不给 `code`/`status`/`expire_at` 的 UPDATE**。`scrap_attachments` **有 DELETE、无 UPDATE**（误传证据可撤回，是"业务表不可 DELETE"的唯一例外）。另含 `ALTER DEFAULT PRIVILEGES`，使后续迁移新建的表/序列/函数自动继承同款权限；默认权限只做放宽，新建的不可变/历史表仍需显式 REVOKE 并重跑 verify。
- **verify-runtime.sql**：权限断言。逐表负向断言：业务表无 DELETE（`auth_sessions`、`login_throttle`、`scrap_attachments` 豁免）、不可变表无 UPDATE、每表需 SELECT+INSERT（`tenants` 仅要求 SELECT）；另含 `tenants` 列级断言。**注释或脚本存在不算通过，必须实际执行。**
- **test-negative.sql**：v7.2 新增约束的 21 项正负用例（资产卡不可变、`object_key` 租户内唯一、附件撤回与元数据守卫、loss 结案类型、资产事件同卡同类型、流水与报损物资一致）。
- **reconcile.sql**：只读一致快照（REPEATABLE READ READ ONLY）下的 **13 项**内部一致性检查。它输出异常行而不修改数据；**空库 0 行不是业务验收通过**，必须导入代表性正负案例——M3 门禁要求每一项都有一个构造的错误样例并被抓到。位置口径：仅 `in_stock`/`repair` 比对 `location_id`。**对账不替代实物抽盘。**
- **cleanup-sessions.sql**：会话整族清理函数。仅当族内全部 token 过期超过宽限期（默认 30 天）且无活跃后继时才删除整族；先清 `parent_session_id` 再删行以避免自外键冲突。

## 边界

M0 必须将 schema 构建、角色断言及逐项负向测试纳入 pytest + 真实 PG 的 CI；函数/触发器写显式 Alembic 迁移，不能依赖 autogenerate 自动发现。当前交付是**已在真实 PG16 上执行通过的 SQL 脚本**，但仍**不声称已提供完整应用集成测试**，也不覆盖并发、性能与业务级验收。
