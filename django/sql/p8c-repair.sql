BEGIN;
-- P8C-REPAIR-BEGIN
CREATE TABLE repair_repairticket (
 id bigserial PRIMARY KEY,
 tenant_id bigint NOT NULL REFERENCES tenants(id), ticket_no text NOT NULL,
 reporter_user_id bigint NOT NULL, reporter_name text NOT NULL, reporter_no text NOT NULL,
 target_type text NOT NULL CHECK(target_type IN ('asset','item','location')), target_id bigint,
 description text NOT NULL CHECK(length(trim(description)) BETWEEN 1 AND 4000),
 photo_attachment_id bigint,
 priority text NOT NULL DEFAULT 'normal' CHECK(priority IN ('low','normal','high')),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','dispatched','repairing','done','cancelled')),
 assigned_to bigint, reject_reason text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 asset_target_id bigint GENERATED ALWAYS AS (CASE WHEN target_type='asset' THEN target_id END) STORED,
 item_target_id bigint GENERATED ALWAYS AS (CASE WHEN target_type='item' THEN target_id END) STORED,
 location_target_id bigint GENERATED ALWAYS AS (CASE WHEN target_type='location' THEN target_id END) STORED,
 UNIQUE(tenant_id,id),
 FOREIGN KEY(tenant_id,reporter_user_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,assigned_to) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,photo_attachment_id) REFERENCES attachments(tenant_id,id),
 FOREIGN KEY(tenant_id,asset_target_id) REFERENCES asset_cards(tenant_id,id),
 FOREIGN KEY(tenant_id,item_target_id) REFERENCES items(tenant_id,id),
 FOREIGN KEY(tenant_id,location_target_id) REFERENCES locations(tenant_id,id),
 CHECK(target_type='location' OR target_id IS NOT NULL),
 CHECK(status NOT IN ('dispatched','repairing','done') OR assigned_to IS NOT NULL),
 CHECK(reject_reason IS NULL OR length(reject_reason)<=2000)
);
CREATE UNIQUE INDEX uq_repair_tenant_no ON repair_repairticket(tenant_id,ticket_no);
CREATE INDEX ix_repair_tenant_reporter ON repair_repairticket(tenant_id,reporter_user_id);
CREATE INDEX ix_repair_tenant_status ON repair_repairticket(tenant_id,status);
-- P8C-REPAIR-END
GRANT SELECT,INSERT,UPDATE ON repair_repairticket TO inventory_runtime;
GRANT USAGE,SELECT ON SEQUENCE repair_repairticket_id_seq TO inventory_runtime;
COMMIT;
