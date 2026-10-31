# 上线前补缺指令包（给 Codex）

- **项目**：学校总务库存系统 `inventory-v4/django`（P0–P7 已全通过阶段门禁）
- **发包人**：控制中枢（验收方，不出业务代码）
- **执行人**：Codex（外部 agent，负责落地业务代码 / CI 配置）
- **目的**：补齐"上线测试"所需的 CI 可发现性、CI 测试覆盖、依赖锁定三项缺口（审计报告中的 B2 / B6 / B9）。
- **不在本包范围**：B1/B4/B5 部署骨架已由控制中枢在 `deploy/` 补齐；B8 真实云 S3/Redis 联调由用户在目标环境实测；**B3（就绪探针 + 结构化 LOGGING）经 2026-10-31 本地实测 + 代码核实，确认已由 Codex 落地，移出本包**（详见审计报告 B3 更正）。

---

## 0. 强约束（违反即驳回）

1. **DDL 唯一来源**：`sql/schema-v7.sql`（27 表 / 14 函数 / 32 触发器）。任何表/函数/触发器/授权改动都必须手写在该文件并通过 `RunSQLFile`（SHA-256 钉死），**严禁 Django autogenerate**。本次任务不涉及 DDL，不得触碰该文件。
2. **运行/迁移身份分离**：业务命令以 `inventory_runtime` 成员身份执行，`require_runtime()` 对超户拦截。不得为图省事放宽。
3. **密钥安全**：不得硬编码 `SECRET_KEY / DEBUG=True / password= / api_key=`；不得新增 `managed=True` 模型。
4. **不得删除/弱化**既有 146 项 Django 测试、`verify_p1–p7.py` 脚本、`deploy/` 制品。
5. 交付后由控制中枢**独立验收**，Codex 不自行声称通过；须附改动清单与本地复跑证据。

---

## 任务 B2 — CI 工作流移到仓库根并重命名（🔴 阻塞）

**现状**：文件位于 `inventory-v4/django/.github/workflows/inventory-p1.yml`，但内容实为 `p0–p7`；且作为子目录嵌套，GitHub **不会自动发现**嵌套工作流 → 远端 CI 实际未运行，门禁形同虚设。

**动作**：
1. 在 **SaaS 仓库根** `.github/workflows/ci.yml` 创建新工作流（内容沿用现有，但 `name:` 改为 `ci` 或 `inventory`，文件名与内容一致）。保留现有"Locate application"逻辑（`manage.py` 存在则用 `.`，否则 `inventory-v4/django`）。
2. 删除或移除 `inventory-v4/django/.github/workflows/inventory-p1.yml`，避免与根工作流重复触发。
3. 工作流触发条件维持 `on: [push, pull_request]`。

**验收**：
- 在仓库根提交后，GitHub Actions 在 push/PR 时**自动触发**（非手动）。
- 工作流文件名 = `ci.yml`，`name` 与内容一致。
- 现有 verify_p1–p7 步骤全部跑通（无回归）。

---

## 任务 B3 — 应用层就绪探针 + LOGGING（✅ 已落地，Codex 无需处理）

**状态（2026-10-31 实测 + 代码核实）**：经控制中枢本地 bootstrap 实测 + 代码核实，B3 已由 Codex 在先前阶段落地，**移出本指令包**：
- `apps/core/views.py` 已有 `ready` 视图（`@require_safe` + `@transaction.non_atomic_requests`，DB `SELECT 1` + Redis ping 2s 超时，不泄露连接串/异常）；`urls.py` 已挂 `health/ready/`；DB/Redis 任一不可用时返回 503（本地无 redis 实测确认）。
- `settings.py:77` 已配 `LOGGING`（结构化 JSON `shared.logging.JsonFormatter` + console + root INFO）。
- `tests/test_readiness.py` + `scripts/verify_readiness.py` 已覆盖。

> 初版审计报告曾误将 B3 标为缺口（仅参考 `deploy/ops-runbook.md` 第 11 节"建议新增"片段而未见实际代码），已更正。详见审计报告 B3 更正段。

---

## 任务 B6 — CI 接入 Django 146 测试套件（🟠 覆盖缺口）

**现状**：CI 仅执行 `verify_p1.py`–`verify_p7.py`，**未执行 `manage.py test tests`（146 项）**，最权威的回归网被挡在门外。

**动作**：在 `ci.yml` 的 `verify_p7.py` 步骤之后追加两步（注意：`apply_schema` 先建基线库 `campus_inventory` 与 `inventory_runtime` 角色，使 Django 测试克隆库继承授权；测试固件内部 `SET LOCAL ROLE inventory_runtime`）：

```yaml
      - run: uv run python manage.py apply_schema
        working-directory: ${{ env.APP_DIR }}
      - run: uv run python manage.py test tests --no-input
        working-directory: ${{ env.APP_DIR }}
```

**验收**：
- CI 跑通且 **146 项全过**（与本地一致）。
- 本地 `manage.py test tests` 仍全过（无回归）。
- `apply_schema` 步骤在 CI 的 postgres 服务下成功（SECRET_KEY 已由 env 提供）。

---

## 任务 B9 — 导出 requirements.txt（🟡 产物管理）

**现状**：仅 `uv.lock` + `pyproject.toml`，无 `requirements.txt`；部分目标平台 / Docker 基础镜像不吃 uv。

**动作**（在应用目录执行）：
```sh
uv export --frozen --format requirements-txt --output-file requirements.txt
```
提交生成的 `requirements.txt`（不提交机密）。

**验收**：
- `requirements.txt` 生成成功、可被 `pip install -r requirements.txt` 解析。
- 在 Docker 基础镜像（python:3.13-slim）中 `pip install -r requirements.txt` 能装齐运行依赖（或可佐证等价）。

---

## 交付清单（Codex 返回时须附）

1. 改动文件清单（B2 的 ci.yml 路径、B3 的 views/urls/settings 三处、B6 的 ci.yml 改动、B9 的 requirements.txt）。
2. 本地 `manage.py test tests` 结果（应 146 passed）。
3. `manage.py check` 与 `precheck_sql` 结果。
4. 声明 B2 工作流是否已在真实 GitHub 仓库根生效（或仅代码改动待用户推送验证）。

控制中枢将据此独立走查 + 复跑，出具验收报告，并汇总最终上线测试就绪结论。
