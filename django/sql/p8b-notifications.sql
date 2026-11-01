BEGIN;
-- P8B-NOTIFICATIONS-BEGIN
CREATE TABLE notifications_notification (
 id bigserial PRIMARY KEY, tenant_id bigint NOT NULL REFERENCES tenants(id),
 recipient_user_id bigint NOT NULL, recipient_name text, type text NOT NULL,
 title text NOT NULL, body text NOT NULL, ref_type text, ref_id bigint,
 is_read boolean NOT NULL DEFAULT false, created_at timestamptz NOT NULL DEFAULT now(),
 FOREIGN KEY(tenant_id,recipient_user_id) REFERENCES users(tenant_id,id),
 CHECK(type IN ('requisition_arrived','requisition_rejected')),
 UNIQUE(tenant_id,recipient_user_id,type,ref_type,ref_id)
);
CREATE INDEX ix_notif_tenant_user ON notifications_notification(tenant_id,recipient_user_id,is_read);
-- P8B-NOTIFICATIONS-END
GRANT SELECT,INSERT,UPDATE ON notifications_notification TO inventory_runtime;
GRANT USAGE,SELECT ON SEQUENCE notifications_notification_id_seq TO inventory_runtime;
COMMIT;
