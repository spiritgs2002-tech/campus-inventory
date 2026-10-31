-- schema-v7.sql —— PostgreSQL 16 新库基线（由 schema-v6.sql 修正而来，替代 v6 作为唯一基线）
--
-- 【制品指纹】SHA-256(SQL 正文，自 BEGIN 语句起): eeab8d609555a567e59df98335fad7c99714473ba7b161f78f8a3d61c0c0f6c3
--              SQL 正文行数: 723
--   每次修改本文件必须同步更新指纹，并在真实 PG16 重跑 G0 与 v7-tools/test-negative.sql。
--
-- v7 相对 v6 的改动：①修正 4 个函数美元引号（reject_history_mutation/protect_order_header/
--   protect_order_line/close_asset_issue_once，v6 为 AS $ ... $$; 导致整文件无法执行）；
--   ②operation_requests/inbound_orders 去除 created_xid 列默认值，仅由 xid_stamp 触发器赋值；
--   ③scrap_attachments 改用 evidence_line_guard：父单 pending 且未决策时可撤回误传证据，决策后 append-only；
--   ④新增 attachments_metadata_guard：附件元数据不可改、deleted_at 单向、retention_until 只可延长；
--   ⑤users 补 updated_at + 触发器。
--
-- v7.2 改动（依据整改意见 v7.2）：
--   ⑥[N1/P0] 修正 evidence_line_guard 与 attachments_metadata_guard 结尾 `END; $$` 缺分号
--      —— 会导致整份 DDL 无法执行。静态预检见 v7-tools/precheck-sql.py。
--   ⑦[N5] outbound_assets 新增 loss_type 生成列；fk_loss_closure 移至 scrap_items 四列唯一
--      约束之后并改为四列外键，强制结案 loss 只能指向 disposal_type='loss' 的同卡明细。
--   ⑧[N7] 新增 asset_card_guard：资产卡禁止物理删除，账实核心字段不可改。
--   ⑨[N8] asset_events 事件来源改为"同卡"三列外键，并新增 scrap_event_type 生成列 + 四列外键
--      强制 action 与 disposal_type 匹配；outbound_assets 补 UNIQUE(tenant_id,id,asset_card_id)。
--   ⑩[N9] scrap_items 补 UNIQUE(tenant_id,id,item_id)；stock_ledger 报损外键改为三列。
--   ⑪[N10] attachments.object_key 由全局唯一改为 UNIQUE(tenant_id,object_key)。
--   ⑫[N6] attachments_metadata_guard 增加孤儿附件提前标记删除分支；evidence_line_guard 拒绝
--      关联已撤回附件；移除 retention_until IS NULL 死代码。
--   ⑬[N11] 补索引 idx_scrap_attachment_ref、idx_asset_issue_history。
--   ⑭[N5 副作用修复] close_asset_issue_once / protect_order_line 的整行 jsonb 比较显式排除
--      生成列——BEFORE 触发器中 NEW 的生成列尚未计算（NULL），参与比较会恒判为"已变更"。
--
-- 【实测】2026-10-31 PostgreSQL 16.15（Homebrew, aarch64-apple-darwin）：
--   单事务 COMMIT、0 报错；27 表 / 14 函数 / 32 触发器断言全等；
--   v7-tools/test-negative.sql 21 项正负用例全通过。
--
-- 请用独立数据库验证；正式迁移通过 Alembic 管理。本文件不宣称已通过实库验收。
BEGIN;

CREATE TABLE tenants (
 id BIGSERIAL PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 login_failure_limit INTEGER NOT NULL DEFAULT 5 CHECK(login_failure_limit>=1), login_lock_minutes INTEGER NOT NULL DEFAULT 15 CHECK(login_lock_minutes>=1), asset_prefix TEXT NOT NULL DEFAULT 'AS', next_asset_number BIGINT NOT NULL DEFAULT 1 CHECK(next_asset_number>0), expire_at DATE, allow_self_approval BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE departments (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 name TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE users (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 username TEXT NOT NULL, password_hash TEXT NOT NULL, name TEXT NOT NULL,
 role TEXT NOT NULL CHECK(role IN ('admin','user')), dept_id BIGINT,
 must_change_password BOOLEAN NOT NULL DEFAULT true,
 active BOOLEAN NOT NULL DEFAULT true,
 UNIQUE(tenant_id,username), FOREIGN KEY(tenant_id,dept_id) REFERENCES departments(tenant_id,id)
);

CREATE TABLE auth_sessions (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 user_id BIGINT NOT NULL, refresh_digest TEXT NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL,
 family_id UUID NOT NULL, parent_session_id BIGINT, rotated_at TIMESTAMPTZ, revoked_at TIMESTAMPTZ, revoked_reason TEXT CHECK(revoked_reason IN ('logout','password_change','disabled','reuse','expired')), FOREIGN KEY(tenant_id,parent_session_id) REFERENCES auth_sessions(tenant_id,id), FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id)
);

CREATE TABLE login_throttle (
 tenant_id BIGINT NOT NULL, user_id BIGINT NOT NULL,
 failed_login_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_login_count>=0), locked_until TIMESTAMPTZ,
 PRIMARY KEY(tenant_id,user_id), FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id)
);

CREATE TABLE locations (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 name TEXT NOT NULL, active BOOLEAN NOT NULL DEFAULT true, UNIQUE(tenant_id,name)
);

CREATE TABLE categories (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 code TEXT NOT NULL, name TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('consumable','fixed')),
 parent_id BIGINT, active BOOLEAN NOT NULL DEFAULT true, UNIQUE(tenant_id,code),
 UNIQUE(tenant_id,id,type), FOREIGN KEY(tenant_id,parent_id,type) REFERENCES categories(tenant_id,id,type)
);

CREATE TABLE items (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 code TEXT NOT NULL, name TEXT NOT NULL, category_id BIGINT NOT NULL,
 type TEXT NOT NULL CHECK(type IN ('consumable','fixed')), unit TEXT NOT NULL,
 spec TEXT, reference_price NUMERIC(14,2) CHECK(reference_price>=0),
 safety_stock NUMERIC(14,3) CHECK(safety_stock>=0), active BOOLEAN NOT NULL DEFAULT true,
 UNIQUE(tenant_id,code), UNIQUE(tenant_id,id,type),
 FOREIGN KEY(tenant_id,category_id,type) REFERENCES categories(tenant_id,id,type)
);

CREATE TABLE suppliers (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 name TEXT NOT NULL, contact TEXT, active BOOLEAN NOT NULL DEFAULT true
);

CREATE TABLE operation_requests (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 actor_id BIGINT NOT NULL, operation TEXT NOT NULL, idempotency_key TEXT NOT NULL,
 created_xid xid8 NOT NULL, request_digest TEXT NOT NULL, response JSONB, business_refs JSONB NOT NULL DEFAULT '[]', completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,actor_id,operation,idempotency_key),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES users(tenant_id,id)
);

CREATE TABLE file_jobs (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 operator_id BIGINT NOT NULL, file_key TEXT NOT NULL, file_digest TEXT NOT NULL,
 job_type TEXT NOT NULL CHECK(job_type IN ('import','export')), template_type TEXT NOT NULL, template_version TEXT NOT NULL, expires_at TIMESTAMPTZ NOT NULL,
 mapping_version TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('preview','queued','running','completed','failed')),
 result JSONB, FOREIGN KEY(tenant_id,operator_id) REFERENCES users(tenant_id,id)
);

CREATE TABLE inbound_orders (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 created_xid xid8 NOT NULL, order_no TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('purchase','return','opening','adjustment_gain')),
 supplier_id BIGINT, operator_id BIGINT NOT NULL, operation_id BIGINT NOT NULL, file_job_id BIGINT,
 transfer_id UUID, executed_at TIMESTAMPTZ NOT NULL DEFAULT now(), remark TEXT,
 CHECK(type<>'purchase' OR supplier_id IS NOT NULL), UNIQUE(tenant_id,order_no),
 FOREIGN KEY(tenant_id,supplier_id) REFERENCES suppliers(tenant_id,id),
 FOREIGN KEY(tenant_id,operator_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES operation_requests(tenant_id,id),
 FOREIGN KEY(tenant_id,file_job_id) REFERENCES file_jobs(tenant_id,id)
);

CREATE TABLE inbound_items (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 order_id BIGINT NOT NULL, item_id BIGINT NOT NULL,
 item_type TEXT NOT NULL CHECK(item_type IN ('consumable','fixed')),
 quantity NUMERIC(14,3) NOT NULL CHECK(quantity>0),
 unit_price NUMERIC(14,2) CHECK(unit_price>=0), location_id BIGINT NOT NULL,
 quality TEXT NOT NULL CHECK(quality IN ('available','damaged')),
 source_outbound_item_id BIGINT, source_outbound_asset_id BIGINT, adjustment_item_id BIGINT,
 CHECK(item_type<>'fixed' OR quantity=trunc(quantity)),
 CHECK(source_outbound_asset_id IS NULL OR (item_type='fixed' AND quantity=1)),
 UNIQUE(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,order_id) REFERENCES inbound_orders(tenant_id,id),
 FOREIGN KEY(tenant_id,item_id,item_type) REFERENCES items(tenant_id,id,type),
 FOREIGN KEY(tenant_id,location_id) REFERENCES locations(tenant_id,id)
);

CREATE TABLE asset_cards (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 asset_no TEXT NOT NULL, item_id BIGINT NOT NULL, item_type TEXT NOT NULL DEFAULT 'fixed' CHECK(item_type='fixed'),
 source_inbound_item_id BIGINT NOT NULL, location_id BIGINT NOT NULL, responsible_user_id BIGINT,
 status TEXT NOT NULL CHECK(status IN ('in_stock','in_use','repair','scrapped','returned_to_supplier','lost')),
 qr_token TEXT NOT NULL UNIQUE, original_value NUMERIC(14,2) CHECK(original_value>=0),
 purchase_date DATE,
 CHECK((status='in_use' AND responsible_user_id IS NOT NULL) OR (status<>'in_use' AND responsible_user_id IS NULL)),
 UNIQUE(tenant_id,asset_no), UNIQUE(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,item_id,item_type) REFERENCES items(tenant_id,id,type),
 FOREIGN KEY(tenant_id,source_inbound_item_id,item_id) REFERENCES inbound_items(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,location_id) REFERENCES locations(tenant_id,id),
 FOREIGN KEY(tenant_id,responsible_user_id) REFERENCES users(tenant_id,id)
);

CREATE TABLE outbound_orders (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 order_no TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('requisition','purchase_return','transfer')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','cancelled')),
 applicant_id BIGINT NOT NULL, operator_id BIGINT, operation_id BIGINT, executed_at TIMESTAMPTZ, remark TEXT, transfer_id UUID, self_issued BOOLEAN NOT NULL DEFAULT false,
 CHECK(status<>'completed' OR (operator_id IS NOT NULL AND operation_id IS NOT NULL AND executed_at IS NOT NULL)),
 UNIQUE(tenant_id,order_no), FOREIGN KEY(tenant_id,applicant_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,operator_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES operation_requests(tenant_id,id)
);

CREATE TABLE outbound_items (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 order_id BIGINT NOT NULL, item_id BIGINT NOT NULL,
 item_type TEXT NOT NULL CHECK(item_type IN ('consumable','fixed')),
 requested_qty NUMERIC(14,3) NOT NULL CHECK(requested_qty>0),
 actual_qty NUMERIC(14,3) NOT NULL DEFAULT 0 CHECK(actual_qty>=0 AND actual_qty<=requested_qty),
 source_inbound_item_id BIGINT, location_id BIGINT, recipient_id BIGINT, shortfall_reason TEXT,
 CHECK(item_type<>'fixed' OR (requested_qty=trunc(requested_qty) AND actual_qty=trunc(actual_qty))),
 UNIQUE(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,order_id) REFERENCES outbound_orders(tenant_id,id),
 FOREIGN KEY(tenant_id,item_id,item_type) REFERENCES items(tenant_id,id,type),
 FOREIGN KEY(tenant_id,source_inbound_item_id,item_id) REFERENCES inbound_items(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,location_id) REFERENCES locations(tenant_id,id),
 FOREIGN KEY(tenant_id,recipient_id) REFERENCES users(tenant_id,id)
);

CREATE TABLE outbound_assets (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 outbound_item_id BIGINT NOT NULL, item_id BIGINT NOT NULL, asset_card_id BIGINT NOT NULL,
 closed_at TIMESTAMPTZ, close_reason TEXT CHECK(close_reason IN ('return','transfer','loss','purchase_return')), loss_scrap_item_id BIGINT,
 CHECK((closed_at IS NULL AND close_reason IS NULL AND loss_scrap_item_id IS NULL) OR (closed_at IS NOT NULL AND close_reason IS NOT NULL)),
 CHECK((close_reason IS NOT DISTINCT FROM 'loss')=(loss_scrap_item_id IS NOT NULL)), UNIQUE(tenant_id,outbound_item_id,asset_card_id),
 FOREIGN KEY(tenant_id,outbound_item_id,item_id) REFERENCES outbound_items(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,asset_card_id,item_id) REFERENCES asset_cards(tenant_id,id,item_id),
 UNIQUE(tenant_id,id,item_id),
 UNIQUE(tenant_id,id,asset_card_id),
 -- N5：结案原因 loss 必须指向 disposal_type='loss' 的报损明细。
 -- 用常量生成列参与四列外键，使"同租户 + 同卡 + 类型=loss"由数据库强制；
 -- loss_scrap_item_id 为空时外键不检查（MATCH SIMPLE 语义）。
 loss_type TEXT GENERATED ALWAYS AS ('loss'::text) STORED
);

CREATE TABLE inventory (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 item_id BIGINT NOT NULL, item_type TEXT NOT NULL DEFAULT 'consumable' CHECK(item_type='consumable'),
 location_id BIGINT NOT NULL, quality TEXT NOT NULL CHECK(quality IN ('available','damaged')),
 quantity NUMERIC(14,3) NOT NULL DEFAULT 0 CHECK(quantity>=0), version BIGINT NOT NULL DEFAULT 0 CHECK(version>=0), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,item_id,location_id,quality),
 FOREIGN KEY(tenant_id,item_id,item_type) REFERENCES items(tenant_id,id,type),
 FOREIGN KEY(tenant_id,location_id) REFERENCES locations(tenant_id,id)
);

CREATE TABLE scrap_orders (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 order_no TEXT NOT NULL, disposal_type TEXT NOT NULL CHECK(disposal_type IN ('scrap','loss','recovery','stock_damage')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
 applicant_id BIGINT NOT NULL, approver_id BIGINT, approved_at TIMESTAMPTZ,
 self_approved BOOLEAN NOT NULL DEFAULT false, allow_self_approval_snapshot BOOLEAN NOT NULL DEFAULT false,
  responsibility_opinion TEXT, operation_id BIGINT, reason TEXT NOT NULL CHECK(length(trim(reason))>0),
 CHECK(status<>'approved' OR (approver_id IS NOT NULL AND approved_at IS NOT NULL AND operation_id IS NOT NULL)),
 CHECK(status<>'approved' OR self_approved=(applicant_id=approver_id)),
 CHECK(NOT self_approved OR (status='approved' AND allow_self_approval_snapshot)),
 UNIQUE(tenant_id,order_no),
 FOREIGN KEY(tenant_id,applicant_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,approver_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES operation_requests(tenant_id,id)
);

CREATE TABLE scrap_items (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 scrap_id BIGINT NOT NULL, item_id BIGINT NOT NULL,
 item_type TEXT NOT NULL CHECK(item_type IN ('consumable','fixed')),
 quantity NUMERIC(14,3), asset_card_id BIGINT, location_id BIGINT, quality TEXT,
 source_loss_item_id BIGINT, recovery_location_id BIGINT, recovery_quality TEXT CHECK(recovery_quality IN ('available','damaged')),
 source_outbound_asset_id BIGINT, residual_value NUMERIC(14,2) CHECK(residual_value>=0),
 CHECK((item_type='consumable' AND quantity IS NOT NULL AND quantity>0 AND asset_card_id IS NULL
        AND location_id IS NOT NULL AND quality IS NOT NULL AND quality IN ('available','damaged') AND source_outbound_asset_id IS NULL)
    OR (item_type='fixed' AND quantity IS NULL AND asset_card_id IS NOT NULL AND location_id IS NULL AND quality IS NULL)),
 UNIQUE(tenant_id,id,asset_card_id),
 -- N9：供 stock_ledger 的三列外键引用，保证流水的物资与报损明细的物资一致。
 UNIQUE(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,source_loss_item_id,asset_card_id) REFERENCES scrap_items(tenant_id,id,asset_card_id),
 FOREIGN KEY(tenant_id,recovery_location_id) REFERENCES locations(tenant_id,id),
 FOREIGN KEY(tenant_id,scrap_id) REFERENCES scrap_orders(tenant_id,id),
 FOREIGN KEY(tenant_id,item_id,item_type) REFERENCES items(tenant_id,id,type),
 FOREIGN KEY(tenant_id,asset_card_id,item_id) REFERENCES asset_cards(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,location_id) REFERENCES locations(tenant_id,id),
 FOREIGN KEY(tenant_id,source_outbound_asset_id,item_id) REFERENCES outbound_assets(tenant_id,id,item_id)
);


CREATE TABLE adjustment_orders (
 id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 order_no TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('count','quality_transfer')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
 applicant_id BIGINT NOT NULL, approver_id BIGINT, approved_at TIMESTAMPTZ, operation_id BIGINT,
 self_approved BOOLEAN NOT NULL DEFAULT false, allow_self_approval_snapshot BOOLEAN NOT NULL DEFAULT false,
 reason_category TEXT NOT NULL CHECK(reason_category IN ('gain','loss','entry_error','quality_change','other')),
 reason TEXT NOT NULL CHECK(length(trim(reason))>0),
 CHECK(status<>'approved' OR (approver_id IS NOT NULL AND approved_at IS NOT NULL AND operation_id IS NOT NULL)),
 CHECK(status<>'approved' OR self_approved=(applicant_id=approver_id)),
 CHECK(NOT self_approved OR (status='approved' AND allow_self_approval_snapshot)),
 UNIQUE(tenant_id,order_no),
 FOREIGN KEY(tenant_id,applicant_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,approver_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES operation_requests(tenant_id,id)
);
CREATE TABLE adjustment_items (
 id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id), created_at TIMESTAMPTZ NOT NULL DEFAULT now(), UNIQUE(tenant_id,id),
 order_id BIGINT NOT NULL, item_id BIGINT NOT NULL,
 item_type TEXT NOT NULL CHECK(item_type IN ('consumable','fixed')),
 location_id BIGINT NOT NULL, direction TEXT NOT NULL CHECK(direction IN ('in','out')),
 quality TEXT NOT NULL CHECK(quality IN ('available','damaged')),
 to_quality TEXT CHECK(to_quality IN ('available','damaged')), quantity NUMERIC(14,3) NOT NULL CHECK(quantity>0),
 observed_at TIMESTAMPTZ NOT NULL, book_qty NUMERIC(14,3), counted_qty NUMERIC(14,3),
 inventory_version BIGINT, target_inventory_version BIGINT,
 original_value NUMERIC(14,2) CHECK(original_value>=0),
 CHECK(book_qty IS NULL OR book_qty>=0), CHECK(counted_qty IS NULL OR counted_qty>=0),
 CHECK(inventory_version IS NULL OR inventory_version>=0),
 CHECK(target_inventory_version IS NULL OR target_inventory_version>=0),
 CHECK(
 (item_type='fixed' AND direction='in' AND quantity=1 AND to_quality IS NULL)
 OR (item_type='consumable' AND inventory_version IS NOT NULL AND book_qty IS NOT NULL AND
   ((to_quality IS NULL AND counted_qty IS NOT NULL AND
     ((direction='in' AND counted_qty-book_qty=quantity) OR (direction='out' AND book_qty-counted_qty=quantity)))
    OR (to_quality IS NOT NULL AND to_quality<>quality AND direction='out' AND target_inventory_version IS NOT NULL)))
 ),
 UNIQUE(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,order_id) REFERENCES adjustment_orders(tenant_id,id),
 FOREIGN KEY(tenant_id,item_id,item_type) REFERENCES items(tenant_id,id,type),
 FOREIGN KEY(tenant_id,location_id) REFERENCES locations(tenant_id,id)
);

CREATE TABLE stock_ledger (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 item_id BIGINT NOT NULL, item_type TEXT NOT NULL DEFAULT 'consumable' CHECK(item_type='consumable'),
 location_id BIGINT NOT NULL, quality TEXT NOT NULL CHECK(quality IN ('available','damaged')),
 direction TEXT NOT NULL CHECK(direction IN ('in','out')), quantity NUMERIC(14,3) NOT NULL CHECK(quantity>0),
 balance_after NUMERIC(14,3) NOT NULL CHECK(balance_after>=0),
 inbound_item_id BIGINT, outbound_item_id BIGINT, scrap_item_id BIGINT, adjustment_item_id BIGINT,
 operator_id BIGINT NOT NULL, operation_id BIGINT NOT NULL,
 CHECK(num_nonnulls(inbound_item_id,outbound_item_id,scrap_item_id,adjustment_item_id)=1),
 CHECK((inbound_item_id IS NOT NULL AND direction='in') OR (num_nonnulls(outbound_item_id,scrap_item_id)=1 AND direction='out') OR adjustment_item_id IS NOT NULL),
 FOREIGN KEY(tenant_id,item_id,item_type) REFERENCES items(tenant_id,id,type),
 FOREIGN KEY(tenant_id,location_id) REFERENCES locations(tenant_id,id),
 FOREIGN KEY(tenant_id,inbound_item_id,item_id) REFERENCES inbound_items(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,outbound_item_id,item_id) REFERENCES outbound_items(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,scrap_item_id,item_id) REFERENCES scrap_items(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,operator_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES operation_requests(tenant_id,id)
);

CREATE TABLE asset_events (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 asset_card_id BIGINT NOT NULL, action TEXT NOT NULL CHECK(action IN ('opening','purchase','issue','return','repair_start','repair_finish','scrap','purchase_return','loss','found','found_gain')),
 from_status TEXT CHECK(from_status IN ('in_stock','in_use','repair','scrapped','returned_to_supplier','lost')), to_status TEXT NOT NULL CHECK(to_status IN ('in_stock','in_use','repair','scrapped','returned_to_supplier','lost')),
 inbound_item_id BIGINT, outbound_asset_id BIGINT, scrap_item_id BIGINT, adjustment_item_id BIGINT, transfer_id UUID,
 source_loss_item_id BIGINT, location_id BIGINT NOT NULL, responsible_user_id BIGINT,
 operator_id BIGINT NOT NULL, operation_id BIGINT NOT NULL, reason TEXT,
 FOREIGN KEY(tenant_id,asset_card_id) REFERENCES asset_cards(tenant_id,id),
 FOREIGN KEY(tenant_id,inbound_item_id) REFERENCES inbound_items(tenant_id,id),
 FOREIGN KEY(tenant_id,outbound_asset_id,asset_card_id) REFERENCES outbound_assets(tenant_id,id,asset_card_id),
 FOREIGN KEY(tenant_id,scrap_item_id,asset_card_id) REFERENCES scrap_items(tenant_id,id,asset_card_id),
 -- N8：按 action 派生处置类型，用于四列外键强制"事件类型 ↔ 明细处置类型"匹配（外键见后段）。
 -- 生成表达式只能引用本行基列且必须不可变，CASE 满足；非 scrap/loss/found 动作此处为 NULL，外键不检查。
 scrap_event_type TEXT GENERATED ALWAYS AS (CASE action WHEN 'scrap' THEN 'scrap' WHEN 'loss' THEN 'loss' WHEN 'found' THEN 'recovery' END) STORED,
 FOREIGN KEY(tenant_id,operator_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES operation_requests(tenant_id,id)
);

CREATE TABLE audit_logs (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 user_id BIGINT NOT NULL, action TEXT NOT NULL, object_type TEXT NOT NULL, object_id BIGINT,
 changes JSONB NOT NULL, reason TEXT,
 FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id)
);


ALTER TABLE inbound_items ADD CONSTRAINT fk_adjustment_gain FOREIGN KEY(tenant_id,adjustment_item_id,item_id) REFERENCES adjustment_items(tenant_id,id,item_id);
CREATE UNIQUE INDEX uq_adjustment_gain ON inbound_items(tenant_id,adjustment_item_id) WHERE adjustment_item_id IS NOT NULL;
-- N5：fk_loss_closure 已移至 scrap_items 的 disposal_type 与四列唯一约束建立之后，并改为四列外键（见文件尾部 N5 段落）。
ALTER TABLE stock_ledger ADD CONSTRAINT fk_ledger_adjustment FOREIGN KEY(tenant_id,adjustment_item_id,item_id) REFERENCES adjustment_items(tenant_id,id,item_id);
ALTER TABLE asset_events ADD CONSTRAINT fk_event_adjustment FOREIGN KEY(tenant_id,adjustment_item_id) REFERENCES adjustment_items(tenant_id,id);
CREATE UNIQUE INDEX uq_adjustment_ledger_direction ON stock_ledger(tenant_id,adjustment_item_id,direction) WHERE adjustment_item_id IS NOT NULL;
-- 找回重复审批由锁定原loss明细和资产卡保证；pending允许多条，成功只能一次。
-- 解决归还与发放表相互引用的建表顺序。
ALTER TABLE inbound_items ADD CONSTRAINT fk_return_item
 FOREIGN KEY(tenant_id,source_outbound_item_id,item_id) REFERENCES outbound_items(tenant_id,id,item_id);
ALTER TABLE inbound_items ADD CONSTRAINT fk_return_asset
 FOREIGN KEY(tenant_id,source_outbound_asset_id,item_id) REFERENCES outbound_assets(tenant_id,id,item_id);
CREATE UNIQUE INDEX uq_asset_return_once ON inbound_items(tenant_id,source_outbound_asset_id)
 WHERE source_outbound_asset_id IS NOT NULL;
CREATE UNIQUE INDEX uq_asset_outstanding ON outbound_assets(tenant_id,asset_card_id) WHERE closed_at IS NULL;
CREATE UNIQUE INDEX uq_ledger_inbound ON stock_ledger(tenant_id,inbound_item_id) WHERE inbound_item_id IS NOT NULL;
CREATE UNIQUE INDEX uq_ledger_outbound ON stock_ledger(tenant_id,outbound_item_id) WHERE outbound_item_id IS NOT NULL;
CREATE UNIQUE INDEX uq_ledger_scrap ON stock_ledger(tenant_id,scrap_item_id) WHERE scrap_item_id IS NOT NULL;
CREATE INDEX idx_auth_family ON auth_sessions(tenant_id,family_id);
CREATE INDEX idx_outbound_status ON outbound_orders(tenant_id,status,created_at);
CREATE INDEX idx_scrap_status ON scrap_orders(tenant_id,status,created_at);
CREATE INDEX idx_ledger_balance ON stock_ledger(tenant_id,item_id,location_id,quality,id);
CREATE INDEX idx_asset_status ON asset_cards(tenant_id,status,item_id);
CREATE INDEX idx_inbound_return_source ON inbound_items(tenant_id,source_outbound_item_id);
CREATE INDEX idx_purchase_return_source ON outbound_items(tenant_id,source_inbound_item_id);
CREATE INDEX idx_asset_history ON asset_events(tenant_id,asset_card_id,id);
-- 生产运行账号应使用独立非owner角色；只给审计/流水/资产事件 SELECT、INSERT。
-- 以下触发器防止普通修改，超级用户/owner仍可更改schema，不等于防篡改认证。
CREATE FUNCTION reject_history_mutation() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
 RAISE EXCEPTION 'append-only history: %', TG_TABLE_NAME;
END;
$$;
CREATE TRIGGER ledger_append_only BEFORE UPDATE OR DELETE ON stock_ledger
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER asset_events_append_only BEFORE UPDATE OR DELETE ON asset_events
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER audit_append_only BEFORE UPDATE OR DELETE ON audit_logs
 FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
-- 已执行/终结单头不可改；pending仅允许状态转换及执行元数据，不允许改申请内容。
CREATE FUNCTION protect_order_header() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
 IF TG_OP='DELETE' OR OLD.status IN ('completed','approved','rejected','cancelled') THEN
   RAISE EXCEPTION 'immutable order: %', TG_TABLE_NAME;
 END IF;
 IF (to_jsonb(NEW)-ARRAY['status','operator_id','operation_id','executed_at','self_issued','approver_id','approved_at','self_approved','allow_self_approval_snapshot','decided_by','decided_at','decision_reason'])
 IS DISTINCT FROM
 (to_jsonb(OLD)-ARRAY['status','operator_id','operation_id','executed_at','self_issued','approver_id','approved_at','self_approved','allow_self_approval_snapshot','decided_by','decided_at','decision_reason']) THEN
   RAISE EXCEPTION 'submitted order business fields are frozen';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER inbound_header_frozen BEFORE UPDATE OR DELETE ON inbound_orders FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER outbound_header_frozen BEFORE UPDATE OR DELETE ON outbound_orders FOR EACH ROW EXECUTE FUNCTION protect_order_header();
CREATE TRIGGER scrap_header_frozen BEFORE UPDATE OR DELETE ON scrap_orders FOR EACH ROW EXECUTE FUNCTION protect_order_header();
CREATE TRIGGER adjustment_header_frozen BEFORE UPDATE OR DELETE ON adjustment_orders FOR EACH ROW EXECUTE FUNCTION protect_order_header();

-- 修改明细时锁父单。执行服务也必须先锁父单，先写实发数据，再终结父单。
CREATE FUNCTION protect_order_line() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE parent_state TEXT; parent_id BIGINT; row_tenant BIGINT;
BEGIN
 IF TG_OP='INSERT' THEN
   parent_id=(to_jsonb(NEW)->>TG_ARGV[1])::BIGINT; row_tenant=NEW.tenant_id;
 ELSE
   parent_id=(to_jsonb(OLD)->>TG_ARGV[1])::BIGINT; row_tenant=OLD.tenant_id;
 END IF;
 EXECUTE format('SELECT status FROM %I WHERE tenant_id=$1 AND id=$2 FOR UPDATE',TG_ARGV[0])
 INTO parent_state USING row_tenant,parent_id;
 IF parent_state IS DISTINCT FROM 'pending' THEN RAISE EXCEPTION 'order not pending'; END IF;
 IF TG_OP='UPDATE' THEN
   IF TG_TABLE_NAME<>'outbound_items' OR
     (to_jsonb(NEW)-ARRAY['actual_qty','location_id','recipient_id','shortfall_reason','source_loss_type']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['actual_qty','location_id','recipient_id','shortfall_reason','source_loss_type']) THEN
     RAISE EXCEPTION 'submitted line is frozen';
   END IF;
 END IF;
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'cancel the order instead of deleting lines'; END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER inbound_line_frozen BEFORE UPDATE OR DELETE ON inbound_items FOR EACH ROW EXECUTE FUNCTION reject_history_mutation();
CREATE TRIGGER outbound_line_frozen BEFORE INSERT OR UPDATE OR DELETE ON outbound_items FOR EACH ROW EXECUTE FUNCTION protect_order_line('outbound_orders','order_id');
CREATE TRIGGER scrap_line_frozen BEFORE INSERT OR UPDATE OR DELETE ON scrap_items FOR EACH ROW EXECUTE FUNCTION protect_order_line('scrap_orders','scrap_id');
CREATE TRIGGER adjustment_line_frozen BEFORE INSERT OR UPDATE OR DELETE ON adjustment_items FOR EACH ROW EXECUTE FUNCTION protect_order_line('adjustment_orders','order_id');

CREATE FUNCTION close_asset_issue_once() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'asset issue cannot be deleted'; END IF;
 IF OLD.closed_at IS NOT NULL OR NEW.closed_at IS NULL OR
   -- N5 引入生成列 loss_type 后必须一并排除：BEFORE 触发器中 NEW 的生成列尚未计算（为 NULL），
   -- 若参与整行比较会与 OLD 的 'loss' 不等，导致结案永远被拒。
   (to_jsonb(NEW)-ARRAY['closed_at','close_reason','loss_scrap_item_id','loss_type']) IS DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['closed_at','close_reason','loss_scrap_item_id','loss_type']) THEN
   RAISE EXCEPTION 'only one closure append is permitted';
 END IF;
 RETURN NEW;
END;
$$;
CREATE TRIGGER close_issue_once BEFORE UPDATE OR DELETE ON outbound_assets FOR EACH ROW EXECUTE FUNCTION close_asset_issue_once();

-- 部署时由DBA以实际运行角色执行：
-- REVOKE UPDATE, DELETE, TRUNCATE ON audit_logs, stock_ledger, asset_events FROM inventory_runtime;
-- GRANT SELECT, INSERT ON audit_logs, stock_ledger, asset_events TO inventory_runtime;
-- runtime不得为表owner/超级用户，不授予schema CREATE或禁用触发器能力。
-- 此基线不创建全局角色，避免影响已有集群；生产部署验收必须验证实际授权。

-- v6补强。以下与上面的CREATE共同构成一个新库事务，并非生产升级脚本。
CREATE TABLE attachments (
 id BIGSERIAL PRIMARY KEY, tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 object_key TEXT NOT NULL, sha256 TEXT NOT NULL CHECK(sha256 ~ '^[0-9a-f]{64}$'),
 size_bytes BIGINT NOT NULL CHECK(size_bytes>0), content_type TEXT NOT NULL,
 retention_until TIMESTAMPTZ NOT NULL, legal_hold BOOLEAN NOT NULL DEFAULT false,
 uploaded_by BIGINT NOT NULL, created_at TIMESTAMPTZ NOT NULL DEFAULT now(), deleted_at TIMESTAMPTZ,
 UNIQUE(tenant_id,id),
 -- N10：object_key 改为租户内唯一。全局唯一会让一个租户通过"重复键"错误探测其他租户的键是否存在。
 -- 服务必须强制 object_key 以租户前缀开头，避免跨租户键碰撞与推断。
 UNIQUE(tenant_id,object_key),
 FOREIGN KEY(tenant_id,uploaded_by) REFERENCES users(tenant_id,id)
);
CREATE TABLE scrap_attachments (
 tenant_id BIGINT NOT NULL, scrap_id BIGINT NOT NULL, attachment_id BIGINT NOT NULL,
 PRIMARY KEY(tenant_id,scrap_id,attachment_id),
 FOREIGN KEY(tenant_id,scrap_id) REFERENCES scrap_orders(tenant_id,id),
 FOREIGN KEY(tenant_id,attachment_id) REFERENCES attachments(tenant_id,id)
);
-- 证据关联：父单 pending 且未决策时可撤回（INSERT/UPDATE/DELETE）；决策后冻结为 append-only。
-- 注：DELETE 分支必须 RETURN OLD（NEW 在 DELETE 上下文中为 NULL，返回 NEW 会静默取消删除）。
CREATE FUNCTION evidence_line_guard() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE parent_state TEXT; parent_id BIGINT; decided BOOLEAN; row_tenant BIGINT;
BEGIN
 IF TG_OP='INSERT' THEN
   parent_id=(to_jsonb(NEW)->>TG_ARGV[1])::BIGINT; row_tenant=NEW.tenant_id;
 ELSE
   parent_id=(to_jsonb(OLD)->>TG_ARGV[1])::BIGINT; row_tenant=OLD.tenant_id;
 END IF;
 -- N6：已标记撤回（误传已删）的附件不得再被关联，避免"对象已删但单据仍引用"。
 IF TG_OP='INSERT' AND EXISTS(SELECT 1 FROM attachments
    WHERE tenant_id=row_tenant AND id=NEW.attachment_id AND deleted_at IS NOT NULL) THEN
   RAISE EXCEPTION 'attachment already withdrawn: %',NEW.attachment_id; END IF;
 SELECT status,(decided_by IS NOT NULL) INTO parent_state,decided
 FROM scrap_orders WHERE tenant_id=row_tenant AND id=parent_id FOR UPDATE;
 IF parent_state IS DISTINCT FROM 'pending' OR decided THEN
   RAISE EXCEPTION 'evidence frozen once order decided: %',TG_TABLE_NAME; END IF;
 IF TG_OP='UPDATE' THEN
   RAISE EXCEPTION 'evidence association is immutable, delete and re-add: %',TG_TABLE_NAME; END IF;
 IF TG_OP='DELETE' THEN RETURN OLD; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER evidence_frozen BEFORE INSERT OR UPDATE OR DELETE ON scrap_attachments
 FOR EACH ROW EXECUTE FUNCTION evidence_line_guard('scrap_orders','scrap_id');

ALTER TABLE departments ADD UNIQUE(tenant_id,name);
ALTER TABLE suppliers ADD UNIQUE(tenant_id,name);
ALTER TABLE outbound_orders ADD COLUMN dept_id BIGINT,
 ADD FOREIGN KEY(tenant_id,dept_id) REFERENCES departments(tenant_id,id),
 ADD CHECK(status<>'completed' OR self_issued=(applicant_id=operator_id));
ALTER TABLE scrap_orders ADD COLUMN decided_by BIGINT, ADD COLUMN decided_at TIMESTAMPTZ,
 ADD COLUMN decision_reason TEXT, ADD FOREIGN KEY(tenant_id,decided_by) REFERENCES users(tenant_id,id),
 ADD CHECK(status NOT IN ('rejected','cancelled') OR
 (decided_by IS NOT NULL AND decided_at IS NOT NULL AND decision_reason IS NOT NULL AND length(trim(decision_reason))>0)),
 ADD UNIQUE(tenant_id,id,disposal_type);
ALTER TABLE adjustment_orders ADD COLUMN decided_by BIGINT, ADD COLUMN decided_at TIMESTAMPTZ,
 ADD COLUMN decision_reason TEXT, ADD FOREIGN KEY(tenant_id,decided_by) REFERENCES users(tenant_id,id),
 ADD CHECK(status NOT IN ('rejected','cancelled') OR
 (decided_by IS NOT NULL AND decided_at IS NOT NULL AND decision_reason IS NOT NULL AND length(trim(decision_reason))>0)),
 ADD UNIQUE(tenant_id,id,type),
 ADD CHECK((type='quality_transfer')=(reason_category='quality_change'));

ALTER TABLE scrap_items ADD COLUMN disposal_type TEXT NOT NULL,
 ADD COLUMN source_loss_type TEXT GENERATED ALWAYS AS ('loss'::text) STORED,
 ADD UNIQUE(tenant_id,id,asset_card_id,disposal_type),
 ADD FOREIGN KEY(tenant_id,scrap_id,disposal_type) REFERENCES scrap_orders(tenant_id,id,disposal_type),
 ADD FOREIGN KEY(tenant_id,source_loss_item_id,asset_card_id,source_loss_type)
 REFERENCES scrap_items(tenant_id,id,asset_card_id,disposal_type),
 ADD CHECK((disposal_type='stock_damage' AND item_type='consumable') OR
 (disposal_type IN ('scrap','loss','recovery') AND item_type='fixed')),
 ADD CHECK((disposal_type='recovery' AND source_loss_item_id IS NOT NULL AND recovery_location_id IS NOT NULL AND recovery_quality IS NOT NULL)
 OR (disposal_type<>'recovery' AND source_loss_item_id IS NULL AND recovery_location_id IS NULL AND recovery_quality IS NULL));
-- N5：结案外键必须在此处建立（依赖上面刚建好的 scrap_items 四列唯一约束）。
-- 仅 loss 结案才检查：loss_scrap_item_id IS NULL 时外键不生效。
ALTER TABLE outbound_assets ADD CONSTRAINT fk_loss_closure
 FOREIGN KEY(tenant_id,loss_scrap_item_id,asset_card_id,loss_type)
 REFERENCES scrap_items(tenant_id,id,asset_card_id,disposal_type);
ALTER TABLE adjustment_items ADD COLUMN adjustment_type TEXT NOT NULL,
 ADD FOREIGN KEY(tenant_id,order_id,adjustment_type) REFERENCES adjustment_orders(tenant_id,id,type),
 ADD CHECK((adjustment_type='quality_transfer' AND item_type='consumable' AND to_quality IS NOT NULL)
 OR (adjustment_type='count' AND to_quality IS NULL));
COMMENT ON COLUMN adjustment_items.direction IS '质量转换一行记源out；审批必须生成同明细in/out两条等量流水。固定资产quality映射available=in_stock、damaged=repair。';

ALTER TABLE asset_events ADD COLUMN source_loss_type TEXT GENERATED ALWAYS AS ('loss'::text) STORED,
 ADD FOREIGN KEY(tenant_id,source_loss_item_id,asset_card_id,source_loss_type)
 REFERENCES scrap_items(tenant_id,id,asset_card_id,disposal_type),
 ADD FOREIGN KEY(tenant_id,scrap_item_id,asset_card_id,scrap_event_type)
 REFERENCES scrap_items(tenant_id,id,asset_card_id,disposal_type),
 ADD FOREIGN KEY(tenant_id,location_id) REFERENCES locations(tenant_id,id),
 ADD FOREIGN KEY(tenant_id,responsible_user_id) REFERENCES users(tenant_id,id),
 ADD CHECK((action='found')=(source_loss_item_id IS NOT NULL)),
 ADD CHECK((to_status='in_use')=(responsible_user_id IS NOT NULL)),
 ADD CHECK((
 (action IN ('opening','purchase','found_gain') AND from_status IS NULL AND to_status IN ('in_stock','repair') AND inbound_item_id IS NOT NULL)
 OR (action='issue' AND from_status='in_stock' AND to_status='in_use' AND outbound_asset_id IS NOT NULL)
 OR (action='return' AND from_status='in_use' AND to_status IN ('in_stock','repair') AND inbound_item_id IS NOT NULL AND outbound_asset_id IS NOT NULL)
 OR (action='repair_start' AND from_status='in_stock' AND to_status='repair')
 OR (action='repair_finish' AND from_status='repair' AND to_status='in_stock')
 OR (action='scrap' AND from_status IN ('in_stock','repair') AND to_status='scrapped' AND scrap_item_id IS NOT NULL)
 OR (action='loss' AND from_status IN ('in_stock','repair','in_use') AND to_status='lost' AND scrap_item_id IS NOT NULL)
 OR (action='found' AND from_status='lost' AND to_status IN ('in_stock','repair') AND scrap_item_id IS NOT NULL)
 OR (action='purchase_return' AND from_status='in_stock' AND to_status='returned_to_supplier' AND outbound_asset_id IS NOT NULL)
 ) IS TRUE),
 ADD CHECK(action<>'found_gain' OR adjustment_item_id IS NOT NULL);
CREATE UNIQUE INDEX uq_found_loss ON asset_events(tenant_id,source_loss_item_id) WHERE action='found';

CREATE FUNCTION inventory_version_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='INSERT' THEN
   IF NEW.version<>0 THEN RAISE EXCEPTION 'initial version must be zero'; END IF;
 ELSE
   IF NEW.version<>OLD.version THEN RAISE EXCEPTION 'version is database managed'; END IF;
   IF ROW(NEW.tenant_id,NEW.item_id,NEW.location_id,NEW.quality) IS DISTINCT FROM ROW(OLD.tenant_id,OLD.item_id,OLD.location_id,OLD.quality) THEN
     RAISE EXCEPTION 'inventory key immutable'; END IF;
   IF NEW.quantity IS DISTINCT FROM OLD.quantity THEN NEW.version=OLD.version+1; END IF;
 END IF;
 NEW.updated_at=clock_timestamp(); RETURN NEW;
END; $$;
CREATE TRIGGER inventory_version BEFORE INSERT OR UPDATE ON inventory FOR EACH ROW EXECUTE FUNCTION inventory_version_guard();

CREATE FUNCTION inbound_insert_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE parent_xid xid8; closed TIMESTAMPTZ;
BEGIN
 SELECT created_xid INTO parent_xid FROM inbound_orders WHERE tenant_id=NEW.tenant_id AND id=NEW.order_id FOR UPDATE;
 IF parent_xid IS DISTINCT FROM pg_current_xact_id() THEN RAISE EXCEPTION 'inbound lines only in creation transaction'; END IF;
 IF NEW.source_outbound_asset_id IS NOT NULL THEN
   SELECT closed_at INTO closed FROM outbound_assets WHERE tenant_id=NEW.tenant_id AND id=NEW.source_outbound_asset_id FOR UPDATE;
   IF NOT FOUND OR closed IS NOT NULL THEN RAISE EXCEPTION 'issue missing or already closed'; END IF;
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER inbound_insert BEFORE INSERT ON inbound_items FOR EACH ROW EXECUTE FUNCTION inbound_insert_guard();
CREATE FUNCTION asset_issue_insert_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
DECLARE state TEXT;
BEGIN
 SELECT o.status INTO state FROM outbound_orders o JOIN outbound_items i ON (i.tenant_id=o.tenant_id AND i.order_id=o.id)
 WHERE i.tenant_id=NEW.tenant_id AND i.id=NEW.outbound_item_id FOR UPDATE OF o;
 IF state IS DISTINCT FROM 'pending' THEN RAISE EXCEPTION 'asset issue parent must be pending'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER asset_issue_insert BEFORE INSERT ON outbound_assets FOR EACH ROW EXECUTE FUNCTION asset_issue_insert_guard();

CREATE FUNCTION xid_stamp() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN NEW.created_xid=pg_current_xact_id(); RETURN NEW; END; $$;
CREATE TRIGGER inbound_xid BEFORE INSERT ON inbound_orders FOR EACH ROW EXECUTE FUNCTION xid_stamp();
CREATE TRIGGER operation_xid BEFORE INSERT ON operation_requests FOR EACH ROW EXECUTE FUNCTION xid_stamp();
CREATE FUNCTION operation_guard() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'operation identity retained'; END IF;
 IF (to_jsonb(NEW)-ARRAY['response','business_refs','completed_at']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['response','business_refs','completed_at']) THEN RAISE EXCEPTION 'operation identity immutable'; END IF;
 IF OLD.created_xid=pg_current_xact_id() THEN RETURN NEW; END IF;
 IF NEW.response IS NOT NULL OR OLD.completed_at>now()-interval '90 days' OR
    NEW.business_refs IS DISTINCT FROM OLD.business_refs OR NEW.completed_at IS DISTINCT FROM OLD.completed_at THEN
   RAISE EXCEPTION 'only expired response clearing permitted'; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER operation_frozen BEFORE UPDATE OR DELETE ON operation_requests FOR EACH ROW EXECUTE FUNCTION operation_guard();

-- 完成时校验，不能在pending行默认actual_qty=0时强制填写差额原因。
CREATE FUNCTION validate_outbound_completion() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN
 IF NEW.status='completed' THEN
   IF NOT EXISTS(SELECT 1 FROM outbound_items WHERE tenant_id=NEW.tenant_id AND order_id=NEW.id AND actual_qty>0) THEN
     RAISE EXCEPTION 'zero issuance must be cancelled'; END IF;
   IF EXISTS(SELECT 1 FROM outbound_items i WHERE i.tenant_id=NEW.tenant_id AND i.order_id=NEW.id AND
     ((i.actual_qty<i.requested_qty AND (i.shortfall_reason IS NULL OR length(trim(i.shortfall_reason))=0)) OR
     (i.item_type='fixed' AND i.actual_qty<>(SELECT count(*) FROM outbound_assets a WHERE a.tenant_id=i.tenant_id AND a.outbound_item_id=i.id)))) THEN
     RAISE EXCEPTION 'shortfall reason or asset count invalid'; END IF;
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER outbound_completion BEFORE INSERT OR UPDATE ON outbound_orders FOR EACH ROW EXECUTE FUNCTION validate_outbound_completion();

CREATE FUNCTION mark_updated() RETURNS trigger LANGUAGE plpgsql SET search_path=pg_catalog,public AS $$
BEGIN NEW.updated_at=clock_timestamp(); RETURN NEW; END; $$;
DO $$ DECLARE t TEXT; BEGIN
 FOREACH t IN ARRAY ARRAY['departments','locations','items','suppliers','categories'] LOOP
 EXECUTE format('ALTER TABLE %I ADD COLUMN updated_at timestamptz NOT NULL DEFAULT now()',t);
 EXECUTE format('CREATE TRIGGER set_updated BEFORE UPDATE ON %I FOR EACH ROW EXECUTE FUNCTION mark_updated()',t);
 END LOOP;
 FOREACH t IN ARRAY ARRAY['audit_logs','stock_ledger','asset_events','operation_requests'] LOOP
 EXECUTE format('CREATE TRIGGER no_truncate BEFORE TRUNCATE ON %I FOR EACH STATEMENT EXECUTE FUNCTION reject_history_mutation()',t);
 END LOOP;
END; $$;
CREATE INDEX idx_inbound_parent ON inbound_items(tenant_id,order_id);
CREATE INDEX idx_outbound_parent ON outbound_items(tenant_id,order_id);
CREATE INDEX idx_scrap_parent ON scrap_items(tenant_id,scrap_id);
CREATE INDEX idx_adjustment_parent ON adjustment_items(tenant_id,order_id);
CREATE INDEX idx_asset_responsible ON asset_cards(tenant_id,responsible_user_id);
CREATE INDEX idx_session_user ON auth_sessions(tenant_id,user_id);
CREATE INDEX idx_session_expiry ON auth_sessions(expires_at);
CREATE INDEX idx_my_outbound ON outbound_orders(tenant_id,applicant_id,created_at);
CREATE INDEX idx_my_scrap ON scrap_orders(tenant_id,applicant_id,created_at);
CREATE INDEX idx_event_issue ON asset_events(tenant_id,outbound_asset_id);
CREATE INDEX idx_audit_time ON audit_logs(tenant_id,created_at);
CREATE INDEX idx_audit_object ON audit_logs(tenant_id,object_type,object_id,created_at);
-- C1：主数据 updated_at 补齐 users（其余主数据在 mark_updated 的 DO 循环中已处理）。
ALTER TABLE users ADD COLUMN updated_at TIMESTAMPTZ NOT NULL DEFAULT now();
CREATE TRIGGER users_set_updated BEFORE UPDATE ON users FOR EACH ROW EXECUTE FUNCTION mark_updated();
-- B4：附件元数据完整性。内容标识/大小/证据策略不可改；deleted_at 仅可单向置位一次且需已过期、无 legal_hold；retention_until 只可延长。
CREATE FUNCTION attachments_metadata_guard() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   RAISE EXCEPTION 'attachment metadata is retained; mark deleted_at instead'; END IF;
 -- 内容标识/大小/类型/上传人/创建时间一律不可改；deleted_at、retention_until、legal_hold 另有单向规则
 IF to_jsonb(NEW)-ARRAY['deleted_at','retention_until','legal_hold'] IS DISTINCT FROM
    to_jsonb(OLD)-ARRAY['deleted_at','retention_until','legal_hold'] THEN
   RAISE EXCEPTION 'attachment metadata immutable'; END IF;
 -- legal_hold 单向：只允许 false -> true（合规保全可随时开启，不得关闭以规避保留）
 IF NEW.legal_hold IS DISTINCT FROM OLD.legal_hold THEN
   IF OLD.legal_hold OR NOT NEW.legal_hold THEN
     RAISE EXCEPTION 'legal_hold can only be turned on'; END IF;
 END IF;
 IF NEW.retention_until IS DISTINCT FROM OLD.retention_until THEN
   IF OLD.retention_until IS NOT NULL AND NEW.retention_until < OLD.retention_until THEN
     RAISE EXCEPTION 'retention_until can only be extended'; END IF;
 END IF;
 IF NEW.deleted_at IS DISTINCT FROM OLD.deleted_at THEN
   IF OLD.deleted_at IS NOT NULL OR NEW.deleted_at IS NULL THEN
     RAISE EXCEPTION 'deleted_at may only be set once'; END IF;
   IF NEW.legal_hold THEN
     RAISE EXCEPTION 'legal_hold blocks deletion'; END IF;
   -- N6：孤儿附件（尚未被任何处置单关联）按"误传撤回"处理，允许在保留期届满前标记删除，
   --     使元数据与"对象已被服务清理"保持一致，避免元数据滞留而对象已删的口径不一致。
   --     已被关联的附件仍受保留期约束；legal_hold 一律阻断。
   IF EXISTS(SELECT 1 FROM scrap_attachments
             WHERE tenant_id=NEW.tenant_id AND attachment_id=NEW.id)
      AND NEW.retention_until > now() THEN
     RAISE EXCEPTION 'retention_until not expired'; END IF;
 END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER attachments_metadata_frozen BEFORE UPDATE OR DELETE ON attachments FOR EACH ROW EXECUTE FUNCTION attachments_metadata_guard();

-- N7：固定资产卡的账实核心字段下沉为数据库守卫。
-- 卡片永不物理删除（报废/遗失走状态）；除状态、位置、责任人外一律不可改。
-- 状态与事件的同事务约束仍未实现（延迟约束），保持"服务 + 对账"边界，见计划 §0.1。
CREATE FUNCTION asset_card_guard() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
BEGIN
 IF TG_OP='DELETE' THEN
   RAISE EXCEPTION 'asset cards are never deleted; use scrapped/lost status'; END IF;
 IF (to_jsonb(NEW)-ARRAY['status','location_id','responsible_user_id']) IS DISTINCT FROM
    (to_jsonb(OLD)-ARRAY['status','location_id','responsible_user_id']) THEN
   RAISE EXCEPTION 'asset card immutable columns changed: %',TG_TABLE_NAME; END IF;
 RETURN NEW;
END; $$;
CREATE TRIGGER asset_card_frozen BEFORE UPDATE OR DELETE ON asset_cards FOR EACH ROW EXECUTE FUNCTION asset_card_guard();

-- N11：N6 的"是否被引用"判定与按卡查发放历史需要索引支撑。
CREATE INDEX idx_scrap_attachment_ref ON scrap_attachments(tenant_id,attachment_id);
CREATE INDEX idx_asset_issue_history ON outbound_assets(tenant_id,asset_card_id);
COMMIT;
