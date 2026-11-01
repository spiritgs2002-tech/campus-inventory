# 报修 CSV 导入 · 独立验收报告

验收方：控制中枢（WorkBuddy 架构师）｜依据：`报修CSV导入指令包（给Codex）` 7 条验收标准
被验收物：Codex 交付的 `apps/repair/management/commands/import_repairs.py` + `tests/test_import_repairs.py` + 使用说明/样例
验收方式：**独立代码走查 + 独立复跑测试**（非仅采信开发方自报）

## 一、验收结论：✅ ACCEPTED（通过）

| 维度 | 结果 |
|---|---|
| 指令包 7 条验收标准 | 全部满足 |
| 专项测试（独立复跑） | **31 / 31 通过**（本机以运行身份 `inv_run` 实跑） |
| 变更范围 | 仅新增命令 + 测试 + 文档，**零 DDL / 零 schema 改动** |
| 宪法符合度 | SQL-first、手写 ORM、`require_runtime()` 守卫、租户隔离、轻量用户复用——全部符合 |

## 二、独立复跑证据（关键，非自报）

本机本地 PG16@55432，预建 `test_inventory`（超户灌 `apply_schema` → G0=**30/14/32** → 跑 `grant-runtime.sql`），再以运行身份 `inv_run` + `--keepdb` 复跑：

```
Ran 31 tests in 2.265s
OK
Found 31 test(s). System check identified no issues (0 silenced).
```

复跑中使用的是与 CI 同构的运行身份（非超户），`require_runtime()` 守卫真实生效。
唯一告警：①本机 dummy 密钥 28 字节触发 HMAC 长度警告（测试环境密钥，非代码问题）；②一条 `Forbidden: /api/v1/repair/76/` 日志，正是租户隔离测试断言的 403，**预期内**。

> 说明：本沙箱无 `redis-server`，含 Celery 依赖的基线测试（如 test_maintenance/p6）无法在本机完整跑；但本命令不触发 Celery，其 31 项自包含测试已全部通过，且新增文件不触碰任何共享代码，266 项基线按组合关系不受影响。完整 297 须在 CI（装 redis）复验。

## 三、代码走查（逐条对照 7 标准）

1. **命令存在 / `--help` / 缺参清晰**：`add_arguments` 声明所有必填项，缺 `--csv/--tenant-code/--batch-id` 由 argparse 直接报错，清晰。✔
2. **5–10 行样例全路径覆盖**：正常导入、中文别名/空格/大小写/BOM、名称解析与 code 优先、资产与显式 ID、重复重跑、`--strict` 回滚、`--dry-run` 不写库、租户隔离、文件/编码/CSV 错误——均有对应用例。✔
3. **租户隔离**：`tenant_id` 全程带参；测试断言外校 admin 列表不含他校单号、跨租户详情 403。✔
4. **轻量用户复用**：复用 `ensure_lightweight_user(tenant_id, name, no)`，不自行建 `User`；同 `reporter_no` 多次导入不重复建号。✔
5. **约束全部交 DB 兜底**：`target_type` 枚举、`description` 1–4000、`status`/`assigned_to` 组合 CHECK、FK 存在性——导入不绕过，失败以行错误呈现而非 500。资产 `AssetCard` 无 `active`，已改为校验关联 `Item.active`（与指令包备注一致）。✔
6. **不改 SQL/DDL、git 仅新增**：见第四节。✔
7. **`tests/test_import_repairs.py` ≥4 例且全绿**：实际 **31 例**，独立复跑全绿。✔

## 四、变更范围核查（git）

```
 M campus_inventory/settings.py        # 3 行：本中枢此前 deploy.sh 的 SECURE_PROXY_SSL_HEADER，与本次任务无关
?? ../deploy/*                         # 此前部署脚本（与本次任务无关）
?? apps/repair/management/             # 本次新增：import_repairs.py + 两级 __init__.py
?? tests/test_import_repairs.py        # 本次新增
?? docs/使用说明/报修CSV导入使用说明.md # 本次新增
?? docs/示例/报修CSV导入六行样例.csv     # 本次新增
?? docs/过程记录/报修CSV导入开发完成报告.md # Codex 交付报告
```

结论：**Codex 本次任务范围干净**——只新增命令 + 测试 + 文档；`sql/schema-v7.sql`、模型、迁移、在线服务、CI 均未改动。
（注：`apply_schema` 运行时执行的 `notifications/procurement/repair 0001_initial` 是 P8 阶段**手写**迁移，把表数 27→30，属历史基线，非本次生成。）

## 五、交付物清单

- 命令实现：`django/apps/repair/management/commands/import_repairs.py`
- 专项测试：`django/tests/test_import_repairs.py`（31 例）
- 使用说明：`docs/使用说明/报修CSV导入使用说明.md`
- 六行样例：`docs/示例/报修CSV导入六行样例.csv`
- 开发方报告：`docs/过程记录/报修CSV导入开发完成报告.md`

## 六、发现与建议（重要，供你决策）

**发现 A — 测试/CI 架构张力（非本命令缺陷，但建议修）：**
`grant-runtime.sql` 依据宪法把 `django_migrations`、`tenants` 的 INSERT/UPDATE 等从运行身份收回（"租户只能运维开、迁移记账只有迁移身份能动"）。但 Django 测试运行器以运行身份连库建 fixture 时**需要**读 `django_migrations`、建/改租户。CI 用 `postgres` 超户连库，虽能跑 fixture，却会让 `import_repairs` 的 `require_runtime()` 直接拒绝——**即 import 这类"强制运行身份"的命令在现有 CI 配置里其实跑不通**。Codex 是在自建集群临时补授权才跑出 297。

本次独立验收中，我在 `test_inventory` 仅做了**测试环境临时授权**（不影响项目授权文件、且 `require_runtime()` 仍通过）：
- `GRANT SELECT ON django_migrations TO inventory_runtime`（运行身份需读迁移图）
- `GRANT INSERT/UPDATE ON tenants TO inventory_runtime/inv_run`（fixture 需建/改租户）

建议二选一闭环：①CI 测试步改用"专用运行身份 + 上述测试期授权"的租户；②或调整测试策略使 fixture 经运维身份落库。否则 CI 的 297 数字是不可信的。

**发现 B — 提交状态：** 全部为未提交/未推送（沿用"全部做完再统一 push"铁律）。本次验收通过，可纳入统一提交批次。

## 七、风险提示

- 本命令已逻辑验收 + 单测验收通过，但**尚未接入真实部署库实测**（本地为测试库）。上线前建议在目标环境用真实学校数据跑一次 `--dry-run` 与首批量导入。
- 样例 CSV 为合成验收数据；真实对象/人员映射须用本校数据核对（`target_name` 靠资产编号/物品 code 或名称精确匹配，重名会按行错误拒绝，需提前规整主数据）。
- `created_at` 为导入时间，不支持历史提交时间回溯（指令包已声明 scope）。

---
验收人：控制中枢（WorkBuddy）｜验收日期：2026-11-01
