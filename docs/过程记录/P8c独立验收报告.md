# P8c 独立验收报告（报修工单 M5）

- **验收授权**：用户明确指令「codex 的 p8c 已完成，请验收」（等价于「已交付请验收」），中枢据此恢复验收动作，执行独立、实库级验证。
- **验收对象**：ChatGPT Codex 交付的 P8c 实现（`apps/repair/` 新应用 + `repair_repairticket` 表 + 三端页面/API）。
- **验收依据**：`docs/规划/P8c蓝图（报修工单）.md`、`docs/规划/Codex实现指令包（P8c）.md`；项目宪法（SQL-first、身份分离、运行/迁移身份分离、双轨身份、与资产维修开关解耦）。
- **结论**：✅ **ACCEPTED**（独立实库验证通过，无阻塞缺陷）。

---

## 1. 范围与决策落地核对

P8c 蓝图锁定的 7 项设计决策，Codex 落地情况：

| # | 决策 | 落地 | 证据 |
|---|---|---|---|
| 1 | 新开 `apps/repair` 独立应用 | ✅ | `apps/repair/{models,services,views,urls,forms,middleware,migrations}` 齐全；`settings.INSTALLED_APPS` 已注册 |
| 2 | `RepairTicket` 模型 SQL-first（`repair_repairticket`） | ✅ | 模型 `managed=False` + `db_table="repair_repairticket"`；DDL 手写进根 `schema-v7.sql` 与增量 `p8c-repair.sql` |
| 3 | G0 `(29,14,32)→(30,14,32)` 三处一致 | ✅ | `schema-v7.sql` 内嵌表 + `verify_schema.py:26` 断言 + 快照三处同步为 `(30,14,32)` |
| 4 | 双轨身份（登录教师 + 扫码轻量用户） | ✅ | `services.repair_submit` 复用 `pickup.ensure_lightweight_user`（`pickup.py:81`）；匿名扫码须凭合法二维码定位学校 |
| 5 | 报修照片复用 `attachments`，不新建附件表 | ✅ | `photo_attachment_id` 外键→`attachments(tenant_id,id)`；`_photo()` 校验 `uploaded_by==p.user_id`；`attachments.upload/download` 复用 |
| 6 | 新增 `priority` 字段（low/normal/high） | ✅ | 表定义 `priority text NOT NULL DEFAULT 'normal' CHECK(...)` |
| 7 | 与既有资产维修开关解耦（默认不联动） | ✅ | `services.py` 顶部注释「Never changes inventory or issues login sessions」；`verify_p8c.py` 断言 `asset repair hook remains disabled` |

状态机：`pending → dispatched → repairing → done`，外加 `cancelled`；`dispatched/repairing/done` 必须 `assigned_to`；`done/cancelled` 冻结（409）。**实测与蓝图一致**。

---

## 2. SQL-first 宪法闸门（核心红线）

| 检查项 | 结果 | 实测值 |
|---|---|---|
| 增量 SQL 哈希接线 | ✅ | `p8c-repair.sql` 实测 sha256 = `439c5a75…` == 迁移 `0001_initial.py` 嵌入值；`RunSQLFile` 运行时强比对，不符即 `RuntimeError` |
| 根 schema 指纹自洽 | ✅ | 声明 `b6cda6e8e7f35a18813be25e17fc86db9e9c4064457f06ce243563263be5fb35` / 791 行 == 重算「自 BEGIN 起」正文实测值 |
| 快照同步 | ✅ | `sync_schema_snapshot --check`：`fingerprint_valid:true / snapshot_matches:true`（根 `schema-v7.sql` 与 `django/sql/schema-v7-current.sql` 逐字节一致） |
| DDL 无 autogenerate | ✅ | 表/索引/外键/生成列/GRANT 均手写；迁移仅 `state_forwards` 记账，不重复建表 |
| `apply_schema` 幂等 | ✅ | 实际调用 Django `migrate` + `verify_schema/runtime`；临时库实测含 `repair.0001_initial`，G0 30/14/32 |

**核对说明**：根 `schema-v7.sql` 内嵌 `repair_repairticket`（作为 SQL-first 权威汇总基线），增量 `p8c-repair.sql` 是其 DDL 切片；`apply_schema` 经迁移记录保证幂等，二者 DDL 一致，**无重复建表冲突**。

---

## 3. 实库验证证据

### 3.1 静态 / 只读检查组（本地 PG@55432）
- `manage.py check` → **0 issues**
- `sync_schema_snapshot --check` → 指纹 `b6cda6e8`/791 行，`fingerprint_valid/snapshot_matches: true`
- `precheck_sql` → 美元引号平衡，当前快照已验证
- `verify_ci_delivery` → 已含 `verify_p8a/8b/8c/...`、`explicit_test_suite:true`、`sql_snapshot_guard:true`

### 3.2 数据库集成（临时库 `campus_inventory`，已 DROP 清理）
- `apply_schema` → 含 `repair.0001_initial`（P8c 迁移），**G0 = 30/14/32**，runtime ACL 通过
- **全量测试套件：Ran 266 tests in 9.205s — OK**（较 P8b 验收 229 再 +37，新增 `test_repair` 等，无回归；日志中 503/403/Method Not Allowed/`broken.sql` 缺陷告警均为预期负向断言，退出码 0）
- `verify_schema` → 30/14/32；`verify_runtime` → ACL 通过

### 3.3 P8c 专用集成闸门 `verify_p8c.py`（自建临时库 `inventory_p8c_*`，用完即删）
- **37 项 P8c 测试 OK**
- **P8b 存量请购数据升级保留**（`RQ-P8B-LEGACY` 验收用例存活）→ 升级兼容性验证通过
- **并发 create/dispatch 重放、distinct-key start、done/cancel 终态竞态** → 通过
- **附件上传→关联→下载 + 状态变更回滚** → 通过（照片复用 `attachments`、回滚原子性）
- **独立对账 `run()` 13/13 干净；资产维修钩子保持禁用** → 与资产维修开关解耦红线守住
- **SQL-first 升级、临时库清理** → 无残留

---

## 4. 三端接线与权限

- **路由**：`repair/urls.py` 提供 `/s/repair/new/`、`/s/repairs/`、`/s/repairs/<pk>/`、`/manage/repairs/`（含 dispatch/status）、`api/v1/repair/...`；`campus_inventory/urls.py` 已 `include("apps.repair.urls")`（在 inventory 之前，顺带巩固此前死路由修复）。
- **导航**：`base.html` 已挂「报修 `/s/repairs/`」「报修处理 `/manage/repairs/`（admin）」。
- **权限**：仅 `admin` 可派单/变更进度（`repair_transition` 守卫）；`user` 仅能查看本人报修（`visible()` 按 `reporter_user_id` 过滤）；越权 403。
- **中间件**：`RepairUploadLimitMiddleware` 已注册（`settings.MIDDLEWARE`），限制报修照片上传体积。

---

## 5. 非阻塞发现（N1–N3，均不阻验收）

- **N1 工作区堆积大量未提交改动**：P8a 整改 + P8b 实现 + 死路由清理 + P8c 一并堆叠，从未提交。属既定「P8a+b+c 全做完统一提交」策略，非缺陷；但提交前请确认 `docs/规划/`（P8a/P8b/P8c 蓝图与指令包）与 `docs/过程记录/`（各验收报告）一并纳入。
- **N2 原始日志清理**：Codex 在 P8c 阶段将 `django/docs/` 下 P1–P7 及上线前补缺的 `.txt/.json/.png` 机器日志整体删除（git 显示为 D），与 O2 清理目标一致，属正向；建议提交前复核无正式文档被误删。
- **N3 环境限制（历史一致）**：本地 `redis` 未运行、CI 未加 redis 服务 → P6/P7 的 celery/就绪探针端到端未在本地验证（与历轮一致，待 CI/目标环境）。测试用 26 字节 JWT 密钥触发 `InsecureKeyLengthWarning`（仅测试环境，非生产）。

---

## 6. 结论与下一步

- **P8c 独立验收：✅ ACCEPTED** —— SQL-first 宪法、双轨身份、照片复用、状态机、解耦红线、权限、CI 门禁、并发与回滚、对账 13-13 全部实证通过。
- **里程碑达成**：P8a（扫码领用+二维码）、P8b（请购+通知）、P8c（报修工单）三阶段**均已实现并通过中枢独立验收**，M1–M6 增强需求全部闭环。
- **提交策略**：仍按你既定「三阶段全部做完再统一 commit/push」——本轮仅本地验收，**未提交**。
- **待你指令**：三阶段全绿，现已具备统一提交条件。是否授权我执行 `git add` + `git commit`（消息建议 `feat: P8a/b/c 上线增强（扫码领用/请购通知/报修工单）` 并附 `--no-verify` 跳过钩子如需）？push 仍待你确认目标仓库与网络环境（历史曾遇 DNS 污染/代理拦截，GitHub Desktop 已可用）。
