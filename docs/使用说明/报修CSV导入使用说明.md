# 报修CSV导入使用说明

用于腾讯文档、企微收集表导出CSV后离线建报修工单。只新增管理命令，不修改在线报修、SQL、模型或迁移；不导入照片，也不回填历史提交时间。

## 执行

在django目录或已配置环境变量的web容器内执行，使用普通inventory_runtime成员LOGIN（例如部署的inv_run），不能使用迁移owner或postgres。将SCHOOL、文件路径和批次改为目标学校的实际值。

```sh
python manage.py import_repairs --help
python manage.py import_repairs --csv /path/repairs.csv --tenant-code SCHOOL --batch-id 20261101-form --dry-run
python manage.py import_repairs --csv /path/repairs.csv --tenant-code SCHOOL --batch-id 20261101-form
```

默认UTF-8 BOM兼容编码utf-8-sig、逗号分隔。可指定 `--encoding gb18030`、`--delimiter ';'` 或 `--delimiter '\t'`；真实制表符也支持。缺少对象类型时默认location，可指定 `--default-target-type asset` 或item。

batch-id为1–80位字母、数字、点、下划线或短横线，且以字母或数字开头。单号按 `RP-<batch-id>-<数据序号，至少5位>` 生成。同一学校重复单号直接跳过，不新增用户或审计，不修改既有工单。

同批重跑须保持原行顺序，只修正失败行；不要删除成功行后重排再重跑。已成功的行即使修改内容也会跳过，不会更新。本命令以学校、批次和行序确定幂等，不校验文件内容摘要；batch-id不应拿来导入另一份无关文件。空白物理行不计入数据序号，多行引号字段仍是一条记录。

## CSV字段

表头忽略大小写及空白，支持规范英文名和中文别名。至少有姓名、工号、故障说明三列。重复映射到同一规范字段的表头会拒绝；收集时间等无关列忽略，数据列数仍须与表头一致。

| 规范字段 | 表头别名举例 | 规则 |
|---|---|---|
| reporter_name | 姓名、报修人、name | 去首尾空白后1–120字 |
| reporter_no | 工号、学号、工号或学号、employee_no | 1–80位ASCII字母、数字、下划线、点或短横线 |
| description | 故障说明、说明、描述 | 去首尾空白后非空，合并备注后最多4000字；不截断 |
| target_type | 对象类型、报修对象类型 | asset/item/location，缺省用命令默认值 |
| target_id | 对象编号、编号、id | 正整数，优先于名称；显式无效编号报行错误，不悄悄改用名称 |
| target_name | 对象名称、名称 | 资产按asset_no；物品优先code，再精确name；场地按精确name；歧义拒绝 |
| priority | 紧急程度、优先级 | low/normal/high，缺省normal |
| status | 状态 | pending/dispatched/repairing/done/cancelled，缺省pending |
| assigned_to_no | 处理人工号、处理人 | 本校启用User的username；dispatched/repairing/done必填 |
| reject_reason | 驳回原因 | 最多2000字，取消时可作说明 |
| note | 备注、地点 | 追加到description末尾，格式为换行加“备注：…” |

场地的编号和名称都不填时允许target_id为空；资产/物品必须定位到具体对象。目标和处理人均按tenant过滤；物品/场地须启用。AssetCard本身没有active列，资产有效性使用其关联Item的active标志，不能用不存在的字段筛选。

报修人复用已有ensure_lightweight_user：同一学校同工号同姓名复用不可登录轻量用户；正式账号、停用账号或同工号姓名不同均拒绝，不会冒用、改密或重新启用账号。不创建JWT或会话。导入工单默认无照片，时间由现有模型的auto_now_add/auto_now机制在导入时生成，命令不赋历史时间，也不写生成列。

历史状态直接作为导入快照写入，仍受现有数据库枚举、状态/处理人及租户复合外键约束。不调用在线状态机，不改变资产库存状态。

## 事务、审计与结果

- 默认：整批一个事务，单行使用保存点；错误行的工单、轻量用户及审计回滚，其他有效行继续，最后提交。审计与工单一起写入，不接受“工单成功但审计失败”的降级。
- `--strict`：遇首个行错误停止，整批新写入全部回滚；输出成功导入0、已回滚数量及未处理数量。此前已存在的重复工单不受影响。
- `--dry-run`：仅解析、校验目标/身份/处理人并统计；不调用用户创建、不写工单或审计，也不分配序列ID。输出“成功导入0”和“dry-run可导入”数量；同批待创建工号的姓名冲突也能检出。
- 只有真正的本校工单号重复才计为跳过；其他唯一/FK/CHECK等错误计为行错误。审计或DB故障不会被伪装成重复。
- 文件完整解码及CSV语法检查先于写入；文件末尾损坏不会导致前半段已提交。

| 退出码 | 含义 |
|---|---|
| 0 | 全部成功或仅重复跳过；合法dry-run也返回0 |
| 1 | 存在行错误；默认模式的其他成功行已提交，strict则整批回滚 |
| 2 | 参数、租户、文件、编码或运行身份错误；未提交本批次 |

错误明细同时显示CSV物理行号和逻辑数据序号。后台管理员可在 `/manage/repairs/` 查看本校导入工单，其他学校无权查看。

## 样例与验收

[六行合成样例](../示例/报修CSV导入六行样例.csv)包含正常场地、名称解析、物品code/name、已完成状态及一个非法工号。样例中的“总库”、PAPER、复印纸和处理人工号u对应测试夹具，使用前须换成本校实际记录；最后一行故意错误，用于验证退出码1。不是实际报修数据。

该样例在测试夹具下首次导入5条、错误1条；同批重跑跳过5条、错误仍1条；strict新批次全部回滚。验证命令为 `python manage.py test tests.test_import_repairs --no-input`，全量为 `python manage.py test tests --no-input`，仅在专用测试数据库中执行。
