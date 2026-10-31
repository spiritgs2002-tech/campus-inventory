# CI 交付定位说明

> ⚠️ 中枢纠正（2026-10-31 19:57）：原"仓库根为 `/Users/andy/Desktop/saas`"的判定**不成立**。本沙箱内 `saas` 不是 git 仓库（无 `.git`），唯一的 git 仓库是同级独立的 `campus-repair`——即用户约定为 `saas/<项目>/` 各自成库。因此权威工作流已改为放在最可能的项目根：

`/Users/andy/Desktop/saas/inventory-v4/.github/workflows/ci.yml`

应用目录是 `/Users/andy/Desktop/saas/inventory-v4/django`。**请用户确认自己实际的 git 根**：如果 `inventory-v4/` 就是该项目的 git 仓库根（最可能是此情况），上述位置即正确；如果实际仓库根是 `inventory-v4/django/`（即 django 才是仓库根），则需把文件下移一层到 `inventory-v4/django/.github/workflows/ci.yml`。无论哪种，**必须保证文件位于你项目 `.git` 所在根的 `.github/workflows/ci.yml`**，GitHub 才会发现并触发。

在应用目录执行（以 inventory-v4 为根示例）：

```sh
python scripts/verify_ci_delivery.py --repo-root /Users/andy/Desktop/saas/inventory-v4
```

命令只读文件，使用标准库，不连接DB/Redis。输出根工作流及可移植副本绝对路径、SHA-256、完整阶段/显式测试接入状态。根文件缺失、重复嵌套入口、模板不同或测试步骤缺失均返回非0；CI本身在依赖安装前执行同一检查。该检查不证明远端运行已成功。

- `ci.yml.template`：与根工作流逐字节相同的可移植副本，不会作为 GitHub 工作流触发。只交付应用子目录时也可审阅完整CI内容。
- `中枢补交ci.yml.snapshot`：本次整改前嵌套补交文件的完整快照，仅作审核证据，不执行。
- `../上线前补缺复验CI交付清单.json`：本次本地实际路径和校验和。

真实 Git 根有三种支持布局：根下直接有 `manage.py`；根下有 `django/manage.py`；SaaS根下有 `inventory-v4/django/manage.py`。入口文件都必须位于**该真实根的** `.github/workflows/ci.yml`。若在其他机器采用不同根层级，将 template 复制到实际根入口后再执行自检；仅设置工作目录不能改变GitHub的工作流发现规则。

修改CI时同步根文件和template，再运行交付自检及测试。当前本地没有Git checkout，真实GitHub push/PR自动触发记录仍须在实际仓库留存。
