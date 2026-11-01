# 遗留项整改与 P8b 实现验收报告

- **验收对象**：Codex 在工作区提交的改动（未 commit）
- **验收授权**：用户明确"codex 已把 3 个遗留问题解决，请验收"；同时用户声明"蓝图你决定就好"（P8b 设计决策权已委托中枢）
- **验收方式**：独立、实库级核验（非 Codex 自检）；本地 PG16@55432 实跑，结束后清理临时库
- **验收日期**：2026-11-01

---

## 0. 范围澄清（重要）

用户字面要求验收"3 个遗留项"，但实库核查发现 **Codex 的实际改动远不止 3 个遗留项**，包含：

1. **3 个遗留项整改**（来自《P8a整改验收报告》的 3 条非阻塞提醒）
2. **完整 P8b 实现**（新增 `apps/procurement` + `apps/notifications` 两个 app、根 schema 两表、导航与移动端 UI、29 个测试）
3. **P8a 移动端/二维码 UI 收尾**（`catalog` 加 `qr_token`、`inventory/queries.py` 加 `resolve_scan`、`inventory/urls.py` 加扫码/移动盘点路由、`base.html` 加入口）

由于用户已将 P8b 蓝图决策权委托中枢，P8b 实现属"蓝图定稿→Codex 实现"正当链路，可接受；但本报告**如实分层**，不把 P8b 冒充为"遗留项"。

---

## 1. 三项遗留项核验

遗留项定义见《P8a整改验收报告》末尾"三、遗留提醒（非阻塞）"。

### 遗留项 1：三份 schema 文件关系需在主文档点明 —— ✅ 已解决

- `django/sql/README.md` 已重写为明确表格，点明三者分工：
  - 根 `inventory-v4/schema-v7.sql` = **唯一当前 DDL 权威源**
  - `django/sql/schema-v7-current.sql` = 其**逐字节同步快照**（静态预检/独立审阅用，不可独立编辑）
  - `django/sql/schema-v7.sql`（旧）= **冻结 P7 基线**，供 `core/0001_initial` 固定哈希，严禁覆盖
- 同步/防漂移机制写清：`sync_schema_snapshot.py --check`（只读校验）/`--write`（编辑根后同步）；指纹范围明确为"独立 `BEGIN;` 起至文件末"。
- CI 已加门禁：`.github/workflows/ci.yml` 新增 `sync_schema_snapshot.py --check`。
- 实核验：`sync_schema_snapshot --check` → `fingerprint_valid:true / snapshot_matches:true`，正文 SHA-256 `4601fdeb…`、761 行，与根文件头声明**逐字节一致**。

### 遗留项 2：两个 `(可删)` 隔离区最终去留 —— ✅ 已解决

- 实查：`docs/过程记录/P8a/_codex_raw_logs(可删)` 与 `docs/过程记录/_django_codex_logs(可删)` **均已删除（各 0 文件）**，原始 Codex 机器日志整体从工作区清除，不再悬决。
- `.gitignore` 新增 `**/_codex_raw_logs(备查)/`、`**/_django_codex_logs(备查)/` 忽略规则备用（当前无此类目录，纯防御）。

### 遗留项 3：本地未跑 redis，P6/P7 celery/就绪探针端到端未验证 —— ⚠️ 仍为环境限制（非代码缺陷）

- 本轮 Codex **未改动 redis/celery 相关代码**，CI 也未新增 redis 服务容器；本地 `redis-server` 仍不在运行。
- 就绪探针本身**有测试覆盖且通过**：`test_readiness.py` 中对 `/health/ready/` 返回 503 的负向断言在 229 测试中正常通过（属预期）。
- 结论：此项并非 Codex 能用代码"解决"的问题，是**环境/CI 依赖**，与历轮一致，待目标环境或 CI（装 redis 后）端到端联通确认。**不计入 Codex 失职**。

> 三项遗留项实际可闭环：**项 1、项 2 已解决；项 3 为环境限制，维持原状（非阻塞）**。

---

## 2. P8b 实现独立验收（超出字面范围，但已委托中枢决策）

### 2.1 SQL-first 宪法合规 —— ✅

- 两张新表 `procurement_requisition`、`notifications_notification` 以**手写 DDL** 加进根 `schema-v7.sql`（带 `P8B-PROCUREMENT-BEGIN/END`、`P8B-NOTIFICATIONS-BEGIN/END` 章节）。
- Django 模型 `managed = False` + 显式 `db_table`（`procurement/models.py:21`、notifications 同构），**不重复建表**；迁移 `0001_initial` 仅记账。
- G0 计数由 `(27,14,32)` → `(29,14,32)`，三处一致：根 schema 头部声明、`verify_schema.py:26` 断言、`apply_schema` 实测输出。
- `apply_schema` 实测成功应用 `catalog.0002_add_item_qr`、`notifications.0001_initial`、`procurement.0001_initial`，G0 = 29/14/32。

### 2.2 入库联动红线 —— ✅ 守住

- `apps/procurement/services.py:195`：`fulfill()` 通过 `orders.create(p, "inbound", "RQ-IN-"+..., payload)` 走统一入库存过账（即 `posting.receive`），**未发现手写库存 UPDATE**，13 项对账不受影响。
- 通知触发点正确：`decide()` 驳回时调 `notify_rejected(row)`；`fulfill()` 到货时调 `notify_arrival(row)`。

### 2.3 身份/权限/隔离 —— ✅（测试覆盖）

- 服务层全程 `require_runtime()` 守卫；`decide/fulfill` 限 admin（`_admin` 角色校验）。
- 测试覆盖租户隔离：`test_requisition` 含 foreign-school 隔离、`test_notifications` 含 `foreign_school_cannot_read` / `other_user_cannot_read` / `same_name_is_not_identity`。
- CSRF 强制：`test_notifications.test_bearer_read_with_csrf_enforced` 通过。

### 2.4 双轨身份复用 —— ✅

- 匿名请购沿用 P8a 的 `pickup.ensure_lightweight_user` + `resolve_scan`（凭二维码 token 定位物品/租户），与既有"扫码领用"身份机制一致。

### 2.5 UI 接线 —— ✅

- `base.html` 新增入口：`移动库存`/`二维码标签`（P8a）、`请购申请`/`🔔通知`/`请购审批`（P8b），均与对应 URL 对应。
- `notifications/context_processors.unread_count` 向模板注入 `unread_notification_count`，铃铛角标可用。
- `procurement/urls.py`、`notifications/urls.py` 的 HTML/API 路由齐全。

### 2.6 CI 门禁 —— ✅

- `.github/workflows/ci.yml` 新增 `verify_p8a.py`、`verify_p8b.py`、`sync_schema_snapshot.py --check`；`verify_ci_delivery` 断言含上述步骤与 `explicit_test_suite`、`sql_snapshot_guard`。

---

## 3. 测试与实库证据

| 检查项 | 命令/来源 | 结果 |
|---|---|---|
| 静态检查 | `manage.py check` | 0 issues |
| 指纹/快照 | `sync_schema_snapshot --check` | fingerprint_valid/snapshot_matches = true（4601fdeb / 761 行）|
| SQL 预检 | `manage.py precheck_sql` | 美元引号平衡、指纹一致 |
| CI 交付守卫 | `verify_ci_delivery` | 含 verify_p8a/verify_p8b/verify_readiness，测试门禁+快照守卫开启 |
| 建库+迁移 | `apply_schema`（临时库）| 含 P8a/P8b 迁移；G0 = 29/14/32；runtime ACL 通过 |
| 全量回归 | `manage.py test tests` | **Ran 229 tests in 8.588s — OK**（200→229，+29，无回归）|
| P8b 专用闸门 | `scripts/verify_p8b.py`（自建临时库）| **Ran 29 tests — OK**；到货入库/通知/对账 13-13 全清；临时库已清理 |

> 临时库 `campus_inventory` 及测试库 `test_campus_inventory` 验收后均已 `DROP`，本地不留痕迹。

---

## 4. 发现项（非阻塞）

1. **死路由（待清理）**：`apps/inventory/urls.py` 中 `path("s/requisition/new/", pickup_views.requisition_placeholder)` 与 `procurement/urls.py` 的同路由 `html_new` 冲突；因 `campus_inventory/urls.py` 中 procurement 先于 inventory 被 include，真实页面 `html_new` 覆盖占位符，`requisition_placeholder` 视图与 `templates/pickup/requisition_pending.html` 成为**不可达死代码**。功能无损，建议后续删除。
2. **遗留项 3（redis）**：环境限制，维持原状，待 CI/目标环境联通确认。

---

## 5. 验收结论

| 范围 | 结论 |
|---|---|
| 遗留项 1（schema 三文件关系文档）| ✅ 已解决 |
| 遗留项 2（隔离区去留）| ✅ 已解决（已删除）|
| 遗留项 3（redis 本地端到端）| ⚠️ 环境限制，非阻塞，维持原状 |
| P8b 实现（请购+通知）| ✅ ACCEPTED（SQL-first 合规、红线守住、229/29 测试通过）|
| P8a 移动端/二维码 UI 收尾 | ✅ ACCEPTED（并入全量测试，无回归）|

**整体判定：可纳入统一提交范围。** 但须注意——本次 Codex 实际交付的是"P8b 完整实现 + P8a UI 收尾 + 遗留项整改"，并非仅"3 个遗留项"。因 P8b 蓝图已委托中枢决策，该超范围交付属正当，且经实库验证通过。

> 提交策略维持既定：P8a + P8b + P8c 全部完成后统一 commit/push；本轮仅本地验收，未提交。

---

## 6. 下一步建议

- 待办：P8c（报修工单 M5）仍按蓝图规划；如需我起草 P8c 蓝图与指令包，请指示。
- 清理：建议删除 `inventory/urls.py` 中 `requisition_placeholder` 死路由及对应模板（非阻塞）。
- 环境：目标环境部署前补跑 redis 联通（CI 装 redis 后 observe worker/beat 与就绪探针端到端）。
