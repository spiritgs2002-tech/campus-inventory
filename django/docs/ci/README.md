# CI 交付定位说明

2026-11-01实测git rev-parse确认：当前真实Git根为 `/Users/andy/Desktop/saas/inventory-v4`，权威入口为该目录的 `.github/workflows/ci.yml`；应用位于其django/子目录。P8a保留既有门禁并加入verify_p8a。历史路径讨论见原验收报告，本说明以当前实际根为准。

在应用目录执行（以 inventory-v4 为根示例）：

```sh
python scripts/verify_ci_delivery.py --repo-root /Users/andy/Desktop/saas/inventory-v4
```

命令只读文件，使用标准库，不连接DB/Redis。输出根工作流及可移植副本绝对路径、SHA-256、完整阶段/显式测试接入状态。根文件缺失、重复嵌套入口、模板不同或测试步骤缺失均返回非0；CI本身在依赖安装前执行同一检查。该检查不证明远端运行已成功。

- `ci.yml.template`：与根工作流逐字节相同的可移植副本，不会作为 GitHub 工作流触发。只交付应用子目录时也可审阅完整CI内容。
- `中枢补交ci.yml.snapshot`：本次整改前嵌套补交文件的完整快照，仅作审核证据，不执行。
- `../上线前补缺复验CI交付清单.json`：本次本地实际路径和校验和。

真实 Git 根有三种支持布局：根下直接有 `manage.py`；根下有 `django/manage.py`；SaaS根下有 `inventory-v4/django/manage.py`。入口文件都必须位于**该真实根的** `.github/workflows/ci.yml`。若在其他机器采用不同根层级，将 template 复制到实际根入口后再执行自检；仅设置工作目录不能改变GitHub的工作流发现规则。

修改CI时同步根文件和template，再运行交付自检及测试。当前本地已有项目Git checkout；本次不执行commit/push，真实GitHub push/PR记录仍由中枢留存。

P8a整改后增加 `scripts/sync_schema_snapshot.py --check`，在依赖安装前只读检查当前SQL指纹与快照一致性；默认precheck_sql也执行相同检查。P8a历史验收日志现归档到项目根docs/过程记录/P8a/，独立复验可用P8A_ARTIFACT_DIR指定新的制品目录。
