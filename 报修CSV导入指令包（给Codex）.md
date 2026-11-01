# 报修 CSV 批量导入命令 · 实现指令包（给 ChatGPT Codex）

> 目的：闭合「方案 A（腾讯文档/企微收集表 → 导出 CSV → 导入系统）」的最后一公里。
> 这是一条**写领域记录的 Django 管理命令**，按本仓库宪法（SQL-first、手写 ORM、租户隔离、运行/迁移身份分离）实现。
> 实现者：用户的 ChatGPT Codex；完成后由中枢（本项目架构师）做逻辑走查与规范验收。
> **严禁**改动 `sql/schema-v7.sql` 或任何 DDL/触发器/函数；**严禁** `manage.py makemigrations` / `autogenerate`；`RepairTicket` 是 `managed=False`，只能用现有 ORM 读写外壳。

---

## 1. 命令规格

| 项 | 值 |
|---|---|
| 命令名 | `python manage.py import_repairs` |
| 文件位置 | `apps/repair/management/commands/import_repairs.py`（需新建 `apps/repair/management/__init__.py`、`apps/repair/management/commands/__init__.py`，若尚不存在） |
| 运行身份 | 应用默认运行身份 `inv_run`（web 容器默认），命令开头调用 `require_runtime()` 守卫；**不要用迁移身份** |
| 租户隔离 | 必填 `--tenant-code`，先 `Tenant.objects.get(code=...)` 取 `tenant_id`，所有写操作带 `tenant_id` |

### CLI 参数
- `--csv PATH`（必填）：CSV 文件路径。
- `--tenant-code CODE`（必填）：目标租户代码（如 `LOCAL` / `SCHOOL`）。
- `--batch-id ID`（必填）：批次标识，用于确定性 `ticket_no` 与可重跑幂等（如 `20261101-form`）。
- `--encoding NAME`（可选，默认 `utf-8-sig`）：CSV 编码（收集表导出常带 BOM）。
- `--delimiter CHAR`（可选，默认 `,`）：支持 `,` / `;` / `\t`。
- `--dry-run`（可选）：只校验与统计，不落库。
- `--strict`（可选）：遇首行错误即中止；否则收集全部错误并继续，最后汇总。
- `--default-target-type {asset,item,location}`（可选）：当 CSV 未给 `target_type` 时的兜底（默认 `location`，因场地类报修可不关联具体对象）。

---

## 2. CSV 规范（表头支持中英文别名）

首行表头，**支持以下任一别名**（实现时做大小写无关 + 去空格 + 中文映射）。至少 `reporter_name`、`reporter_no`、`description` 三列必填。

| 规范列名 | 中文别名（接受其一） | 必填 | 说明 / 约束 |
|---|---|---|---|
| `reporter_name` | 姓名 / 报修人 / 姓名 | ✅ | ≤120 字符，trim 后非空 |
| `reporter_no` | 工号 / 学号 / 工号或学号 | ✅ | 正则 `^[A-Za-z0-9_.-]{1,80}$`；作为轻量用户的 `username` 键 |
| `target_type` | 对象类型 / 报修对象类型 | 否 | `asset` / `item` / `location`；缺省用 `--default-target-type` |
| `target_id` | 对象编号 / 编号 | 否 | 整数；与 `target_name` 二选一优先 |
| `target_name` | 对象名称 / 名称 | 否 | 按类型查 `asset_no` / `code` 或 `name` 精确匹配 |
| `description` | 故障说明 / 说明 / 描述 | ✅ | trim 后 1–4000 字符 |
| `priority` | 紧急程度 / 优先级 | 否 | `low` / `normal` / `high`；缺省 `normal` |
| `status` | 状态 | 否 | 默认 `pending`；允许 `pending/dispatched/repairing/done/cancelled` |
| `assigned_to_no` | 处理人工号 | 否 | 仅当 `status` 非 `pending` 且非 `cancelled` 时必填（见约束⑥）；按 `username` 解析本校启用用户 |
| `reject_reason` | 驳回原因 | 否 | ≤2000 字符；仅 `cancelled` 时有意义 |
| `note` | 备注 / 地点 | 否 | 仅作描述补充，落库到 `description` 末尾（不单独建列） |

> 收集表常见导出没有"对象编号"，所以 `target_name` 名称解析是主路径；解析不出且 `target_type≠location` 的行按错误收集。

---

## 3. 字段映射与校验规则（逐条）

1. **reporter**：`reporter_name`、`reporter_no` 必填且合规 → 调 `apps.inventory.pickup.ensure_lightweight_user(tenant_id, name, reporter_no)` 取/建轻量用户，得到 `reporter_user_id`、`reporter_name`、`reporter_no`。该函数已处理"工号复用 / 同名校验 / 身份一致性"，直接复用，**不要自己建 User**。
2. **target_type**：归一为 `asset/item/location`；非法值 → 行错误。
3. **target 解析**（优先级：显式 `target_id` → `target_name`）：
   - 若 `target_id` 为整数且 >0：直接用它，并校验"存在于本校 + （asset/item 须 `active`）"，否则行错误。
   - 否则用 `target_name` 精确匹配：
     - `asset` → `AssetCard.objects.filter(tenant_id=tenant_id, asset_no=name)`（若多条/0 条 → 错误）；
     - `item` → 先 `code=name` 精确，命中 0 条再 `name=name` 精确（`active=True`）；多条或 0 条 → 错误；
     - `location` → `Location.objects.filter(tenant_id=tenant_id, name=name, active=True)`（租户内 name 唯一）；0 条 → 错误。
   - 若 `target_type=location` 且 `target_id`/`target_name` 皆空 → `target_id=None`（schema 允许）。
   - 若 `target_type∈{asset,item}` 且两者皆空 → 行错误（FK 不可空）。
4. **description**：trim 后长度 1–4000；超长 → 行错误（不要静默截断）。
5. **priority**：默认 `normal`，非法 → 行错误。
6. **status**：默认 `pending`；若非 `pending` 且非 `cancelled`，则 `assigned_to` 必填（schema `CHECK(status NOT IN ('dispatched','repairing','done') OR assigned_to IS NOT NULL)`）→ 用 `assigned_to_no` 经 `User.objects.filter(tenant_id=tenant_id, username=no, active=True)` 解析，0 条/多 → 错误。
7. **reject_reason**：仅记录；≤2000。
8. **ticket_no**：`"RP-" + batch_id + "-" + f"{seq:05d}"`（seq 为 CSV 行序号，从 1 起）。**确定性 + 唯一** → 重跑同批同文件产生相同 `ticket_no`。
9. **photo_attachment_id**：收集表场景无照片 → 固定 `None`。
10. **created_at / updated_at**：由 DB `default now()` 生成（导入时间即记录时间）。**不要尝试回填提交时间**——`created_at` 是 `auto_now_add`，ORM 无法覆盖（若需历史回溯属超出本命令范围，另议）。

---

## 4. 落库与幂等（核心）

- 每条成功行：`RepairTicket.objects.create(tenant_id, ticket_no, reporter_user_id, reporter_name, reporter_no, target_type, target_id, description, priority, status, assigned_to, reject_reason, photo_attachment_id=None)`。
  - **不要**给 `asset_target_id`/`item_target_id`/`location_target_id` 赋值——它们是 PG `GENERATED ALWAYS AS STORED`，由库计算（Django `GeneratedField(db_persist=True)` 会自动排除）。
- **幂等重跑**：用 `try/except IntegrityError` 捕获 `(tenant_id, ticket_no)` 唯一冲突 → 计为 `skipped(duplicate)`，不报错继续。
- **审计**：创建后参考 `apps.repair.services.repair_submit._submit` 调 `apps.core.services.audit(...)` 写一条 `action=create` 的审计（保持与在线报修一致）。若 `core.audit` 对轻量主体签名不兼容，降级为 `try/except` 记录 warning（工单仍创建）；验收时确认审计条目存在。
- **事务**：整批包一个 `transaction.atomic()`；单行校验失败不应回滚整批（除非 `--strict`）。建议逐行 try，行级错误计入汇总、不影响其他行。

---

## 5. 输出与汇总

执行结束打印（或 `--dry-run` 仅打印）汇总：
```
[import_repairs] tenant=LOCAL batch=20261101-form csv=xxx.csv
  总行数        : 120
  成功导入      : 115
  跳过(重复)    : 3
  行错误        : 2
  错误明细:
    行 17 : target_name '投影仪A' 在资产中匹配到多条 (asset_no 不唯一)
    行 53 : reporter_no 非法 (含中文)
```
退出码：全部成功（含重复跳过）= 0；存在行错误 = 1；参数/租户/文件错误 = 2。

---

## 6. 验收标准（中枢复验用）

1. 命令存在且 `--help` 可用；参数缺省报错清晰。
2. 一个 5–10 行样例 CSV（含：正常行、名称解析行、重复重跑、1 行错误）跑通：成功行入库、`/manage/repairs/` 可见；重跑同文件 `skipped(duplicate)` 计数正确、无重复工单。
3. 租户隔离：用 `--tenant-code` A 导入的记录，绝不以租户 B 身份可见（带 `tenant_id` 写、查询带 `tenant_id`）。
4. 轻量用户复用：同一 `reporter_no` 多次导入不重复建 User（`ensure_lightweight_user` 幂等）。
5. 约束全部由 DB 兜底：`target_type` 枚举、`description` 长度、`status`/`assigned_to` 组合 CHECK、FK 存在性——导入不绕过，错误以行错误呈现而非 500。
6. 不改动任何 SQL/DDL；`git status` 仅新增 `apps/repair/management/commands/import_repairs.py` 及必要的 `__init__.py`。
7. 新增 `tests/test_import_repairs.py`：≥4 个用例（正常导入、名称解析、重复重跑幂等、行错误不影响他行、租户隔离）。`manage.py test tests` 全绿。

---

## 7. 参考实现骨架（Codex 据此适配，须核对实际函数签名）

```python
# apps/repair/management/commands/import_repairs.py
import csv, sys
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction, IntegrityError
from django.utils import timezone

from shared.db.runtime import require_runtime
from apps.core.models import Tenant, User
from apps.catalog.models import Item, Location
from apps.inventory.models import AssetCard
from apps.inventory.pickup import ensure_lightweight_user, tenant_available
from apps.repair.models import RepairTicket

TARGETS = {"asset", "item", "location"}
PRIORITIES = {"low", "normal", "high"}
STATUSES = {"pending", "dispatched", "repairing", "done", "cancelled"}

ALIASES = {
    "reporter_name": {"姓名", "报修人", "name"},
    "reporter_no": {"工号", "学号", "工号或学号", "employee_no"},
    "target_type": {"对象类型", "报修对象类型"},
    "target_id": {"对象编号", "编号", "id"},
    "target_name": {"对象名称", "名称"},
    "description": {"故障说明", "说明", "描述"},
    "priority": {"紧急程度", "优先级"},
    "status": {"状态"},
    "assigned_to_no": {"处理人工号", "处理人"},
    "reject_reason": {"驳回原因"},
    "note": {"备注", "地点"},
}

def _header_map(raw):
    m = {}
    for h in raw:
        key = h.strip().lower()
        if key in ALIASES:            # 已是规范名
            m[h] = key
            continue
        for canon, al in ALIASES.items():
            if h.strip() in al or key == canon:
                m[h] = canon; break
    return m

class Command(BaseCommand):
    help = "从收集表导出的 CSV 批量导入报修工单（方案A闭环）"

    def add_arguments(self, p):
        p.add_argument("--csv", required=True)
        p.add_argument("--tenant-code", required=True, dest="tenant_code")
        p.add_argument("--batch-id", required=True, dest="batch_id")
        p.add_argument("--encoding", default="utf-8-sig")
        p.add_argument("--delimiter", default=",")
        p.add_argument("--dry-run", action="store_true")
        p.add_argument("--strict", action="store_true")
        p.add_argument("--default-target-type", default="location")

    def handle(self, *a, **o):
        require_runtime()
        try:
            tenant = Tenant.objects.get(code=o["tenant_code"])
        except Tenant.DoesNotExist:
            raise CommandError(f"租户不存在: {o['tenant_code']}")
        tenant_available(tenant)
        tid = tenant.id
        rows = list(csv.DictReader(open(o["csv"], encoding=o["encoding"]),
                                   delimiter=o["delimiter"]))
        # ... _header_map 归一化、逐行校验、ensure_lightweight_user、target 解析、
        #     RepairTicket.objects.create、IntegrityError 跳过、汇总打印 ...
        # 完整逻辑由 Codex 补全并对照第 3/4 节规则。
        self.stdout.write(self.style.SUCCESS("见第5节汇总格式"))
```

> 上述骨架仅示意字段映射与守卫，**Codex 必须补全逐行解析/校验/落库/汇总**，并对照 `apps/repair/services.py`、`apps/inventory/pickup.py`、`apps/catalog/models.py`、`apps/inventory/models.py`（AssetCard.asset_no）的实际签名与字段名，确保无误。

---

## 8. 不在本命令范围（避免scope creep）

- 照片/附件导入（收集表通常无图；如需，另做附件上传通道）。
- 提交时间回溯（`created_at` 为导入时间）。
- 任何 schema / DDL 变更。
- 在线报修接口改造（本命令只补"离线 CSV 入系统"这一环）。
