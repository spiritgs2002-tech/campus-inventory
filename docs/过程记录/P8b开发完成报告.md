# P8b开发完成报告

日期：2026-11-01。依据：[Codex实现指令包（P8b）](../规划/Codex实现指令包（P8b）.md)。开发交付完成，待WorkBuddy独立硬门禁验收；未提交或推送代码。

## 交付能力

- 新增procurement与notifications两个app、两个managed=False模型、两个手写固定SHA的RunSQL迁移；迁移只镜像ORM状态，不使用CreateModel或自动DDL。
- 请购双轨提交、本人列表、管理员全校列表与状态筛选、批准/驳回、采购到货入库表单及API。
- 到货及驳回站内通知、导航未读数、本人通知列表及已读操作；HTML表单可用，不依赖新增JS。
- 所有写服务有运行身份守卫、可信会话复核、租户过滤；匿名身份创建和建单同一事务，复用P8a轻量用户函数，不创建JWT会话。
- 到货完全调用现有orders.create→posting.receive；申请锁、幂等、入库、通知、审计事务统一，通知失败回滚整个入库；保留原审批人，不用入库执行人覆盖。
- CI在verify_p8a之后接入verify_p8b，工作流和可移植模板同步；SQL指纹/快照校验继续生效。

## 对指令示例的必要适配

1. orders.create真实签名为(principal,kind,key,payload)，没有额外purchase位置参数，且返回(result,replayed)。采购入库实际要求supplier_id及有效采购单价，因此API/管理员表单提供这些字段，未改动既有orders.py/posting.py。
2. 匿名不能使用actor_id=0；改为先锁定学校、复用不可登录轻量用户，再以真实追溯user_id执行幂等建单。匿名须携带现有物品随机QR token确定学校，禁止通过任意item_id反查学校后免鉴权写入。该额外参数是明确的安全约束。
3. 所有申请均有applicant_id，包括轻量用户；通知始终绑定租户+用户ID，不启用不安全的“仅姓名匹配”兜底。同名用户不会互相读通知，管理员也不能跨用户标记。
4. 根schema扩展两表并添加租户复合外键、数量/状态约束及通知去重约束。冻结P7 SQL和P8a迁移制品保持不变；当前迁移数7→9，G0由27/14/32变为29/14/32。旧验证脚本只同步当前迁移总数，保留P7六条迁移升级测试。
5. 轻量账号后续如在原用户ID上设置密码并完成改密，可查看原通知；另建不同用户ID不会按姓名自动合并。使用手册说明这一实际行为，不宣称仅凭同名同工号自动合并。

## 本次验证

| 验证 | 结果 |
|---|---|
| 全量Django测试 | **229/229通过**：既有200 + 新增29；包含申请、审批、到货、幂等、通知归属、匿名工号复用/正式身份拒绝、通知故障回滚及强制CSRF用例 |
| SQL-first新库安装 | 9条迁移；重复应用无动作；makemigrations --check --dry-run无模型漂移；运行ACL通过；真实普通LOGIN四类禁止操作拒绝；提交前故障无残留表/函数 |
| P8a旧库升级回归 | P7六条历史迁移与存量数据→当前九条迁移；故障回滚、原扫码并发、授权、CSRF、QR及移动库存全部通过 |
| P8b专项脚本 | 29项专项测试通过；真实普通LOGIN双连接同键到货仅执行一次，不同键竞争仅一方成功，通知不重复 |
| 对账 | P8b实际到货并发后独立只读一致快照13/13正常 |
| 当前完整根SQL实库建库 | PostgreSQL16.15，29表/14函数/32触发器，21项SQL正反用例通过 |
| 静态与配置 | ruff通过；git diff --check通过；Django check及生产check --deploy均0问题；配置示例、SQL指纹/当前快照、CI副本及阶段接线检查通过 |

日志位于本地 `docs/过程记录/P8b/_codex_raw_logs(备查)/`：全量及迁移权限验收日志.txt、P8a升级及并发验收日志.txt、P8b专项验收日志.txt、当前完整SQL实库验证.json、生产配置检查.txt、SQL指纹及快照检查.json、CI交付检查.json。机器日志保持Git忽略。全部测试在专用临时PG集群执行，结束已停止并清理；没有迁移现有业务库。

## 文件与复验入口

应用代码：django/apps/procurement/、django/apps/notifications/；模板：django/templates/procurement/、django/templates/notifications/；迁移SQL：django/sql/p8b-procurement.sql、p8b-notifications.sql；专项脚本：django/scripts/verify_p8b.py；测试：django/tests/test_requisition.py、test_notifications.py。另同步根schema、当前快照、G0、应用注册、路由、导航及CI。

操作与接口细节见 [P8b操作与复验说明](../使用说明/P8b操作与复验说明.md)。本轮未重新执行P2–P7全部独立脚本，其业务测试包含于本轮全量套件；不把历史单独验证记录记为本次新测。远端GitHub Actions、真实微信和生产环境尚需目标环境验收。未开始P8c。
