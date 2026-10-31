-- v5 新库基线；请用独立数据库验证，通过 Alembic 管理正式迁移。
-- 事务层跨行保证详见实施计划；本文件不宣称通过实库验收。
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
 must_change_password BOOLEAN NOT NULL DEFAULT true, failed_login_count INTEGER NOT NULL DEFAULT 0 CHECK(failed_login_count>=0), locked_until TIMESTAMPTZ,
 active BOOLEAN NOT NULL DEFAULT true, auth_version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,username), FOREIGN KEY(tenant_id,dept_id) REFERENCES departments(tenant_id,id)
);

CREATE TABLE auth_sessions (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 user_id BIGINT NOT NULL, refresh_digest TEXT NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL,
 family_id UUID NOT NULL, parent_session_id BIGINT, revoked_at TIMESTAMPTZ, FOREIGN KEY(tenant_id,parent_session_id) REFERENCES auth_sessions(tenant_id,id), FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id)
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
 request_digest TEXT NOT NULL, response JSONB, business_refs JSONB NOT NULL DEFAULT '[]', completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
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
 order_no TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('purchase','return','opening','adjustment_gain')),
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
 purchase_date DATE, useful_life_months INTEGER CHECK(useful_life_months>0), salvage_value NUMERIC(14,2),
 CHECK(salvage_value IS NULL OR (original_value IS NOT NULL AND salvage_value BETWEEN 0 AND original_value)),
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
 UNIQUE(tenant_id,id,item_id)
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
 evidence_file_keys JSONB NOT NULL DEFAULT '[]', responsibility_opinion TEXT, operation_id BIGINT, reason TEXT NOT NULL CHECK(length(trim(reason))>0),
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
 asset_name TEXT, asset_spec TEXT, original_value NUMERIC(14,2) CHECK(original_value>=0),
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
 FOREIGN KEY(tenant_id,scrap_item_id) REFERENCES scrap_items(tenant_id,id),
 FOREIGN KEY(tenant_id,operator_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES operation_requests(tenant_id,id)
);

CREATE TABLE asset_events (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 asset_card_id BIGINT NOT NULL, action TEXT NOT NULL CHECK(action IN ('opening','purchase','issue','return','repair_start','repair_finish','scrap','purchase_return','loss','found','found_gain')),
 from_status TEXT, to_status TEXT NOT NULL CHECK(to_status IN ('in_stock','in_use','repair','scrapped','returned_to_supplier','lost')),
 inbound_item_id BIGINT, outbound_asset_id BIGINT, scrap_item_id BIGINT, adjustment_item_id BIGINT, transfer_id UUID,
 operator_id BIGINT NOT NULL, operation_id BIGINT NOT NULL, reason TEXT,
 FOREIGN KEY(tenant_id,asset_card_id) REFERENCES asset_cards(tenant_id,id),
 FOREIGN KEY(tenant_id,inbound_item_id) REFERENCES inbound_items(tenant_id,id),
 FOREIGN KEY(tenant_id,outbound_asset_id) REFERENCES outbound_assets(tenant_id,id),
 FOREIGN KEY(tenant_id,scrap_item_id) REFERENCES scrap_items(tenant_id,id),
 FOREIGN KEY(tenant_id,operator_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES operation_requests(tenant_id,id)
);

CREATE TABLE audit_logs (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 user_id BIGINT NOT NULL, action TEXT NOT NULL, object_type TEXT NOT NULL, object_id BIGINT,
 changes JSONB NOT NULL, reason TEXT, ip INET,
 FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id)
);


ALTER TABLE inbound_items ADD CONSTRAINT fk_adjustment_gain FOREIGN KEY(tenant_id,adjustment_item_id,item_id) REFERENCES adjustment_items(tenant_id,id,item_id);
CREATE UNIQUE INDEX uq_adjustment_gain ON inbound_items(tenant_id,adjustment_item_id) WHERE adjustment_item_id IS NOT NULL;
ALTER TABLE outbound_assets ADD CONSTRAINT fk_loss_closure FOREIGN KEY(tenant_id,loss_scrap_item_id,asset_card_id) REFERENCES scrap_items(tenant_id,id,asset_card_id);
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
CREATE FUNCTION reject_history_mutation() RETURNS trigger LANGUAGE plpgsql AS $$
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
CREATE FUNCTION protect_order_header() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' OR TG_TABLE_NAME='inbound_orders'
    OR OLD.status IN ('completed','approved','rejected','cancelled') THEN
   RAISE EXCEPTION 'immutable order: %', TG_TABLE_NAME;
 END IF;
 IF (to_jsonb(NEW)-ARRAY['status','operator_id','operation_id','executed_at','self_issued','approver_id','approved_at','self_approved','allow_self_approval_snapshot'])
 IS DISTINCT FROM
 (to_jsonb(OLD)-ARRAY['status','operator_id','operation_id','executed_at','self_issued','approver_id','approved_at','self_approved','allow_self_approval_snapshot']) THEN
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
CREATE FUNCTION protect_order_line() RETURNS trigger LANGUAGE plpgsql AS $$
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
     (to_jsonb(NEW)-ARRAY['actual_qty','location_id','recipient_id','shortfall_reason']) IS DISTINCT FROM
     (to_jsonb(OLD)-ARRAY['actual_qty','location_id','recipient_id','shortfall_reason']) THEN
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

CREATE FUNCTION close_asset_issue_once() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
 IF TG_OP='DELETE' THEN RAISE EXCEPTION 'asset issue cannot be deleted'; END IF;
 IF OLD.closed_at IS NOT NULL OR NEW.closed_at IS NULL OR
   (to_jsonb(NEW)-ARRAY['closed_at','close_reason','loss_scrap_item_id']) IS DISTINCT FROM
   (to_jsonb(OLD)-ARRAY['closed_at','close_reason','loss_scrap_item_id']) THEN
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
COMMIT;
