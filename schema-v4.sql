-- v4 新库基线；请用独立数据库验证，通过 Alembic 管理正式迁移。
-- 事务层跨行保证详见实施计划；本文件不宣称通过实库验收。
BEGIN;

CREATE TABLE tenants (
 id BIGSERIAL PRIMARY KEY, code TEXT NOT NULL UNIQUE, name TEXT NOT NULL,
 status TEXT NOT NULL DEFAULT 'active' CHECK(status IN ('active','disabled')),
 expire_at DATE, allow_self_approval BOOLEAN NOT NULL DEFAULT false, created_at TIMESTAMPTZ NOT NULL DEFAULT now()
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
 phone_enc TEXT, phone_blind TEXT, phone_key_version INTEGER, blind_key_version INTEGER,
 active BOOLEAN NOT NULL DEFAULT true, auth_version INTEGER NOT NULL DEFAULT 1,
 UNIQUE(tenant_id,username), FOREIGN KEY(tenant_id,dept_id) REFERENCES departments(tenant_id,id)
);

CREATE TABLE auth_sessions (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 user_id BIGINT NOT NULL, refresh_digest TEXT NOT NULL UNIQUE, expires_at TIMESTAMPTZ NOT NULL,
 revoked_at TIMESTAMPTZ, FOREIGN KEY(tenant_id,user_id) REFERENCES users(tenant_id,id)
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
 safety_stock NUMERIC(14,3) NOT NULL DEFAULT 0 CHECK(safety_stock>=0), active BOOLEAN NOT NULL DEFAULT true,
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
 request_digest TEXT NOT NULL, response JSONB NOT NULL, completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,actor_id,operation,idempotency_key),
 FOREIGN KEY(tenant_id,actor_id) REFERENCES users(tenant_id,id)
);

CREATE TABLE import_jobs (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 operator_id BIGINT NOT NULL, file_key TEXT NOT NULL, file_digest TEXT NOT NULL,
 template_type TEXT NOT NULL CHECK(template_type IN ('items','assets','inventory')),
 mapping_version TEXT NOT NULL, status TEXT NOT NULL CHECK(status IN ('preview','completed','failed')),
 result JSONB, FOREIGN KEY(tenant_id,operator_id) REFERENCES users(tenant_id,id)
);

CREATE TABLE inbound_orders (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 order_no TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('purchase','return','opening')),
 supplier_id BIGINT, operator_id BIGINT NOT NULL, operation_id BIGINT NOT NULL, import_job_id BIGINT,
 executed_at TIMESTAMPTZ NOT NULL DEFAULT now(), remark TEXT,
 CHECK(type<>'purchase' OR supplier_id IS NOT NULL), UNIQUE(tenant_id,order_no),
 FOREIGN KEY(tenant_id,supplier_id) REFERENCES suppliers(tenant_id,id),
 FOREIGN KEY(tenant_id,operator_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,operation_id) REFERENCES operation_requests(tenant_id,id),
 FOREIGN KEY(tenant_id,import_job_id) REFERENCES import_jobs(tenant_id,id)
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
 source_outbound_item_id BIGINT, source_outbound_asset_id BIGINT,
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
 status TEXT NOT NULL CHECK(status IN ('in_stock','in_use','repair','scrapped','returned_to_supplier')),
 qr_token TEXT NOT NULL UNIQUE, original_value NUMERIC(14,2) NOT NULL CHECK(original_value>=0),
 purchase_date DATE, useful_life_months INTEGER CHECK(useful_life_months>0), salvage_value NUMERIC(14,2),
 CHECK(salvage_value IS NULL OR salvage_value BETWEEN 0 AND original_value),
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
 order_no TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('requisition','purchase_return')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','completed','cancelled')),
 applicant_id BIGINT NOT NULL, operator_id BIGINT, operation_id BIGINT, executed_at TIMESTAMPTZ, remark TEXT,
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
 returned_at TIMESTAMPTZ, UNIQUE(tenant_id,outbound_item_id,asset_card_id),
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
 quantity NUMERIC(14,3) NOT NULL DEFAULT 0 CHECK(quantity>=0), updated_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,item_id,location_id,quality),
 FOREIGN KEY(tenant_id,item_id,item_type) REFERENCES items(tenant_id,id,type),
 FOREIGN KEY(tenant_id,location_id) REFERENCES locations(tenant_id,id)
);

CREATE TABLE scrap_orders (
 id BIGSERIAL PRIMARY KEY,
 tenant_id BIGINT NOT NULL REFERENCES tenants(id),
 created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id),
 order_no TEXT NOT NULL, type TEXT NOT NULL CHECK(type IN ('consumable_loss','fixed_scrap')),
 status TEXT NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','cancelled')),
 applicant_id BIGINT NOT NULL, approver_id BIGINT, approved_at TIMESTAMPTZ,
 self_approved BOOLEAN NOT NULL DEFAULT false, allow_self_approval_snapshot BOOLEAN NOT NULL DEFAULT false,
 operation_id BIGINT, reason TEXT NOT NULL CHECK(length(trim(reason))>0),
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
 source_outbound_asset_id BIGINT, residual_value NUMERIC(14,2) CHECK(residual_value>=0),
 CHECK((item_type='consumable' AND quantity IS NOT NULL AND quantity>0 AND asset_card_id IS NULL
        AND location_id IS NOT NULL AND quality IS NOT NULL AND quality IN ('available','damaged') AND source_outbound_asset_id IS NULL)
    OR (item_type='fixed' AND quantity IS NULL AND asset_card_id IS NOT NULL AND location_id IS NULL AND quality IS NULL)),
 FOREIGN KEY(tenant_id,scrap_id) REFERENCES scrap_orders(tenant_id,id),
 FOREIGN KEY(tenant_id,item_id,item_type) REFERENCES items(tenant_id,id,type),
 FOREIGN KEY(tenant_id,asset_card_id,item_id) REFERENCES asset_cards(tenant_id,id,item_id),
 FOREIGN KEY(tenant_id,location_id) REFERENCES locations(tenant_id,id),
 FOREIGN KEY(tenant_id,source_outbound_asset_id,item_id) REFERENCES outbound_assets(tenant_id,id,item_id)
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
 inbound_item_id BIGINT, outbound_item_id BIGINT, scrap_item_id BIGINT,
 operator_id BIGINT NOT NULL, operation_id BIGINT NOT NULL,
 CHECK(num_nonnulls(inbound_item_id,outbound_item_id,scrap_item_id)=1),
 CHECK((inbound_item_id IS NOT NULL AND direction='in') OR (inbound_item_id IS NULL AND direction='out')),
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
 asset_card_id BIGINT NOT NULL, action TEXT NOT NULL CHECK(action IN ('opening','purchase','issue','return','repair_start','repair_finish','scrap','purchase_return')),
 from_status TEXT, to_status TEXT NOT NULL CHECK(to_status IN ('in_stock','in_use','repair','scrapped','returned_to_supplier')),
 inbound_item_id BIGINT, outbound_asset_id BIGINT, scrap_item_id BIGINT,
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

-- 解决归还与发放表相互引用的建表顺序。
ALTER TABLE inbound_items ADD CONSTRAINT fk_return_item
 FOREIGN KEY(tenant_id,source_outbound_item_id,item_id) REFERENCES outbound_items(tenant_id,id,item_id);
ALTER TABLE inbound_items ADD CONSTRAINT fk_return_asset
 FOREIGN KEY(tenant_id,source_outbound_asset_id,item_id) REFERENCES outbound_assets(tenant_id,id,item_id);
CREATE UNIQUE INDEX uq_asset_return_once ON inbound_items(tenant_id,source_outbound_asset_id)
 WHERE source_outbound_asset_id IS NOT NULL;
CREATE UNIQUE INDEX uq_asset_outstanding ON outbound_assets(tenant_id,asset_card_id) WHERE returned_at IS NULL;
CREATE UNIQUE INDEX uq_ledger_inbound ON stock_ledger(tenant_id,inbound_item_id) WHERE inbound_item_id IS NOT NULL;
CREATE UNIQUE INDEX uq_ledger_outbound ON stock_ledger(tenant_id,outbound_item_id) WHERE outbound_item_id IS NOT NULL;
CREATE UNIQUE INDEX uq_ledger_scrap ON stock_ledger(tenant_id,scrap_item_id) WHERE scrap_item_id IS NOT NULL;
CREATE INDEX idx_users_phone ON users(tenant_id,blind_key_version,phone_blind);
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
COMMIT;
