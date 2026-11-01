# P8c报修操作与复验说明

日期：2026-11-01。范围：通用报修工单；工单进度与资产维修开关默认独立，不自动改变资产状态。

## 安装升级

在django目录执行 `python scripts/sync_schema_snapshot.py --check`。使用迁移owner在专用应用库执行 `python manage.py apply_schema`，随后verify_schema/verify_runtime。当前共10条迁移，G0为30表/14函数/32触发器；已有P8b库只新增repair/0001_initial。日常web、命令和任务仍使用普通inventory_runtime成员LOGIN，不能使用DBA或迁移owner运行。

repair/0001使用固定SHA的手写p8c-repair.sql；所有模型managed=False。DDL从根schema-v7.sql的P8C-REPAIR章节摘录，当前快照自动同步；已发布P7/P8a/P8b迁移及SQL保持原字节。新增表的索引与表同事务建立，没有在线修改历史大表或回填历史数据。迁移无破坏性降级，异常时依事务回滚；部署恢复按已验证备份和后续正向迁移处理。

## 教师报修

1. 登录后进入 `/s/repair/new/`，选择资产、物品或场地，填写对象编号、故障说明和紧急程度。场地编号可以留空，但说明须写明具体位置；资产/物品须指定有效本校编号。
2. 资产二维码预填支持 `/s/repair/new/?asset=<资产QR token>`；也支持 `?target_type=asset&target_id=<id>`。前者会校验二维码归属，后者仍在提交时校验对象租户。
3. 可选上传一张PNG/JPEG照片，最多10MiB。页面为普通multipart表单，不依赖JS；由既有attachments.services.upload落库，原图不改写。API也可先上传附件，再提交其ID。
4. 成功显示工单号。相同表单重复提交不会重复建单；相同幂等键改故障描述、优先级、目标或照片会返回409。
5. `/s/repairs/`查看自己的工单和进度，点击工单号查看详情。管理员可查看本校全部；处理人身份本身不扩大普通用户查看权限。

## 免密登记

免密文字报修须填写姓名和工号，并携带已有物品/资产的随机QR token确定学校。入口例如 `/s/repair/new/?token=<物品QR token>`；支持二维码对应对象、资产所在场地及未指定编号的场地报修。不接受客户端tenant_id，也不通过任意target_id反查学校。二维码与目标不匹配会拒绝。

登记复用P8a不可登录的轻量用户，姓名工号是未核验声明；不会签发JWT或会话。已有正式账号、停用账号或同工号不同姓名不能免密冒用。普通工单查询仍须登录，不开放按姓名工号查询他人历史记录。

现有附件上传服务要求有效会话，免密身份无法调用。**免密报修支持文字，附照片须登录**；未通过临时会话或伪造principal绕过附件鉴权。页面会明确提示此限制。

免密报修后请记录工单号，联系管理员核验同工号身份。若在原用户ID上设置密码、完成首登改密，登录后可查看原工单；另建不同用户ID不会按姓名工号自动合并历史工单。

## 总务处理

管理员进入 `/manage/repairs/`，可按状态筛选并分页查看。

| 当前状态 | 可操作 |
|---|---|
| 待派单pending | 派给本校启用的处理人；或取消 |
| 已派单dispatched | 开始处理；或取消 |
| 处理中repairing | 完成；或取消 |
| 已完成done / 已取消cancelled | 终态，不再变更 |

派单不强制maintainer角色，处理人指向现有User记录；非本校或停用人员不能接单。所有状态变更由admin执行，带幂等键、加锁并记审计；不能跳步或重复派单。取消可填写最多2000字说明。

工单完成不会调用assets.repair_finish，资产原有repair_start/repair_finish工具保持原规则。若需要改变资产库存状态，管理员另按原有资产操作处理。

## 接口

所有写接口POST，须Idempotency-Key。Cookie/免密调用须CSRF cookie与X-CSRFToken；有效Bearer调用不要求CSRF cookie，过期或无效凭据不会降级为免密。

| 路由 | 输入与权限 |
|---|---|
| `/api/v1/repair/` | JSON：target_type、target_id（场地可空）、description（1–4000字）、priority（low/normal/high，默认normal）、可选photo_attachment_id；免密另需name、employee_no、token |
| `/api/v1/repair/{id}/` | GET：本人或本校admin；跨用户/学校返回403 |
| `/api/v1/repair/{id}/dispatch/` | JSON：assigned_to；仅admin |
| `/api/v1/repair/{id}/status/` | JSON：action=start/done/cancel，可选reason；仅admin |

提交返回document与business_refs；首次提交201，重放200并设置Idempotency-Replayed=true。状态接口返回200；非法跃迁/终态变更409。照片仅接受当前报修人上传、本校、未删除且在保留期内或已保全的PNG/JPEG附件；不允许引用他人附件ID。

照片通过已有受权下载接口读取，不生成公开URL。照片继续遵守既有附件保留与清理政策；工单不自动延长保留期，照片清理后工单及附件元数据仍留存。组合表单上传后业务事务失败时，DB记录回滚；外部对象可能成为孤儿，沿用既有孤儿对象清理机制，不宣称外部存储可事务回滚。

## 复验

在专用PostgreSQL16测试环境配置DBA连接、DEBUG=true及随机测试SECRET_KEY。应用目录执行：

```sh
python scripts/sync_schema_snapshot.py --check
python scripts/verify_p1.py
python scripts/verify_p8a.py
python scripts/verify_p8b.py
python scripts/verify_p8c.py
```

P8c脚本自建唯一临时库与普通LOGIN，先建立P8b九条迁移和存量请购，再验证增量故障、升级、专项测试、工单并发、真实附件上传下载及独立13项对账。文件存储只在显式DEBUG测试环境使用/tmp私有目录；结束清理临时数据库、LOGIN和照片。

生产配置下另运行 `python manage.py check --deploy`。本轮机器日志留在docs/过程记录/P8c/_codex_raw_logs(备查)/，由Git忽略。真实微信、手机和目标部署环境、真实云桶及远端CI仍须现场验收。
