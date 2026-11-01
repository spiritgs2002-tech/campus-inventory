# P8c 蓝图（报修工单）：通用报修工单模块

> 版本：v1（细化蓝图，中枢已拍板设计决策）
> 日期：2026-11-01
> 作者：控制中枢（架构师/项目经理）
> 关联：主蓝图 `docs/规划/P8增强方案（蓝图）.md`；P8a/P8b 已交付并验收；本蓝图对应主蓝图 M5。
> 下游：实现层任务书 `docs/规划/Codex实现指令包（P8c）.md`（交 ChatGPT Codex 执行）。

---

## 0. 本阶段范围（仅 P8c / M5）
- **M5 通用报修工单**：教师/领用人上报故障（物品 / 场地 / 设备 + 照片 + 说明）→ 总务处派单（`assigned_to`）→ 状态跟踪 → 完成。
- 不在本阶段：M1/M2/M6（P8a 已交付）、M3/M4（P8b 已交付）。

---

## 1. 已拍板的关键决策（中枢定稿，用户授权"你决定就好"）
1. **独立新 app `apps/repair`**：与 `procurement`/`notifications` 同构，不塞进 `inventory`。
2. **双轨身份**：复用 P8a 的 `ensure_lightweight_user`（@ `apps/inventory/pickup.py:81`）；未登录报修须填「姓名+工号」留痕，**禁止匿名写业务单**。
3. **照片复用 `attachments`**：报修照片走既有 `attachments.services.upload(principal, key, file)`（或 API `POST /api/v1/attachments/`），取回 `attachment_id` 存入 `RepairTicket.photo_attachment_id`；不另建存储。
4. **增加 `priority` 字段**：回应主蓝图 §10 的待确认项——报修区分紧急程度，取值 `low/normal/high`，可空、默认 `normal`（轻量，不引入新枚举表）。
5. **与既有资产维修开关解耦（默认不联动）**：现有 `apps/inventory/assets.repair`（admin 切换资产 `in_stock↔repair`）是独立轻量机制，P8c 不自动调用它，避免双重状态来源冲突；仅在**可选钩子**中说明如何手动同步（默认关闭，非阻塞）。
6. **SQL-first 红线**：`repair_repairticket` 手写 DDL 进根 `schema-v7.sql`；模型 `managed=False`，迁移仅记账；G0 由 `(29,14,32)` → `(30,14,32)`（仅 +1 表，无新增函数/触发器，维持 14/32）。
7. **门禁类型**：主蓝图定为**软门禁**（P8c 软门禁），验收通过后仍与 P8a/P8b 一并统一 commit/push。

---

## 2. 目标与用户流程
- **教师端（自助上报）**：
  1. `GET /s/repair/new/` → 移动友好报修页（选 `target_type`：资产/物品/场地；填 `target_id` 或资产二维码扫码带入、`description`、`priority`、上传照片、姓名+工号或登录态）。
  2. `POST /api/v1/repair/` → 双轨身份建单，`status=pending`。
  3. `GET /s/repairs/` → 查看"我上报的工单"及状态（正式用户按 `reporter_user_id`，轻量按 `name+no+tenant`）。
- **处理端（总务/admin）**：
  1. `GET /manage/repairs/` → 工单列表（状态筛选：pending/dispatched/repairing/done/cancelled）。
  2. 派单 `POST /api/v1/repair/<pk>/dispatch/`（`assigned_to` = 某 `user` id）→ `status=dispatched`。
  3. 进度 `POST /api/v1/repair/<pk>/status/`（`action` ∈ {start, done, cancel}）→ `repairing/done/cancelled`；状态机见 §3。

---

## 3. 数据模型（`repair_repairticket`，SQL-first）
```sql
CREATE TABLE repair_repairticket (
  id bigserial PRIMARY KEY,
  tenant_id bigint NOT NULL,
  ticket_no text NOT NULL,
  reporter_user_id bigint NULL,
  reporter_name text NOT NULL,
  reporter_no text NOT NULL,
  target_type text NOT NULL,            -- asset | item | location
  target_id bigint NULL,                -- 资产卡 id / 物品 id / 场地 id
  description text NOT NULL,
  photo_attachment_id bigint NULL,      -- 复用 attachments.attachment_id
  priority text NOT NULL DEFAULT 'normal', -- low | normal | high
  status text NOT NULL DEFAULT 'pending',  -- pending|dispatched|repairing|done|cancelled
  assigned_to bigint NULL,              -- 处理人 user id
  reject_reason text NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  updated_at timestamptz NOT NULL DEFAULT now()
);
CREATE UNIQUE INDEX uq_repair_tenant_no ON repair_repairticket(tenant_id, ticket_no);
CREATE INDEX ix_repair_tenant_reporter ON repair_repairticket(tenant_id, reporter_user_id);
CREATE INDEX ix_repair_tenant_status ON repair_repairticket(tenant_id, status);
```
- **状态机**：`pending → dispatched → repairing → done`；任意非终态可 `cancelled`；`done`/`cancelled` 为终态，再变更须 409。
- **隔离模型**：沿用宪法 D3「复合外键 + 服务过滤」，所有查询带 `tenant_id`，不新增函数/触发器，故 G0 函数/触发器维持 14/32。

---

## 4. 角色与权限
- 维持 `admin` / `user` 两角色；`user` 可上报、查看本人工单；`admin` 可派单/改状态/看全部。
- 所有写操作继续走 `require_role` + `require_runtime` 守卫；照片上传须运行身份。

---

## 5. 复用清单（实现层见指令包，禁止重写）
- 双轨身份：`apps/inventory/pickup.ensure_lightweight_user`（@ `pickup.py:81`）。
- 照片：`apps/attachments/services.upload` + `download`（@ `apps/attachments/services.py:87/26`）；视图复用 `api_attachments`。
- 基座：幂等 `shared.idempotency.service.execute` + `validate_key`、审计 `apps.core.services.audit`、运行身份 `shared.db.runtime.require_runtime`、权限 `shared.rbac.permission.require_role`。
- 导航：`templates/base.html`（加「报修」「报修处理」入口）；移动页沿用 `templates/pickup/` 的 server-rendered + viewport 风格。

---

## 6. 验收标准（V-repair 系列，软门禁）
- **V-repair-1**：教师提交报修（含照片上传、双轨身份），`status=pending`，`reporter_name/no` 与 `photo_attachment_id` 留痕；匿名缺姓名/工号断言 422。
- **V-repair-2**：admin 派单（`assigned_to`）→ `status=dispatched`；`start` → `repairing`；`done` → `done`；跨态非法转移（如 pending 直接 done）断言 409；`cancel` 终止。
- **V-repair-3**：报修人 `GET /s/repairs/` 见本人工单及状态；跨用户/跨租户读取断言 403。
- **回归**：全量 Django 测试（当前 229 → ≥235 全绿）；`manage.py check --deploy` 0 问题；`verify_schema` G0 = `(30,14,32)`；`sync_schema_snapshot --check` 通过；新增 `scripts/verify_p8c.py` 专项闸门。

---

## 7. 已知限制（须写入《使用手册》，不阻塞验收）
- 轻量免密报修人的工单按「姓名+工号+租户」关联；该用户后续用同一工号登录无法自动并回历史工单（同 P8b 限制）。
- 资产维修开关（`assets.repair`）与工单默认不联动；如需联动，在指令包可选钩子中实现，默认关闭。
- 派单 `assigned_to` 指向 `user`，本阶段不强制 `maintainer` 角色。

---

## 8. 与既有维修机制的关系
- 既有 `/assets/<pk>/repair_start|finish/`（`apps/inventory/read_views.py` + `assets.repair`）保留为**资产快速状态切换**工具，与工单系统并存、互不依赖。
- 两者语义不冲突：工单描述"谁报修/谁处理/进度"，资产开关描述"资产当前是否在修"。
