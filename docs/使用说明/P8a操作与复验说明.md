# P8a 操作与复验说明

日期：2026-11-01。范围：扫码本人领用、移动库存、二维码标签；不含请购业务、通知、报修。

## 安装与升级

先用迁移owner（例如inventory_app）在专用应用库执行 `uv run python manage.py apply_schema`，再检查verify_schema/verify_runtime。不要在postgres维护库迁移。运行web/命令时切回普通inventory_runtime成员LOGIN，例如inv_run；应用按实际权限检查，不硬编码登录名。

已有P7库只新增catalog/0002_add_item_qr，总迁移数6→7，27张业务表/14函数/32触发器不变。新增items.qr_token及两个索引，列可空，不在DDL回填；索引创建有锁，生产规模耗时需预发验证并安排维护窗口。已发布P7 SQL及迁移哈希不改，当前完整源与冻结制品关系见 [SQL说明](../../django/sql/README.md)。

同步依赖用 `uv sync --frozen`；不支持uv的平台使用 `python -m pip install --require-hashes -r requirements.txt`。新增qrcode[pil]已锁定，二维码在服务端本地生成。

## 教师领用

1. 微信或其他扫码器打开 `/s/pickup/<token>/`。页面展示本次扫码学校、物品、位置和当前可领取状态；不加载微信SDK或外部JS。
2. 已登录时收件人固定为当前用户；未登录时填写姓名和工号。已有正式账号须登录后返回此页或重新扫码，不能用免密登记冒用既有账号。
3. 提交普通HTML表单即可完成，不依赖JS。成功显示单号；刷新/重复提交原表单会重放原结果，不会重复出库。
4. 耗材按有效可用库位顺序分配，支持跨库位满足本次数量；损坏库存和停用库位不参与。固定资产每次只领取扫码的这一件，在库、无开放责任关系才可领取。
5. 登录失效、强制改密、学校停用/到期不会静默降级为匿名。未登录登记不签发JWT/会话，不获得库存列表、其他订单等通用访问权限。

免密姓名工号是**未核验声明**，不是实名认证。仅复用仍为启用user、带专用不可登录标记、姓名完全一致的免密记录；不同姓名、正式账号、停用/改密记录均拒绝。管理员核验后可在已有用户记录上设置初始密码，转为正常首登改密流程，无需新建同工号账号。出库备注、审计和响应会区分登录身份与免密登记。

匿名成功登记默认每校每类操作每分钟最多60次，环境PICKUP_ANONYMOUS_PER_MINUTE可设1–10000；同键重放不计数。另有：ANONYMOUS_SCAN_TENANTS 免密开关（`*`/空值/租户ID列表）、PICKUP_ANONYMOUS_MAX_QTY 单次数量上限（默认20）、ANONYMOUS_PER_EMPLOYEE_PER_HOUR 同工号每小时上限（默认10）、ANONYMOUS_NEW_IDENTITIES_PER_HOUR 每校每小时新工号上限（默认30）；请购、报修的免密提交共用这些限制。达到限制返回429，失败不遗留新用户或操作占位。此限制使用PG，不要求新增Redis依赖。

## 移动库存及标签

- `/s/inventory/`：admin/user登录后访问，支持名称/编号搜索和独立分页。完全复用既有stock/assets权限：教师只看耗材可用提示及本人当前在用资产，管理员看本校完整明细。
- “申请”链接到 `/s/requisition/new/`，本阶段返回待开放说明，不建请购单。
- `/s/labels/`：仅管理员。GET不写库，POST按钮为本校缺少二维码的启用耗材每批生成100条。新建耗材自动生成随机token，既有token不会被回填覆盖。搜索/分页后使用浏览器打印功能；打印CSS隐藏导航和操作控件。
- `GET /api/v1/qr/<token>/png`：仅管理员，先按当前学校校验对象，再用request.build_absolute_uri生成领用URL，返回PNG。用户/匿名不能批量打印，外校token返回404。

可选CLI：配置MAINTENANCE_ACTORS_JSON映射本校已改密的管理员后，用普通运行LOGIN执行 `generate_item_qr --tenant-id <id> --idempotency-key <key>`；支持batch-size和dry-run。按已有受控运维约定限定学校及审计身份；无需开启自动维护或证据清理开关。

## JSON API

POST `/api/v1/pickup/<token>/`，仅application/json，必需Idempotency-Key。耗材quantity必填且最多3位小数；资产可不传quantity，传入时必须为1。匿名另需字符串name/employee_no；工号支持字母数字、点、下划线、连字符。

不能传tenant_id、recipient_id、item_id、asset_ids、location_id等；对象由扫码token确定，收件人由当前身份确定。成功返回document/business_refs/pickup，首次201、重放200及Idempotency-Replayed头。相同键改数量或扫码对象返回409。

Bearer调用使用现有JWT鉴权；Cookie及匿名JSON调用须先GET页面取得CSRF Cookie，再带X-CSRFToken和Cookie提交。普通HTML表单已含CSRF与幂等键。接口不会把失败的Bearer/失效会话当作匿名请求处理。

## 独立复验

在专用PG16测试集群的DBA测试环境中运行verify_p1、verify_p2_p3、verify_p4、verify_p5、verify_p6、verify_p7、verify_p8a及verify_readiness。各脚本新建唯一临时库/LOGIN并清理；p6/p7另需Redis binary。verify_p8a验证P7存量升级、故障回滚、五类真实连接竞争、身份边界、强制CSRF表单和最终13项对账。

当前CI真实Git根为inventory-v4，入口为 `.github/workflows/ci.yml`，可运行 `python scripts/verify_ci_delivery.py --repo-root /Users/andy/Desktop/saas/inventory-v4` 检查模板及P8a门禁。commit/push由中枢/用户执行。

真人微信扫码、目标HTTPS域名、实际打印机和生产规模迁移仍须在目标环境验收；本地生成/独立解码PNG不等同于这些现场验收。

过程记录与历史制品统一见 [P8a归档目录](../过程记录/P8a/README.md)。verify_p8a.py 默认把新制品写入 docs/过程记录/P8a/复验制品/，可用 P8A_ARTIFACT_DIR 指定每次复验的独立目录。
