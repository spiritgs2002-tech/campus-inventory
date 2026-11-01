BEGIN;
-- P8A-ITEM-QR-BEGIN
-- P8a: nullable item capability; no backfill in the schema transaction.
ALTER TABLE items ADD COLUMN qr_token text;
CREATE UNIQUE INDEX uq_items_tenant_qr ON items(tenant_id, qr_token) WHERE qr_token IS NOT NULL;
CREATE INDEX idx_items_qr_lookup ON items(qr_token) WHERE qr_token IS NOT NULL;
-- P8A-ITEM-QR-END
COMMIT;
