# P8a 开发完成报告

> 2026-11-01归档：本报告保留开发交付时的验证结论，仅调整材料链接；后续独立验收及整改见同级目录上层的报告。

日期：2026-11-01  
依据：[Codex实现指令包（P8a）](../../规划/Codex实现指令包（P8a）.md)  
代码目录：`/Users/andy/Desktop/saas/inventory-v4/django/`  
状态：**M1 / M2 / M6 开发及本地验证完成，等待控制中枢独立验收。没有执行commit或push，没有进入P8b/P8c。**

## 一、完成范围

| 模块 | 完成内容 |
|---|---|
| M1 扫码本人领用 | 公开扫码页、登录/未登录双轨身份、JSON接口、纯HTML表单；耗材和固定资产即时生成completed领用出库单。收件人固定为当前身份，扫码对象不能被请求体替换。 |
| M2 移动库存 | 教师/管理员移动库存页、名称/编号搜索、耗材/资产独立分页；直接复用stock/assets查询和原权限。教师仍只见耗材可用提示与本人在用资产。申请入口返回待开放说明，不新增请购业务。 |
| M6 二维码标签 | qrcode[pil] 8.2、管理员PNG端点、标签搜索/分页/打印CSS、缺失耗材token批量生成。新建耗材自动生成token，已有token不被回填覆盖。URL由请求动态生成。 |
| SQL-first扩展 | items.qr_token可空列、租户内非空唯一索引、公开token定位用的非唯一查询索引。新增手写RunSQL迁移；G0保持27表/14函数/32触发器。 |

## 二、入口与操作

| 方法 / 路径 | 权限及行为 |
|---|---|
| GET `/s/pickup/<token>/` | 公开读取本次扫码的学校、物品、位置、可领取状态；不显示责任人、资产原值、事件历史等受限信息。 |
| POST 同一扫码页面 | 普通server-rendered表单，带CSRF与幂等键，无需JS即可完成领取。 |
| POST `/api/v1/pickup/<token>/` | application/json、Idempotency-Key；Bearer沿用JWT，Cookie/匿名请求校验CSRF。首次201、重放200，返回Idempotency-Replayed。 |
| GET `/s/inventory/` | admin/user登录后访问，沿用本校/本人范围。 |
| GET `/api/v1/qr/<token>/png` | 仅管理员、先校验本校对象，再生成PNG；外校404，教师403、匿名401。 |
| GET/POST `/s/labels/` | 管理员标签页；GET只读，POST每批为100条缺失耗材生成标签。 |
| GET `/s/requisition/new/` | 登录用户可读的P8b待开放说明。 |

完整配置、API字段、CLI及复验方法见 [P8a操作与复验说明](../../使用说明/P8a操作与复验说明.md)。后台导航已增加移动库存和管理员二维码标签入口。

## 三、安全与一致性

- 所有业务入口保留普通运行身份检查，兼容inv_run等inventory_runtime成员LOGIN；不硬编码数据库登录名或口令。
- 已登录领取继续B9联查，并在取得业务锁后复核。失效/撤销会话、强制改密不自动变为匿名写入。公开GET仍可显示扫码物品，但会明确阻止提交；已有可信学校范围时仍保留该范围。
- 匿名姓名工号是**未核验声明，不是实名认证**。只创建/复用带专用不可登录标记的启用user记录；正式账号、停用记录、姓名不符、已设置真实密码的记录均拒绝免密复用。没有JWT/会话签发，不能访问通用库存或订单接口。
- applicant/operator/recipient均绑定本次身份；拒绝请求体中的tenant_id、recipient_id、item_id、asset_ids、location_id等覆盖字段。匿名只在唯一、有效token定位后建立学校范围；跨对象/学校歧义token拒绝选择，不取“第一条”。
- 新建轻量用户、操作记录、出库头/明细、库存/资产事件与审计同事务提交，失败不遗留用户或幂等占位。未核验姓名/工号另写不可变审计，防止后续资料修改丢失原始声明。
- 库存实际扣减和资产发放完全复用posting.issue。耗材按可用库位顺序分配，损坏库存/停用库位不参与；成功凭证明确列出每个领取库位及数量。固定资产必须在库且无开放发放关系。
- 幂等摘要包含扫码token、规范数量、收件人和身份模式。同键改变物品或数量409；并发同键只处理一次。
- 匿名成功登记默认每校每分钟60次，可用PICKUP_ANONYMOUS_PER_MINUTE调整；重放不重复计数，超限失败不遗留新身份。此限制使用PG，无新增Redis业务依赖。
- 页面沿用严格CSP、模板转义和CSRF；二维码及页面使用私有/no-store响应，公开页不外发referrer。

## 四、指令伪代码的必要适配

1. 原伪代码先手工建OutboundAsset、再调用issue，会与issue内部创建关系重复。本实现只由issue建立责任关系。
2. 现有OutboundOrder没有dept_id列，因此不传该字段、不改出库头结构；申请人/收件人按既有模型存储。
3. family_id=None不能通过既有B9的lock_order_context。登录路径继续复用该函数；匿名路径采用独立、仅限扫码的上下文，校验学校和带不可登录标记的轻量用户，不放宽全局鉴权。
4. P7初始迁移和SQL已固定SHA，不能直接覆盖其制品。当前权威schema更新后，打包为schema-v7-current.sql；旧schema-v7.sql保留原字节，P8a新增p8a-item-qr.sql及catalog/0002_add_item_qr。新库依次应用冻结基线和增量；旧库只升增量，不重复ALTER。
5. JSON接口保持JSON-only；为了满足纯HTML/微信网页操作，扫码页面另支持普通POST表单。两条入口调用同一业务服务与幂等域。

这些适配没有更改orders.py/posting.py、既有出库语义或已发布迁移。新RunSQL子类只镜像ORM字段状态，不使用AddField生成DDL；所有模型继续managed=False。源/快照/增量及旧字节保护见 [SQL制品说明](../../../django/sql/README.md)。

## 五、主要改动文件

以下代码路径相对django/：

| 文件 | 内容 |
|---|---|
| `apps/catalog/models.py`、`services.py` | Item.qr_token、新建耗材生成token |
| `apps/catalog/migrations/0002_add_item_qr.py`、`shared/db/operations.py` | 固定哈希SQL增量及手写模型状态映射 |
| `sql/p8a-item-qr.sql`、`sql/schema-v7-current.sql`、上级schema-v7.sql | 新增列/索引的权威章节、增量和完整快照 |
| `apps/inventory/queries.py` | 统一扫码解析与限定范围 |
| `apps/inventory/pickup.py`、`pickup_views.py` | 双轨身份、本人领用服务、HTML/JSON/移动库存/标签视图 |
| `apps/inventory/qr.py`、`apps/catalog/management/commands/generate_item_qr.py` | PNG编码与受控批量回填 |
| `apps/inventory/urls.py`、`templates/pickup/`、`static/pickup.css`、`templates/base.html` | 路由、独立移动模板、打印样式及导航 |
| `pyproject.toml`、`uv.lock`、`requirements.txt` | 新增qrcode[pil]及哈希锁定 |
| `tests/test_pickup.py`、`test_qr.py`、`test_p8a_migrations.py` | 新增32项测试 |
| `scripts/verify_p8a.py` | P7存量升级、失败回滚、真实运行身份、五类并发及HTTP验证 |
| 项目根`.github/workflows/ci.yml`、`docs/ci/ci.yml.template`、`scripts/verify_ci_delivery.py` | 保留既有CI并接入P8a验证，根入口与模板同步 |
| `README.md`、`sql/README.md`、`docs/P8a操作与复验说明.md` | 升级、操作和复验说明 |

原测试仅将迁移条数断言6更新为7、当前权威快照指向新版本；原160项全部保留。旧SQL基线、授权制品、orders.py/posting.py及已发布迁移均校验未改。

## 六、本次实际验证

环境：Python3.13.15、Django5.2.18、PostgreSQL16测试集群。DB验证均使用脚本创建的唯一临时库和身份，结束清理；没有迁移现有业务库。以下是本次新测，不沿用历史通过结论。

| 验证项 | 结果与证据 |
|---|---|
| 全量Django及迁移权限 | **192/192 OK**：原160+新增32；G0=27/14/32，7条迁移；无模型状态漂移；SQL 21/21；普通LOGIN越权拒绝及提交前回滚通过。[日志](P8a全量验收日志.txt) |
| V-scan-1~3、V-inv-1、V-qr-1 | 23项扫码/身份/库存用例、7项QR用例全部通过；另2项SQL源与迁移制品保护测试通过，包含正式账号防冒用、跨校、过期/停用、CSRF、回放、超领、回滚和打印权限。 |
| P7→P8a存量升级 | 先建立6条P7迁移及存量数据；注入增量COMMIT前失败，新增列回滚；实际升级后存量数据保留、列可空，重复迁移无动作。[日志](P8a升级及并发验收日志.txt) |
| 五类真实连接竞争 | 同键领用、耗材竞争、同一资产竞争、匿名同工号同键、匿名库存不足均通过；未重复扣减/发放，失败身份回滚。[同一日志](P8a升级及并发验收日志.txt) |
| 13项内部一致性 | 登录/匿名领用、并发、故障及HTML提交后13项对账全部为0。[对账结果](P8a对账结果.json) |
| 既有阶段回归 | [认证主数据](P8a认证主数据回归日志.txt)、[单据](P8a单据回归日志.txt)、[库存对账](P8a库存对账回归日志.txt)、[附件](P8a附件回归日志.txt)、[清理调度](P8a清理调度回归日志.txt)、[就绪探针](P8a就绪探针回归日志.txt)全部通过。 |
| 依赖锁定及安装 | 冻结导出逐字节一致；全新Python3.13环境使用pip --require-hashes安装成功，pip check正常，QR生成运行通过。[日志](P8a依赖安装验收日志.txt) |
| QR有效性 | PNG被Pillow识别，动态请求URL用例通过；独立zxing-cpp成功解码最终合成PNG，文件SHA记录一致。解码器未加入应用依赖。[日志](P8a二维码解码验收日志.txt)、[合成示例](P8a示例二维码.png) |
| 移动页面 | 本地普通运行身份预览，实际浏览器检查1280及390px；390px下页面scrollWidth=390，表单358px、输入/按钮320px，无横向溢出。只读检查及清理已完成。[记录](P8a页面检查记录.json) |
| 静态及配置 | ruff通过；check/check --deploy为0 issues；precheck退出0；配置示例通过；CI语法/模板/P8a步骤、冻结SQL字节与原领用逻辑未改验证通过。[日志](P8a静态配置及制品验收日志.txt)、[CI清单](P8aCI交付清单.json) |

预期负向用例会记录403/409/503或破损SQL的FAIL输出，以套件最终OK和脚本退出0为准。

## 七、交接边界

- P8a本地开发完成，**不自行宣称独立硬门禁通过**。由控制中枢走查、提交/推送并验证真实GitHub Actions。
- 真实微信扫码、目标HTTPS域名、纸质标签/打印机及生产规模迁移锁预算需在目标环境验收；合成二维码中的testserver不是可供实际领用的地址。
- 匿名模式只提供未核验登记和限定物品的即时记账，不证明真实身份或现场交付；需要实名管控时应使用正式登录账户及学校流程。
- M3请购、M4通知、M5报修不在本次范围；本次仅提供不会404的请购占位说明。
