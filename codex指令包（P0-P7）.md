# 实现指令包：学校总务处出入库管理系统（Django + PostgreSQL）

> 本文件是给**实现代理 codex（ChatGPT Codex）**的自包含任务书。控制中枢（项目架构师）拆分为原子任务逐期下发，你落地代码，由他做逻辑走查与规范验收。
> 详细规范见同目录 `开发规范（Django+PG）.md`（唯一开发契约）与 `实施计划v7（最终版）.md`（已 Ratified）。

## 1. 你的角色
你是实现代理（codex）。每期收到一个 Phase 的原子任务 + 验收标准，落地代码并自测，回报文件清单、关键验证数字、偏差。不得静默偏离规范；规范有歧义/矛盾时明确指出。
**门禁节奏**：按控制中枢裁决采用"一个阶段一个阶段 做-查-盖-下一阶段"硬门禁——完成一 Phase 并自测后回报，待控制中枢独立验收通过、整改闭环，才着手下一 Phase 的业务代码（上一阶段一过即可立即接续，无需额外开工批准）。

## 2. 技术栈（硬性，违反 = 返工）
- **Django 5 + Python 3.13 + psycopg3 + PostgreSQL 16 + Celery/Redis + Django Templates / Alpine.js / HTMX / Bootstrap 5**。
- **明确排除**：FastAPI、Vue3、SQLite(WAL)、SwiftUI。其 JWT / RBAC 设计思路已转化为 Django 形态。
- 数据库基线：`schema-v7.sql`（27 表 / 14 函数 / 32 触发器，已在真实 PG16.15 实测 G0 通过）——它是 **DDL 唯一来源**。

## 3. 全局红线（不得违反）
- `schema-v7.sql` 是唯一 DDL 来源；Django Models 仅作 ORM 读写外壳（`managed=False`），**禁止**让 Django 生成表/触发器/函数；迁移用 `RunSQL` 整文件应用 `schema-v7.sql` + `v7-tools/grant-runtime.sql`，**禁止** autogenerate。
- `Idempotency-Key`：所有写命令必带，按 `租户+操作者+operation+key` 键域去重（`operation_requests`）。
- 租户隔离：复合外键 `(tenant_id,…)` + 服务端过滤双保留。
- 运行/迁移账号分离：`inventory_runtime` 为 NOLOGIN 角色（非 owner/超级用户，无 TRUNCATE/TRIGGER/schema CREATE）。
- 敏感字段应用层加密：指定 PII 列经 `shared/crypto` 加密，密钥来自 env/secret manager，不入库。

## 4. 现有脚手架（已存在，作起点，**勿推翻重做**）
目录 `inventory-v4/django/`（若从零开始则按此结构重建）：
- `campus_inventory/`（settings / urls / wsgi / asgi）
- `apps/{core,catalog,inventory,attachments,reconcile}`
- `shared/{rbac,auth,idempotency,crypto,tenant}`
- `core/migrations/0001_initial.py` 用 `RunSQLFile` 整文件应用 `schema-v7.sql` + `v7-tools/grant-runtime.sql`；全部 27 表 `managed=False` ORM 模型。
- 管理命令：`precheck_sql`（包 `v7-tools/precheck-sql.py`）、`apply_schema`、`verify_runtime`、`reconcile`。
- `settings.py` 已配 psycopg3 / CSP（**注意：django-csp 尚未安装也未加入 INSTALLED_APPS，需补**）/ Redis / Celery。
- `shared/` 下 `rbac/permission.py`、`auth/backends.py`、`tenant/middleware.py` 目前是 **P0 占位 stub**，授权/P2 实现时填充。

## 5. 原子任务（P0–P7，逐期下发）

> **门禁分级与节奏**（控制中枢 2026-10-31 裁决）：采用"一个阶段一个阶段 做-查-盖-下一阶段"硬门禁。每个 Phase 完成并**经控制中枢独立复现 + 逻辑走查 + 规范验收通过**后，才放行下一 Phase；未通过的整改项闭环后方可继续。
> - **硬门禁（必须单独查透、不与其他阶段合并验收）**：`P2 认证与 RBAC`（全系统授权地基，错一处下游全崩）、`P5 库存与资产`（不可变台账与 `inventory.version` 版本逻辑）、`P7 收尾`（跨所有表的终局一致性）。
> - **软门禁（可成对、仍逐阶段查）**：`P3 主数据` 在 P2 通过后可与 `P4 单据核心` 连续交付；`P6 附件与隔离` 风险低，可贴着 P4/P5 走。软门禁允许上一阶段"通过"后立即着手下一阶段（省往返），但控制中枢仍逐阶段独立验收。
> - **基线状态**：`P0`、`P1` 已于 2026-10-31 验收通过，作为后续阶段起点；当前从 **P2** 开始。

| Phase | 范围 | 门禁 | 交付物 | 验收（指向开发规范） |
|---|---|---|---|---|
| **P0 脚手架** ✅已验收 | Django 项目 + settings + apps 骨架 + CI 预检 | 软 | 可 `runserver`/`check`；`precheck_sql` 命令 | §1、§2、§3 |
| **P1 迁移基线** ✅已验收 | `0001_initial` 应用 schema-v7 + grant；27 表 Models | 软 | 空库 G0 通过（27 业务表/14 函数/32 触发器） | §3、§9 |
| **P2 认证与 RBAC** | 会话族后端、JWT 签发、refresh/logout/撤销、`require_role`、B9 一条联查、`login_throttle` | **硬** | 登录/刷新/登出/撤销用例过；M1 授权断言过 | §4、§5、§9 |
| **P3 主数据** | tenants/departments/users 管理、catalog CRUD | 软（可接 P4） | HTMX CRUD + 租户内唯一约束 | §2、§6 |
| **P4 单据核心** | 入库/出库/报损/调整 CRUD + 提交/审批 + 锁顺序 + 幂等 | 软（可接 P3） | 负向用例 + 单据流转正确 | §6、§7、§9 |
| **P5 库存与资产** | inventory 扣减、stock_ledger、asset_cards/events、资产编号分配 | **硬** | 对账 13 项内一致 | §7、§9 |
| **P6 附件与隔离** | attachments 上传/关联/撤回、对象存储、4 通路隔离测试 | 软（贴 P4/P5） | 4 通路拒绝跨租户 | §8 |
| **P7 收尾** | reconcile/cleanup/强制改密/文档 | **硬** | 全 CI 绿 | §9 |

> **当前进度（2026-10-31）**：`P0`、`P1` 已验收通过，`inventory-v4/django/` 已存在且为权威基线。你从 **P2** 起步，**扩展既有 `django/` 树，不要推翻重做 P0/P1**；技术栈、全局红线、交付物等其余条款不变。P0 整改项 `O2`（`.env.example` 中连接串配置键名误拼、应为 `DB_HOST`；见 P1 验收报告）由控制中枢闭环，无需你处理。

## 6. 验收门禁（CI）
- CI-1：`precheck_sql` 退出码 0（美元引号成对 + 语句终止）。
- G0：空库应用 `schema-v7.sql` → **27 业务表 / 14 函数 / 32 触发器**。注意 Django 会在 `public` 建 `django_migrations` 记账表（第 28 张 BASE TABLE），断言须 `AND table_name NOT LIKE 'django_%'`。
- M1：`grant-runtime.sql` + `verify-runtime.sql` 负向断言必须过（否则阻断）。
- 负向：`v7-tools/test-negative.sql` 21 项全 PASS。
- 对账：`reconcile.sql` 13 项内部一致性（不替代实物抽盘）。

## 7. 交付与自测要求
- 代码置于 `inventory-v4/django/`；**不得修改 `schema-v7.sql` 的 DDL 来源**；**不得引入 FastAPI/Vue3**；**不绕过 RunSQL 写 DDL**；**不省 Idempotency-Key**；**不省 B9 一条联查授权**。
- 自测可用运行中的 PG16：`PGHOST=/tmp PGPORT=55432 PGUSER=postgres`（实例在 `/tmp/pg16data`，端口 55432）。每 Phase 完成后回报验证数字与偏差。
- 汇报：文件清单 + 关键断言数字 + precheck 退出码 + verify-runtime 结果 + 任何偏离。
