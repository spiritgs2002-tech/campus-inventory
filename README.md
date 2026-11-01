# 学校总务库存系统

应用代码和部署说明见 [Django主文档](django/README.md)，规划与验收记录位于docs/。

## SQL权威源及历史迁移

| 文件 | 职责 |
|---|---|
| schema-v7.sql | 唯一可编辑的当前DDL权威源 |
| django/sql/schema-v7-current.sql | 自动生成的当前打包快照，须与权威源逐字节一致 |
| django/sql/schema-v7.sql | P7冻结历史基线，由初始迁移固定哈希引用，禁止覆盖或删除 |

在django目录运行 `python scripts/sync_schema_snapshot.py --check` 校验指纹及快照。修改权威源后使用 `--write` 同步当前快照；新增DDL另建迁移，不改写历史基线。详见 [SQL制品约定](django/sql/README.md)。

过程机器日志保留在docs/过程记录/P8a/_codex_raw_logs(备查)/与docs/过程记录/_django_codex_logs(备查)/，用于本地备查，由.gitignore排除，不随代码提交。原验收报告保留原文，其旧路径按上述归档位置查阅。
