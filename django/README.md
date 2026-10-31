# 学校总务库存系统 · P0–P7

Python 3.13、Django 5.2、PostgreSQL 16；依赖由 uv.lock 锁定。

```sh
uv sync --frozen
export DEBUG=true
export SECRET_KEY="$(python3 -c 'import secrets; print(secrets.token_urlsafe(64))')"
export ALLOWED_HOSTS=localhost,127.0.0.1,testserver
export DB_HOST=localhost DB_PORT=5432 DB_NAME=campus_inventory DB_USER=inventory_app
export CSRF_TRUSTED_ORIGINS=http://localhost:8000,http://127.0.0.1:8000
# DB_PASSWORD 从环境/secret manager 注入；程序不自动读取 .env。
uv run python manage.py check
uv run python manage.py precheck_sql
uv run python manage.py verify_config_examples
```

`.env.example` 和本示例统一使用 `campus_inventory` 作为应用数据库名；这是示例值，应按 DBA 创建的实际专用库名覆盖。`DB_HOST` 是 settings 实际读取的配置键：TCP 示例为 `DB_HOST=localhost`，Unix socket 示例为 `DB_HOST=/tmp`。P0 记录里的 `DB_NAME=postgres` 仅供当时只读启动检查；P1 建库不能应用到该维护库。

| 配置场景 | DB_NAME 示例 | 用途 |
|---|---|---|
| 应用运行及应用迁移 | campus_inventory | 专用应用库，与 `.env.example` 一致；按实际库名覆盖 |
| 自动验收脚本管理连接 | postgres | 仅供脚本创建和清理独立临时库，脚本不在此维护库应用业务 DDL |

`verify_config_examples` 校验提交的 `.env.example`、README 和实际 DATABASES.default.HOST 表达式，并在独立 Python 进程回读自定义 socket 路径；不连接数据库，也不读取或限制你的本地 `.env`。输出键名十六进制 `44425f484f5354` 及字节值 `[68,66,95,72,79,83,84]`，方便独立验收核对原始键名。CI 已纳入此命令，拼错键名或重复赋值会阻断。

## 迁移与验证

先由 DBA 创建**专用空应用库**及独立迁移身份。迁移身份须为库/对象 owner，首次创建权限角色时需 CREATEROLE，或由 DBA 预建 `inventory_runtime`。日常运行身份须另建为普通 LOGIN 并授予 `inventory_runtime`，不得使用迁移身份启动生产服务。

```sh
# 仅在迁移进程使用 DB_USER/DB_PASSWORD 注入迁移身份：
uv run python manage.py apply_schema
uv run python manage.py verify_schema
uv run python manage.py verify_runtime
# 完成后改为普通运行身份再启动服务。
uv run python manage.py runserver 127.0.0.1:8000
```

`apply_schema` 调用 Django migrate，重复执行不重复建表；存在未记账表时拒绝接管。`verify_schema` 断言 27 张业务表 / 14 个函数 / 32 个触发器；`django_migrations` 是第 28 张表，计数排除 Django 元数据。`verify_runtime` 执行打包授权断言，额外保护迁移记账表、检查 NOLOGIN；实际登录身份还需单独核对成员关系和权限。

## DDL 与制品约束

上级 `schema-v7.sql` 是唯一 DDL 来源。`sql/` 为其**逐字节副本**及验证脚本，使此目录能独立交付；副本不得单独修改。源文件与副本的对应关系、同步及复核步骤见 [SQL 制品说明](sql/README.md)。迁移中的 SHA-256 固定 schema/grant 字节，变更会拒绝执行。后续数据库变更必须新增手写版本迁移，不得重写已应用迁移及制品。

`core/0001_initial` 的两个 `RunSQLFile`（RunSQL 子类）分别整文件执行 schema、grant。两文件各自带 BEGIN/COMMIT，因此迁移设 `atomic=False`；**schema 是单事务，schema + grant + Django 记账并非一个共同事务**。schema 失败会回滚全部业务 DDL；若 schema 已提交而 grant/记账失败，停止重试，由 DBA 验证后恢复备份或重新建立空测试库；`apply_schema` 不自动 fake、不删库、不覆盖已有表。

其余迁移使用 `SeparateDatabaseAndState(database_operations=[])` 保存 27 个 unmanaged ORM 模型状态，不执行 DDL。两张无 id 的表使用 Django 5.2 CompositePrimaryKey；复合关系由 ForeignObject 映射，仍以数据库复合外键兜底。xid8 由触发器赋值并 RETURNING；生成列不参与 ORM 写入。

跨 app 关联使用字符串模型名。P2 已接入会话族与 JWT，P3 的服务和查询均使用认证结果中的租户 ID，拒绝请求体覆盖租户。Django session 使用签名 Cookie 后端，服务端每次通过 B9 单条联查核验数据库会话族；不需要内置 auth/session/admin 表。

新增手写迁移 `core/0003_runtime_access_guards`：授予运行角色对 django_migrations 的 SELECT，以支持 runserver 状态检查；并将触发器函数 inbound_insert_guard 定点设为 SECURITY DEFINER，以便它在运行角色没有入库头 UPDATE 权限的情况下执行父单行锁。该函数保留固定 search_path=pg_catalog,public、按 NEW.tenant_id 过滤及创建事务 xid 守卫；运行角色不能在 public 创建对象。入库头不可改、记账表不可写的权限与触发器守卫继续生效。没有修改已发布初始迁移、schema 或 grant 制品。

## 首次开通与浏览器操作

在独立运维身份下完成专用库迁移后，开通租户及首位管理员。密码通过隐藏输入提示或 INITIAL_ADMIN_PASSWORD 环境变量注入，不能放到命令行参数中：

```sh
uv run python manage.py provision_tenant --code SCHOOL --name 示例学校 --admin-username first_admin --idempotency-key ops-provision-001
```

该命令属于受控租户开通流程，运行角色不能执行；同键同参数重放不重复开通。开通后切换为具备 inventory_runtime 权限的普通登录身份运行服务，从 `/auth/login/` 登录。首位管理员首次登录必须改密，再建立第二位管理员。日常页面与 API 的业务操作全部受运行身份检查；迁移 owner 或超级用户会收到 503。

- 主数据页面：`/manage/departments/`、`/manage/users/`、`/manage/tenant/`，以及 `/catalog/items|categories|locations|suppliers/`。
- 管理员可新增、编辑和停用；普通用户只能查询启用的目录。停用保留原记录，不能物理删除。
- 管理员停用/降级时至少保留一位活跃管理员；有在用资产或 pending 申请时须先交接。禁用、降级与重置密码会撤销目标用户的所有会话族。
- 租户 code/status/expire_at 的修改、增删租户属于受控运维；学校设置页只开放运行角色获准修改的字段。修改自批开关必须填写原因并记录审计。
- Bootstrap 5.3.3、HTMX 2.0.4、Alpine CSP 3.14.9 使用本地固定资源，来源及许可证见 static/vendor/README.md。没有内联脚本或 unsafe-eval。

## JSON API 契约

认证接口：`POST /api/v1/auth/login|refresh|logout|change-password/`，使用 JSON，并携带 `Idempotency-Key`、`Origin`（必须在 CSRF_TRUSTED_ORIGINS 中）及 `X-Inventory-Request: 1`。登录参数为 tenant_code/username/password；改密为 old_password/new_password；刷新/登出请求体为空对象。access JWT 有效期 300 秒；refresh 通过 httpOnly、生产 Secure、SameSite=Strict、Path=/api/v1/auth 的 Cookie 保存，有效期 7 天。

刷新会创建同族后继行；旧凭证在 10 秒内重现返回 REFRESH_ALREADY_ROTATED，超窗重现撤销整族（即使旧行自身已过期也检测重用）。刷新丢响应时若客户端已收到新 Cookie，可用新 Cookie 和新幂等键继续；若仅剩旧 Cookie，则按上述重用策略处理，必要时重新登录。登出清除全族 parent_session_id。改密、禁用和重置密码撤销该用户全部会话族；强制改密状态不能访问主数据。

主数据接口：`/api/v1/master/departments|users|tenant/`、`/api/v1/catalog/items|categories|locations|suppliers/`。GET 集合为分页列表（q/page）；GET `集合/<id>/` 为详情；POST 集合新增，PUT `集合/<id>/` 为完整编辑；DELETE `集合/<id>/` 为停用（JSON 可含 reason）。tenant 集合表示当前租户设置，不支持通过业务 API 开通/删除租户。

API 以 `Authorization: Bearer <access_token>` 鉴权。所有写命令携带 Idempotency-Key（1–128字符），同键同参数重放返回原结果，异参返回409；只清过期响应后返回 ALREADY_PROCESSED。使用签名 Session Cookie 而非 Bearer 时，JSON 写入也需要合法 CSRF Cookie 及 X-CSRFToken。HTML 表单已内置 CSRF 与幂等键，HTMX 自动携带请求头。

创建用户参数：username/name/role/dept_id/password（初始密码），active 默认 true；编辑时提交完整字段，password 留空表示保持原密码。密码至少12字符、UTF-8最多72字节，bcrypt cost=12。用户列表、审计及幂等响应不包含密码、密码哈希或明文凭证。编辑其他主数据也应提交完整字段，active=false 表示停用。

分类参数：code/name/type/parent_id/active；物资：code/name/type/category_id/unit/spec/reference_price/safety_stock/active；库位：name/active；供应商：name/contact/active。分类和物资 type 创建后冻结，父分类不得成环；引用限于同租户、同类型且启用的选项（编辑时可保留原有停用关联）。code、用户名、名称按基线的租户内约束判重，部门/供应商等名称统一去首尾空白。

供应商 contact 为可选敏感联系信息，写入前通过 shared/crypto 加密。须配置 PII_ENCRYPTION_KEYS（逗号分隔的 Fernet keys，首项加密、全部用于解密）；缺密钥拒绝保存联系信息，历史明文值拒绝自动展示。API 将 contact 视为只写字段；管理员编辑页可解密显示，响应 no-store，审计只记录联系信息发生变更。不要把该密钥、AUTH_SIGNING_KEY 或 SECRET_KEY 提交到仓库。

每个身份判定由单条会话族/用户/租户联查完成；写命令在取得锁后再次复核权限，未声称整个写请求只有一条 SQL。操作顺序为幂等键→租户→按ID排序用户→目标业务记录。租户/用户采用 FOR NO KEY UPDATE，避免操作键外键隐含 KEY SHARE 造成升级死锁。仅对40P01/55P03最多尝试3次且复用原幂等键；网络失败不自动重试。连接 lock_timeout=3秒、statement_timeout=15秒。

## P4 单据

页面入口为 `/inbound/`、`/outbound/`、`/scrap/`、`/adjustment/`；集合 `/new/` 新建，`/<id>/` 只读详情，`/<id>/copy/` 复制为未保存的新表单。单据服务以 `schema-v7.sql` 为准：入库头没有 status/draft 字段，入库立即生效；其他申请提交时同时创建完整头、明细，pending 期间也不得追加或改写申请内容。需要修改时取消并复制新申请，保留原记录。入库不可取消，只能通过合法退货、归还或调整流程处理差额。

| 单据 | 业务类型 | 状态流转 / 权限 |
|---|---|---|
| inbound | purchase / opening / return | 管理员即时执行；purchase 必填供应商；不对外开放派生 adjustment_gain |
| outbound | requisition | 用户为本人申请、管理员可代申请；pending → completed / cancelled；实发少于申请须填原因，全零实发须取消 |
| outbound | purchase_return / transfer | 管理员即时执行；采购来源及累计额度复核，转移为同一 transfer_id 的归还 + 再发放 |
| scrap | scrap / loss / recovery / stock_damage | pending → approved / rejected / cancelled；用户只可申请本人在用资产处置或本人遗失的找回；耗材报损仅管理员 |
| adjustment | count / quality_transfer | 仅管理员；pending → approved / rejected / cancelled；审批复核原账面数量及版本，质量转换还复核目标版本 |

管理员处理所有本校单据，普通用户只能查询/取消本人出库与处置申请。审批默认禁止自批；租户明确开启例外并提交原因时记录自批及开关快照，原因另记不可变审计。资产报废必须先归还；遗失会关闭实际在用关系，找回不恢复原责任人。

JSON 集合 `/api/v1/inbound|outbound|scrap|adjustment/` 支持 GET 列表、POST 新单；`/<id>/` GET 详情、DELETE 取消（JSON reason 必填）；PUT 不开放。`/<id>/copy/` GET 复制数据，已完成领用只复制未满足量。POST `/<id>/execute/` 用于出库，`/<id>/approve|reject/` 用于处置/调整，`/<id>/cancel/` 取消。所有写入仍须 Idempotency-Key、JWT 或合法 Cookie + CSRF。

新单 JSON 为 `{"type":"requisition","lines":[{"item_id":1,"quantity":"2.000","location_id":1}]}`；reason 为处置/调整必填，调整另需 reason_category。执行为 `{"lines":[{"id":明细ID,"actual_qty":"2.000","asset_ids":[]}]}`，须确认全部明细且 ID 不重复；可补充申请时未填的 location_id/recipient_id，不能改写已填值。实发不足另填 shortfall_reason。固定资产实发须选择件数相等的 asset_ids。

调整前 GET `/api/v1/inventory/observe/?item_id=…&location_id=…&quality=available` 获取 book_qty/inventory_version/observed_at。盘点提交这些原值以及 counted_qty、差额 quantity、direction；质量转换改填 to_quality/target_inventory_version，不填 counted_qty。固定资产只支持逐件盘盈，不填耗材快照；固定资产盘亏使用遗失流程。浏览器表单可“观察当前账面”并自动计算盘点差额。

写命令锁顺序延伸为：操作键→租户→排序用户→目标单据→按表/ID排序来源→排序资产卡→完整维度排序库存。不可变的 inbound_items 不授予 UPDATE；其来源额度使用包含租户/表/ID 的事务 advisory lock，其他可变来源使用行锁。该例外避免 PostgreSQL 的 SELECT FOR UPDATE 权限需求破坏不可变权限边界。单据流转及其必要流水、资产事件、关系结案在同一事务提交；失败全回滚。重试会从原始明细副本重新执行，避免复用已回滚的派生 ID。

P4 包含流转必需的记账基础实现。P5、P6 已通过独立验收；P7 已补齐清理、调度及文档，等待控制中枢独立复验。P5 观察项 O1 已闭环：对账服务归属 `apps/reconcile/services.py`，管理命令归属 `apps/reconcile/management/commands/reconcile.py`，`manage.py reconcile` 用法保持不变。

## P5 库存、资产与对账

| 页面 / JSON API | 规则 |
|---|---|
| `/stock/`、`/api/v1/stock/` | 管理员查看本校耗材的库位/质量数量、数据库版本；普通用户只见已启用耗材的有货/不足提示，包含尚无库存行的物资，不返回精确数量、版本或库位。 |
| `/stock/ledger/`、`/api/v1/stock/ledger/` | 仅管理员；本校完整流水，API 含四类来源明细 ID、操作请求与操作者。 |
| `/assets/`、`/api/v1/assets/` | 管理员查本校全部资产；普通用户仅见当前本人负责且在用的资产。支持按资产号/物资名/编码搜索。 |
| `/assets/<id>/`、`/api/v1/assets/<id>/` | 资产详情及分页事件；普通用户不返回原值、他人历史责任身份、操作者、原因等管理字段。归还后不再具有该卡访问权限。 |
| `/assets/scan/<token>/` 及同名 `/api/v1/` 路径 | 资产标签解析；仍须登录并通过相同租户/本人资产授权，token 不构成免登录凭证。 |
| `/assets/<id>/repair_start/`、`repair_finish/` 及同名 `/api/v1/` 路径 | 仅管理员；在库→维修中→在库。HTML 为确认表单，JSON 使用 POST，reason 必填；完成可选 location_id。同事务写事件与审计，失败不留空幂等占位。 |

只读 API 均为 GET，不能直接修改库存、流水或卡片核心字段。过滤参数严格验证，单页 25 条；通用 q/item_id/page，库存可用 low=true 筛不足；管理员另可按库存 location_id/quality、资产 status/location_id/recipient_id、流水 direction 过滤。资产事件使用详情页的 page 参数。HTMX 刷新复用相同授权查询。

“可用量”按本校该物资全部 available 库存合计，damaged 不参与。可用量为 0 或小于 safety_stock 时提示不足；未设置安全线按 0 处理。管理员的行数量仍是对应库位/质量的库存量，充足提示使用上述物资级合计。固定资产以卡片状态和责任关系管理，不混入耗材库存。

维修写入使用原有幂等、租户/用户/来源/资产锁顺序；修复中的卡不能发放，在用及终态卡不能进入维修。资产号继续由租户计数器 UPDATE…RETURNING 批量分配，二维码 token 使用 secrets.token_urlsafe(32)。

使用**普通运行数据库 LOGIN**执行手工对账（它是受控运维命令，不对学校用户开放跨租户 HTTP 对账接口）：

```sh
uv run python manage.py reconcile
uv run python manage.py reconcile --json --sample-limit 20
```

对账在一个 REPEATABLE READ、READ ONLY 事务中执行 13 项检查；0 问题退出 0，任何异常结果退出非 0，不自动改账。默认返回每项最多 20 条样例，可设 1–100；完整问题数仍全部统计。拒绝嵌套业务事务。检查范围为当前专用库内所有租户，输出带 tenant_id，输出文件应按运维权限保存。

当前诊断制品为 `sql/reconcile-v2.sql`，权威源在 `../v7-tools/reconcile-v2.sql`，支持独立 SQL 客户端执行。旧 `reconcile.sql` 保留历史记录，旧脚本四项单据流水检查会误报固定资产，不能再用作当前门禁。v2 限定耗材流水，并校验来源库位/质量/数量，增加责任人精确匹配和事件链衔接；schema、grant、verify 及已发布迁移保持原样。

P5 对账证明内部一致性，不替代实物抽盘。卡片与事件同事务由服务保证；现有 DDL 没有延迟事件约束，直接 SQL 改卡而漏记事件的残余风险通过 M3 故障注入验证为可检出，未声称数据库会自动补记。

## 自动验收（仅独立测试 PG 集群）

P6 的附件接口、保留策略和对象存储配置见下文“P6 附件与隔离”。

```sh
# 管理连接示例（不是应用默认库）：DBA测试身份需 CREATEDB + CREATEROLE。
# postgres 仅供脚本创建独立临时库，不要在此连接下手动执行 apply_schema/migrate。
export DB_HOST=/tmp DB_PORT=55432 DB_USER=postgres DB_NAME=postgres
uv run python scripts/verify_p1.py
uv run python scripts/verify_p2_p3.py
uv run python scripts/verify_p4.py
uv run python scripts/verify_p5.py
# Requires an installed Redis binary; the script starts/stops its own loopback instance.
REDIS_SERVER=/absolute/path/to/redis-server uv run python scripts/verify_p6.py
REDIS_SERVER=/absolute/path/to/redis-server uv run python scripts/verify_p7.py
```

脚本仅新建并清理自己生成的唯一命名测试库及身份，不删除现有库；验证非超级用户迁移、重复迁移、G0、权限断言、原 21 项 SQL 用例、真实普通登录身份拒绝越权、提交前故障全部回滚，以及 Django 测试套件。测试会创建/复用集群级 `inventory_runtime` 权限角色，因此仅在独立测试 PG 集群运行。

只跑无数据库回归：`uv run python manage.py test tests.test_config_examples tests.test_scaffold tests.test_sql_operation tests.test_models.ModelContractTests`。
只跑全量套件：`uv run python manage.py test tests --noinput -v2`，需要测试登录可建测试库。

CI 工作流现位于 SaaS 根目录 [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)，name 为 ci，push/PR 触发。旧嵌套工作流已移除。Locate application 兼容应用本身作为仓库根、`inventory-v4/django` 和 `django` 三种布局。verify_p1 已内含全量 Django 测试；工作流仍在 verify_p7 后显式创建专用 `campus_inventory` 测试基线、apply_schema 并再次执行完整套件，另列 G0/M1 检查。`postgres` 维护库只供 DBA 测试脚本建立临时库，不能在其中应用业务 schema。当前目录未初始化 Git；文件落在预定根路径不代表远端已自动执行，实际 GitHub 仓库根及 push/PR 记录须独立确认。

交付自检：在应用目录执行 `python scripts/verify_ci_delivery.py --repo-root /Users/andy/Desktop/saas`，会输出根工作流绝对路径、SHA-256、模板一致性及显式测试接入状态。CI 在安装依赖前也执行此检查，缺文件、模板漂移、遗漏阶段/测试或重复嵌套工作流都会阻断。可移植副本为 [docs/ci/ci.yml.template](docs/ci/ci.yml.template)，供项目以其他层级作为真实 Git 根时复制到其 `.github/workflows/ci.yml`；不能靠 working-directory 使嵌套文件被 GitHub 发现。只扫描 `inventory-v4/` 会漏掉当前 SaaS 根入口，复验须使用上述命令或直接检查其绝对路径。详情见 [CI交付定位说明](docs/ci/README.md)。

`verify_p4.py` 另建临时库与普通 LOGIN，执行六种双线程竞争：同键提交、重复执行、库存竞争、累计归还、相同调整快照与采购退货额度；不复用业务库。测试日志见 docs/P4全量验收日志.txt、docs/P4并发验收日志.txt。

`verify_p5.py` 使用真实普通 LOGIN，在有采购、领用、归还、转移、退货、报损、遗失/找回、盘盈、质量转换和维修的混合数据上验证 13 项对账；另验五种并发、不可变权限边界、M3 与 CLI 正负退出码。18 项 P5 单元/集成测试中的故障矩阵逐项验证 13 种问题均可检出。证据见 docs/P5全量验收日志.txt、docs/P5运行身份及并发验收日志.txt、docs/P5对账结果.json。

`/health/live/` 为无数据库存活检查，不代表数据库/Redis 就绪。`/health/ready/` 检查 DB `SELECT 1` 与 Redis PING，健康返回200，任一失败返回503，详情只列依赖状态。Redis 连接/读取均设2秒超时并关闭重试，探针不进入 ATOMIC_REQUESTS，响应 no-store。GET/HEAD 可用，其他方法405；健康路径不依赖用户身份，生产仍遵守 HTTPS 与 ALLOWED_HOSTS。此探针只代表连接可用，不代表迁移、运行角色权限、S3或任务消费正常。

应用 LOGGING 输出 UTC 时间、level、logger、message 的单行 JSON；探针失败另带 dependency/error_type，不输出异常原文、密码、连接 URL。root 为 INFO，保留已有 logger；无需额外日志依赖。真实连接与故障验证可执行 `REDIS_SERVER=/absolute/path/to/redis-server uv run python scripts/verify_readiness.py`，与其他 verify 脚本一样仅在独立 PG 测试集群运行。

生产显式配置 SECRET_KEY、ALLOWED_HOSTS、DB_*、REDIS_URL、CSRF_TRUSTED_ORIGINS，默认启用 HTTPS 与严格 CSP。网关 IP 限流、TLS 和实际运行登录身份验权属于部署配置，尚未宣称部署验收通过。P0–P7 已按独立阶段门禁完成；上线前补缺的本地证据与远端/目标环境验收单独记录。

不支持 uv 的 Python3.13 平台可使用 `python -m pip install --require-hashes -r requirements.txt`。该文件由 `uv export --frozen --format requirements-txt --output-file requirements.txt` 导出，固定直接/间接依赖及哈希；后续调整依赖须先更新 uv.lock，再重新导出，CI 会检查两者一致。真实 Docker/云依赖部署另行验收。

## P6 附件与隔离

| 页面 / JSON API | 行为 |
|---|---|
| `/attachments/`、`/api/v1/attachments/` | 本校且当前用户有权访问的附件列表；管理员看本校，普通用户看本人上传或关联本人处置申请的附件。 |
| `/attachments/upload/`、POST `/api/v1/attachments/` | 上传单个 PNG/JPEG/静态 PDF，最大10MiB；API 使用 multipart 的 file，可选 sha256。服务验证实际内容、计算摘要并从存储读回校验后登记；忽略客户端 MIME 推断，声明类型不符会拒绝。 |
| `/attachments/link/`、POST `/api/v1/scrap/<scrap_id>/attachments/<attachment_id>/` | 关联待处理处置单；普通用户只能处理本人申请和本人上传的文件。管理员可关联本校附件。 |
| `/attachments/<id>/withdraw/<scrap_id>/`、DELETE 上述关联 API | 原因必填；撤回关联不删文件及元数据。决策后拒绝新增和撤回证据。 |
| `/attachments/<id>/download/` 及同名 API | 每次重新授权并校验对象前缀、摘要与大小；服务代理下载，不返回对象键或公开 URL，响应 attachment/nosniff/no-store。 |
| `/attachments/<id>/hold/` 及同名 POST API | 管理员提供 reason 设置 legal_hold=true；没有解除保全接口。 |
| `/attachments/import/`、POST `/api/v1/attachments/import/` | CSV 批量关联，file 最大64KiB、200条；仅 scrap_id、attachment_id 两列，无重复；任一越域、越权或已决策记录则全部回滚。 |
| GET `/api/v1/attachments/?ids=1,2` | 使用参数化原生 SQL 的批量元数据查询，租户及当前用户权限双过滤，不返回对象键。 |

所有写入继续要求 Idempotency-Key；HTML 自带 CSRF/幂等键。Cookie API 写入必须有 X-CSRFToken，Bearer API 由既有 JWT 认证后放行。批量关联是 P6 的批量导入隔离通路，不代表期初 Excel/5000 行库存导入已经交付。

上传在 Django 临时文件处理器之前限流至文件大小上限；文件后缀、MIME、内容解析、图片像素上限和静态 PDF 策略共同验证。服务保留原始字节，不保存客户端文件名。静态 PDF 最多200页，拒绝加密、脚本/嵌入文件等活动内容；这属于内容格式校验，不是病毒扫描。

生产默认使用 S3：配置 ATTACHMENT_S3_BUCKET、ATTACHMENT_S3_REGION，S3 兼容服务可设 HTTPS 的 ATTACHMENT_S3_ENDPOINT。凭证使用 boto3 标准环境/IAM 角色机制，不写入数据库或源码。对象键为服务生成的 `tenants/<tenant_id>/evidence/<HMAC>.<extension>`；条件 PUT 禁止覆盖并请求 AES256 服务端加密。云端桶仍须由运维配置私有访问及禁止把普通导出 TTL 套到 evidence 前缀。

ATTACHMENT_RETENTION_DAYS 必须由运维按学校确认的保留政策显式配置（1–36525 天），未配置时上传返回503；示例/测试的30天不构成学校政策。仅 DEBUG=true 时可显式选择 ATTACHMENT_STORAGE_BACKEND=filesystem，并设置公开静态目录以外的 ATTACHMENT_STORAGE_ROOT，供本地验收；没有公开 media 路由。

对象写入与 PG 提交不是分布式事务。键由幂等身份及文件摘要派生：SQL 失败后原键重试会核验并复用已写对象。P7 的未登记对象回收与证据清理须由运维显式启用；保留期缩短和解除保全没有常规任务或业务接口。

`attachments.verify_integrity` 是只读 Celery 任务，参数为发起会话的 tid/sub/fid 与 attachment_id；执行时重新进行 B9 联查及附件授权，拒绝伪造租户、已撤销会话和越域 ID。任务仅接受 JSON；不会依赖 Web 请求的全局租户状态。业务 raw SQL 路径必须使用显式过滤，当前基线仍为复合外键+服务过滤，未新增 RLS，也不声称任意无过滤 SQL 会自动隔离。

`verify_p6.py` 自建临时 PG 库、普通 LOGIN、独立 Redis、独立 Celery worker 和本地 S3 协议测试服务。真实 SDK PUT/GET 验证、四通路拒绝、三种并发及撤回/保全均有实测；S3 测试服务检查协议和匿名拒绝，不模拟完整 AWS IAM 签名验权，未对真实云桶宣称联调通过。证据见 docs/P6全量验收日志.txt、docs/P6四通路及并发验收日志.txt、docs/P6对账迁移复核日志.txt。

## P7 受控清理与调度

操作手册见 [P7运维说明](docs/P7运维说明.md)。清理属于部署运维，不提供 HTTP 入口。每个租户须配置一个已改密、启用且仍为本校管理员的审计身份；进程仍使用普通 `inventory_runtime` 成员 LOGIN。此身份由受控环境 allowlist 指定，不采用学校用户 JWT 或请求参数冒充运维权限。维护允许覆盖已停用/到期租户的历史数据；面向用户的 API 继续严格执行 B9 和强制改密检查。

```sh
# 以下 ID 必须替换为实际租户及管理员 ID；凭证按原有环境注入。
export MAINTENANCE_ACTORS_JSON='{"1":2}'
uv run python manage.py cleanup-sessions --tenant-id 1 --dry-run
uv run python manage.py cleanup-sessions --tenant-id 1 --idempotency-key ops-session-20261031-001
uv run python manage.py cleanup_responses --tenant-id 1 --idempotency-key ops-response-20261031-001
# 学校批准保留策略并核验私有存储配置后，才能开启证据删除。
export ATTACHMENT_CLEANUP_ENABLED=true
uv run python manage.py cleanup_evidence --tenant-id 1 --dry-run
uv run python manage.py cleanup_evidence --tenant-id 1 --idempotency-key ops-evidence-20261031-001
uv run python manage.py cleanup_orphans --tenant-id 1 --idempotency-key ops-orphan-20261031-001
```

`cleanup_sessions` 为 `cleanup-sessions` 等价别名。默认每批25，可指定 `--batch-size 1..1000`；同键重试冻结原批次，新批次用新键。dry-run 不写操作记录、审计或删除对象。会话只清理**整族全部 token 过期超过30天**的记录，先断开族内父链接再删整族，绝不按单行 revoked_at 直接删除。90天按 `completed_at` 清除幂等 `response`，长期保留身份、摘要和业务关联；旧键返回 ALREADY_PROCESSED 与 business_refs，不重新执行业务。

证据仅选择保留期已满且未保全的本校记录：先提交 deleted_at 墓碑以隐藏下载/关联入口，再删除私有对象并确认不存在，最后记完成操作和审计。任一步失败保留元数据及关联，下一次原键重试或新调度可继续；物理删除成功而 SQL 失败也可从“对象已不存在”恢复。墓碑不表示物理删除完成，须以 `maintenance.evidence.deleted` 审计/完成记录和存储核验为准。任务拒绝嵌套业务事务。

孤儿回收仅扫描本租户 evidence 前缀中规范对象键，至少等待24小时；分页游标随计划保留，完成一轮后重头核验。删除前在同一租户锁内重新检查登记状态、对象时间/ETag；上传先登记则跳过，回收先完成则后续上传重新写对象。登记过的元数据（含墓碑）不会误作孤儿。保全和保留期由数据库单向守卫保护，任务不会解除保全或缩短保留期。

S3 自动清理要求**从未启用版本控制的桶**，`Enabled` 和 `Suspended` 均拒绝；简单 DELETE 在版本桶不等于物理删除。版本桶须单独设计历史版本及 Object Lock 策略后另行验收。部署账号需能读取桶版本状态、限定前缀 LIST、HEAD/GET 和条件 DELETE；普通对象写入身份不得改变桶策略。清理期间桶版本策略不能变更。filesystem 仅 DEBUG 开发验收可用，拒绝路径任一层符号链接。

自动调度默认关闭。配置 `MAINTENANCE_ENABLED=true` 后，每个 allowlist 租户每天02:00/02:10/02:20/02:30依次执行 sessions/responses/evidence/orphans（Asia/Shanghai）；证据两类另需 `ATTACHMENT_CLEANUP_ENABLED=true`。每小时45分执行一次专用应用库全库只读对账。只启动一个 beat 实例：

```sh
uv run celery -A campus_inventory worker --loglevel=INFO --concurrency=2
uv run celery -A campus_inventory beat --loglevel=INFO --schedule /private/ops/inventory/beat.db
```

部署前创建受控、可写且持久的 beat 目录，示例路径不由应用自动创建。worker 和 beat 使用同一套环境、队列与私有 Redis；配置变更后重启两者。任务使用 JSON、迟确认、原 task ID 重试，失败10/20/30秒退避最多3次，进程丢失可重投；5xx及对象未确认删除会重试，最终失败记录 ERROR 和 `ok=false`。监控必须检查该业务结果及 ERROR，不能只看 Celery SUCCESS。已排队任务不会因停止 beat 自动消失；关闭维护开关并重启 worker 可拒绝剩余清理。人工命令是显式操作，不受自动调度总开关限制，仍受租户 allowlist 和证据开关限制。

`verify_p7.py` 在临时库和普通 LOGIN 上实际启动 Redis、worker、beat，验证会话整族规则、90天响应、对象503后的已提交墓碑、原键重试、元数据/关联保留、孤儿分页、登记竞争、保全竞争、调度停止与持久计划重启。测试专用秒级间隔仅 DEBUG 下允许。生产启用、云桶联调、远端 CI 和实物抽盘均须分别留存独立证据。
