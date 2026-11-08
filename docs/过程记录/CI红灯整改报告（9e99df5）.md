# CI 红灯整改报告（针对提交 9e99df5）

- 日期：2026-11-08
- 范围：`.github/workflows/ci.yml`、`django/docs/ci/ci.yml.template`、`django/campus_inventory/settings.py`、`django/sql/grant-ci-test.sql`
- 不涉及业务代码。9e99df5 的独立验收报告等审计记录保持原样，本文件是另建的整改答复。

## 一、核实结果

| # | 问题 | 核实方式 | 结论 |
|---|------|----------|------|
| 1 | `verify_ci_delivery.py` 报 `CI_TEMPLATE_DRIFT` | `diff .github/workflows/ci.yml django/docs/ci/ci.yml.template` | **属实**。9e99df5 只改了根 ci.yml，没有同步 template。另经 `cmp` 确认，template 与 9e99df5 之前 d8198b6 的 ci.yml 逐字节一致 |
| 2a | 以 inventory_ci 跑 `manage.py test tests --keepdb` 出现 17 项失败 | 本机 PostgreSQL 16.15，按 9e99df5 的 ci.yml 建 inventory_ci、test_campus_inventory，执行 grant-runtime.sql + grant-ci-test.sql 后跑全量 | **属实**：`FAILED (failures=2, errors=15)`。其中 13 项为 `test_all_thirteen_checks_detect_seeded_corruption` 的子测试，另外 4 项是 `test_g0_and_runtime_commands`、`test_runtime_can_read_migration_status_but_not_provision_tenants`、`test_migration_identity_cannot_execute_business_requests`、`test_runtime_effective_permissions_and_migration_journal_protection`。另发现：stdin 不关闭时，套件会卡在“初始管理员密码”的 getpass 交互提示 |
| 2b | 以 inventory_ci 跑 verify_p7/p8a/p8b/p8c/readiness 失败 | 同上环境执行 `scripts/verify_p7.py` | **属实**：`permission denied to create database` |
| 3 | 9e99df5 提交说明称“原 CI 用 postgres 超户跑 manage.py test 导致运行身份守卫直接失败” | ① 本机用 DBA 连接（postgres）跑全量；② 查 GitHub Actions 历史 | **不成立**。① 本机 DBA 连接下全量 297 项 OK，包括 9e99df5 新增的 31 项 import_repairs 测试；② 远端 run #7（d8198b6，即旧模型）为 success，只有 run #8（9e99df5）为 failure。原因在于测试本身在业务路径上执行 `SET LOCAL ROLE inventory_runtime`（见 `tests/order_fixtures.py`、`tests/test_auth_masterdata.py` 等），在 owner 路径上执行 `RESET ROLE`，`require_runtime()` 校验的是真实运行角色 |

## 二、实际修改

1. **`django/docs/ci/ci.yml.template`**：在全量测试步骤前加 5 行注释，说明测试必须使用 owner/DBA 连接，并在测试内部切换到 inventory_runtime；同时写明禁止为测试给运行角色授予 CREATEDB 或额外表权限。
2. **`.github/workflows/ci.yml`**：改为与 template 逐字节相同，恢复 9e99df5 之前已经跑绿的结构：
   - verify_p1–p8c 按顺序用 DBA 连接执行（DB_USER=postgres、DB_NAME=postgres）。这些脚本自己创建一次性库，并在库内创建和切换运行角色；
   - 全量测试 `manage.py test tests --no-input` 用 DBA 连接执行，由 Django 自建、自删测试库；
   - verify_readiness 用 DBA 连接执行；
   - 删除 inventory_ci 建角色步骤、预建 test_campus_inventory 的步骤，以及 job 级的 `TEST_DB_NAME`。
3. **删除 `django/sql/grant-ci-test.sql`**：该文件会把 tenants 的 INSERT/UPDATE 和 django_migrations 的读权授回运行角色，与 grant-runtime.sql 的权限模型相冲突，也正是 `test_runtime_effective_permissions...` 失败的原因。删除后已无任何引用，也消除了误用到生产库的风险。
4. **`django/campus_inventory/settings.py`**：删除 `DATABASES.default.TEST.NAME = $TEST_DB_NAME` 覆盖。删除原因：一旦该变量被误指向真实库，再配合 `--keepdb` 或不配合，都可能让测试直连甚至销毁业务库。同一提交新增的 `SECURE_PROXY_SSL_HEADER` 属于部署功能，未改动。

运行守卫没有放宽：运行角色仍无 CREATEDB，没有新增任何授权，没有跳过或禁用任何测试（日志中 skip 计数为 0）。

## 三、验证结果（本次在本机实际重跑）

环境：PostgreSQL 16.15（与 CI 的 `postgres:16.15` 同版本）、Python 3.13、`uv sync --frozen`、本机 redis-server。全新 cluster：删除上次复现留下的库和角色后，按修改后 ci.yml 的步骤顺序逐步执行。

| 步骤 | 结果 |
|------|------|
| verify_ci_delivery.py --repo-root（`template_matches: true`，sha256 8374539580f3…1305，两文件相同） | rc=0 |
| sync_schema_snapshot.py --check | rc=0 |
| uv sync --frozen / precheck_sql / check / verify_config_examples | 全部 rc=0 |
| verify_p1、p2_p3、p4、p5、p6、p7、p8a、p8b、p8c | 全部 rc=0 |
| 建 campus_inventory、apply_schema（G0: 30 表 / 14 函数 / 32 触发器） | rc=0 |
| `manage.py test tests --no-input` | **Ran 297 tests … OK**，rc=0 |
| verify_schema / verify_runtime（campus_inventory） | rc=0 |
| verify_readiness.py | rc=0 |
| requirements.txt 与 uv.lock 一致性 | rc=0 |

此外单独重跑了上述 17 项涉及的测试类，以及 test_import_repairs，全部 OK。

本地运行 verify_p8a 时会生成 `docs/过程记录/P8a/复验制品/`，这是临时产物，已删除，未提交。

## 四、尚未闭环项

- **远端 GitHub Actions 结果**：本报告只证明本机按 ci.yml 步骤复现全绿。推送后的远端运行结果需要以 Actions 页面为准，本次没有拿它当作已验证。
- **inventory_ci 方案的初衷**：如果后续确实需要“以 LOGIN 运行身份端到端跑业务命令”的覆盖，应另起任务，新增只针对运行身份的专项步骤（不创建库、不修改租户、不追加授权）。不应再把全量套件或一次性库脚本放到运行身份下执行。
