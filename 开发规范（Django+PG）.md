# 开发规范（Django + PostgreSQL）—— 学校总务处出入库管理系统

> **文档定位**：本规范是《实施计划 v7（最终版）》的**唯一开发契约**。计划已 `Ratified`（D1 已裁决 = A1-b，归入校园 SaaS 的 Django 栈）。codex（实现子代理）的所有产出必须以本规范为验收标准。
>
> **裁决基线（2026-10-31）**：
> - 技术栈维持项目宪法 `ARCHITECTURE.md`：**Django 5 + psycopg3 + PostgreSQL 16 + Celery/Redis + Django Templates / Alpine.js / HTMX / Bootstrap 5**。
> - **明确排除**（本次裁决不适用）：FastAPI、Vue3、SQLite(WAL)、SwiftUI 桌面小组件。新指令中的 JWT / RBAC 设计思路已转化为 Django 形态（见 §4、§5）。
> - 数据库基线：`schema-v7.sql`（30 表 / 14 函数 / 32 触发器，已于真实 PG16.15 实测 G0 通过）。

---

## 0. 全局约束（红线，来自用户指令 + 计划，codex 不得违反）

| 约束 | 含义 | 落地位置 |
|---|---|---|
| PG WAL | PostgreSQL 默认即 WAL；生产开启 `archive_mode` 做 PITR，RPO≤15min / RTO≤4h | 部署配置（非代码）；`reconcile.sql` 演练 |
| 敏感字段应用层加密 | 指定 PII 列经 `crypto.FernetField` 在应用层加密，密钥来自 env/secret manager，不入库 | `apps/shared/crypto/` |
| Idempotency-Key | 所有写命令必带；按 `租户+操作者+operation+key` 键域去重（表 `operation_requests`） | 中间件 + 服务层（§8） |
| 租户隔离 | 复合外键 `(tenant_id,…)` + 服务端过滤双保留；测试覆盖 4 通路 | `apps/shared/tenant/`（§8） |
| 运行/迁移账号分离 | `inventory_runtime` 为 NOLOGIN 角色，非 owner/超级用户，无 TRUNCATE/TRIGGER/schema CREATE | `v7-tools/grant-runtime.sql` |

---

## 1. 技术栈与运行环境

- **语言/框架**：Python 3.13 + Django 5。
- **驱动**：`psycopg[binary,pool]`（psycopg3），`DATABASES` 的 `ENGINE` 用 `django.db.backends.postgresql`。
- **数据库**：PostgreSQL 16（开发/CI/生产同一主版本，补丁版本经部署锁文件记录）。
- **异步任务**：Celery + Redis（导入、对象存储清理、会话族清理、对账调度）。
- **前端**：Django Templates + Alpine.js + HTMX + Bootstrap 5（**非 SPA、非 Vue3**）。
- **项目归属**：校园 SaaS monorepo 的 `apps/asset`（总务/资产）。若暂以独立仓库开发，项目名 `campus_inventory`，但必须可整体并入 `apps/asset`。

### settings 要点
- `SECRET_KEY`、`DB_` 连接串、`REDIS_URL`、加密密钥均来自环境变量 / secret manager，**不进版本库**。
- `DEBUG` 按环境；生产 `ALLOWED_HOSTS` 白名单；`CSRF_TRUSTED_ORIGINS` + 自定义请求头校验（refresh/logout 检查 Origin 白名单）。
- 启用 CSP，**禁止内联脚本**（Alpine 用 `x-data` 外部脚本或 `django-csp` 放行 nonce）；用户输入一律转义。

---

## 2. 项目布局（apps 拆分）

```
campus_inventory/                # 或 apps/asset/ 下的总务子域
├── campus_inventory/            # 项目配置（settings / urls / wsgi）
├── apps/
│   ├── core/                    # tenants, departments, users,
│   │                            #   auth_sessions, login_throttle,
│   │                            #   operation_requests, file_jobs
│   ├── catalog/                 # locations, categories, items, suppliers
│   ├── inventory/               # inbound_*, outbound_*, scrap_*,
│   │                            #   adjustment_*, inventory,
│   │                            #   stock_ledger, asset_cards, asset_events
│   ├── attachments/             # attachments（对象存储元数据）
│   ├── procurement/             # 请购（P8b）：requisition 模型、审批与入库联动
│   ├── notifications/           # 站内通知（P8b）：notification 模型与铃铛角标
│   ├── repair/                  # 报修工单（P8c）：repair_ticket 模型与派单
│   └── reconcile/               # 包装 reconcile.sql 的管理命令
└── shared/                      # 跨域复用
    ├── rbac/                    # 角色、权限、require_role
    ├── auth/                    # 会话族后端、JWT 签发
    ├── idempotency/             # Idempotency-Key 中间件/helper
    ├── crypto/                  # Fernet 字段加密
    └── tenant/                  # 租户上下文中间件 + 隔离过滤
```

- 复合外键（`tenant_id, id, …`）在 Model 中用 `models.ForeignObject` 仅作 ORM 关联，**不生成约束/索引**（Django 内部 API，不受弃用策略保护）。
- 触发器、函数、角色授权**禁止**由 `makemigrations --autogenerate` 生成；每次变更手写 `RunSQL` 并同步 `verify-runtime.sql`。

---

## 3. 数据库与迁移策略（关键）

1. **`schema-v7.sql` 是 SQL 规则唯一来源**。Django Models 只作 ORM 读写外壳，**不负责 DDL**。
2. 初始迁移 `core/migrations/0001_initial.py`：
   - `migrations.RunSQL` 读取并**整文件应用** `schema-v7.sql`（单事务 COMMIT）。
   - 随后 `RunSQL` 应用 `v7-tools/grant-runtime.sql`（运行时角色授权）。
3. 后续变更：扩展 → 迁移 → 收缩；破坏性变更**不承诺无损 downgrade**；已执行业务的纠正一律走**冲正单**。
4. 提供管理命令（CI / 运维）：
   - `manage.py precheck_sql` —— 包装 `v7-tools/precheck-sql.py`（CI 第 1 步，美元引号成对 + 语句终止）。
   - `manage.py apply_schema` —— 幂等应用基线（开发/测试库）。
   - `manage.py verify_runtime` —— 包装 `v7-tools/verify-runtime.sql`（负向断言）。
   - `manage.py reconcile` —— 包装 `v7-tools/reconcile.sql`（13 项内部一致性）。

---

## 4. 认证与登录流程（JWT + 会话族）

对齐计划 §2 的**会话族模型**（`auth_sessions`：`family_id`、`rotated_at`、`revoked_at`、`parent_session_id`、`revoked_reason`）。

### 4.1 两套凭证
- **Django session**：HTMX 服务端渲染主用。
- **JWT access token**：`/api/v1` JSON 表面（程序化 / 未来客户端）短时效凭证。
- **refresh**：httpOnly / Secure / SameSite=Strict / Path=/api/v1/auth cookie。

### 4.2 登录时序
1. `POST /api/v1/auth/login` `{tenant_code, username, password, idempotency_key}`（HTMX 同逻辑走 `/auth/login`）。
2. 服务：校验**租户状态**（active、未过期）+ 用户名租户内唯一 + **bcrypt**（cost≥10）验证。
   - 失败计入 `login_throttle`（5 次失败锁 15 分钟，**不争用用户业务行锁**）；失败提示**不泄漏账号是否存在**；网关 IP 限流。
3. 创建会话族：插入 `auth_sessions(family_id, …)`，返回 access JWT + 设置 refresh cookie。
4. 响应：`{access_token, user:{role, tenant}, force_change_password}`。

### 4.3 刷新 / 登出 / 撤销
- **刷新**：`POST /api/v1/auth/refresh` → 旋转族（新行，`rotated_at` 标记旧行）返回新 access；旧 token 10s 内重现返回 `REFRESH_ALREADY_ROTATED`；超窗重现**撤销整族**。
- **登出**：`POST /api/v1/auth/logout` → 事务内 `revoked_at` 整族 + 清 `parent_session_id`。
- **撤销**（改密 / 禁用 / 重置密码）：事务内 `revoked_at` 该用户**全部会话族**。

### 4.4 授权检查（B9 硬约束）
每次请求在**一条联查**中完成：`auth_sessions JOIN users JOIN tenants`（按族聚合），检查 **族未撤销 + 用户 active + 租户 active/未过期**。**不设 `auth_version`**（族撤销语义等价于版本跳跃）。
- 允许缓存，键 = **族 ID**；失效点：族撤销、改密、用户启停、租户状态/到期变更、管理员强制下线；缓存**最长 60s 且事件时主动失效**；不缓存则每次联查（M2 压测决定）。
- **强制改密**：新建/重置后首次登录要求改密。

---

## 5. RBAC 权限分级

| 角色 | 权限 |
|---|---|
| `admin` | 租户管理员：开通/禁用用户、配置自批开关、导出、资产编号分配、审批 |
| `user` | 普通操作员：单据录入/提交、查询 |

- **自批禁止**：`admin` 提交的单据不得自审（服务层断言 + 审计留痕）。
- **运行/迁移账号分离**：`inventory_runtime` NOLOGIN 角色（非 owner、非超级用户、无角色继承、无 TRIGGER/TRUNCATE、无 schema CREATE）；迁移用独立账号。授权经 `grant-runtime.sql`，断言经 `verify-runtime.sql`（含 `tenants` 列级 + 负向断言，M1 硬性阻断）。
- **导出按角色分级授权**（admin 可导出，user 受限）。
- **实现**：`shared/rbac/permission.py` 提供 `require_role(*roles)` 装饰器/中间件；视图层统一经 `require_role` + §4.4 会话族联查。

---

## 6. 路由 / 视图结构（Django，非 FastAPI/Vue3）

- URL 分组：`apps/<app>/urls.py` → 项目 `urls.py` `include`。
  - `/` 仪表盘（HTMX 局部刷新）
  - `/auth/` 登录 / 登出 / 刷新 / 改密（HTMX + `/api/v1/auth` JSON）
  - `/catalog/items|locations|categories|suppliers/` CRUD（HTMX 表单 + 列表局部）
  - `/inbound/`、`/outbound/`、`/scrap/`、`/adjustment/` 单据 CRUD + 提交/审批
  - `/stock/`、`/assets/` 库存/资产查询
  - `/attachments/` 上传/关联/撤回
  - `/api/v1/…` JSON 表面（JWT 保护）供程序化访问
- **视图**：FBV + `@require_role`；表单用 Django Forms；响应用 HTMX（部分 HTML 片段）+ Bootstrap5 + Alpine 交互。JSON 表面可用轻量序列化（或仅 `/api/v1` 引入 DRF，**可选**）。
- **统一锁顺序**（计划 §5）：操作键 → 租户行（管理员人数、自批/审批、资产编号分配）→ 按 ID 排序用户行 → 目标单据 → 按类型/ID 排序来源明细 → 按 ID 排序资产卡 → 按完整键排序库存行。资产编号 `UPDATE tenants SET next_asset_number=next_asset_number+n RETURNING` 一次分配，**禁止 MAX+1**。

---

## 7. 核心工作流（服务层职责）

| 域 | 要点 |
|---|---|
| 入库 | 头(`inbound_orders`)+明细(`inbound_items`)同事务，xid 守卫；`draft→approved→completed`；completed 差额原因必填 + 卡数校验 |
| 出库 | `issue`/`requisition`；`actual_qty`/`shortfall_reason`；`location`/`recipient` 冻结后不可改 |
| 报损 | `loss`/`scrap`/`recovery`/`found`；`source_loss` 必须指向 `disposal_type='loss'` 同卡；`scrap_items` 四列唯一 + 生成列派生类型；证据（`scrap_attachments`）pending 未决策可撤回，决策后 append-only（`evidence_line_guard`） |
| 调整 | `adjustment_orders`/`adjustment_items` |
| 库存 | `inventory` 行 `version` 自增 + 维度不可变；扣减条件更新 + 影响行数检查；`stock_ledger` 双流水（consumable） |
| 资产 | `asset_cards`（`asset_card_guard` 禁删 + 核心字段不可改）；`asset_events` 同卡 + 类型匹配（`scrap_event_type` 生成列 + 四列外键）；`found` 部分唯一索引兜底一次找回 |

**服务层保证项**（计划 §1，未下沉 DB）：物资/源单业务类型一致、累计退还额度、租户与角色授权、质量双流水与建卡数、卡片状态变化同步事件、对象存储权限、pending 单不追加业务明细。直接 UPDATE 卡片是否同事务配事件，本版以服务断言 + 对账发现（**延迟事件约束未实现，残余风险，M3 必须测试**）。

---

## 8. 全局约束落地细则

- **PG WAL**：默认开启；生产 `archive_mode=on` + 每日备份（保留 30 天、独立权限）。试点前**实际恢复到指定时点**并跑 `reconcile.sql`，记录耗时与结果。
- **敏感字段应用层加密**：当前敏感数据 = 凭证（bcrypt）+ token（仅 refresh cookie，业务表不落明文 token）。若未来补采联系方式/证件号，经 `shared/crypto.FernetField` 加密，密钥来自 env/secret manager。
- **Idempotency-Key**：所有写命令必带；服务端按 `租户+操作者+operation+key` 键域（`operation_requests`）去重；同操作者同键同摘要重放返回已处理结果，不同摘要 409；90 天清 `response`（注意 `created_at` 为占位、`completed_at` 默认非业务完成时间，清理以**最后更新**为准）。
- **租户隔离**：复合外键 + 服务端过滤双保留；隔离测试覆盖 **原生 SQL、后台任务(Celery)、批量导入、对象存储下载** 四条通路。
- **附件**：`object_key` 租户内唯一（`UNIQUE(tenant_id,object_key)`）；服务强制键带租户前缀；上传完成由服务校验内容/摘要/归属再登记；过保留期且无 `legal_hold` 才可删对象并标 `deleted_at`（对象删除非事务，需可重试清理任务）；`legal_hold` 只能 false→true（解除走受控运维）。

---

## 9. 测试与验收门禁（CI）

| 阶段 | 命令 / 脚本 | 通过标准 |
|---|---|---|
| CI-1 预检 | `precheck-sql.py` | 美元引号成对、语句终止，退出码 0 |
| G0 建库 | 应用 `schema-v7.sql` | 单事务 COMMIT、0 报错；**30 表 / 14 函数 / 32 触发器**断言全等 |
| M1 授权 | `grant-runtime.sql` + `verify-runtime.sql` | 负向断言全过（否则阻断） |
| 负向用例 | `test-negative.sql` | 21 项全 PASS |
| 对账 | `reconcile.sql` | 13 项内部一致性（不替代实物抽盘） |
| 租户隔离 | 4 通路测试 | 全部拒绝跨租户读写/文件 |
| 会话族 | 锁定/改密/撤销/刷新丢响应 | 全过 |

---

## 10. 与 codex 的实现契约（原子任务拆分 + 分阶段门禁）

> codex（实现子代理）按以下 Phase 顺序交付。**采用"一个阶段一个阶段 做-查-盖-下一阶段"的硬门禁节奏**（控制中枢 2026-10-31 裁决）：每个 Phase 完成并**经控制中枢独立复现 + 逻辑走查 + 规范验收通过**后，才放行下一 Phase；未通过的整改项闭环后方可继续。本节奏的依据是本项目的安全关键性——P2 是授权地基、P5 是不可变台账、P7 是跨表终局一致性；若"全部做完再查"，偏差会被埋进数千行 diff 难以逐条走查，且下游阶段会在错误地基上叠加。
>
> **门禁分级**（按安全 / 复杂度权重，非所有阶段等价）：
> - **硬门禁（必须单独查透、不与其他阶段合并验收）**：`P2 认证与 RBAC`（全系统授权地基）、`P5 库存与资产`（不可变台账与 `inventory.version` 版本逻辑）、`P7 收尾`（跨所有表的终局一致性）。
> - **软门禁（可成对、仍逐阶段查）**：`P3 主数据` 在 P2 通过后可与 `P4 单据核心` 连续交付；`P6 附件与隔离` 风险低，可贴着 P4/P5 走。软门禁允许 Codex 在上一阶段"通过"后立即着手下一阶段（省往返），但控制中枢仍逐阶段独立验收。
>
> **运行节奏**：Codex 做当前阶段 → 控制中枢独立复现 + 逻辑走查 → 出"通过 / 整改清单" → Codex 整改 → 复验通过 → 放行下一阶段。Codex **不得**在上一阶段门禁未通过前开始下一阶段的业务代码；上一阶段一过即可立即接续，无需等待额外的"开工批准"。
>
> **codex 禁止**：修改 `schema-v7.sql` 的 DDL 来源、引入 FastAPI/Vue3、绕过 `RunSQL` 写 DDL、省略 `Idempotency-Key`、省略 §4.4 一条联查授权。

| Phase | 范围 | 门禁 | codex 交付物 | 验收标准（指向本文） |
|---|---|---|---|---|
| **P0 脚手架** ✅已验收 | Django 项目 + settings + PG 连接 + apps 骨架 + CI 预检 | 软 | 可 `manage.py runserver`、CI-1 通过 | §1、§2、§3 管理命令 |
| **P1 迁移与基线** ✅已验收 | `0001_initial` 应用 `schema-v7.sql` + `grant-runtime.sql`；Models（含 `ForeignObject` 复合外键） | 软 | 空库 G0 通过（27/14/32） | §3、§9 G0 |
| **P2 认证与 RBAC** | 会话族后端、JWT 签发、refresh/logout/撤销、`require_role`、B9 一条联查、`login_throttle` | **硬** | 登录/刷新/登出/撤销用例全过；M1 授权断言过 | §4、§5、§9 M1 |
| **P3 主数据** | tenants/departments/users 管理、catalog（items/locations/categories/suppliers）CRUD | 软（可接 P4） | HTMX CRUD + 租户内唯一约束 | §2、§6 |
| **P4 单据核心** | 入库 / 出库 / 报损 / 调整 单据 CRUD + 提交/审批 + 服务层锁顺序 + Idempotency | 软（可接 P3） | 负向用例 + 单据流转正确 | §6、§7、§9 负向 |
| **P5 库存与资产** | inventory 扣减、stock_ledger、asset_cards/asset_events、资产编号分配 | **硬** | 对账 13 项内一致 | §7、§9 对账 |
| **P6 附件与隔离** | attachments 上传/关联/撤回、对象存储、4 通路隔离测试 | 软（贴 P4/P5） | 4 通路拒绝跨租户 | §8 |
| **P7 收尾** | reconcile 命令、cleanup-sessions 任务、强制改密流程、文档 | **硬** | 全 CI 绿 | §9 |

> **P8 增强（P8a / P8b / P8c，已交付并通过验收）**：在 P0–P7 验收通过后，按本规范同一契约持续扩展——
> - **P8a 扫码领用 / 移动盘点 / 二维码标签**：复用 `inventory` 域，新增 `pickup.py` / `qr.py`（`items.qr_token` 经 SQL-first 增量 DDL + RunSQL 迁移，哈希闸门校验）；匿名轻量用户沿用 §4 双轨身份思路。
> - **P8b 请购 + 站内通知**：新增 `apps/procurement`、`apps/notifications`；请购审批后入库**强制复用** `orders.create(inbound)` → `posting.receive`，不手写库存 UPDATE，保障 §7 对账 13 项不崩。
> - **P8c 报修工单**：新增 `apps/repair`（`repair_repairticket` 表，模型 `managed=False`）；状态机 `pending→dispatched→repairing→done/cancelled`；与既有资产维修开关解耦。
> - **契约延续**：SQL-first（DDL 手写 + 迁移哈希闸门）、运行/迁移身份分离、G0 同步（基线由 `27/14/32` 升至 `30/14/32`）、CI 门禁（`verify_p8a/b/c`）均严格遵循本规范，未引入偏离。

---

## 11. 待办与未决（不阻塞开发）

- **D2** 学校决策 B1–B9（§10）于 M0 前回收。
- **D3** 隔离模型与 `ARCHITECTURE.md` 对齐（实现已落地复合外键+服务过滤）。
- **U1** `v5审核逐项答复与v6验收说明.md` 污染尾部重新生成归档（v7.2 复核未复现）。
