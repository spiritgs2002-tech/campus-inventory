# P8b 蓝图（细化）：请购申请 + 站内通知 + 入库联动

> 阶段：P8b（**硬门禁**）
> 日期：2026-11-01
> 来源主蓝图：`docs/规划/P8增强方案（蓝图）.md` §3 M3/M4、§6 P8b、§7 V-req/V-notify
> 配套宪法：`schema-v7.sql`（唯一 DDL 规则源）、`开发规范（Django+PG）.md`、P8a 已验收代码
> 对应指令包：`docs/规划/Codex实现指令包（P8b）.md`

---

## 0. 本阶段范围（仅 P8b，两块）
- **M3 请购申请 + 审批 + 采购入库联动**：教师缺货申请 → 总务主任审批 → 采购到货入库增库存 → 回填并通知。
- **M4 站内通知**：铃铛 + 通知列表 + 已读；与 M3 入库联动联动触发。

> 不在本阶段：M5 报修工单（留待 P8c）；M1/M2/M6 已由 P8a 交付并通过验收。

---

## 1. 本次锁定的关键设计决策
1. **新模块布局（独立 app，便于并入 `apps/asset`）**：
   - `apps/procurement`：请购申请（`Requisition` 模型 + 提交 / 审批 / 入库联动服务）。
   - `apps/notifications`：站内通知（`Notification` 模型 + 列表 / 已读 API + `base.html` 铃铛）。
2. **双轨身份（同 P8a）**：已登录 `user`/`admin` 用会话身份（`request.principal`）；未登录填「姓名 + 工号」→ 复用 `apps/inventory/pickup.ensure_lightweight_user(tenant_id, name, employee_no)` 建/查轻量领用人；**禁止**任何匿名写入业务单。
3. **入库联动必须复用既有 inbound 写链路**：审批通过后的「标记采购完成并入库」**必须经由现有 `apps/inventory/orders.create(kind="inbound", type="purchase", …)`**（其内部 `posting.receive` @ `posting.py:253` 增库存），**严禁**另写库存扣减/增加逻辑。完成后回填 `requisition.inbound_order_id`、置 `status=purchased`。
4. **通知触发点**：
   - **主触发**：入库联动完成（`status` 转 `purchased`）→ 给申请人创建「您申请的 XX 已到货，请前来领取」通知。
   - **次触发（建议）**：审批**驳回** → 给申请人创建「申请被驳回」通知（reason 可见）。
5. **通知收件人解析**：申请人若为正式 `user`（有 `recipient_user_id`）→ 按 `user_id` 推送；若为 P8a 建的轻量免密账号（`password_hash` 以 `!pickup-unverified:` 开头）→ 按 `recipient_name` + `tenant_id` 关联。**已知限制**：轻量账号后续用同一工号登录也无法自动并回历史通知，须在《使用手册》说明。
6. **G0 计数**：本阶段新增 **2 张表**，`verify_schema` 期望值由 `(27, 14, 32)` 改为 `(29, 14, 32)`（未新增函数/触发器时维持 14/32）。同步重算 `schema-v7.sql` 指纹与 `django/sql/schema-v7-current.sql` 快照（遵循 P8a 已落地的指纹机制）。

---

## 2. 数据模型（SQL-first，严禁 autogenerate）

### 2.1 `procurement_requisition`
| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigint PK | |
| tenant_id | bigint | 复合外键隔离维度 |
| order_no | text | 自生成单号（如 `RQ-<uuid>`），`UNIQUE(tenant_id, order_no)` |
| applicant_id | bigint NULL | 正式 `user` 的 id；轻量用户为空 |
| applicant_name | text | 必填（双轨均留痕） |
| applicant_no | text | 工号（轻量用户即 username） |
| item_id | bigint | 指向 `catalog.items`（申请的具体耗材/资产） |
| qty | numeric(14,3) | 申请数量 |
| reason | text | 申请事由 |
| status | text | `pending`/`approved`/`rejected`/`cancelled`/`purchased`（db_default `pending`） |
| approved_by | bigint NULL | 审批人 user id |
| inbound_order_id | bigint NULL | 入库联动生成的 `inbound_orders.id` |
| reject_reason | text NULL | 驳回原因 |
| created_at / updated_at | timestamptz | |

索引：`UNIQUE(tenant_id, order_no)`、`(tenant_id, applicant_id)`、`(tenant_id, status)`。

### 2.2 `notifications_notification`
| 字段 | 类型 | 说明 |
|---|---|---|
| id | bigint PK | |
| tenant_id | bigint | |
| recipient_user_id | bigint NULL | 正式 user；轻量用户为空 |
| recipient_name | text NULL | 轻量用户姓名（与 tenant_id 联合定位） |
| type | text | 如 `requisition_arrived` / `requisition_rejected` |
| title | text | |
| body | text | |
| ref_type | text NULL | 如 `requisition` |
| ref_id | bigint NULL | 关联对象 id |
| is_read | bool | db_default `false` |
| created_at | timestamptz | |

索引：`(tenant_id, recipient_user_id, is_read) WHERE recipient_user_id IS NOT NULL`；`(tenant_id, recipient_name, is_read) WHERE recipient_name IS NOT NULL`。

---

## 3. 用户流程

### M3 请购申请
- **教师端（移动友好）**：
  - 移动库存页（`/s/inventory/`）缺货项旁「申请」按钮 → `GET /s/requisition/new/?item=<pk>`（预填物品，server-rendered 表单，微信浏览器兼容）。
  - 提交：`POST /api/v1/requisition/`，body `{item_id, qty, reason, name?, employee_no?}`，携带 `Idempotency-Key`。
- **审批端（admin）**：
  - 列表：`GET /manage/requisitions/`（仅 admin，含 pending/历史）。
  - 审批：`POST /api/v1/requisition/<id>/decide/` `{action: approve|reject, reason}`（`@require_role("admin")`）。
  - 入库联动：`POST /api/v1/requisition/<id>/fulfill/` `{location_id, quality, supplier_id?}`（`@require_role("admin")`，复用 `orders.create(inbound, purchase)`）→ 库存增加、回填、触发通知。
- **申请人查看**：`GET /s/requisitions/`（`user` 仅见本人）。

### M4 站内通知
- **铃铛**：`templates/base.html` 导航新增「🔔 通知」链接 `/s/notifications/`，旁显未读数徽标（新增 context processor 按当前 `principal` 计算未读）。
- **列表**：`GET /s/notifications/`（`@require_role("admin","user")`）列出本人通知，含「标记已读」按钮（HTMX 或简单 POST）。
- **已读 API**：`POST /api/v1/notifications/<id>/read/`（`@require_role("admin","user")`，只能标记本人通知）。

---

## 4. 角色与权限
- `user`：提交请购、查看本人请购、查看本人通知、标记本人通知已读。
- `admin`：审批请购、执行入库联动、查看全部请购、查看全部通知（便于总务跟进）。
- 所有写接口：`@require_role(...)` + `require_runtime()` 守卫（运行身份 `inv_run`）；迁移/建表用 `inventory_app`。

---

## 5. 验收标准（V 系列，沿用主蓝图 §7）
- **V-req-1**：教师（双轨身份均测）提交请购 → `status=pending`，`applicant_name/no` 留痕。
- **V-req-2**：admin `approve` 与 `reject` 两条路径均正确（reject 须带 `reason` 并写入 `reject_reason`）。
- **V-req-3**：`approve` 后 `fulfill` → 对应物品库存增加（与 `posting.receive` 一致）+ `requisition.status=purchased` + `inbound_order_id` 回填 + `G0` 仍为 `(29,14,32)`。
- **V-req-4**：申请人登录后 `GET /s/requisitions/` 可见本人记录（含已审批/已到货状态）。
- **V-notify-1**：`fulfill` 后申请人铃铛可见「XX 已到货」通知并可标记已读；`reject` 后有「申请被驳回」通知。
- **回归**：全量 Django 测试（当前 200）+ 新增 ≥6；CI 增 `verify_p8b.py` 步骤全绿；`manage.py check --deploy` 0 问题；`verify_schema` G0 = `(29,14,32)`。

---

## 6. 关键技术注意 / 风险
- **入库联动复用红线**：务必走 `orders.create(kind="inbound", type="purchase", …)`（`orders.py:376` `create` + `posting.receive` `posting.py:253`），不手写库存 `UPDATE`，否则破坏 `reconcile.sql` 13 项内部一致性。
- **双轨留痕**：匿名提交仍须 `name` + `employee_no` 非空，禁止匿名业务单。
- **勿改既有语义**：不得改动 `orders.py`/`posting.py` 的入库/出库语义，仅新增调用方（`procurement` 调 `orders.create`）。
- **通知关联限制**（§1.5）：轻量免密用户通知按 `name+tenant` 而非 `user_id` 关联，须在《使用手册》说明「免密登记后请用同一工号登录以统一查收通知」。
- **SQL-first 红线**：DDL 改动一律手写 `RunSQL` + 同步 `schema-v7.sql`；新增 2 表后同步更新 G0 期望值与快照。

---

## 7. 交付物与分工
- **中枢（我）**：本细化蓝图 + 《Codex 实现指令包（P8b）》；独立验收并出具《P8b 独立验收报告》。
- **Codex（用户侧外部 agent）**：按指令包实现 `apps/procurement` + `apps/notifications` 的业务代码、模板、迁移、测试、CI 步骤。
- **用户确认**：部署/联调（目标环境）、真实数据初始化、最终上线拍板。

---

## 8. 待你确认 / 提供（若有补充请告知，否则按本蓝图执行）
- 请购申请是否需要「预算/经费来源」「期望到货日期」字段？（本蓝图暂未含，可在 `reason` 中描述或后续追加。）
- 驳回通知是否必须（本蓝图默认开启次触发，若不需要可去掉）。
- 通知是否允许 admin 主动群发（本蓝图仅做系统触发，不含手动群发）。
