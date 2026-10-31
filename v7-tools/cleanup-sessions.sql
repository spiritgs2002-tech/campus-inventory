-- cleanup-sessions.sql —— 会话"整族清理"的可执行实现（P1-2）
-- 规则（与实施计划 §2 一致）：
--   1. 族内全部 token 过期超过 p_grace（默认 30 天）**且** 没有活跃后继时，才能删除整族；
--   2. "活跃后继"= 同族中存在 revoked_at IS NULL 且 expires_at >= now() 的行；
--   3. 不得删除刚轮换/撤销的 token，否则刷新 token 复用检测会失去证据；
--   4. 自外键处理：先清空该族的 parent_session_id，再删行（两条语句分离执行），
--      避免同一语句内先删被引用行而触发自引用外键冲突。
-- 调度：由具备 auth_sessions 的 DELETE 权限的角色（如 inventory_runtime）每日调用一次。
--       SELECT cleanup_expired_session_families();            -- 默认 30 天宽限
--       SELECT cleanup_expired_session_families('60 days');   -- 自定义宽限
--
-- v7.1 修正：早期版本在 EXISTS 子查询里写 HAVING 而没有 GROUP BY，聚合会退化成"全表单组"，
--           导致判定取决于整库会话而非单个会话族（漏删或误删）。改为 CTE 先按 (tenant_id, family_id)
--           分组聚合，再用半连接匹配。
-- 依赖索引（schema-v7.sql 基线已建）：idx_auth_family(tenant_id,family_id)、idx_session_expiry(expires_at)。

CREATE OR REPLACE FUNCTION cleanup_expired_session_families(p_grace interval DEFAULT interval '30 days')
RETURNS bigint LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
DECLARE v_deleted bigint;
BEGIN
  -- 步骤 1：先解开自引用，避免步骤 2 删除时触发自外键冲突
  WITH fam AS (
    SELECT s.tenant_id, s.family_id
    FROM auth_sessions s
    GROUP BY s.tenant_id, s.family_id
    HAVING bool_and(s.expires_at < now() - p_grace)
       AND count(*) FILTER (WHERE s.revoked_at IS NULL AND s.expires_at >= now()) = 0
  )
  UPDATE auth_sessions s SET parent_session_id = NULL
  WHERE s.parent_session_id IS NOT NULL
    AND EXISTS (SELECT 1 FROM fam f
                WHERE f.tenant_id = s.tenant_id AND f.family_id = s.family_id);

  -- 步骤 2：整族删除（CTE 与步骤 1 同一判定口径）
  WITH fam AS (
    SELECT s.tenant_id, s.family_id
    FROM auth_sessions s
    GROUP BY s.tenant_id, s.family_id
    HAVING bool_and(s.expires_at < now() - p_grace)
       AND count(*) FILTER (WHERE s.revoked_at IS NULL AND s.expires_at >= now()) = 0
  )
  DELETE FROM auth_sessions s
  WHERE EXISTS (SELECT 1 FROM fam f
                WHERE f.tenant_id = s.tenant_id AND f.family_id = s.family_id);

  GET DIAGNOSTICS v_deleted = ROW_COUNT;
  RETURN v_deleted;
END; $$;

-- 建议的调度封装（可由外部 cron / Celery beat 调用，无需额外权限）：
--   SELECT cleanup_expired_session_families();
-- 负向自检（M0 必测，预期返回 0）：
--   构造一族 A：两条 token，一条已过期 40 天、另一条未过期 → 该族不得被删除；
--   构造一族 B：两条 token 均过期 40 天、均未撤销 → 该族应被整族删除；
--   构造一族 C：token 过期 10 天（< 30 天宽限）→ 不得被删除。
