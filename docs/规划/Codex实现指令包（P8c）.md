# Codex 实现指令包（P8c）：通用报修工单

> 阶段：P8c（**软门禁**）
> 日期：2026-11-01
> 来源蓝图：`docs/规划/P8c蓝图（报修工单）.md`（细化）+ 主蓝图 `docs/规划/P8增强方案（蓝图）.md`（M5）
> 执行者：ChatGPT Codex（用户侧外部 agent）
> 验收者：控制中枢（独立验收，出具《P8c 独立验收报告》）
> 配套宪法：`schema-v7.sql`（唯一 DDL 规则源）、`开发规范（Django+PG）.md`、P8a/P8b 已验收代码

---

## 0. 本阶段范围（仅 P8c / M5）
- **M5 通用报修工单**（新 app `apps/repair`）：教师上报 → admin 派单 → 状态跟踪 → 完成；含照片（`attachments` 复用）、双轨身份、状态机。
- 不在本阶段：M1/M2/M6（P8a）、M3/M4（P8b）。**严禁改动** P8a/P8b 已交付文件与 `procurement`/`notifications`/`inventory` 的既有语义（仅新增 `repair` 调用方与 `base.html` 导航项）。

---

## 1. 强约束（违反即判 REJECTED）
1. **SQL-first**：`repair_repairticket` DDL **手写 RunSQL 迁移**，禁止 `makemigrations --auto`；**先更新根 `schema-v7.sql`** 再写迁移；新增 1 表后同步更新 G0 期望值与快照。
2. **运行/迁移身份分离**：运行期写接口以 `inv_run` 执行（`require_runtime()` 守卫）；迁移/建表以 `inventory_app`；测试沿用 CI 配置（`postgres` 超级用户 + `DEBUG=true`）。
3. **禁止硬编码密钥/口令/域名**：通知/文案/URL 动态生成；密钥走环境变量。
4. **G0 同步**：新增 1 张表 → `apps/core/management/commands/verify_schema.py` 期望值由 `(29,14,32)` 改为 `(30,14,32)`（无新增函数/触发器，维持 14/32）；重算根 `schema-v7.sql` 指纹并逐字节同步 `django/sql/schema-v7-current.sql`，确保 `python scripts/sync_schema_snapshot.py --check` 返回 `fingerprint_valid:true / snapshot_matches:true`。
5. **照片复用红线**：报修照片**必须**经 `apps/attachments/services.upload(principal, key, file)`（或 API `POST /api/v1/attachments/`）落库，取回 `attachment_id` 存 `RepairTicket.photo_attachment_id`；**禁止**自建文件存储或 S3 直传逻辑。
6. **CI 门禁**：`manage.py test tests --no-input` 全绿（当前 229，新增后 ≥235）；在 `.github/workflows/ci.yml` 中 **`verify_p8b.py` 步骤之后**新增一步 `uv run python scripts/verify_p8c.py`（搜索 `verify_p8b.py` 锚点插入，避免行号漂移）；`ci.yml` 不被破坏。
7. **安全**：双轨匿名提交须 `name`+`employee_no` 留痕，**禁止**匿名写业务单；报修人只能查看本人工单（按 `reporter_user_id` 或 `reporter_name+tenant` 归属校验），跨用户/跨租户读取断言 403。

---

## 2. 现状复用清单（务必复用，禁止重写）
- **轻量用户**：`apps/inventory/pickup.ensure_lightweight_user(tenant_id, name, employee_no)`（@ `pickup.py:81`）——报修匿名提交直接复用。
- **照片**：`apps/attachments/services.upload(principal, key, file, expected_sha256="")` 返回 `{"attachment_id": ...}`（@ `apps/attachments/services.py:87`）；下载 `download(p, attachment_id)`（@ `:26`）；前端上传走 `POST /api/v1/attachments/`。
- **原子/幂等/审计基座**：`shared.idempotency.service.execute` + `validate_key`（参照 `pickup.py` 示范）、`apps.core.services.audit`、`shared.db.runtime.require_runtime`、`shared.rbac.permission.require_role`。
- **仓储/ID 生成**：单号用 `f"RP-{uuid4().hex}"`（与 P8a `PK-`、P8b `RQ-` 同风格）。
- **模板/导航**：`templates/base.html`（导航分支 `@ 第 7–15 行含 `{% if principal.role == 'admin' %}`）；移动页沿用 `templates/pickup/` 轻量 server-rendered 风格（viewport + Bootstrap5，微信浏览器兼容）。
- **测试范式**：`tests/` 下用例（如 `test_pickup.py`、`order_fixtures.py` 用 `SET LOCAL ROLE inventory_runtime`），新增测试照此风格。

---

## 3. 数据模型变更（SQL-first）

### 3.1 根 `schema-v7.sql`（唯一规则源）
新增章节（DDL 与索引），保持其为整库规则源：
```sql
-- repair_repairticket
CREATE TABLE repair_repairticket (
  id bigserial PRIMARY KEY,
  tenant_id bigint NOT NULL,
  ticket_no text NOT NULL,
  reporter_user_id bigint NULL,
  reporter_name text NOT NULL,
  reporter_no text NOT NULL,
  target_type text NOT NULL,
  target_id bigint NULL,
  description text NOT NULL,
  photo_attachment_id bigint NULL,
  priority text NOT NULL DEFAULT 'normal',
  status text NOT NULL DEFAULT 'pending',
  assigned_to bigint NULL,
  reject_reason text NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_repair_tenant_no ON repair_repairticket(tenant_id, ticket_no);
CREATE INDEX ix_repair_tenant_reporter ON repair_repairticket(tenant_id, reporter_user_id);
CREATE INDEX ix_repair_tenant_status ON repair_repairticket(tenant_id, status);
```
（`tenant_id` 复合外键 + 服务端过滤沿用宪法 D3；本阶段不新增函数/触发器，G0 维持 14/32。）

### 3.2 迁移文件（仅新增表，内容从 3.1 逐字摘录）
- `apps/repair/migrations/0001_initial.py`：`operations=[migrations.RunSQL(<repair_repairticket DDL 全文>)]`，**不要**用 `CreateModel`（严守 SQL-first）。
- Model 定义：`apps/repair/models.py`（`RepairTicket`，`managed=False`、`db_table="repair_repairticket"`，`tenant_id`/`target_id`/`assigned_to`/`photo_attachment_id` 用 `BigIntegerField`）。

### 3.3 G0 与快照同步
- 改 `apps/core/management/commands/verify_schema.py` 期望元组：`(29,14,32)` → `(30,14,32)`，同步更新其文案。
- 重算根 `schema-v7.sql` 头部指纹（SHA-256 前 8 位，与 `scripts/sync_schema_snapshot.py` 口径一致），将根文件逐字节复制到 `django/sql/schema-v7-current.sql`；跑 `python scripts/sync_schema_snapshot.py --check` 必须 `fingerprint_valid:true / snapshot_matches:true`。

---

## 4. 教师端报修提交（双轨身份）
- 路由（新建 `apps/repair/urls.py` 并在 `campus_inventory/urls.py` 加 `path("", include("apps.repair.urls"))`，**置于 `inventory` include 之前**以避免路由覆盖问题）：
  - `GET /s/repair/new/` → `html_repair_new`（server-rendered 移动页；支持 `?asset=<token>` 或 `?target_type=...&target_id=...` 预填；含照片上传控件）。
  - `POST /api/v1/repair/` → `api_repair_create`。
- 视图 `api_repair_create(request)`：
  1. 仅 `POST` + JSON；校验 `Idempotency-Key`（`validate_key`）。
  2. body：`target_type`(必填, ∈ {asset,item,location})、`target_id`(可空，location 可空)、`description`(非空)、`priority`(可选, ∈ {low,normal,high}, 默认 normal)、`photo_attachment_id`(可空)、`name`、`employee_no`（匿名时必填）。
  3. **照片先传**：若前端分两步，先调 `attachments` 上传拿 `attachment_id` 再随 body 提交；若单请求带文件，视图内调 `attachments.services.upload` 取 id。
  4. **身份双轨**：`request.principal` 存在且 `role in ("admin","user")` → `reporter_user_id`=其 user_id；否则要求 `name`+`employee_no` → 调 `ensure_lightweight_user(tenant_id, name, employee_no)`。
  5. 调用 `repair_submit(principal, target_type, target_id, description, priority, photo_attachment_id, reporter_id, name, no, key)`（见 §5），`status=pending`。
  6. 返回 `{document, business_refs}`，设 `Idempotency-Replayed` 头。

---

## 5. 服务层（复用幂等/审计基座）
新增 `apps/repair/services.py`：
```python
OPERATION = "repair.ticket.create"

def repair_submit(principal, target_type, target_id, description, priority,
                 photo_attachment_id, reporter_id, name, no, key):
    require_runtime()
    validate_key(key)
    p = orders.identity(principal) if principal is not None else None
    tenant_id = p.tenant_id if p else tenant_id_from_target(target_type, target_id)
    digest = {"target_type": target_type, "target_id": target_id,
              "identity": "authenticated" if p else "unverified"}
    def work():
        ticket = RepairTicket.objects.create(
            tenant_id=tenant_id, ticket_no="RP-"+uuid4().hex,
            reporter_user_id=reporter_id, reporter_name=name, reporter_no=no,
            target_type=target_type, target_id=target_id, description=description,
            photo_attachment_id=photo_attachment_id, priority=priority or "normal",
            status="pending",
        )
        core.audit(p or anon_principal, "repair", ticket.pk, {},
                   {"action":"create","status":"pending"}, "报修工单")
        return {"document": record(ticket),
                "business_refs":[{"type":"repair","id":ticket.pk}]}
    return execute(tenant_id, (p.user_id if p else 0), OPERATION, key, digest, work)
```
（`tenant_id_from_target` / `anon_principal` 处理轻量路径；匿名路径须先 `ensure_lightweight_user` 并在同一事务内建单，参照 `pickup.submit` @ `pickup.py:317`。）

---

## 6. 处理端（admin 派单 + 状态机）
- 路由：
  - `GET /manage/repairs/` → `html_repairs_admin`（`@require_role("admin")`）：列表（状态筛选 pending/dispatched/repairing/done/cancelled）。
  - `POST /api/v1/repair/<int:pk>/dispatch/` → `api_repair_dispatch`（`@require_role("admin")`）：body `assigned_to`(user id) → `status=dispatched`。
  - `POST /api/v1/repair/<int:pk>/status/` → `api_repair_status`（`@require_role("admin")`）：body `action` ∈ {start,done,cancel}。
- 状态机（`repair_transition` 服务，`OPERATION` 区分）：
  - `pending → dispatched`（dispatch）
  - `dispatched → repairing`（start）
  - `repairing → done`（done）
  - 任意非终态 → `cancelled`（cancel）；`done`/`cancelled` 为终态，再变更 `BusinessError("FROZEN_DOCUMENT", …, 409)`。
  - 非法跃迁（如 pending 直接 done）同样 409。
- 所有变更经 `execute(...)` 幂等；返回更新后记录；写 `core.audit`。

### 6.1 可选钩子（默认关闭，非阻塞）
当 `target_type='asset'` 且工单 `done` 时，如需把资产状态同步回 `in_stock`，可在 `done` 处理末尾**可选地**调用 `apps.inventory.assets.repair(admin_principal, target_id, "repair_finish", key, {"reason": "工单完成"})`——但仅当该资产此前经 `repair_start` 进入 `repair` 态才有意义。**默认不启用**；若启用须在同一事务/幂等上下文内且校验资产当前态，避免与既有资产开关冲突。指令包默认实现不含此联动。

---

## 7. 报修人查看（本人工单）
- 路由：`GET /s/repairs/` → `html_repairs_user`（`@require_role("admin","user")`）：`user` 仅 `filter(reporter_user_id=…)`（正式）或 `filter(reporter_name=…, reporter_no=…)`（轻量）；`admin` 见全部。

---

## 8. 测试（新增，沿用 CI 配置）
新增 `tests/test_repair.py`，覆盖验收项（配置：`DB_USER=postgres`、`DEBUG=true`、`DB_HOST/PORT` 指向测试 PG）：
- **V-repair-1**：构造 `user` principal，`POST /api/v1/repair/` → 断言 `RepairTicket.status=pending`、`reporter_name/no` 留痕、`photo_attachment_id` 非空（先上传一张测试附件取 id）；匿名（body 含 `name`+`employee_no`）→ 轻量用户复用、单已建；匿名缺 `name`/`employee_no` 断言 422。
- **V-repair-2**：admin `dispatch`（`assigned_to`）→ `status=dispatched`；`status` action=start → `repairing`；action=done → `done`；pending 直接 done 断言 409；cancel 终止。
- **V-repair-3**：`user` `GET /s/repairs/` 见本人工单；跨用户读取他人工单断言 403；跨租户读取断言 403。
- **回归**：务必跑全量 `manage.py test tests --no-input`（当前 229 → ≥235 全绿）；`manage.py check --deploy` 0 问题；`verify_schema` G0 `(30,14,32)`；`sync_schema_snapshot --check` 通过。

---

## 9. 交付物清单（Codex 完成后须存在）
- `apps/repair/`（models.py、services.py、urls.py、views.py、`migrations/0001_initial.py` RunSQL）
- 根 `schema-v7.sql`（新增 `repair_repairticket` DDL 章节 + 重算指纹）
- `django/sql/schema-v7-current.sql`（与根逐字节一致）
- `apps/core/management/commands/verify_schema.py`（G0 → `(30,14,32)`）
- `campus_inventory/settings.py`（INSTALLED_APPS 注册 `repair` + `campus_inventory/urls.py` include）
- `templates/base.html`（「报修」「报修处理」入口）、`templates/repair/*`
- `scripts/verify_p8c.py`（V-repair 专项校验，参照 `scripts/verify_p8a.py`/`verify_p8b.py` 风格）
- `.github/workflows/ci.yml`（`verify_p8b.py` 后新增 `verify_p8c.py` 步骤）
- `tests/test_repair.py`
- **不要改动**：既有 `apps/inventory/`（含已清掉的死路由）、`procurement`/`notifications`/P8a 已交付文件、`0001_initial` 基线迁移。

---

## 10. 验收口径（中枢独立验收，软门禁）
- 代码走查：是否严守 §1 七条强约束（尤其 SQL-first、G0 同步、照片复用 `attachments`、双轨留痕、状态机/权限校验）。
- 功能：V-repair-1~3 全部通过；全量测试 ≥235 绿；`verify_schema` G0 = `(30,14,32)`；`sync_schema_snapshot --check` 通过。
- 安全：匿名路径无权限绕过；工单不可跨用户/跨租户读取。
- 不通过则出具整改清单，返回 Codex 重修；通过则中枢出《P8c 独立验收报告》并盖软门禁章。

---

## 11. 提交与推送（Codex 不负责，由中枢/用户执行）
Codex 交付后**不要**自行 push。由中枢复核并 `commit + push` 到 `origin/main`（与 P8a/P8b 一并，遵循用户既定策略"P8a+b+c 全做完再统一提交"），随后中枢跑独立验收。

---

## 12. 已知限制（须写入《使用手册》，不阻塞验收）
- 轻量免密报修人的工单按「姓名+工号+租户」关联；后续用同一工号登录无法自动并回历史工单。
- 资产维修开关（`assets.repair`）与工单默认不联动（§6.1 可选钩子默认关闭）。
- 派单 `assigned_to` 指向 `user`，本阶段不强制 `maintainer` 角色。
