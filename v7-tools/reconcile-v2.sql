-- P5 reconcile v2: read-only diagnostics. Original reconcile.sql remains archived.
-- Fix fixed-asset false positives; verify ledger dimensions, event chains and responsibility.
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;

-- check: inventory_ledger
WITH totals AS (
 SELECT tenant_id,item_id,location_id,quality,sum(CASE direction WHEN 'in' THEN quantity ELSE -quantity END) qty
 FROM stock_ledger GROUP BY tenant_id,item_id,location_id,quality
)
SELECT 'inventory_ledger' AS issue,coalesce(i.tenant_id,t.tenant_id) tenant_id,
 coalesce(i.item_id,t.item_id) item_id,coalesce(i.location_id,t.location_id) location_id,coalesce(i.quality,t.quality) quality,i.quantity,t.qty
FROM inventory i FULL JOIN totals t USING(tenant_id,item_id,location_id,quality)
WHERE coalesce(i.quantity,0)<>coalesce(t.qty,0);

-- check: asset_latest_event
WITH history AS (SELECT e.*,row_number() OVER w AS event_number,lag(to_status) OVER w AS previous_status FROM asset_events e WINDOW w AS (PARTITION BY tenant_id,asset_card_id ORDER BY id)),
latest AS (SELECT DISTINCT ON(tenant_id,asset_card_id) * FROM history ORDER BY tenant_id,asset_card_id,id DESC)
SELECT 'asset_latest_event' AS issue,a.tenant_id,a.id FROM asset_cards a LEFT JOIN latest e ON e.tenant_id=a.tenant_id AND e.asset_card_id=a.id
WHERE e.id IS NULL
   OR a.status IS DISTINCT FROM e.to_status
   OR (a.status IN ('in_stock','repair') AND a.location_id IS DISTINCT FROM e.location_id)
   OR a.responsible_user_id IS DISTINCT FROM e.responsible_user_id
   OR ((a.status='in_use') IS DISTINCT FROM (a.responsible_user_id IS NOT NULL))
   OR EXISTS(SELECT 1 FROM history h WHERE h.tenant_id=a.tenant_id AND h.asset_card_id=a.id AND
       ((h.event_number=1 AND (h.from_status IS NOT NULL OR h.action NOT IN ('opening','purchase','found_gain')))
        OR (h.event_number>1 AND h.from_status IS DISTINCT FROM h.previous_status)));

-- check: asset_open_issues
SELECT 'asset_open_issues' AS issue,a.tenant_id,a.id FROM asset_cards a LEFT JOIN outbound_assets o
 ON o.tenant_id=a.tenant_id AND o.asset_card_id=a.id AND o.closed_at IS NULL
GROUP BY a.id,a.status HAVING count(o.id)<>CASE WHEN a.status='in_use' THEN 1 ELSE 0 END
 OR EXISTS(SELECT 1 FROM outbound_assets oa JOIN outbound_items oi ON oi.tenant_id=oa.tenant_id AND oi.id=oa.outbound_item_id
 JOIN outbound_orders oo ON oo.tenant_id=oi.tenant_id AND oo.id=oi.order_id
 WHERE oa.tenant_id=a.tenant_id AND oa.asset_card_id=a.id AND oa.closed_at IS NULL
 AND (oo.status<>'completed' OR oi.recipient_id IS DISTINCT FROM a.responsible_user_id));

-- check: fixed_issued_count
SELECT 'fixed_issued_count' AS issue,i.tenant_id,i.id FROM outbound_items i JOIN outbound_orders o ON o.tenant_id=i.tenant_id AND o.id=i.order_id
WHERE o.status='completed' AND i.item_type='fixed' AND i.actual_qty<>(SELECT count(*) FROM outbound_assets a WHERE a.tenant_id=i.tenant_id AND a.outbound_item_id=i.id);

-- check: fixed_created_count
SELECT 'fixed_created_count' AS issue,i.tenant_id,i.id FROM inbound_items i JOIN inbound_orders o ON o.tenant_id=i.tenant_id AND o.id=i.order_id
WHERE o.type IN ('purchase','opening','adjustment_gain') AND i.item_type='fixed'
AND (i.quantity<>(SELECT count(*) FROM asset_cards a WHERE a.tenant_id=i.tenant_id AND a.source_inbound_item_id=i.id)
 OR EXISTS(SELECT 1 FROM asset_cards c WHERE c.tenant_id=i.tenant_id AND c.source_inbound_item_id=i.id
 AND (SELECT count(*) FROM asset_events e WHERE e.tenant_id=c.tenant_id AND e.asset_card_id=c.id AND e.inbound_item_id=i.id
 AND e.action=CASE o.type WHEN 'purchase' THEN 'purchase' WHEN 'opening' THEN 'opening' ELSE 'found_gain' END)<>1));

-- check: excess_consumable_return
SELECT 'excess_consumable_return' AS issue,o.tenant_id,o.id FROM outbound_items o
WHERE o.item_type='consumable' AND o.actual_qty<(SELECT coalesce(sum(i.quantity),0) FROM inbound_items i WHERE i.tenant_id=o.tenant_id AND i.source_outbound_item_id=o.id);

-- check: quality_transfer_ledger
SELECT 'quality_transfer_ledger' AS issue,i.tenant_id,i.id FROM adjustment_items i JOIN adjustment_orders a ON a.tenant_id=i.tenant_id AND a.id=i.order_id
LEFT JOIN stock_ledger l ON l.tenant_id=i.tenant_id AND l.adjustment_item_id=i.id
WHERE a.status='approved' AND a.type='quality_transfer'
GROUP BY i.id HAVING count(l.id)<>2 OR
 coalesce(sum(CASE WHEN l.direction='in' THEN l.quantity ELSE -l.quantity END),0)<>0 OR
 count(*) FILTER(WHERE l.direction='out' AND l.quality=i.quality AND l.quantity=i.quantity AND l.location_id=i.location_id)<>1 OR
 count(*) FILTER(WHERE l.direction='in' AND l.quality=i.to_quality AND l.quantity=i.quantity AND l.location_id=i.location_id)<>1;

-- check: inbound_ledger
-- [B6] 单据↔流水：每条已入账耗材明细必须有且仅有一条匹配方向与水量的流水
SELECT 'inbound_ledger' AS issue,i.tenant_id,i.id FROM inbound_items i JOIN inbound_orders o ON o.tenant_id=i.tenant_id AND o.id=i.order_id
LEFT JOIN stock_ledger l ON l.tenant_id=i.tenant_id AND l.inbound_item_id=i.id
WHERE i.item_type='consumable' AND o.type IN ('purchase','return','opening','adjustment_gain')
GROUP BY i.id HAVING count(l.id)<>1
   OR count(*) FILTER(WHERE l.direction='in' AND l.quantity=i.quantity AND l.location_id=i.location_id AND l.quality=i.quality)<>1;

-- check: outbound_ledger
SELECT 'outbound_ledger' AS issue,i.tenant_id,i.id FROM outbound_items i JOIN outbound_orders o ON o.tenant_id=i.tenant_id AND o.id=i.order_id
LEFT JOIN stock_ledger l ON l.tenant_id=i.tenant_id AND l.outbound_item_id=i.id
WHERE i.item_type='consumable' AND o.status='completed' AND i.actual_qty>0
GROUP BY i.id HAVING count(l.id)<>1
   OR count(*) FILTER(WHERE l.direction='out' AND l.quantity=i.actual_qty AND l.location_id=i.location_id AND l.quality='available')<>1;

-- check: scrap_ledger
SELECT 'scrap_ledger' AS issue,s.tenant_id,s.id FROM scrap_items s JOIN scrap_orders o ON o.tenant_id=s.tenant_id AND o.id=s.scrap_id
LEFT JOIN stock_ledger l ON l.tenant_id=s.tenant_id AND l.scrap_item_id=s.id
WHERE s.item_type='consumable' AND o.status='approved' AND o.disposal_type='stock_damage'
GROUP BY s.id HAVING count(l.id)<>1
 OR count(*) FILTER(WHERE l.direction='out' AND l.quantity=s.quantity AND l.location_id=s.location_id AND l.quality=s.quality)<>1;

-- check: adjustment_ledger
SELECT 'adjustment_ledger' AS issue,i.tenant_id,i.id FROM adjustment_items i JOIN adjustment_orders o ON o.tenant_id=i.tenant_id AND o.id=i.order_id
LEFT JOIN stock_ledger l ON l.tenant_id=i.tenant_id AND l.adjustment_item_id=i.id
WHERE i.item_type='consumable' AND o.status='approved' AND o.type='count'
GROUP BY i.id HAVING count(l.id)<>1
   OR count(*) FILTER(WHERE l.direction=i.direction AND l.quantity=i.quantity AND l.location_id=i.location_id AND l.quality=i.quality)<>1;

-- check: ledger_balance_after
-- [B7] 流水滚动结余：balance_after 必须等于按 id 顺序的累计水量
SELECT 'ledger_balance_after' AS issue,x.tenant_id,x.id FROM (
 SELECT l.id,l.tenant_id,l.item_id,l.location_id,l.quality,l.balance_after,
   sum(CASE l.direction WHEN 'in' THEN l.quantity ELSE -l.quantity END) OVER(
     PARTITION BY l.tenant_id,l.item_id,l.location_id,l.quality ORDER BY l.id
     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) running_qty
 FROM stock_ledger l) x
WHERE x.balance_after IS DISTINCT FROM x.running_qty;

-- check: purchase_return_excess
-- [B7] 采购退货累计不得超过对应采购入库明细水量
SELECT 'purchase_return_excess' AS issue,i.tenant_id,i.id FROM inbound_items i
WHERE i.item_type='consumable' AND i.quantity<(
 SELECT coalesce(sum(o.actual_qty),0) FROM outbound_items o JOIN outbound_orders oo
   ON oo.tenant_id=o.tenant_id AND oo.id=o.order_id
 WHERE o.tenant_id=i.tenant_id AND o.source_inbound_item_id=i.id
   AND oo.type='purchase_return' AND oo.status='completed');

COMMIT;
