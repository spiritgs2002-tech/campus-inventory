# Codex 实现指令包（P8b）：请购申请 + 站内通知 + 入库联动

> 阶段：P8b（**硬门禁**）
> 日期：2026-11-01
> 来源蓝图：`docs/规划/P8b蓝图（请购+通知）.md`（细化）+ 主蓝图 `docs/规划/P8增强方案（蓝图）.md`
> 执行者：ChatGPT Codex（用户侧外部 agent）
> 验收者：控制中枢（独立验收，出具《P8b 独立验收报告》）
> 配套宪法：`schema-v7.sql`（唯一 DDL 规则源）、`开发规范（Django+PG）.md`、P8a 已验收代码

---

## 0. 本阶段范围（仅 P8b）
- **M3 请购申请 + 审批 + 采购入库联动**（新 app `apps/procurement`）。
- **M4 站内通知**（新 app `apps/notifications`）：铃铛 + 列表 + 已读 + 与 M3 入库联动触发。

> 不在本阶段：M5 报修工单（留待 P8c）；M1/M2/M6（P8a 已交付）。

---

## 1. 强约束（违反即判 REJECTED）
1. **SQL-first**：所有 DDL/索引**手写 RunSQL 迁移**，禁止 Django `makemigrations --auto`；所有结构变更**先更新根 `schema-v7.sql`** 再写迁移；新增 2 表后同步更新 G0 期望值与快照。
2. **运行/迁移身份分离**：运行期写接口以 `inv_run`（运行时成员）执行（`require_runtime()` 守卫）；迁移/建表以 `inventory_app`；测试沿用 CI 配置（`postgres` 超级用户 + `DEBUG=true`）。
3. **禁止硬编码密钥/口令/域名**：通知文案、URL 一律动态生成；密钥走环境变量。
4. **G0 同步**：新增 2 张表 → `apps/core/management/commands/verify_schema.py:26` 期望值由 `(27,14,32)` 改为 `(29,14,32)`（无新增函数/触发器时维持 14/32）；重算根 `schema-v7.sql` 指纹并逐字节同步 `django/sql/schema-v7-current.sql`，确保 `python scripts/sync_schema_snapshot.py --check` 返回 `fingerprint_valid:true / snapshot_matches:true`。
5. **入库联动复用红线**：审批通过后的「采购到货入库」**必须**经由 `apps/inventory/orders.create(kind="inbound", type="purchase", …)`（其内部 `posting.receive` @ `apps/inventory/posting.py:253` 增库存），**严禁**手写库存 `UPDATE`；否则破坏 `reconcile.sql` 13 项一致性。
6. **CI 门禁**：`manage.py test tests --no-input` 全绿（当前 200，新增后 ≥206）；在 `.github/workflows/ci.yml` 第 83 行 `verify_p8a.py` **之后**补一步 `uv run python scripts/verify_p8b.py`；`ci.yml` 不被破坏。
7. **安全**：双轨匿名提交仍须 `name`+`employee_no` 留痕，**禁止**匿名写业务单；通知已读 API 只能标记本人通知（按 `recipient_user_id` 或 `recipient_name+tenant` 归属校验）。

---

## 2. 现状复用清单（务必复用，禁止重写）
- **轻量用户**：`apps/inventory/pickup.ensure_lightweight_user(tenant_id, name, employee_no)`（@ `pickup.py:81`）——请购匿名提交直接复用，禁止重复实现。
- **入库写链路**：`apps/inventory/orders.create(principal, "inbound", "purchase", key, payload)`（`orders.py:376`）→ `posting.receive` 增库存；入库单 `operator_id` = 执行 fulfill 的 admin。
- **原子/幂等/审计基座**：`shared.idempotency.service.execute` + `validate_key`（`pickup.py` 已示范）、`apps.core.services.lock_order_context` / `operation_id` / `audit`、`shared.db.runtime.require_runtime`、`shared.rbac.permission.require_role`。
- **仓储/ID 生成**：单号用 `f"{prefix}-{uuid4().hex}"`（与 P8a `PK-` 同风格）。
- **模板/导航**：`templates/base.html`（导航 @ 第 7–15 行，含 `{% if principal.role == 'admin' %}` 分支）、移动页沿用 `templates/pickup/` 的轻量 server-rendered 风格（viewport + Bootstrap5，微信浏览器兼容）。
- **测试范式**：`tests/` 下用例（如 `test_pickup.py`、`order_fixtures.py` 用 `SET LOCAL ROLE inventory_runtime`），新增测试照此风格；入库增库存断言参考现有 inbound 测试。

---

## 3. 数据模型变更（SQL-first）

### 3.1 根 `schema-v7.sql`（唯一规则源）
新增两个章节（DDL 与索引），保持其为整库规则源：
```sql
-- procurement_requisition
CREATE TABLE procurement_requisition (
  id bigserial PRIMARY KEY,
  tenant_id bigint NOT NULL,
  order_no text NOT NULL,
  applicant_id bigint NULL,
  applicant_name text NOT NULL,
  applicant_no text NOT NULL,
  item_id bigint NOT NULL,
  qty numeric(14,3) NOT NULL,
  reason text NOT NULL,
  status text NOT NULL DEFAULT 'pending',
  approved_by bigint NULL,
  inbound_order_id bigint NULL,
  reject_reason text NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_req_tenant_no ON procurement_requisition(tenant_id, order_no);
CREATE INDEX ix_req_tenant_applicant ON procurement_requisition(tenant_id, applicant_id);
CREATE INDEX ix_req_tenant_status ON procurement_requisition(tenant_id, status);

-- notifications_notification
CREATE TABLE notifications_notification (
  id bigserial PRIMARY KEY,
  tenant_id bigint NOT NULL,
  recipient_user_id bigint NULL,
  recipient_name text NULL,
  type text NOT NULL,
  title text NOT NULL,
  body text NOT NULL,
  ref_type text NULL,
  ref_id bigint NULL,
  is_read boolean NOT NULL DEFAULT false,
  created_at timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX ix_notif_tenant_user ON notifications_notification(tenant_id, recipient_user_id, is_read) WHERE recipient_user_id IS NOT NULL;
CREATE INDEX ix_notif_tenant_name ON notifications_notification(tenant_id, recipient_name, is_read) WHERE recipient_name IS NOT NULL;
```
（`tenant_id` 复合外键 + 服务端过滤沿用宪法 D3；本阶段不新增函数/触发器，故 G0 函数/触发器维持 14/32。）

### 3.2 迁移文件（仅新增表，内容从 3.1 逐字摘录）
- `apps/procurement/migrations/0001_initial.py`：`operations=[migrations.RunSQL(<procurement_requisition DDL 全文>)]`，**不要**用 `CreateModel`（严守 SQL-first）。
- `apps/notifications/migrations/0001_initial.py`：同上，仅 `notifications_notification` DDL。
- Model 定义：`apps/procurement/models.py`（`Requisition`，`managed=False` 或仅 ORM 外壳，`tenant_id`/`item_id` 用 `BigIntegerField`）、`apps/notifications/models.py`（`Notification`，同写法）。

### 3.3 G0 与快照同步
- 改 `apps/core/management/commands/verify_schema.py:26`：`if counts != (27, 14, 32):` → `(29, 14, 32)`；并同步更新该文件第 30 行文案。
- 重算根 `schema-v7.sql` 头部指纹（SHA-256 前 8 位，与 `scripts/sync_schema_snapshot.py` 口径一致），将根文件逐字节复制到 `django/sql/schema-v7-current.sql`；跑 `python scripts/sync_schema_snapshot.py --check` 必须 `fingerprint_valid:true / snapshot_matches:true`。

---

## 4. M3 请购申请（app `apps/procurement`）

### 4.1 提交（双轨身份）
- 路由（新建 `apps/procurement/urls.py` 并在 `campus_inventory/urls.py` 加 `path("", include("apps.procurement.urls"))`）：
  - `GET /s/requisition/new/` → `html_requisition_new`（server-rendered 移动页，支持 `?item=<pk>` 预填）。
  - `POST /api/v1/requisition/` → `api_requisition_create`。
- 视图 `api_requisition_create(request)`：
  1. 仅 `POST` + JSON；校验 `Idempotency-Key`（`validate_key`）。
  2. body：`item_id`(必填)、`qty`(>0)、`reason`(非空)、`name`、`employee_no`（匿名时必填）。
  3. **身份双轨**：`request.principal` 存在且 `role in ("admin","user")` → 用会话身份（`applicant_id`=其 user_id）；否则要求 `name`+`employee_no` → 调 `ensure_lightweight_user(tenant_id, name, employee_no)` 得轻量用户。
  4. 调用 `requisition_submit(principal, item_id, qty, reason, applicant_id, name, no, key)`（见 4.2），`status=pending`。
  5. 返回 `{document, business_refs}`，设 `Idempotency-Replayed` 头。

### 4.2 服务层（复用幂等/审计基座）
新增 `apps/procurement/services.py`：
```python
OPERATION = "procurement.requisition.create"

def requisition_submit(principal, item_id, qty, reason, applicant_id, name, no, key):
    require_runtime()
    validate_key(key)
    p = orders.identity(principal) if principal is not None else None
    tenant_id = p.tenant_id if p else tenant_id_from_item(item_id)
    digest = {"item_id": item_id, "qty": format(qty.normalize(),"f"),
              "identity": "authenticated" if p else "unverified"}
    def work():
        # 复合外键隔离：所有查询带 tenant_id
        requisition = Requisition.objects.create(
            tenant_id=tenant_id, order_no="RQ-"+uuid4().hex,
            applicant_id=applicant_id, applicant_name=name, applicant_no=no,
            item_id=item_id, qty=qty, reason=reason, status="pending",
        )
        core.audit(p or anon_principal, "requisition", requisition.pk, {},
                  {"action":"create","status":"pending"}, "请购申请")
        return {"document": record(requisition),
                "business_refs":[{"type":"requisition","id":requisition.pk}]}
    return execute(tenant_id, (p.user_id if p else 0), OPERATION, key, digest, work)
```
（`tenant_id_from_item` / `anon_principal` 处理轻量路径；匿名路径须先 `ensure_lightweight_user` 并在同一事务内建单，参照 `pickup.submit` @ `pickup.py:317`。）

### 4.3 审批（admin）
- 路由：`POST /api/v1/requisition/<int:pk>/decide/` → `api_requisition_decide`（`@require_role("admin")`）。
- 服务 `decide(principal, pk, action, key, reason)`（`OPERATION="procurement.requisition.decide"`）：
  - 租户内取 `Requisition`（`status=pending`），否则 `BusinessError("FROZEN_DOCUMENT", …, 409)`。
  - `approve`：`status=approved`、`approved_by=principal.user_id`。
  - `reject`：`reason` 必填且 ≤2000 → `status=rejected`、`reject_reason=reason`；并建「申请被驳回」通知（见 §6）。
  - 经 `execute(...)` 幂等；返回更新后记录。
- 列表（admin）：`GET /manage/requisitions/` → `html_requisitions_admin`（含 pending/历史，状态筛选）。

### 4.4 入库联动（复用 orders.create，硬约束 §1.5）
- 路由：`POST /api/v1/requisition/<int:pk>/fulfill/` → `api_requisition_fulfill`（`@require_role("admin")`）。
- 服务 `fulfill(principal, pk, key, location_id, quality, supplier_id=None)`（`OPERATION="procurement.requisition.fulfill"`）：
  ```python
  def work():
      req = Requisition.objects.select_for_update(no_key=True).get(
          tenant_id=p.tenant_id, pk=pk, status="approved")
      # 复用既有入库写链路（posting.receive 增库存），禁止手写库存 UPDATE
      payload = {
          "type": "purchase",
          "supplier_id": supplier_id,
          "remark": f"请购到货入库 · 关联申请 {req.order_no}",
          "lines": [{"item_id": req.item_id, "quantity": req.qty,
                     "location_id": location_id, "quality": quality}],
      }
      result = orders.create(p, "inbound", "purchase", key, payload)
      inbound_id = result["business_refs"][0]["id"]
      req.status = "purchased"; req.inbound_order_id = inbound_id
      req.approved_by = p.user_id; req.save(update_fields=[...])
      notify_arrival(req)   # 见 §6 主触发
      return {"document": record(req), "inbound": result["document"]}
  return execute(p.tenant_id, p.user_id, OPERATION, key, digest, work)
  ```
  - **payload 字段形状**须与现有入库单完全一致：参考 `apps/inventory/order_forms.py` 的 `HeaderForm`/`LineForm` 与 `parse_document`，`item_type` 由 `prepare_rows` 自动推导，无需在 payload 提供。
  - **断言**：fulfill 后 `catalog.items` 对应 `inventory` 行 `quantity` 增加 `req.qty`（同 `posting.receive` 语义）。

### 4.5 申请人查看
- 路由：`GET /s/requisitions/` → `html_requisitions_user`（`@require_role("admin","user")`）：`user` 仅 `filter(applicant_id=…)`（正式）或 `filter(applicant_name=…, applicant_no=…)`（轻量）；`admin` 见全部。

---

## 5. M4 站内通知（app `apps/notifications`）

### 5.1 模型与服务
- `apps/notifications/services.py`：
  - `create_notification(tenant_id, recipient_user_id, recipient_name, type, title, body, ref_type=None, ref_id=None)` → `Notification.objects.create(...)`（运行身份，不依赖外部事务）。
  - `notify_arrival(req)`：若 `req.applicant_id` → `recipient_user_id=req.applicant_id`；否则 `recipient_name=req.applicant_name`。`type="requisition_arrived"`，`title=f"您申请的{item_name}已到货"`，`body="请前往总务处领取"`，`ref_type="requisition"`, `ref_id=req.id`。
  - `notify_rejected(req)`：`type="requisition_rejected"`，`title="您的请购申请被驳回"`，`body=req.reject_reason`。
  - `mark_read(principal, pk)`：仅当通知归属当前 principal（按 `recipient_user_id` 或 `recipient_name+tenant`）才置 `is_read=true`，否则 403。

### 5.2 铃铛（base.html）
- 新增 `apps/notifications/context_processors.py: unread_count(request)`：若 `getattr(request,"principal",None)`，按 `recipient_user_id=principal.user_id`（或 `recipient_name=principal.name` 兜底）统计 `is_read=false` 数；返回 `{"unread_notification_count": n}`。
- 在 `campus_inventory/settings.py` 的 `TEMPLATES` `context_processors` 注册该处理器。
- 编辑 `templates/base.html` 导航（第 9–13 行附近）：加 `<a href="/s/notifications/">🔔 通知{% if unread_notification_count %} <span class="badge bg-danger">{{ unread_notification_count }}</span>{% endif %}</a>`（对所有已登录角色可见）。

### 5.3 列表与已读 API
- 路由（新建 `apps/notifications/urls.py` 并 include）：
  - `GET /s/notifications/` → `html_notifications`（`@require_role("admin","user")`）：列出本人通知（分页），每条带「标记已读」按钮（HTMX `POST /api/v1/notifications/<id>/read/`）。
  - `POST /api/v1/notifications/<int:pk>/read/` → `api_notification_read`（`@require_role("admin","user")`）。
- 视图调用 `mark_read(principal, pk)`。

---

## 6. 测试（新增，沿用 CI 配置）
新增 `tests/test_requisition.py` 与 `tests/test_notifications.py`，覆盖验收项（配置：`DB_USER=postgres`、`DEBUG=true`、`DB_HOST/PORT` 指向测试 PG）：
- **V-req-1**：构造 `user` principal（复用现有夹具），`POST /api/v1/requisition/` → 断言 `Requisition.status=pending`、`applicant_name/no` 留痕；另测匿名（body 含 `name`+`employee_no`）→ 轻量用户被复用、单已建。
- **V-req-2**：admin `POST .../decide/` `approve` → `status=approved`；`reject`（带 reason）→ `status=rejected` + `reject_reason`；缺 reason 的 reject 断言 422。
- **V-req-3**：approve 后 admin `POST .../fulfill/`（`location_id`/`quality`）→ 断言对应 `inventory.quantity` 增加 `req.qty`（与 `posting.receive` 一致）、`Requisition.status=purchased`、`inbound_order_id` 非空；`verify_schema` G0 = `(29,14,32)`。
- **V-req-4**：`user` `GET /s/requisitions/` 见本人记录（含状态）。
- **V-notify-1**：fulfill 后断言 `Notification` 存在（`type=requisition_arrived`、归属申请人）；`GET /s/notifications/` 列表含该条；`POST .../read/` 后 `is_read=true`；reject 路径断言 `requisition_rejected` 通知存在。跨租户读取通知断言 403。
- **回归**：务必跑全量 `manage.py test tests --no-input`（当前 200 → ≥206 全绿）；`manage.py check --deploy` 0 问题；`verify_schema` G0 `(29,14,32)`；`sync_schema_snapshot --check` 通过。

---

## 7. 交付物清单（Codex 完成后须存在）
- `apps/procurement/`（models.py、services.py、urls.py、views.py、`migrations/0001_initial.py` RunSQL）
- `apps/notifications/`（models.py、services.py、urls.py、views.py、context_processors.py、`migrations/0001_initial.py` RunSQL）
- 根 `schema-v7.sql`（新增 2 表 DDL 章节 + 重算指纹）
- `django/sql/schema-v7-current.sql`（与根逐字节一致）
- `apps/core/management/commands/verify_schema.py`（G0 → `(29,14,32)`）
- `campus_inventory/settings.py`（INSTALLED_APPS 注册 2 app + context_processors 注册）
- `campus_inventory/urls.py`（include 2 app）
- `templates/base.html`（铃铛）、`templates/procurement/*`、`templates/notifications/*`
- `scripts/verify_p8b.py`（V-req/V-notify 专项校验，参照 `scripts/verify_p8a.py` 风格）
- `.github/workflows/ci.yml`（在 `verify_p8a.py` 后新增 `verify_p8b.py` 步骤）
- `tests/test_requisition.py`、`tests/test_notifications.py`
- **不要改动**：既有 `apps/inventory/orders.py`/`posting.py` 的入库/出库语义（仅新增 `procurement` 调用方）、P8a 已交付文件、`0001_initial` 基线迁移。

---

## 8. 验收口径（中枢独立验收，硬门禁）
- 代码走查：是否严守 §1 七条强约束（尤其 SQL-first、G0 同步、入库复用 `orders.create`、双轨留痕、通知归属校验）。
- 功能：V-req-1~4、V-notify-1 全部通过；全量测试 ≥206 绿；`verify_schema` G0 = `(29,14,32)`；`sync_schema_snapshot --check` 通过。
- 安全：匿名路径无权限绕过；通知不可跨用户/跨租户读取或标记。
- 不通过则出具整改清单，返回 Codex 重修；通过则中枢出《P8b 独立验收报告》并盖硬门禁章。

---

## 9. 提交与推送（Codex 不负责，由中枢/用户执行）
Codex 交付后**不要**自行 push。由中枢复核并 `commit + push` 到 `origin/main`（与 P8a 一并，待 P8c 完成后统一提交，遵循用户既定策略），随后中枢跑独立验收。

---

## 10. 已知限制（须写入《使用手册》，不阻塞验收）
- 轻量免密用户（P8a 建的 `!pickup-unverified:` 账号）的通知按「姓名+工号+租户」关联；该用户后续用同一工号登录无法自动并回历史通知。提示：「免密登记/申请后，请用同一工号登录以统一查收通知」。
- 本阶段不含通知的邮件/短信/企微推送，仅系统内消息（主蓝图 §0 决策 3）。
