BEGIN;
-- P8B-PROCUREMENT-BEGIN
CREATE TABLE procurement_requisition (
 id bigserial PRIMARY KEY, tenant_id bigint NOT NULL REFERENCES tenants(id),
 order_no text NOT NULL, applicant_id bigint NOT NULL, applicant_name text NOT NULL,
 applicant_no text NOT NULL, item_id bigint NOT NULL, qty numeric(14,3) NOT NULL CHECK(qty>0),
 reason text NOT NULL CHECK(length(reason) BETWEEN 1 AND 2000),
 status text NOT NULL DEFAULT 'pending' CHECK(status IN ('pending','approved','rejected','purchased')),
 approved_by bigint, inbound_order_id bigint, reject_reason text,
 created_at timestamptz NOT NULL DEFAULT now(), updated_at timestamptz NOT NULL DEFAULT now(),
 UNIQUE(tenant_id,id), UNIQUE(tenant_id,order_no), UNIQUE(tenant_id,inbound_order_id),
 FOREIGN KEY(tenant_id,applicant_id) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,item_id) REFERENCES items(tenant_id,id),
 FOREIGN KEY(tenant_id,approved_by) REFERENCES users(tenant_id,id),
 FOREIGN KEY(tenant_id,inbound_order_id) REFERENCES inbound_orders(tenant_id,id),
 CHECK((status='purchased')=(inbound_order_id IS NOT NULL)),
 CHECK(status NOT IN ('approved','purchased') OR approved_by IS NOT NULL),
 CHECK(status!='rejected' OR length(trim(reject_reason))>0)
);
CREATE INDEX ix_req_tenant_applicant ON procurement_requisition(tenant_id,applicant_id);
CREATE INDEX ix_req_tenant_status ON procurement_requisition(tenant_id,status);
-- P8B-PROCUREMENT-END
GRANT SELECT,INSERT,UPDATE ON procurement_requisition TO inventory_runtime;
GRANT USAGE,SELECT ON SEQUENCE procurement_requisition_id_seq TO inventory_runtime;
COMMIT;
