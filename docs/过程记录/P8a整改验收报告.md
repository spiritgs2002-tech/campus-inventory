# P8a 整改验收报告

- **验收对象**：Codex 交付的 P8a 整改（针对《P8a 独立验收整改报告》中记录的 O1 / O2 / O3 三项待整改点）
- **验收方**：项目控制中枢（独立验收，非 Codex 自检）
- **验收日期**：2026-11-01
- **验收结论**：✅ **ACCEPTED（整改通过）**

---

## 一、整改项与验收依据

| 编号 | 整改项 | 来源 | 验收方式 |
|---|---|---|---|
| O1 | `schema-v7.sql` 制品指纹与正文不一致（声明 `2e29a75c` ≠ 实测 `2521a010`） | 首验整改报告 | 重算"自 BEGIN 起"正文 SHA-256，比对声明；跑 `sync_schema_snapshot --check` |
| O3 | 根 `schema-v7.sql` 与 `django/sql/schema-v7.sql` 两份基线分叉、未统一 | 首验整改报告 | 比对三份 schema 文件关系与角色 |
| O2 | Codex 运行残片（原始 `.txt/.json/.png` 日志）散落于 `django/docs/` 与 `docs/过程记录/P8a/` | 首验整改报告 | 目录清点 + 隔离整理 |

> 注：O1/O3 为 SQL-first 宪法相关（指纹自洽、基线单一可信源）；O2 为仓库整洁度问题。

---

## 二、O1 指纹自洽 —— 已修复并验证 ✅

### 2.1 权威源（根 `schema-v7.sql`）
- 文件头声明指纹：`SHA-256(自 BEGIN 起) = 2e29a75c7441211875b3f43386125aa258a9ad77ef6ec0aa896b26d86b3cd49e`，正文行数 **728**。
- 独立重算"自 `BEGIN;` 起至文件末尾"正文：
  - `sha256 = 2e29a75c7441211875b3f43386125aa258a9ad77ef6ec0aa896b26d86b3cd49e`
  - 正文行数 = **728**
- **声明 == 实测，O1 闭环。**

### 2.2 快照同步（`django/sql/schema-v7-current.sql`）
- 该文件为根权威源的**逐字节自动同步副本**（`sync_schema_snapshot.py --write` 生成）。
- `diff` 确认与根 `schema-v7.sql` **字节一致**；`--check` 返回 `fingerprint_valid: true`、`snapshot_matches: true`。

### 2.3 静态预检
- `precheck_sql`：指纹 `2e29a75c…`、正文 728 行、当前快照已校验；美元引号块 15 个（CREATE FUNCTION 14 个）成对且以 `;` 终止 —— **[OK]**。
- `manage.py check`：**System check identified no issues (0 silenced)**。

### 2.4 列落点正确性
- `qr_token` 同时出现在两处，但属于**不同表**：`asset_cards.qr_token`（资产卡，第 181 行内联）、`items.qr_token`（物品，第 758 行 `ALTER TABLE items ADD COLUMN qr_token`）。
- 无"列已存在"冲突，新库按基线执行不会报错。

---

## 三、O3 基线统一 —— 已通过角色分工解决 ✅（非删除式）

Codex 未简单合并两份文件，而是明确了三份 schema 的**权威角色**，从设计上消除分叉导致的指纹漂移：

| 文件 | 角色 | 状态 |
|---|---|---|
| `schema-v7.sql`（项目根） | **权威当前源**：含 P8a `qr_token` 基线 + 末尾 `ALTER` 附录；指纹唯一可信 | 整改后自洽 |
| `django/sql/schema-v7-current.sql` | 权威源的**自动同步逐字节快照**（打包用，仅供只读校验） | 与根字节一致 |
| `django/sql/schema-v7.sql` | **冻结的 P7 历史基线**：供 `core/0001_initial` 固定哈希使用，**严禁覆盖** | 按 `django/sql/README.md` 保留 |

- 旧的 `django/sql/schema-v7.sql` 是 P7 发布时的冻结基线，被 `core/0001_initial` 以固定 SHA 引用；若用当前快照覆盖，会使 `0001_initial` 哈希校验失败或导致后续增量重复建列。故**保留而非删除**是正确的。
- 三份文件职责清晰、均有文档依据（`django/sql/README.md`），原始"两份分叉、指纹错乱"的问题已消除。

> **备注（非缺陷）**：现存在 3 份 schema 文件，数量偏多。建议在 `django/sql/README.md` 已说明的基础上，于仓库总览文档再点明三者关系，便于后续维护者理解。不 blocking。

---

## 四、SQL-first 哈希闸门 —— 运行时强制校验（已接线并实证）✅

- 迁移 `apps/catalog/migrations/0002_add_item_qr.py` 嵌入期望值 `c2984037239667a377f6678dcaa7c0c37497cf1ee1d023e9f511783b7727fa6d`（第 13 行）。
- `shared/db/operations.py::RunSQLFile.database_forwards` 在运行时：
  1. 读取 SQL 文件字节并 `sha256` 比对嵌入值，不符即抛 `RuntimeError`；
  2. 强制 `atomic=False`（SQL 文件自带 BEGIN/COMMIT）。
- `p8a-item-qr.sql` 实测 `sha256 = c2984037…` 与嵌入值**完全一致**。
- 实证：`apply_schema` 成功执行 `catalog.0002_add_item_qr`（未触发哈希异常），证明闸门真实生效。

---

## 五、实库功能验证（本地 PG16@55432）✅

### 5.1 建库与基线
- `CREATE DATABASE campus_inventory` → `apply_schema`：依次应用 `core.0002 / catalog.0001 / inventory.0001 / attachments.0001 / core.0003_runtime_access_guards / catalog.0002_add_item_qr`。
- `verify_schema`：**G0 passed: 27 business tables / 14 functions / 32 triggers**。
- `verify_runtime`：**runtime ACL assertions passed**。

### 5.2 P8a 专项验证（`scripts/verify_p8a.py`）
- **UPGRADE**：P7 数据保留；QR 扩展仅应用一次；重复迁移幂等 no-op。
- **CONCURRENCY 1–5**：相同键 → 单文档单库存移动、同响应身份；消耗品竞争 → 单完成单短缺不超卖；同资产 → 恰好一个开启关系与发放事件；匿名同员工/键 → 单追溯用户单文档；匿名短缺 → 失败追溯用户与占位操作回滚。
- **AUTHORIZATION**：正式身份冒用、跨校 token、收件人篡改均被拒绝。
- **ATOMICITY**：发料后失败回滚库存/单据/审计/操作/身份。
- **HTTP**：强制 CSRF 无 JS 表单/重放、二维码 PNG、移动盘点通过。
- **RECONCILE**：13/13 干净。
- 结论：**"P8a upgrade/runtime/concurrency/HTTP verification passed; temporary resources cleaned"**。

### 5.3 Django 回归测试套件
- `manage.py test tests --no-input`：**Ran 200 tests in 8.449s — OK**（较首验基线 192 项新增 8 项 P8a 测试，无回归）。
- 日志中的 `/health/ready/` 503、`Method Not Allowed`、`precheck_sql` 对 `broken.sql` 报"发现缺陷"均为**预期负向断言**（探针正确返回 503、预检正确拦截坏 SQL），退出码 0，非缺陷。

### 5.4 收尾
- 临时库 `campus_inventory` / `test_campus_inventory` 已 `DROP`，本地不留运行痕迹。

---

## 六、O2 运行残片整理 —— 已隔离 ✅

按"过程性文档归 `docs/`、原始机器日志不入库"原则，本轮将 Codex 运行残片**迁出并隔离**（仅移动、未删除，可随时还原）：

| 位置（整理前） | 动作 | 结果 |
|---|---|---|
| `docs/过程记录/P8a/` | 保留 `P8a开发完成报告.md` / `P8a归档清单.json` / `README.md`；其余 18 项（`*验收日志.txt`、`*回归日志.txt`、`*.json` 快照、`P8a示例二维码.png` 及 `整改复验/`、`复验制品/` 子目录） | 迁入 `docs/过程记录/P8a/_codex_raw_logs(可删)/` |
| `django/docs/`（应用源码目录） | 迁出全部 53 个 `.txt/.json/.png` 运行残片（P1–P7 及上线前补缺全周期） | 迁入 `docs/过程记录/_django_codex_logs(可删)/` |
| `django/docs/` 保留项 | `P7运维说明.md`（正式运维文档）、`ci/`（CI 参考） | 留在原地，未被误移 |

- 应用源码目录 `django/docs/` 现已恢复干净（仅剩正式文档与 CI 参考）。
- 所有被迁文件集中于两个 `(可删)` 隔离区，便于你在最终提交前统一决定保留或删除。

---

## 七、最终判定

| 整改项 | 结果 |
|---|---|
| O1 指纹自洽 | ✅ ACCEPTED |
| O3 基线统一（角色分工） | ✅ ACCEPTED |
| SQL-first 哈希闸门 | ✅ 接线并实证生效 |
| P8a 功能/并发/授权/原子性 | ✅ 实库全过 |
| 回归测试 | ✅ 200/200 OK |
| O2 运行残片 | ✅ 已隔离整理 |

**P8a 整改验收结论：ACCEPTED。** 代码与文档均达到可随 P8b/P8c 一并提交的状态。

### 遗留提醒（非阻塞）
1. 三份 schema 文件关系建议在主文档再点明一次（见第三节备注）。
2. `_codex_raw_logs(可删)/` 与 `_django_codex_logs(可删)/` 两个隔离区在最终 `git push` 前请确认：若要彻底剔除噪声，可整体删除这两个目录；若留作备查，建议改名为 `_codex_raw_logs(备查)/` 以避免误删。
3. redis 未在本地沙箱运行，P6/P7 的 celery/就绪探针端到端联通未在本地验证（与历史一致，待 CI 或目标环境跑通）。
