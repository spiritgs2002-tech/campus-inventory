# Codex 实现指令包（P8a）：扫码领用 + 移动库存 + 二维码标签

> 阶段：P8a（硬门禁）
> 日期：2026-11-01
> 来源蓝图：`docs/规划/P8增强方案（蓝图）.md`
> 执行者：ChatGPT Codex（用户侧外部 agent）
> 验收者：控制中枢（独立验收，出具 P8a 验收报告）
> 配套宪法：`schema-v7.sql`（唯一 DDL 规则源）、`开发规范（Django+PG）.md`

---

## 0. 本阶段范围（仅 P8a，三块）
- **M1 扫码一键领用**（核心）：微信/任意扫码器扫物品二维码 → 移动网页 → 确认领用 → 即时生成出库/领用单。
- **M2 移动库存查看**：教师（`user` 角色）移动端浏览库存（耗材+资产）并搜索。
- **M6 二维码标签生成与打印**：新增 `qrcode[pil]` 依赖；二维码 PNG 端点 + 管理员打印页。

> 不在本阶段：M3 请购申请、M4 站内通知、M5 报修工单（留待 P8b/P8c）。

---

## 1. 强约束（违反即判 REJECTED）
1. **SQL-first**：所有 DDL/触发器/函数/授权**手写 RunSQL 迁移**，禁止 Django `makemigrations --auto`/`squash` 生成表结构；所有结构变更**先更新 `schema-v7.sql`** 再写迁移。
2. **运行/迁移身份分离**：运行期写接口以 `inv_run`（运行时成员）执行；迁移/建表以 `inventory_app`（迁移 owner）。测试沿用 CI 配置（`postgres` 超级用户 + `DEBUG=true`）。
3. **禁止硬编码密钥/口令/域名**：二维码 base URL 用 `request.build_absolute_uri()` 动态生成；密钥一律环境变量。
4. **依赖锁定（B9）**：新增依赖必须同步 `requirements.txt`（带 hash）与 `uv.lock`；`uv export --frozen` 校验。
5. **G0 无回归**：27 表 / 14 函数 / 32 触发器不变（本阶段只新增 `items.qr_token` 列与若干函数/视图，不允许改动既有对象语义）。
6. **CI 门禁**：`manage.py test tests --no-input` 全绿（当前 160，新增后 ≥160）；`.github/workflows/ci.yml` 不被破坏。
7. **安全**：扫码领用强制「本人领取」——收件人 = 当前操作人；匿名免密仅作留痕，**不得**借此绕过权限签发非本人/非扫码物品的出库。

---

## 2. 现状复用清单（务必复用，禁止重写）
- 出库建单与签发：`apps/inventory/orders.py` 的 `command()`/`create()`、`posting.issue()`、`posting.lock_sources()`、`posting.lock_stock()`、`core.lock_order_context()`、`core.audit()`、`shared.idempotency.service.execute` —— 领用路径**必须复用** `posting.issue`，不得另写库存扣减逻辑。
- 资产二维码：`AssetCard.qr_token`（`posting.py:203` 用 `secrets.token_urlsafe(32)` 生成）；扫码解析现有 `queries.asset_detail(pk=, token=)`。
- 库存读取：`apps/inventory/queries.py` 的 `stock()`（耗材）、`assets()`（资产）—— M2 直接复用。
- 列表渲染：`read_views.html_read` + `inventory/list.html` + `inventory/page.html` 套路。
- 鉴权装饰器：`shared.rbac.permission.require_role`；匿名处理见 §4。
- 测试范式：`tests/` 下现有用例（如 `test_maintenance.py`、`order_fixtures.py` 使用 `SET LOCAL ROLE inventory_runtime`），新增测试照此风格。

---

## 3. 数据模型变更（SQL-first）

### 3.1 新增 `catalog.items.qr_token`
- **`schema-v7.sql`**：在 `items` 表新增列并加唯一约束：
  ```sql
  ALTER TABLE items ADD COLUMN qr_token text;
  CREATE UNIQUE INDEX uq_items_tenant_qr ON items(tenant_id, qr_token) WHERE qr_token IS NOT NULL;
  ```
  （同时把该 DDL 作为新章节写入 `schema-v7.sql`，保持其为唯一规则源。）
- **迁移文件**：`apps/catalog/migrations/00xx_add_item_qr.py`，内容仅 `operations=[migrations.RunSQL(...)]`，SQL 文本与上面一致；**不要**用 `AddField`（严守 SQL-first）。
- `catalog.Item` 模型（`apps/catalog/models.py`）：在 `Item` 增加 `qr_token = models.TextField(null=True, db_index=True)`（与 `AssetCard.qr_token` 同写法，`managed=False` 不变）。
- **可选回填**：新增 management 命令 `apps/catalog/management/commands/generate_item_qr.py`，对 `qr_token IS NULL` 的 `items` 批量写入 `secrets.token_urlsafe(32)`（去重）。**不强制**在迁移里回填，避免大表锁。

---

## 4. M1 扫码一键领用

### 4.1 新增统一扫码解析（token 同时覆盖资产与耗材）
- 新增 `apps/inventory/queries.py` 函数 `resolve_scan(token)`：
  - 先 `AssetCard.objects.filter(tenant_id, qr_token=token).first()` → 命中返回 `("asset", card)`；
  - 否则 `Item.objects.filter(tenant_id, qr_token=token, active=True).first()` → 返回 `("item", item)`；
  - 都未命中 → 抛 `BusinessError("NOT_FOUND", "二维码无效或不属于本学校", 404)`。

### 4.2 移动领用页（GET，公开可读）
- 路由（加到 `apps/inventory/urls.py`，或在新建 `apps/pickup/urls.py` 并 include）：
  - `GET /s/pickup/<slug:token>/` → `html_pickup`（server-rendered，移动端友好）。
- 视图 `apps/inventory/pickup_views.py: html_pickup(request, token)`：
  - 调用 `resolve_scan(token)`，渲染 `templates/pickup/pickup.html`：展示物品名/编号/位置/当前库存（耗材）或资产信息（资产）；耗材显示数量输入框（默认 1，max=可用量），资产固定 1 件；「确认领用」按钮；含 `{% csrf_token %}` 与 `idempotency_key`。
  - **GET 不要求登录**（公开），但页面顶部若 `request.principal` 存在则显示「当前登录：姓名」；否则显示「未登录，请填写姓名与工号」。
  - 页面**独立移动模板** `templates/pickup/pickup.html`（轻量、含 viewport、无后台导航 chrome），适配微信内置浏览器（纯 server-rendered 表单，避免被微信浏览器拦截的 JS）。

### 4.3 领用接口（POST，双轨身份）
- 路由：`POST /api/v1/pickup/<slug:token>/` → `api_pickup`。
- 视图 `api_pickup(request, token)`：
  1. 仅接受 `POST` + `application/json`；校验 `Idempotency-Key` 头（沿用现有幂等约定）。
  2. 解析 body：`quantity`（耗材必填且 >0、≤可用；资产忽略或固定 1）、`name`、`employee_no`（匿名时必填）。
  3. **身份双轨**：
     - 若 `hasattr(request, "principal")` 且 `request.principal.role in ("admin","user")` → `principal = request.principal`，`recipient_id = principal.user_id`。
     - 否则（匿名）要求 `name` 与 `employee_no` 非空 → 调 `ensure_lightweight_user(tenant_id, name, employee_no)`：
       - 以 `username = employee_no`、`tenant_id` 查/建 `core.User`：`role="user"`、`name=name`、`active=True`、`must_change_password=False`、`password_hash` = **项目既有密码哈希函数的不可用值**（参考 `shared.auth` 中生成密码哈希的 helper，写入一个永不匹配的值，使该账号无法登录）；`dept_id` 可空。
       - 用合成 principal：`types.SimpleNamespace(tenant_id=tenant_id, user_id=user.pk, role="user", family_id=None)`。
  4. **安全约束**：收件人 = 上面确定的 `recipient_id`（强制本人领取，禁止代领他人或任意物品）。
  5. 调用 `pickup_issue(principal, token, quantity, recipient_id, idempotency_key)`（见 4.4）。
  6. 返回 `{ "document": ..., "business_refs": [...] }`，并设 `Idempotency-Replayed` 头。

### 4.4 即时签发（复用 posting.issue，绕过 admin 执行闸）
- 新增 `apps/inventory/pickup.py: pickup_issue(...)`，**不**走 `orders.create`（其 `requisition` 类型只建 pending 且非 admin 不可 execute）。改为：
  ```python
  from . import posting
  from apps.core import services as core
  from shared.idempotency.service import execute

  def _work(ctx, ...):
      p = ctx["principal"]
      recipient = p.user_id
      obj = OutboundOrder.objects.create(
          tenant_id=p.tenant_id, order_no="PK-"+uuid4().hex,
          type="requisition", applicant_id=recipient,
          dept_id=ctx["users"][recipient].dept_id, status="pending",
          operation_id=ctx["operation_id"],
      )
      if asset:
          line = OutboundItem.objects.create(tenant_id=p.tenant_id, order_id=obj.pk,
              item_id=card.item_id, item_type="fixed", requested_qty=Decimal(1),
              recipient_id=recipient)
          OutboundAsset.objects.create(tenant_id=p.tenant_id, outbound_item_id=line.pk,
              item_id=card.item_id, asset_card_id=card.pk, closed_at=None)
          executions = [{"actual_qty": Decimal(1), "asset_ids": [card.pk],
                        "location_id": card.location_id, "recipient_id": recipient}]
      else:  # consumable
          stock = Inventory.objects.filter(tenant_id=p.tenant_id, item_id=item.id,
                      location_id=avail_location, quality="available").first()
          line = OutboundItem.objects.create(tenant_id=p.tenant_id, order_id=obj.pk,
              item_id=item.id, item_type="consumable", requested_qty=quantity,
              location_id=stock.location_id, recipient_id=recipient)
          executions = [{"actual_qty": quantity, "asset_ids": [],
                        "location_id": stock.location_id, "recipient_id": recipient}]
      # 即时签发（复用既有原子逻辑）
      posting.lock_sources(ctx, [{"asset_card_id": card.pk}] if asset else [])
      if not asset:
          posting.lock_stock(ctx, [(item.id, stock.location_id, "available")])
      posting.issue(ctx, obj, [line], executions)
      core.audit(p, "outbound", obj.pk, {},
                 {"action": "pickup", "status": "completed"}, "扫码领用")
      return {"document": record(obj), "business_refs": [{"type": "outbound", "id": obj.pk}]}

  def pickup_issue(principal, token, quantity, recipient_id, key):
      # 解析 + 校验 token 属于该 tenant；构建 discover
      def discover():
          ids = posting.discover_users(principal.tenant_id, [])
          ids.add(recipient_id)
          return ids
      def locked():
          tenant, users, p = core.lock_order_context(principal, discover)
          ctx = {"tenant": tenant, "users": users, "principal": p,
                 "operation_id": core.operation_id(p.tenant_id, p.user_id, "inventory.pickup", key)}
          return _work(ctx, ...)
      return execute(principal.tenant_id, principal.user_id, "inventory.pickup", key, {}, locked)
  ```
  - **约束校验**：资产必须 `card.status == "in_stock"` 且未开放发放关系；耗材 `quantity <= 可用库存`；`recipient_id` 必须等于 `principal.user_id`（本人领取）。
  - 复用 `posting.issue` 即自动把资产置 `in_use`、责任人和耗材库存扣减，语义与现有领用一致。

---

## 5. M2 移动库存查看
- 路由：`GET /s/inventory/` → `html_inventory_mobile`。
- 视图 `apps/inventory/pickup_views.py: html_inventory_mobile(request)`：
  - `@require_role("admin", "user")`（教师登录后浏览）。
  - 复用 `queries.stock(request.principal, request.GET)`（耗材）与 `queries.assets(request.principal, request.GET)`（资产）分别取列表；支持 `?q=关键词` 搜索（name/code）。
  - 渲染 `templates/pickup/inventory_mobile.html`（移动端列表 + 搜索框；缺货项旁放「申请」按钮，**本阶段仅占位链接** `href="/s/requisition/new/"` 待 P8b 实现，不报错即可）。
- 不新增 API；直接复用现有 `queries` 返回结构。

---

## 6. M6 二维码标签生成与打印
- **依赖**：`uv add "qrcode[pil]"` → `uv export --frozen --format requirements-txt > requirements.txt`（保持 B9 带 hash 锁定），并确认 `uv.lock` 更新、CI 中 `requirements` 比对通过。
- 新增 `apps/inventory/qr.py`：
  - `qr_png(token) -> bytes`：用 `resolve_scan(token)` 拿到对象 → 生成该对象的领用 URL `request.build_absolute_uri(f"/s/pickup/{token}/")` → `qrcode.make` 成 PNG 字节。
  - 注意：`qr_png` 内部需要 `request` 才能 `build_absolute_uri`；改为传入 `base_url` 或在视图层拼 URL 后传字符串给 `qr_png(url)`（更纯函数，便于测试）。
- 路由：`GET /api/v1/qr/<slug:token>/png` → `api_qr_png`，**`@require_role("admin")**`（仅管理员可批量打印）；返回 `HttpResponse(png, content_type="image/png")`。
- 管理打印页 `GET /s/labels/` → `html_labels`（admin）：列出资产与耗材（分页/搜索），每行展示内联 `<img src="/api/v1/qr/<token>/png">` + 名称/编号，便于打印贴纸。模板 `templates/pickup/labels.html`。

---

## 7. 测试（新增，沿用 CI 配置）
新增 `tests/test_pickup.py` 与 `tests/test_qr.py`，覆盖验收项：
- **V-scan-1**：为某资产与某耗材生成/补全 `qr_token`；`GET /s/pickup/<token>/` 返回 200 且页面含物品名（**不要求登录**）。
- **V-scan-2**：构造 `user` 角色 principal（复用现有测试夹具），`POST /api/v1/pickup/<asset_token>/` → 断言 `OutboundOrder` 状态 `completed`、`type=requisition`、`OutboundAsset` 已建、对应 `AssetCard.status == "in_use"`、`responsible_user_id == 该 user`。
- **V-scan-3**：匿名 `POST`（body 含 `name`+`employee_no`）→ 断言轻量 `core.User` 被创建（或复用）、`OutboundOrder.applicant_id` = 该轻量用户、`recipient` 一致；耗材路径断言库存扣减。
- **V-inv-1**：`user` 登录后 `GET /s/inventory/` 返回 200 且含物品列表。
- **V-qr-1**：admin `GET /api/v1/qr/<token>/png` 返回 `content_type=image/png` 且字节非空。
- **回归**：务必跑全量 `manage.py test tests --no-input`（配置：`DB_USER=postgres` 走 TCP 信任、`DEBUG=true`、`DB_HOST/PORT` 指向测试 PG、`REDIS_URL` 可空或本地）——确认 160 → ≥165 全绿；`check --deploy` 0 问题；`verify_schema` G0 仍是 27/14/32。

---

## 8. 交付物清单（Codex 完成后须存在）
- `apps/catalog/models.py`（Item.qr_token）+ `apps/catalog/migrations/00xx_add_item_qr.py`（RunSQL）
- `schema-v7.sql`（新增 items.qr_token DDL 章节）
- `apps/catalog/management/commands/generate_item_qr.py`（可选回填）
- `apps/inventory/queries.py`（`resolve_scan`）
- `apps/inventory/pickup_views.py`（`html_pickup`、`api_pickup`、`html_inventory_mobile`、`api_qr_png`、`html_labels`）
- `apps/inventory/pickup.py`（`pickup_issue`）
- `apps/inventory/urls.py`（或新建 `apps/pickup/urls.py` 并 include）新增上述路由
- `templates/pickup/pickup.html`、`inventory_mobile.html`、`labels.html`
- `requirements.txt` + `uv.lock`（新增 `qrcode[pil]`）
- `tests/test_pickup.py`、`tests/test_qr.py`
- **不要改动**：既有 `orders.py`/`posting.py` 的领用语义、`OutboundOrder` 表结构、`issue()` 逻辑（仅新增调用方）。

---

## 9. 验收口径（中枢独立验收，硬门禁）
- 代码走查：是否严守 §1 七条强约束（尤其 SQL-first、身份分离、本人领取、依赖锁定）。
- 功能：V-scan-1~3、V-inv-1、V-qr-1 全部通过；全量测试 ≥165 绿；G0 无回归。
- 安全：匿名免密路径无权限绕过（除本人领取的扫码物品外，不能签发出库/读受限数据）。
- 不通过则出具整改清单，返回 Codex 重修；通过则中枢出《P8a 独立验收报告》并盖硬门禁章。

---

## 10. 提交与推送（Codex 不负责，由中枢/用户执行）
Codex 交付后**不要**自行 push。由中枢复核并 `commit + push` 到 `origin/main`，随后中枢跑独立验收。
