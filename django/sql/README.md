# SQL 制品与 P8a 增量迁移

唯一当前DDL权威源为项目根 inventory-v4/schema-v7.sql。P8a新增items.qr_token及两个索引，已有27表/14函数/32触发器的SQL正文保持不变。

| 权威内容 | 打包制品 | 用途 |
|---|---|---|
| 项目根 schema-v7.sql 当前完整快照 | sql/schema-v7-current.sql | 自动同步的逐字节副本、静态预检、独立审阅；不是第二份可独立编辑的来源 |
| 权威文件 P8A-ITEM-QR-BEGIN / END 章节 | sql/p8a-item-qr.sql | 同一章节加BEGIN/COMMIT，手写增量RunSQL，固定SHA-256 |
| P7发布时的历史schema | sql/schema-v7.sql | core/0001_initial固定制品，禁止覆盖 |
| ../v7-tools/grant-runtime.sql、verify-runtime.sql等 | sql/同名文件 | 已发布权限与验证制品，保持原字节 |
| ../v7-tools/reconcile-v2.sql | sql/reconcile-v2.sql | 13项只读内部一致性对账 |

历史schema-v7.sql的SHA-256仍为2cc1062a3386ed9815fce2fbb60b05df8d3b5827d70fec9a586b5543ac367f82。不能用当前完整快照覆盖它，也不能改已发布初始迁移中的哈希。新库按“冻结P7基线→P8a增量”安装；旧库只执行新增catalog/0002_add_item_qr。迁移总数由6变7，G0计数仍27/14/32。

新操作AddItemQRSQL继承RunSQLFile（RunSQL子类），整文件执行DDL并按其哈希校验。state_forwards只镜像手写SQL新增的ORM字段，不调用AddField/自动生成DDL，所有模型继续managed=False。迁移atomic=False，SQL文件自身的BEGIN/COMMIT保护新增列及索引；不提供破坏性降级。

### 当前快照同步与防漂移

在 django/ 目录运行：

```sh
# 只读：复算文件头指纹和正文行数，校验当前快照逐字节一致。
python scripts/sync_schema_snapshot.py --check
# 仅在编辑根权威源后执行：更新其文件头，并同步当前快照。
python scripts/sync_schema_snapshot.py --write
python manage.py precheck_sql
```

指纹范围明确为独立 `BEGIN;` 语句行的首字节至文件末尾，包含最后换行；正文行数使用该范围的 splitlines()。不能从注释中的 BEGIN 单词起算。默认检查、管理命令和CI都不会静默修复错误。单独交付django/时检查打包快照自身；缺少根权威源时拒绝 --write。

同步工具只更新根源文件头和 schema-v7-current.sql，永不修改冻结 schema-v7.sql、增量SQL或迁移哈希。新DDL必须另建增量；P8a权威章节与增量的关系仍由tests.test_p8a_migrations验证。删除旧基线或用当前快照覆盖它，会使core/0001_initial的SHA校验失败，或令后续增量重复新增列，因此不能按同名普通副本去重。

apply_schema仅在专用应用库使用迁移owner；日常运行使用普通inventory_runtime成员LOGIN。新增列可空，不在DDL里回填。二维码回填在管理员标签页POST或受控generate_item_qr命令中分批完成。

普通CREATE INDEX在迁移时会加锁；本次未验证生产规模的大表耗时，部署前应备份并在预发/维护窗口确认锁和超时预算。故障前回滚及P7存量数据升级的历史证据见 [P8a归档](../../docs/过程记录/P8a/README.md)。

## P8b增量

根权威源新增P8B-PROCUREMENT与P8B-NOTIFICATIONS章节，当前完整快照包含29表/14函数/32触发器。两份新增量p8b-procurement.sql及p8b-notifications.sql分别固定SHA，由对应app的0001迁移执行，含新表/序列运行权限；已有P7/P8a制品不变。新库当前9条迁移，P8a旧库只增加2条；同步工具不改写历史增量。

## P8c增量

当前权威源新增P8C-REPAIR章节，完整快照为30表/14函数/32触发器。p8c-repair.sql由repair/0001以固定SHA执行，新库10条迁移、P8b旧库增加1条。新表中三个生成列按target_type映射asset/item/location，并各自建立tenant+ID复合外键；不是新增业务表或触发器。旧SQL与迁移字节保持不变，照片保留策略继续由既有附件服务维护。
