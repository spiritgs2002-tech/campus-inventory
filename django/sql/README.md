# 固定 SQL 制品

- `schema-v7.sql`：上级唯一 DDL 来源的逐字节交付副本，原文件 SHA-256 为 `2cc1062a3386ed9815fce2fbb60b05df8d3b5827d70fec9a586b5543ac367f82`。
- `grant-runtime.sql` / `verify-runtime.sql`：来自上级 `v7-tools/`；P1 补充 Django 迁移记账保护及 NOLOGIN 检查，未改变 27 张业务表的 DDL。
- `precheck-sql.py` / `test-negative.sql`：来自上级 `v7-tools/` 的原样副本。
- `reconcile-v2.sql`：P5 只读对账的新版本，来自 `../v7-tools/reconcile-v2.sql`，SHA-256 为 `e4811e039ff8300ed9d20825e3f9f242494f806bf5ff0996667e28661baf05f5`。没有 DDL，不需要数据库迁移；不覆盖历史 reconcile.sql 或 P1 制品。当前 reconcile 管理命令只使用 v2。

初始 schema/grant 的哈希固定在 `core/0001_initial` 中。不要单独修改这些副本，也不要修改已发布迁移；后续变更另写版本迁移。

## 权威源与副本

在 SaaS 项目中，**唯一 DDL 权威源为 `inventory-v4/schema-v7.sql`**，工具脚本权威源为 `inventory-v4/v7-tools/`。本目录只保存发布时的字节相同副本，不是第二个可独立维护的来源。独立交付 Django 目录时使用固定副本，保留初始迁移哈希以核验来源。

| 权威源（相对 django/） | 交付副本 |
|---|---|
| ../schema-v7.sql | sql/schema-v7.sql |
| ../v7-tools/grant-runtime.sql | sql/grant-runtime.sql |
| ../v7-tools/verify-runtime.sql | sql/verify-runtime.sql |
| ../v7-tools/precheck-sql.py | sql/precheck-sql.py |
| ../v7-tools/test-negative.sql | sql/test-negative.sql |
| ../v7-tools/reconcile-v2.sql | sql/reconcile-v2.sql |

## 同步与复核

仅在**尚未发布的新制品**打包时，从已审定的权威源复制文件。已发布的 P1 基线及迁移不得覆盖；后续 SQL 变更新建版本文件与手写迁移，沿用以下“源文件 → 同版本副本 → 校验”流程。不要从副本反向覆盖权威源，也不要只修改副本或仅更新迁移哈希来绕过检查。

以下是当前 P1 制品的同步位置示例，在 `django/` 下执行；本次整改无需重新复制，因为现有文件字节一致：

```sh
cp ../schema-v7.sql sql/schema-v7.sql
for name in grant-runtime.sql verify-runtime.sql precheck-sql.py test-negative.sql; do
  cp "../v7-tools/$name" "sql/$name"
done
```

复核来源一致性及哈希，并执行现有守护测试：

```sh
cmp ../schema-v7.sql sql/schema-v7.sql
for name in grant-runtime.sql verify-runtime.sql precheck-sql.py test-negative.sql; do
  cmp "../v7-tools/$name" "sql/$name" || exit 1
done
shasum -a 256 sql/schema-v7.sql sql/grant-runtime.sql
uv run python manage.py test tests.test_models.ModelContractTests
```

测试配置按项目 README 注入。涉及新 SQL 行为时，还须在独立 PG16 测试库执行相应迁移及 `scripts/verify_p1.py`，不能以哈希一致或静态预检代替真实数据库验收。
