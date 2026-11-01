# P8a 独立验收报告

- **验收对象**：P8a（扫码领用 + 移动库存 + 二维码标签）
- **交付方**：ChatGPT Codex（用户转交实现）
- **验收方**：控制中枢（独立验收，硬门禁）
- **验收日期**：2026-11-01
- **验收依据**：`docs/规划/Codex实现指令包（P8a）.md` 的验收标准（V-scan-1~3、V-inv-1、V-qr-1 + 回归 + SQL-first 合规 + 依赖锁定）

---

## 一、验收结论

**判定：ACCEPTED（通过，含 3 项非阻塞观察/整改建议）**

P8a 三大能力（M1 扫码领用、M2 移动库存查看、M6 二维码标签）均已落地，且静态代码质量、SQL-first 合规、安全红线、全量回归、运行探活、二维码生成**全部实测通过**。唯一合规瑕疵是 `schema-v7.sql` 文件头指纹未随 DDL 增补更新（功能性无影响），列为整改项 O1。

---

## 二、静态核查（代码审查）

| 检查项 | 结果 | 证据 |
|---|---|---|
| SQL-first：items.qr_token 进入权威 schema | ✅ | 根 `schema-v7.sql` 第 758-760 行 `ALTER TABLE items ADD COLUMN qr_token text` + 唯一/查找部分索引 |
| 迁移与 SQL 文件哈希一致 | ✅ | 迁移 `0002_add_item_qr.py` 声明哈希 `c2984037…` 与 `sql/p8a-item-qr.sql` 实际 sha256 **逐字节一致** |
| 模型字段与迁移一致 | ✅ | `catalog/models.py:80` `qr_token = TextField(null=True, db_index=True)`，与迁移（nullable）一致 |
| 依赖锁定（B9） | ✅ | `requirements.txt` 含 `qrcode==8.2` + `pillow==13.0.0`（带 hash）；`uv.lock` 含 `qrcode 8.2`（extras=pil）|
| 路由接线 | ✅ | `apps/inventory/urls.py:8-13` 注册 `/s/pickup/<token>/`、`/api/v1/pickup/<token>/`、`/s/inventory/`、`/api/v1/qr/<token>/png`、`/s/labels/`、`/s/requisition/new/` |
| 限流配置项 | ✅ | `settings.py:181` `PICKUP_ANONYMOUS_PER_MINUTE`（env 覆盖，默认 60，范围 1–10000，越界 `ImproperlyConfigured`）|
| 安全红线（本人领取） | ✅ | `pickup.py:144` `recipient_id != p.user_id → 403`；匿名只能本人领取 |
| 安全红线（轻量用户隔离） | ✅ | `pickup.py:21` `UNVERIFIED = "!pickup-unverified:"` 前缀；`ensure_lightweight_user` 拒绝复用正式/禁用/需改密身份 |
| 幂等 | ✅ | `pickup_issue` / `submit` 均 `validate_key` + `execute(...)`（幂等服务）|
| 匿名免密不走 JWT | ✅ | `AnonymousPickup` 合成 principal，非 `orders.identity`；API 端点 CSRF/Bearer 校验 |
| 迁移数断言已同步 | ✅ | `test_auth_masterdata.py:565` 断言 `== 7`（P8a 前为 6，已随第 7 个迁移修正）|

---

## 三、动态验证（沙箱实测）

| 验证项 | 命令/方式 | 结果 |
|---|---|---|
| Schema 完整性 G0 | `manage.py verify_schema` | ✅ **27 业务表 / 14 函数 / 32 触发器**（无回归）|
| 运行身份 ACL | `manage.py verify_runtime` | ✅ runtime ACL 断言通过 |
| 生产配置体检 | `manage.py check --deploy` | ✅ 0 错误；仅 6 条标准 WARNING（HSTS/SSL/SESSION_COOKIE_SECURE 等部署加固项；其中 W009/W018 由本次验收用的 `DEBUG=true`+测试短密钥触发，非代码缺陷）|
| **全量回归** | `manage.py test tests --no-input`（postgres 超级用户 + `DEBUG=true`，CI 同款）| ✅ **Ran 192 tests in 8.347s — OK**（P8a 前 160 → 192，+32 例，无回归）|
| 二维码 PNG 生成（M6）| 进程内 `qr.qr_png(...)` | ✅ 803 字节，合法 PNG 头 `89504e47…` |
| 服务以运行身份启动 | gunicorn（`DB_USER=inv_run`）| ✅ `/health/live/` → **200** |
| 路由 + 鉴权 | 实跑 curl | ✅ `/s/inventory/`（未登录）→ **302**（重定向登录，路由+鉴权正常）；`/s/pickup/无效token/` → **404**（无效二维码正确拒绝，路由+运行时守卫+扫码解析正常）|

> 注：日志中出现的 `RuntimeError: injected DB failure` / `creation event failed` 是测试刻意注入的**负面用例**（验证错误处理路径），属预期输出，套件整体 OK。

---

## 四、验收标准逐项对照

| 标准 | 要求 | 验证方式 | 结论 |
|---|---|---|---|
| V-scan-1 | 扫码页 `/s/pickup/<token>/` 对有效 token 渲染领用表单 | `test_pickup.py`（23 例）+ 实跑无效 token 返回 404 确认路由/守卫 | ✅ |
| V-scan-2 | 登录用户扫码领用 → 生成 requisition 出库单并即时完成 | `test_pickup.py` 鉴权路径 + `pickup_issue` 复用 `posting.issue` | ✅ |
| V-scan-3 | 匿名（姓名+工号）扫码领用 → 建轻量用户 + 出库单，且不可登录 | `test_pickup.py` 匿名路径（轻量用户 `!pickup-unverified:` 前缀、越权拒绝）| ✅ |
| V-inv-1 | 移动库存页 `/s/inventory/` 对 user 角色可访问、可分页/搜索 | `html_inventory_mobile`（`require_role("admin","user")`）+ 实跑 302 未登录守卫 | ✅ |
| V-qr-1 | 管理员可为耗材/资产生成二维码 PNG 标签 | `qr.qr_png` 实测 + `test_qr.py`（7 例）| ✅ |
| 回归 | 全量测试全绿，G0 无回归 | 192 tests OK；G0 27/14/32 | ✅ |

---

## 五、交付文件清单（P8a 新增/修改）

**新增（P8a 特性）**
- `django/apps/inventory/pickup.py`（扫码领用核心逻辑）
- `django/apps/inventory/pickup_views.py`（html/api 视图：扫码页、移动库存、二维码 PNG、标签页）
- `django/apps/inventory/qr.py`（二维码生成 + 批量 token 分配）
- `django/sql/p8a-item-qr.sql`（items.qr_token DDL，手写 RunSQL）
- `django/apps/catalog/migrations/0002_add_item_qr.py`（哈希校验迁移）
- `django/templates/pickup/`（pickup.html、inventory_mobile.html、labels.html、requisition_pending.html）
- `django/static/pickup.css`
- `django/tests/test_pickup.py`（23）、`test_qr.py`（7）、`test_p8a_migrations.py`（2）
- `django/scripts/verify_p8a.py`（P8a 验收守卫脚本）

**修改（支撑/收尾）**
- `schema-v7.sql`（根，追加 items.qr_token ALTER）⚠️ 见 O1
- `django/requirements.txt`、`django/uv.lock`（锁定 qrcode/pillow）
- `django/campus_inventory/settings.py`（PICKUP_ANONYMOUS_PER_MINUTE）
- `django/apps/inventory/urls.py`（6 条新路由）
- `django/apps/catalog/models.py`（qr_token 字段）、`services.py`
- `django/apps/inventory/queries.py`（resolve_scan/stock/assets/page）
- `django/shared/db/operations.py`（AddItemQRSQL 运算）
- `django/.github/workflows/ci.yml`、`scripts/verify_p1.py`、`precheck_sql.py`、`test_auth_masterdata.py`、`test_models.py` 等（CI/预检/断言同步）
- `django/.env.example`、`README.md`、`docs/ci/`（文档同步）

---

## 六、观察项与整改建议（非阻塞）

- **O1（合规瑕疵）**：根 `schema-v7.sql` 文件头声明指纹 `2e29a75c…`，但按"SQL 正文自 BEGIN 起"计算实际为 `2521a010…` —— Codex 追加 items.qr_token 的 ALTER 后**未同步更新指纹**，违反该文件自身"每次修改须更新指纹"的宪法自检纪律。功能性无影响（迁移哈希已正确锁定 DDL），建议 Codex 用其指纹工具重算并回填。
- **O2（仓库整洁）**：`django/docs/` 下遗留约 20 个 Codex 交付日志（`P8a*.txt / *.json / *.png / *.md`），属 Codex 工作过程产物，**非项目交付物**。提交前建议归档至 `docs/过程记录/` 或删除，避免污染仓库。
- **O3（基线去重）**：仓库内存在两份 `schema-v7.sql`（根目录已更新 50584B；`django/sql/` 旧版 50237B 未更新）。建议统一为一份权威基线，避免后续"唯一来源"漂移。

---

## 七、结论与下一步

P8a **ACCEPTED**。三大新能力（扫码即时领用、移动库存查看、二维码标签生成）功能完整、测试充分（192 全绿）、安全红线到位、SQL-first 与依赖锁定合规。O1–O3 为收尾级整改建议，不阻塞本阶段。

- 待用户确认后：可提交（含本报告与 O2 清理）；随后启动 **P8b（请购申请 + 站内通知 + 入库联动，硬门禁）** 与 **P8c（通用报修工单，软门禁）**。
- 按用户既定策略：**push 推迟至全部功能（P8a/P8b/P8c）做完后统一提交**。
