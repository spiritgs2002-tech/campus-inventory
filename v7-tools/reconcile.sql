-- 一致快照只读检查；每个结果集均应0行。任何非空均需调查，不自动修复。
-- v7 改动：
--   [B6] 新增"单据↔流水"完整性：已执行/已审批单据必须有且仅有一条 water-matched 流水。
--        原 7 项只查"库存 = 流水累计"，若某单据既没写流水又没改库存是查不出来的。
--   [B7] 新增流水滚动结余 balance_after 核对；新增采购退货累计额度核对。
--   [B8] asset_latest_event 明确位置口径：仅 in_stock/repair 才比对 location_id，
--        其余状态的位置属历史快照，不参与比对以避免误报。
BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY;
WITH totals AS (
 SELECT tenant_id,item_id,location_id,quality,sum(CASE direction WHEN 'in' THEN quantity ELSE -quantity END) qty
 FROM stock_ledger GROUP BY tenant_id,item_id,location_id,quality
)
SELECT 'inventory_ledger' AS issue,coalesce(i.tenant_id,t.tenant_id) tenant_id,
 coalesce(i.item_id,t.item_id) item_id,i.quantity,t.qty
FROM inventory i FULL JOIN totals t USING(tenant_id,item_id,location_id,quality)
WHERE coalesce(i.quantity,0)<>coalesce(t.qty,0);

WITH latest AS (SELECT DISTINCT ON(tenant_id,asset_card_id) * FROM asset_events ORDER BY tenant_id,asset_card_id,id DESC)
SELECT 'asset_latest_event' AS issue,a.id FROM asset_cards a LEFT JOIN latest e ON e.tenant_id=a.tenant_id AND e.asset_card_id=a.id
WHERE e.id IS NULL
   OR a.status IS DISTINCT FROM e.to_status
   OR (a.status IN ('in_stock','repair') AND a.location_id IS DISTINCT FROM e.location_id)
   OR ((a.status='in_use') IS DISTINCT FROM (a.responsible_user_id IS NOT NULL));

SELECT 'asset_open_issues' AS issue,a.id FROM asset_cards a LEFT JOIN outbound_assets o
 ON o.tenant_id=a.tenant_id AND o.asset_card_id=a.id AND o.closed_at IS NULL
GROUP BY a.id,a.status HAVING count(o.id)<>CASE WHEN a.status='in_use' THEN 1 ELSE 0 END;

SELECT 'fixed_issued_count' AS issue,i.id FROM outbound_items i JOIN outbound_orders o ON o.tenant_id=i.tenant_id AND o.id=i.order_id
WHERE o.status='completed' AND i.item_type='fixed' AND i.actual_qty<>(SELECT count(*) FROM outbound_assets a WHERE a.tenant_id=i.tenant_id AND a.outbound_item_id=i.id);

SELECT 'fixed_created_count' AS issue,i.id FROM inbound_items i JOIN inbound_orders o ON o.tenant_id=i.tenant_id AND o.id=i.order_id
WHERE o.type IN ('purchase','opening','adjustment_gain') AND i.item_type='fixed'
AND i.quantity<>(SELECT count(*) FROM asset_cards a WHERE a.tenant_id=i.tenant_id AND a.source_inbound_item_id=i.id);

SELECT 'excess_consumable_return' AS issue,o.id FROM outbound_items o
WHERE o.item_type='consumable' AND o.actual_qty<(SELECT coalesce(sum(i.quantity),0) FROM inbound_items i WHERE i.tenant_id=o.tenant_id AND i.source_outbound_item_id=o.id);

SELECT 'quality_transfer_ledger' AS issue,i.id FROM adjustment_items i JOIN adjustment_orders a ON a.tenant_id=i.tenant_id AND a.id=i.order_id
LEFT JOIN stock_ledger l ON l.tenant_id=i.tenant_id AND l.adjustment_item_id=i.id
WHERE a.status='approved' AND a.type='quality_transfer'
GROUP BY i.id HAVING count(l.id)<>2 OR
 coalesce(sum(CASE WHEN l.direction='in' THEN l.quantity ELSE -l.quantity END),0)<>0 OR
 count(*) FILTER(WHERE l.direction='out' AND l.quality=i.quality AND l.quantity=i.quantity)<>1 OR
 count(*) FILTER(WHERE l.direction='in' AND l.quality=i.to_quality AND l.quantity=i.quantity)<>1;

-- [B6] 单据↔流水：每条已入账耗材明细必须有且仅有一条匹配方向与水量的流水
SELECT 'inbound_ledger' AS issue,i.id FROM inbound_items i JOIN inbound_orders o ON o.tenant_id=i.tenant_id AND o.id=i.order_id
LEFT JOIN stock_ledger l ON l.tenant_id=i.tenant_id AND l.inbound_item_id=i.id
WHERE o.type IN ('purchase','return','opening','adjustment_gain')
GROUP BY i.id HAVING count(l.id)<>1
   OR count(*) FILTER(WHERE l.direction='in' AND l.quantity=i.quantity)<>1;

SELECT 'outbound_ledger' AS issue,i.id FROM outbound_items i JOIN outbound_orders o ON o.tenant_id=i.tenant_id AND o.id=i.order_id
LEFT JOIN stock_ledger l ON l.tenant_id=i.tenant_id AND l.outbound_item_id=i.id
WHERE o.status='completed' AND i.actual_qty>0
GROUP BY i.id HAVING count(l.id)<>1
   OR count(*) FILTER(WHERE l.direction='out' AND l.quantity=i.actual_qty)<>1;

SELECT 'scrap_ledger' AS issue,s.id FROM scrap_items s JOIN scrap_orders o ON o.tenant_id=s.tenant_id AND o.id=s.scrap_id
LEFT JOIN stock_ledger l ON l.tenant_id=s.tenant_id AND l.scrap_item_id=s.id
WHERE o.status='approved' AND o.disposal_type IN ('scrap','loss','stock_damage')
GROUP BY s.id HAVING count(l.id)<>1;

SELECT 'adjustment_ledger' AS issue,i.id FROM adjustment_items i JOIN adjustment_orders o ON o.tenant_id=i.tenant_id AND o.id=i.order_id
LEFT JOIN stock_ledger l ON l.tenant_id=i.tenant_id AND l.adjustment_item_id=i.id
WHERE o.status='approved' AND o.type='count'
GROUP BY i.id HAVING count(l.id)<>1
   OR count(*) FILTER(WHERE l.direction=i.direction AND l.quantity=i.quantity)<>1;

-- [B7] 流水滚动结余：balance_after 必须等于按 id 顺序的累计水量
SELECT 'ledger_balance_after' AS issue,x.id FROM (
 SELECT l.id,l.tenant_id,l.item_id,l.location_id,l.quality,l.balance_after,
   sum(CASE l.direction WHEN 'in' THEN l.quantity ELSE -l.quantity END) OVER(
     PARTITION BY l.tenant_id,l.item_id,l.location_id,l.quality ORDER BY l.id
     ROWS BETWEEN UNBOUNDED PRECEDING AND CURRENT ROW) running_qty
 FROM stock_ledger l) x
WHERE x.balance_after IS DISTINCT FROM x.running_qty;

-- [B7] 采购退货累计不得超过对应采购入库明细水量
SELECT 'purchase_return_excess' AS issue,i.id FROM inbound_items i
WHERE i.item_type='consumable' AND i.quantity<(
 SELECT coalesce(sum(o.actual_qty),0) FROM outbound_items o JOIN outbound_orders oo
   ON oo.tenant_id=o.tenant_id AND oo.id=o.order_id
 WHERE o.tenant_id=i.tenant_id AND o.source_inbound_item_id=i.id
   AND oo.type='purchase_return' AND oo.status='completed');
COMMIT;
