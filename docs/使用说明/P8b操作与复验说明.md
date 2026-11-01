# P8b操作与复验说明

范围：请购申请、管理员审批、采购到货入库、站内通知；不含报修及外部推送。

## 安装和身份

从django目录执行 `python scripts/sync_schema_snapshot.py --check`。使用迁移owner在专用应用库执行 `python manage.py apply_schema`，新库共9条迁移，P8a旧库新增procurement/0001与notifications/0001。G0为29表/14函数/32触发器。日常服务继续使用普通inventory_runtime成员LOGIN，不以迁移owner运行。

新增两表和索引的DDL在根schema-v7.sql，当前快照自动同步；冻结P7基线和P8a增量字节不变。新迁移SQL包含新表及序列运行权限，所有模型managed=False；不执行自动建表。

## 操作路径

- 登录用户进入 `/s/requisition/new/?item=<物品ID>`，填写数量与原因提交；姓名、工号取当前可信会话，客户端不能代指定申请人或学校。
- 未登录申请须使用 `/s/requisition/new/?item=<物品ID>&token=<该物品二维码token>`。JSON提交同样需要token、name、employee_no；token决定学校及物品，不能通过递增item_id推断租户。姓名工号属于未核验声明，复用P8a轻量记录，不签发登录会话，正式/停用/不同姓名工号冲突须登录。
- `/s/requisitions/` 查看本人申请；管理员可见本校全部。`/manage/requisitions/` 提供状态筛选、批准、驳回和到货入库表单。
- 驳回必须填写原因，最多2000字；审批后不能重新审批。
- 批准后到货须选择本校库位、供应商、质量并填写采购单价。调用既有采购入库链路；库存、资产卡（固定资产）、申请状态、审计和到货通知同事务提交。通知失败将回滚入库。到货后教师仍须通过现有领用流程领取，不自动发放。
- 导航铃铛显示本人未读数，`/s/notifications/` 列表分页，普通HTML表单可标记已读。相同姓名不能读取他人通知；管理员也不能代读他人通知。

## JSON接口

所有写请求使用POST，业务写接口必需Idempotency-Key；仅JSON。Cookie及匿名写须CSRF cookie和X-CSRFToken；有效Bearer JWT调用不依赖CSRF cookie。无效凭据不降级为匿名。

| 路由 | 内容 |
|---|---|
| /api/v1/requisition/ | item_id、qty、reason；匿名另需name、employee_no、token |
| /api/v1/requisition/{id}/decide/ | action=approve或reject，驳回另需reason；admin |
| /api/v1/requisition/{id}/fulfill/ | location_id、quality、supplier_id、unit_price；admin |
| /api/v1/notifications/{id}/read/ | 本人通知已读，重复调用安全；不要求幂等键 |

提交及审批/到货响应含document和business_refs，重放返回Idempotency-Replayed=true。不同请求复用同键拒绝；不同键重复到货拒绝。库存写入仅由orders.create→posting.receive完成。

## 轻量身份及通知限制

免密登记/申请后，请用同一工号登录以统一查收通知。轻量账号不可直接登录，管理员须核验并设置初始密码、完成首登改密；在原用户ID转为正式账号后可访问原申请与通知。另建不同用户ID不会按姓名自动合并历史记录。通知只在系统内展示，不发送邮件、短信或企微消息。

## 复验

配置专用PG16测试集群DBA连接和测试SECRET_KEY/DEBUG=true。在django目录运行 `python scripts/verify_p1.py`（含全量测试），`python scripts/verify_p8a.py`，`python scripts/verify_p8b.py`。P8b脚本自建唯一临时库，验证专项用例、真实普通LOGIN到货竞争及独立只读13项对账，结束清理。生产配置下运行 `python manage.py check --deploy`。不在业务库运行测试或以DBA运行web。

本轮原始日志在docs/过程记录/P8b/_codex_raw_logs(备查)/，由.gitignore排除提交。远端CI及实际微信/目标部署环境需另行验收。
