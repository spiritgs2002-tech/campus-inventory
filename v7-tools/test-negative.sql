-- test-negative.sql —— v7.2 新增/强化约束的正向 + 负向用例
--
-- 前置：已通过 G0 的空库（先执行 schema-v7.sql）
-- 执行：psql -v ON_ERROR_STOP=1 -d <db> -f test-negative.sql
--
-- 每项都以"期望报错 / 期望成功"双断言写成，任一项不符即 RAISE 使脚本非零退出。
-- 这是 M1 硬门禁"逐项负向测试"的可执行载体；空库跑通不代表业务验收通过，
-- 仍需按 B10 对 reconcile.sql 每一项构造业务级错误样例。
--
-- 实测：2026-10-31 PostgreSQL 16.15，21 项全部 PASS，退出码 0。

BEGIN;

-- ==================== 种子数据 ====================
INSERT INTO tenants(id,code,name,status,login_failure_limit,login_lock_minutes,asset_prefix,next_asset_number,allow_self_approval)
VALUES (1,'T1','租户一','active',5,30,'AS',1,false),
       (2,'T2','租户二','active',5,30,'BS',1,false);

INSERT INTO users(id,tenant_id,username,password_hash,name,role,must_change_password,active)
VALUES (1,1,'u1','h','用户1','admin',false,true),
       (2,1,'u2','h','用户2','user',false,true),
       (3,2,'u3','h','用户3','admin',false,true);

INSERT INTO locations(id,tenant_id,name,active) VALUES (1,1,'库1',true),(2,2,'库2',true);
INSERT INTO categories(id,tenant_id,code,name,type,active) VALUES
 (1,1,'C1','耗材类','consumable',true),(2,1,'C2','资产类','fixed',true);
INSERT INTO items(id,tenant_id,code,name,category_id,type,unit,active) VALUES
 (1,1,'I1','耗材A',1,'consumable','个',true),
 (2,1,'I2','资产B',2,'fixed','台',true),
 (3,1,'I3','耗材C',1,'consumable','个',true);
INSERT INTO operation_requests(id,tenant_id,actor_id,operation,idempotency_key,request_digest)
VALUES (1,1,1,'seed','k1','d1'),(2,1,1,'seed','k2','d2'),(3,1,1,'seed','k3','d3'),(4,1,1,'seed','k4','d4');

-- 入库头与明细必须同事务（created_xid 守卫）
INSERT INTO inbound_orders(id,tenant_id,order_no,type,operator_id,operation_id,executed_at)
VALUES (1,1,'IN1','opening',1,1,now());
INSERT INTO inbound_items(id,tenant_id,order_id,item_id,item_type,quantity,location_id,quality)
VALUES (1,1,1,2,'fixed',1,1,'available'),
       (2,1,1,1,'consumable',100,1,'available');

INSERT INTO asset_cards(id,tenant_id,asset_no,item_id,item_type,source_inbound_item_id,location_id,status,qr_token)
VALUES (1,1,'AS-0001',2,'fixed',1,1,'in_stock','qr-1'),
       (2,1,'AS-0002',2,'fixed',1,1,'in_stock','qr-2');

INSERT INTO outbound_orders(id,tenant_id,order_no,type,status,applicant_id,operation_id,self_issued)
VALUES (1,1,'OUT1','requisition','pending',2,2,false);
INSERT INTO outbound_items(id,tenant_id,order_id,item_id,item_type,requested_qty,actual_qty,location_id,recipient_id)
VALUES (1,1,1,2,'fixed',1,0,1,2);

-- 报损单：1=loss(卡1)、2=scrap(卡1)、3=stock_damage(耗材)
INSERT INTO scrap_orders(id,tenant_id,order_no,disposal_type,status,applicant_id,self_approved,allow_self_approval_snapshot,reason,operation_id)
VALUES (1,1,'SC1','loss','pending',2,false,false,'遗失',3),
       (2,1,'SC2','scrap','pending',2,false,false,'报废',4),
       (3,1,'SC3','stock_damage','pending',2,false,false,'库存损坏',4);
INSERT INTO scrap_items(id,tenant_id,scrap_id,item_id,item_type,quantity,asset_card_id,location_id,quality,disposal_type)
VALUES (1,1,1,2,'fixed',NULL,1,NULL,NULL,'loss'),
       (2,1,2,2,'fixed',NULL,1,NULL,NULL,'scrap'),
       (3,1,3,1,'consumable',5,NULL,1,'available','stock_damage');

COMMIT;

-- ==================== 断言框架 ====================
CREATE OR REPLACE FUNCTION t_expect_error(p_label text, p_sql text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  BEGIN
    EXECUTE p_sql;
    RAISE EXCEPTION 'NEGATIVE_TEST_FAILED: %', p_label;
  EXCEPTION WHEN others THEN
    IF SQLERRM LIKE 'NEGATIVE_TEST_FAILED%' THEN
      RAISE EXCEPTION 'FAIL[%] 期望报错但执行成功', p_label;
    END IF;
    RAISE NOTICE 'PASS[%] 已拒绝 -> %', p_label, left(SQLERRM,110);
  END;
END; $$;

CREATE OR REPLACE FUNCTION t_expect_ok(p_label text, p_sql text) RETURNS void
LANGUAGE plpgsql AS $$
BEGIN
  BEGIN
    EXECUTE p_sql;
    RAISE NOTICE 'PASS[%] 已放行', p_label;
  EXCEPTION WHEN others THEN
    RAISE EXCEPTION 'FAIL[%] 期望成功但被拒绝: %', p_label, SQLERRM;
  END;
END; $$;

-- ==================== N7 资产卡不可变守卫 ====================
SELECT t_expect_error('N7-改资产编号',
  $$UPDATE asset_cards SET asset_no='AS-9999' WHERE tenant_id=1 AND id=1$$);
SELECT t_expect_error('N7-改二维码',
  $$UPDATE asset_cards SET qr_token='qr-x' WHERE tenant_id=1 AND id=1$$);
SELECT t_expect_error('N7-改来源入库明细',
  $$UPDATE asset_cards SET source_inbound_item_id=2 WHERE tenant_id=1 AND id=1$$);
SELECT t_expect_error('N7-物理删除卡片',
  $$DELETE FROM asset_cards WHERE tenant_id=1 AND id=1$$);
SELECT t_expect_ok('N7-改状态允许',
  $$UPDATE asset_cards SET status='repair' WHERE tenant_id=1 AND id=1$$);
SELECT t_expect_ok('N7-改位置允许',
  $$UPDATE asset_cards SET location_id=1 WHERE tenant_id=1 AND id=1$$);
UPDATE asset_cards SET status='in_stock' WHERE tenant_id=1 AND id=1;

-- ==================== N10 object_key 租户内唯一 ====================
INSERT INTO attachments(id,tenant_id,object_key,sha256,size_bytes,content_type,retention_until,uploaded_by)
VALUES (1,1,'shared/key.pdf',repeat('a',64),100,'application/pdf',now()+interval '365 days',1);
SELECT t_expect_ok('N10-跨租户可用相同object_key',
  $$INSERT INTO attachments(id,tenant_id,object_key,sha256,size_bytes,content_type,retention_until,uploaded_by)
    VALUES (2,2,'shared/key.pdf',repeat('b',64),100,'application/pdf',now()+interval '365 days',3)$$);
SELECT t_expect_error('N10-同租户内object_key重复',
  $$INSERT INTO attachments(id,tenant_id,object_key,sha256,size_bytes,content_type,retention_until,uploaded_by)
    VALUES (3,1,'shared/key.pdf',repeat('c',64),100,'application/pdf',now()+interval '365 days',1)$$);

-- ==================== N6 附件撤回与元数据守卫 ====================
INSERT INTO attachments(id,tenant_id,object_key,sha256,size_bytes,content_type,retention_until,uploaded_by)
VALUES (4,1,'orphan.pdf',repeat('d',64),100,'application/pdf',now()+interval '365 days',1);
SELECT t_expect_ok('N6-孤儿附件保留期未到也可标记删除',
  $$UPDATE attachments SET deleted_at=now() WHERE tenant_id=1 AND id=4$$);
SELECT t_expect_error('N6-已删除附件不可再被处置单关联',
  $$INSERT INTO scrap_attachments(tenant_id,scrap_id,attachment_id) VALUES (1,1,4)$$);

INSERT INTO attachments(id,tenant_id,object_key,sha256,size_bytes,content_type,retention_until,uploaded_by)
VALUES (5,1,'linked.pdf',repeat('e',64),100,'application/pdf',now()+interval '365 days',1);
INSERT INTO scrap_attachments(tenant_id,scrap_id,attachment_id) VALUES (1,1,5);
SELECT t_expect_error('N6-已关联附件保留期未到不可删除',
  $$UPDATE attachments SET deleted_at=now() WHERE tenant_id=1 AND id=5$$);
SELECT t_expect_error('N6-附件元数据sha256不可改',
  $$UPDATE attachments SET sha256=repeat('f',64) WHERE tenant_id=1 AND id=5$$);
SELECT t_expect_ok('N6-legal_hold可单向开启',
  $$UPDATE attachments SET legal_hold=true WHERE tenant_id=1 AND id=5$$);
SELECT t_expect_error('N6-legal_hold不可关闭',
  $$UPDATE attachments SET legal_hold=false WHERE tenant_id=1 AND id=5$$);

-- ==================== N5 结案 loss 必须指向 loss 明细 ====================
INSERT INTO outbound_assets(id,tenant_id,outbound_item_id,item_id,asset_card_id)
VALUES (1,1,1,2,1);
SELECT t_expect_error('N5-loss结案指向scrap明细被拒',
  $$UPDATE outbound_assets SET closed_at=now(),close_reason='loss',loss_scrap_item_id=2 WHERE tenant_id=1 AND id=1$$);
SELECT t_expect_ok('N5-loss结案指向loss明细放行',
  $$UPDATE outbound_assets SET closed_at=now(),close_reason='loss',loss_scrap_item_id=1 WHERE tenant_id=1 AND id=1$$);

-- ==================== N8 资产事件来源须同卡且类型匹配 ====================
INSERT INTO asset_events(id,tenant_id,asset_card_id,action,from_status,to_status,scrap_item_id,location_id,operator_id,operation_id)
VALUES (1,1,1,'loss','in_stock','lost',1,1,1,1);
SELECT t_expect_error('N8-scrap事件引用loss类型明细被拒',
  $$INSERT INTO asset_events(id,tenant_id,asset_card_id,action,from_status,to_status,scrap_item_id,location_id,operator_id,operation_id)
    VALUES (2,1,1,'scrap','in_stock','scrapped',1,1,1,1)$$);
SELECT t_expect_error('N8-事件引用他卡处置明细被拒',
  $$INSERT INTO asset_events(id,tenant_id,asset_card_id,action,from_status,to_status,scrap_item_id,location_id,operator_id,operation_id)
    VALUES (3,1,2,'loss','in_stock','lost',1,1,1,1)$$);
SELECT t_expect_ok('N8-同卡同类型放行(scrap事件引用scrap明细)',
  $$INSERT INTO asset_events(id,tenant_id,asset_card_id,action,from_status,to_status,scrap_item_id,location_id,operator_id,operation_id)
    VALUES (4,1,1,'scrap','in_stock','scrapped',(SELECT id FROM scrap_items WHERE tenant_id=1 AND disposal_type='scrap' AND asset_card_id=1),1,1,1)$$);

-- ==================== N9 流水与报损明细须同一物资 ====================
-- 注：固定资产报损不产生流水（stock_ledger 仅 consumable），故用耗材报损明细 id=3(item_id=1) 验证。
SELECT t_expect_error('N9-流水物资与报损明细不一致被拒',
  $$INSERT INTO stock_ledger(tenant_id,item_id,item_type,location_id,quality,direction,quantity,balance_after,scrap_item_id,operator_id,operation_id)
    VALUES (1,3,'consumable',1,'available','out',1,0,3,1,1)$$);
SELECT t_expect_ok('N9-同一物资放行',
  $$INSERT INTO stock_ledger(tenant_id,item_id,item_type,location_id,quality,direction,quantity,balance_after,scrap_item_id,operator_id,operation_id)
    VALUES (1,1,'consumable',1,'available','out',1,0,3,1,1)$$);

-- ==================== 收尾 ====================
DROP FUNCTION t_expect_error(text,text);
DROP FUNCTION t_expect_ok(text,text);
SELECT 'negative tests completed (21 assertions)' AS result;
