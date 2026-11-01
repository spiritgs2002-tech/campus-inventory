# P8c开发完成报告

日期：2026-11-01。依据：[Codex实现指令包（P8c）](../规划/Codex实现指令包（P8c）.md)。本地开发交付完成，待WorkBuddy独立验收；未提交或推送代码。

## 一、交付范围

新增apps/repair，完成双轨报修、照片关联、本人列表及详情、管理员派单/状态筛选、开始处理/完成/取消、幂等与审计。页面含普通HTML表单及照片上传控件，无新增JS依赖。状态机为pending→dispatched→repairing→done，任意非终态可cancelled，终态不能变更。

根schema新增一张repair_repairticket表，手写固定SHA的RunSQL迁移、managed=False模型及ORM状态镜像；当前10条迁移，G0=30表/14函数/32触发器。CI在verify_p8b后接入verify_p8c，根工作流和可移植模板同步。

工单默认不调用资产维修开关；inventory、procurement、notifications、attachments既有业务实现及已发布迁移/SQL不改写。支持性验证脚本和旧测试只同步当前迁移总数9→10，保留历史版本升级校验。

## 二、必要适配与安全边界

| 指令示例问题或边界 | 实际实现 |
|---|---|
| 匿名actor_id=0不符合已有幂等/审计外键 | 同事务内锁学校、复用P8a不可登录轻量用户，以真实追溯user_id建单；不签发会话 |
| 用任意target_id反查租户缺少匿名学校凭证 | 免密要求现有随机QR token，目标与token匹配；客户端不能指定学校或报修人 |
| 按姓名兜底查询可能同名越权 | 报修人始终存真实User ID，查询按tenant+reporter_user_id；同名不会共享工单 |
| 附件示例返回attachment_id，与真实API不符 | 使用既有upload返回的record.id及replay标记；文件存储和下载均复用attachments |
| 现有附件upload必须通过有效会话复核 | 登录用户支持照片；免密支持文字报修，附照片须登录。没有为轻量用户制造临时会话或绕过orders.identity，此限制在页面和手册明确说明 |
| target_id同时引用资产/物品/场地，单个普通外键无法约束 | 新表新增三个只读生成列，按target_type映射对应ID，分别建立租户复合外键；不新增函数或触发器 |
| 一次表单同时上传照片和建单 | 附件DB登记、工单与审计同事务；失败回滚DB。外部对象可能遗留孤儿，遵循既有孤儿清理，不另建存储 |
| 后续同工号登录的历史归属 | 原User ID完成核验、设置密码并改密后可查看历史；不同User ID不按姓名工号自动合并 |

照片只允许当前报修人已上传、本校、未删除、在保留策略内的PNG/JPEG；阻止其他用户/租户附件引用。上传入口复用既有大小/多文件解析限制与内容检查。Bearer与Cookie/免密CSRF策略沿用现有约定，无效登录不会降级。详情API/HTML对跨用户、跨租户访问均返回403。

## 三、本次实测结果

所有DB验证在新建的独立PostgreSQL16.15临时集群中执行，结束已停止并删除临时数据目录；未迁移现有业务库。

| 验证 | 本次结果 |
|---|---|
| 全量Django测试 | **266/266通过**：P8b基线229 + 新增37，无回归 |
| P8c专项 | **37/37通过**：真实照片上传后API建单、信任身份留痕、免密复用/冲突/回滚、完整状态机、非法跃迁、终态、本人列表/详情、跨用户/租户403、CSRF、HTML照片重放与冲突、过大/非图照片拒绝等 |
| SQL-first新库与权限 | 10条迁移；重复应用无动作；makemigrations --check --dry-run无待生成变更；实际普通LOGIN四类禁止操作均拒绝；初始SQL提交前故障无残留表/函数 |
| P8b→P8c存量升级 | 先应用9条历史迁移并保存请购数据；注入P8c SQL提交前失败，新表不存在，旧数据保留；随后升级到10条迁移并复验存量请购存在 |
| 普通LOGIN双连接竞争 | 同键建单/派单只处理一次并重放；不同键同时start只有一方成功；done/cancel竞争只有一个终态，另一方409 |
| 普通LOGIN照片与事务 | 既有附件upload→工单关联→download成功；状态审计故障回滚；集成测试另验证照片登记后工单审计失败时附件/工单/操作记录全部回滚 |
| 独立对账 | 工单竞争及照片关联后，以独立只读一致快照执行13项对账，全部正常；资产状态/事件不受工单完成影响 |
| P8a/P8b回归 | 两个独立脚本在当前schema重新执行，通过原扫码升级/并发/HTTP、采购到货并发/通知及对账验证 |
| 当前完整权威SQL独立建库 | G0=30表/14函数/32触发器；21项SQL正反用例通过 |
| 配置与静态 | Django check及生产check --deploy均0问题；配置示例、ruff、git diff --check、SQL指纹/快照及CI交付检查通过 |

新证据在本机 `docs/过程记录/P8c/_codex_raw_logs(备查)/`：全量及迁移权限验收日志.txt、P8a升级及并发验收日志.txt、P8b专项验收日志.txt、P8c专项验收日志.txt、当前完整SQL实库验证.json、生产配置检查.txt、静态检查.txt、SQL指纹及快照检查.json、CI交付检查.json、最终制品检查.json。该隔离目录由.gitignore排除提交，不在源码目录散放过程日志。

日志中的预期拒绝、broken.sql预检FAIL及注入异常属于负面用例，最终脚本均成功退出。P2–P7的全部独立脚本未在本轮重跑，其测试包含于本轮全量套件；不将历史单独验证结果记为新测。

## 四、文件与复验入口

- django/apps/repair/：models、forms、services、views、urls、multipart解析限制及0001手写SQL迁移。
- django/sql/p8c-repair.sql、根schema-v7.sql及当前完整快照：DDL、复合外键、索引和运行权限。
- django/templates/repair/、django/static/repair.css、base导航：登记、成功、列表、详情及处理表单。
- django/scripts/verify_p8c.py、django/tests/test_repair.py：升级/故障/并发/实际普通LOGIN验证及37项测试。
- settings、根路由、G0、CI及当前迁移数断言同步。

使用方法、API参数与复验命令见 [P8c操作与复验说明](../使用说明/P8c操作与复验说明.md)。

## 五、验收边界

可交WorkBuddy按V-repair-1至3独立验收，不自行宣称软门禁已获通过。免密照片限制需在复核时关注，已在第二节明确列出。真实微信/手机、目标部署环境与真实云桶、远端GitHub Actions未在本轮验证。工单照片沿用附件保留与清理政策，不自动延长保留期。没有启用可选资产状态联动，也未执行commit/push。
