# v6 再审问题清单（WorkBuddy 审核）

审核对象：`实施计划v6.md`、`schema-v6.sql`、`v5审核逐项答复与v6验收说明.md`、`v6-tools/`（grant-runtime.sql、verify-runtime.sql、reconcile.sql、README.md）
基准：v5 审核意见 + v5 审核逐项答复
审核日期：2026-10-30　审核人：WorkBuddy

> 结论：**v6 对 v5 的绝大多数意见都已妥善处理**（含我此前判断有误的 S6），整体成熟度明显提升。但本版引入了一个**阻断级 SQL 语法错误**（4 个函数美元引号不匹配，schema 无法执行），以及若干 verify 脚本缺口、未落地的会话清理流程等中低问题。下面先确认 v5 意见的处理情况，再列 v6 新发现。

---

## 一、v5 意见处理确认（再审认定）

| v5编号 | 本版处理 | 再审认定 |
|---|---|---|
| C1 quality_change 枚举缺失 | schema 已含 `reason_category` + `adjustment_orders` 加 `CHECK((type='quality_transfer')=(reason_category='quality_change'))` | 已修正 ✓ |
| C2 附录称 v4 字段为"新增" | v6 重写为唯一规则来源，不再沿用平行补充规则 | 已修正 ✓ |
| C3 auth_version 用途不明 | **删除 auth_version**，改每次校验会话族撤销 + 用户/租户状态；改密/禁用事务内撤销全部会话 | 已修正且更干净 ✓ |
| C4 DDL 与 Alembic 边界 | §10 明确空库 DDL ≠ 生产迁移，函数/触发器/授权不能靠 autogenerate | 已澄清 ✓ |
| S1 质量转换单行双流水 | 保留一行，加列注释 + `uq_adjustment_ledger_direction` 约束 in/out 各至多一条，服务/对账保证恰好两条 | 可接受，已文档化 ✓ |
| S2 部分发放/零发放约束 | 改为 `validate_outbound_completion` 触发器，completed 时校验差额原因、非零总量、固定资产卡数 | 比原建议的 CHECK 更合理 ✓ |
| S3 幂等键域 | 明确"每操作者键域"，跨人执行锁目标单、跨人导入锁 file_jobs | 已澄清 ✓ |
| S4 会话清理 | 整族清理：全部过期 >30 天且无活跃后继才删，先清 parent 再删，处理自外键 | 方向正确，但**清理实现缺失（见 P1-2）** |
| S5 updated_at | 主数据补 `updated_at`+触发器；历史以不可变时间/审计为准 | 已补 ✓ |
| S6 num_nonnulls 版本门槛 | **本人原判断有误**：num_nonnulls 自 PG9.6 即存在，非 PG14。**v6 改 pin PG16 为正确选择**，但与 num_nonnulls 无关 | 本人误判，v6 处置正确；真实版本下限其实是 PG13（见 P1-3） |
| S7 冻结理由体验 | 取消/驳回提供携带原内容的新申请，决策原因与申请理由分离保存 | 已采纳 ✓ |
| B1 期初切换 | §8 新增冻结/快照/导入/对账/签字启用/只读封存/失败处置完整清单 | 已补 ✓ |
| B2 部门个人耗用统计 | B9 明确"领用统计≠耗用统计"，M0 学校确认 | 已补 ✓ |
| B3 证据保留期 | 独立 `attachments`+`scrap_attachments`，含 `retention_until`/`legal_hold`，证据对象不受导出 TTL 影响 | 已落地 ✓ |
| B4 实发卡数=发卡数 | `validate_outbound_completion` + reconcile `fixed_issued_count` + M3 双重覆盖 | 已补 ✓ |
| B5 低库存不通知 | 不新增无业务字段，二期复用 safety_stock | 已采纳 ✓ |
| SEC1 运行角色防篡改 | **提供 grant-runtime.sql + verify-runtime.sql**，M1 阻断；并注明实际登录成员也要查 | 大幅改善（verify 仍有缺口，见 P1-1） |
| SEC2 审计存 IP | 应用审计**不再存 IP**；网关安全 IP 默认 ≤7 天、限运维，且不做"一律敏感个人信息"定性 | 已修正 ✓ |
| SEC3 对账脚本 | 提供只读 `reconcile.sql` 覆盖 7 类检查 | 已交付 ✓ |
| SEC4 锁定用户仍可回收 | M1/M3 验证被锁用户仍可由管理员回收/转移，管理员不绕过自身授权 | 已纳入 ✓ |

> 说明：v5 答复文档把 Excel 工作簿的 31 项与我的 20 项合并为 51 项逐条回复，逻辑清晰、基本都成立。以下仅列 v6 新引入或仍残余的问题。

---

## 二、v6 新发现问题

### P0（阻断 / 必须修）

**P0-1　schema-v6.sql 四个函数美元引号不匹配，文件无法执行**
- 受影响函数（均为 `LANGUAGE plpgsql SET search_path=... AS $` 开、以 `$$;` 闭）：
  1. `reject_history_mutation`（约第 383 行）
  2. `protect_order_header`（约第 395 行）
  3. `protect_order_line`（约第 414 行）
  4. `close_asset_issue_once`（约第 441 行）
- 其余 7 个函数（inventory_version_guard、inbound_insert_guard、asset_issue_insert_guard、xid_stamp、operation_guard、validate_outbound_completion、mark_updated）均用正确的 `AS $$ … $$;`。
- PostgreSQL 美元引号必须成对一致；单 `$` 不是合法开界符，psql 会在第一条函数处报 `unterminated dollar-quoted string` / 语法错误，**整个 schema 建表事务回滚，一张表都建不出来**。
- **修复**：把上述 4 处 `AS $` 改为 `AS $$`（闭引号 `$$;` 不变）。修复后建议立即在 PG16 空库跑一次 `psql -f schema-v6.sql` 验证，这正是 M0 的第一步。

### P1（高 / 应在 M0–M1 前闭环）

**P1-1　verify-runtime.sql 断言不完整，存在权限回归盲区**
- 当前仅对 `audit_logs/stock_ledger/asset_events` 断言"仅 SELECT+INSERT"；仅对全部表断言"非 owner、无 TRIGGER、无 TRUNCATE"。但**未断言**：
  - 业务表（含 `outbound_orders`、`outbound_items`、`scrap_orders`、`adjustment_orders` 等）runtime **无 DELETE**（当前 grant 未授予 DELETE，但脚本未验证；一旦后续迁移误授 DELETE，verify 不会拦）；
  - `inbound_orders`、`inbound_items` 以及三张历史表 runtime **无 UPDATE**（grant 已 REVOKE，verify 未校验）。
- **建议**：在 verify 的表循环中增加：对全部业务表断言 `NOT has_table_privilege(r,oid,'DELETE')`（豁免 `auth_sessions`、`login_throttle`）；对 `inbound_orders`、`inbound_items` 及三张历史表断言 `NOT has_table_privilege(r,oid,'UPDATE')`。使"不可篡改"由触发器 + 权限双层在脚本层可验证。

**P1-2　会话整族清理流程只有文字、无实现**
- 计划 §2 描述"族内全部 token 过期 >30 天且无活跃后继才删除整族，事务内先清 parent_session_id 再删"。但 schema 与 v6-tools **均未提供对应清理函数/存储过程/任务**，仅说明"保留过期 token 用于复用检测"。
- **建议**：补充一个可重试的清理函数（或明确由应用层定时任务实现），并在 M1/M2 验证中列名；否则该安全设计停留在纸面，长期运行 `auth_sessions` 仍会膨胀（v5 的 S4 关切未真正闭环）。

**P1-3　真实 PG 版本下限是 13，而非"仅因 num_nonnulls"**
- 本版大量使用 `pg_current_xact_id()`（列默认值、xid_stamp/inbound_insert_guard/operation_guard），该函数自 **PostgreSQL 13** 提供。因此即便不谈 num_nonnulls，schema 的有效最低版本也是 13。
- 当前 pin PG16 完全没问题，但建议在 §2 写明"最低 PG13（xid 函数），运行基线 PG16"，避免未来有人拿 12 建库踩坑。

**P1-4　inventory_version_guard 与快照审批的并发边界需 M2 强覆盖**
- 触发器自动 `version+1` 且仅在 quantity 变化时递增，依赖服务"先锁库存行、再比对 adjustment_items 中快照的 inventory_version/target_inventory_version"。但**数据库层不保证 adjustment_items.inventory_version 与库存行当前 version 一致**——仍属服务保证（答复已自承）。
- **建议**：M2 必须包含"审批瞬间被并发入库/出库改 version 导致快照过期回滚"的用例；并在 reconcile 之外补充"adjustment_items 快照 version 与库存当前 version 在审批时一致"的运行期断言（或触发器级最终一致性检查）。

### P2（中 / 可随实现消化）

**P2-1　scrap_attachments 不可删除，pending 阶段也无法撤错附件**
- `evidence_frozen` 复用 `protect_order_line`，其 DELETE 分支一律 `RAISE 'cancel the order instead of deleting lines'`。即证据附件**任何阶段都不能删**，连 pending 时误传的附件也只能靠取消整单处理。
- 对"证据 append-only"可接受，但建议文档明确此行为；若希望 pending 允许撤回未决附件，可把该行 DELETE 放行（仅当父单 pending 且未决策）。

**P2-2　冗余唯一约束**
- `scrap_items` 同时有 `UNIQUE(tenant_id,id,asset_card_id)`（CREATE 时）与 `UNIQUE(tenant_id,id,asset_card_id,disposal_type)`（ALTER 时）。后者是前者的超集、且是 FK 目标，前者可删，避免维护歧义。
- 同理 `outbound_assets` 的 `UNIQUE(tenant_id,id,item_id)` 与 `UNIQUE(tenant_id,outbound_item_id,asset_card_id)` 各自有用，保留。

**P2-3　EXECUTE 授权不含未来函数**
- `GRANT EXECUTE ON ALL FUNCTIONS IN SCHEMA public` 仅覆盖当前函数。后续迁移新增函数/触发器后，runtime 可能因缺 EXECUTE 而触发失败（触发器以 runtime 身份执行）。
- **建议**：在迁移收尾步骤注明"重新执行 grant-runtime.sql"，或改用语义更稳的默认权限（`ALTER DEFAULT PRIVILEGES`）。

**P2-4　operation_requests.created_xid 默认与触发器重复赋值**
- 列默认 `pg_current_xact_id()` 与 `xid_stamp` 触发器都写同一值，冗余但无害。可去掉列默认，仅保留触发器，减少阅读歧义。

**P2-5　reconcile.sql 未覆盖附件/实物一致性**
- reconcile 7 项均为金额/数量/状态内部一致性，不含 `attachments`/`scrap_attachments` 与单据状态的一致性（如证据仅挂在 pending 或已决单上）、也不替代实物抽盘（README 已声明）。建议在 M4 实物抽盘流程中补"证据附件与处置单状态"的人工核对项。

**P2-6　quality_transfer 仍保留"单行双流水"不对称模型**
- v6 已用注释 + 唯一索引缓解，但 `adjustment_items` 仅记 `direction='out'`，目标 in 由审批事务补写。维护者仍易误读。若 M2 实现中出现"只写了一条流水"的 bug，reconcile 的 `quality_transfer_ledger` 会抓到（net≠0 或条数≠2），因此**风险可控**，仅作提醒。

### P3（低 / 记录即可）

- `asset_events` 的 `source_loss_item_id` 列本身可空，仅靠 `CHECK((action='found')=(...IS NOT NULL)` 约束；非 found 时 FK 部分为空自动放行，符合预期，无问题。
- `login_throttle` 无清理/索引之外的逻辑，依赖服务实现；建议 M1 用例覆盖"锁定期间管理员仍可回收/转移资产"（已列入 SEC4）。
- 所有函数均已 `SET search_path = pg_catalog, public`，消除了 v5 未设 search_path 的潜在提权面，这是 v6 的安全改进，值得肯定。

---

## 三、对 v5 本人的校正

- **S6 原判断有误**：我曾称 `num_nonnulls` 需 PG14，答复方举 PG9.6 官方文档为证，成立。v6 改 pin PG16 仍是稳妥基线。真实版本下限来自 `pg_current_xact_id()`（PG13），见 P1-3。特此更正，避免误导。

---

## 四、建议的下一步（优先级）

| 优先级 | 编号 | 动作 |
|---|---|---|
| P0 | P0-1 | 4 个函数 `AS $` → `AS $$`；随后立即在 PG16 空库实跑 schema-v6.sql 验证建表 |
| P1 | P1-1 | 补 verify 对"业务表无 DELETE、不可变表无 UPDATE"的断言 |
| P1 | P1-2 | 提供会话整族清理函数或明确应用层实现，纳入 M1/M2 验证 |
| P1 | P1-3 | §2 写明最低 PG13 / 基线 PG16 |
| P1 | P1-4 | M2 增加快照过期回滚并发用例 + 运行期 version 一致性断言 |
| P2 | P2-1~P2-5 | 文档澄清 / 冗余约束清理 / 默认权限 / 冗余默认值 / 附件核对项 |

> 总体评价：v6 是**高质量的设计交付**——多租户双隔离、三层防篡改（触发器+运行角色+应用）、幂等长期保留、统一锁顺序+乐观版本、资产状态机+事件溯源+对账、权限治理（自批留痕、最后管理员保护、release 角色脚本化）均已成型。当前唯一会"让 M0 第一步就翻车"的是 P0-1 的美元引号笔误，修掉后建议把 v6-tools 三脚本在真实 PG16 上跑通一次作为 v6 定稿门槛。本审核未实际执行 PostgreSQL（本机无 psql/Docker），P0-1 为静态阅读确定性结论，其余以 M0 实跑 + 负向/并发测试为准。
