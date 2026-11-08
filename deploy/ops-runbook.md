# 上线运行手册（Go-Live Runbook）

本手册用于把 `inventory-v4/django` 从代码态推进到**可上线测试**态。部署制品见同目录 `Dockerfile` / `docker-compose.yml` / `nginx.conf` / `systemd/` / `env.production.template`。

> 边界说明：本系统采用"迁移身份 / 运行身份"分离与 DDL 由 `schema-v7.sql` 统一管控的强约束模型；任何违背下列顺序的操作都会导致 `apply_schema` 拒接或 `require_runtime` 拦截。**严格按第 5 节顺序执行。**

---

## 1. 架构与依赖
- 应用：Django 5.2 + gunicorn（WSGI）
- 数据库：PostgreSQL 16（专库）
- 异步/调度：Celery 5.5 + Redis 7（broker/result）
- 静态：由 nginx 直接服务（见 nginx.conf），无需 whitenoise
- 附件：默认私有 S3（AES256 + 条件 PUT）；DEBUG 下可用本地私有目录
- 反向代理：nginx（TLS 终止、限流、静态）

## 2. 前置条件
- PostgreSQL 16 实例（独立专库，非 postgres 维护库）
- Redis 7 实例（与应用同私有网络）
- 若用 Docker：`docker` + `docker compose`；若用裸机：`uv` 已装、Python 3.13
- 一个 S3 私有桶（或 DEBUG 下的本地私有目录），**证据前缀禁止套用导出 TTL**

## 3. 密钥与敏感配置生成
```sh
# SECRET_KEY（必填，64 字节 url-safe）
python3 -c 'import secrets; print(secrets.token_urlsafe(64))'

# PII 加密密钥（必填，至少 1 个 Fernet key；可多 key 轮换）
python3 -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'

# AUTH_SIGNING_KEY（可选，缺省用 SECRET_KEY）
# 同法生成另一串 token_urlsafe(64)
```
将上述值写入 `env.production.template` 复制出的 `.env` 或 `/etc/campus-inventory/env`，**绝不入库**。

## 4. 数据库角色分离（关键）
由 DBA 在专库执行：
```sql
-- 迁移 owner：建库/对象，首次建角色需 CREATEROLE
CREATE ROLE inventory_app LOGIN PASSWORD '<强密码>' CREATEDB CREATEROLE;
-- 运行身份：普通 LOGIN，仅授予 inventory_runtime 成员，无 CREATEDB/CREATEROLE
CREATE ROLE inventory_runtime NOLOGIN;
CREATE ROLE inv_run LOGIN PASSWORD '<强密码>' INHERIT
    NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
GRANT inventory_runtime TO inv_run;
```
- **迁移步骤**用 `inventory_app` 连接；
- **运行/服务（web/worker/beat）**用 `inv_run` 连接（`DB_USER=inv_run`）；
- 迁移 owner 或超级用户启动服务会收到 503（`require_runtime` 拦截）。

## 5. Bootstrap 顺序（严格）
```sh
export DB_HOST=127.0.0.1 DB_PORT=5432 DB_NAME=campus_inventory \
       DB_USER=inventory_app DB_PASSWORD='<迁移密码>' \
       SECRET_KEY='<上面生成>' ALLOWED_HOSTS=inventory.example.edu \
       CSRF_TRUSTED_ORIGINS=https://inventory.example.edu

# 1) 应用 DDL（幂等；首次建角色需 CREATEROLE）
uv run python manage.py apply_schema
# 2) 断言 G0 = 27 表 / 14 函数 / 32 触发器
uv run python manage.py verify_schema
# 3) 运行身份授权断言（NOLOGIN/记账表 SELECT 等）
uv run python manage.py verify_runtime

# 4) 开通首个租户与管理员（受控运维身份；运行身份不可执行）
uv run python manage.py provision_tenant \
    --code SCHOOL --name 示例学校 \
    --admin-username first_admin --idempotency-key ops-provision-001
# 初始密码经 INITIAL_ADMIN_PASSWORD 环境变量或隐藏输入，勿放命令行参数

# 5) 切换为运行身份启动服务（见第 7 节）
```

## 6. 静态资源
- Docker/compose：web 容器启动时自动 `collectstatic --noinput`，nginx 共享 `staticfiles` 卷。
- 裸机：`uv run python manage.py collectstatic --noinput`，由 nginx `alias /static/` 服务。

## 7. 启动服务
### 方式 A：Docker Compose（推荐用于测试）
```sh
cp env.production.template .env   # 填值
docker compose -f docker-compose.yml --env-file .env up --build
# 默认仅启 web；如需 worker/beat，取消对应服务注释或在 compose 中默认启用
```
### 方式 B：Systemd（裸机）
```sh
# 放置代码到 /opt/campus-inventory/django，建 inventory 用户，填 /etc/campus-inventory/env
cp systemd/*.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable --now campus-inventory-web campus-inventory-worker campus-inventory-beat
# nginx 另装并引用 nginx.conf
```

## 8. 健康与就绪
- 存活：`GET /health/live/` → `{"status":"ok","phase":"P0"}`（无 DB/Redis 依赖）
- 就绪：建议新增 `/health/ready/`（见第 11 节补丁），编排据此判定流量放行
- 手动连通：检查 gunicorn 日志、`celery -A campus_inventory status`

## 9. 受控清理与调度（默认关闭）
开启前确认：MAINTENANCE_ACTORS_JSON 指向**已改密、启用、本校管理员**；证据删除需 `ATTACHMENT_CLEANUP_ENABLED=true` 且桶已核验。
```sh
MAINTENANCE_ENABLED=true MAINTENANCE_ACTORS_JSON='{"1":2}' uv run celery -A campus_inventory beat ...
```
每天 02:00–02:30 依次 sessions/responses/evidence/orphans；每小时 :45 全库只读对账。仅一个 beat 实例。

## 10. 备份与恢复
- PostgreSQL：每日 `pg_dump`（逻辑）或物理备份；保留策略与证据保留期对齐
- 证据对象：若启用自动清理，桶必须**从未启用版本控制**（`apps/attachments/storage.py` 对 `Enabled`/`Suspended` 均拒绝清理，因为版本桶中的简单 DELETE 不等于物理删除）；对象备份使用跨桶复制或离线同步。确需版本桶/Object Lock 时须单独设计并另行验收。
- `apply_schema` 不自动备份；DDL 失败且 grant/记账已提交时，由 DBA 恢复备份或重建空库

## 11. 推荐加固补丁（可选但建议，上线前套用）
**就绪探针**（apps/core/views.py 增加）：
```python
import psycopg  # 或 django.db.connection
@transaction.non_atomic_requests
@require_safe
def ready(request):
    try:
        from django.db import connection
        with connection.cursor() as cur:
            cur.execute("SELECT 1")
        # Redis 探活
        import redis
        r = redis.Redis.from_url(settings.REDIS_URL, socket_connect_timeout=2)
        r.ping()
        return JsonResponse({"status": "ok"})
    except Exception as e:
        return JsonResponse({"status": "error", "detail": str(e)}, status=503)
```
并在 `apps/core/urls.py` 加 `path("health/ready/", views.ready, name="health-ready")`。

**LOGGING**（campus_inventory/settings.py 增加结构化日志）：
```python
LOGGING = {
    "version": 1, "disable_existing_loggers": False,
    "handlers": {"console": {"class": "logging.StreamHandler"}},
    "root": {"handlers": ["console"], "level": "INFO"},
}
```

## 12. 监控要点
- **维护任务结果**：监控必须检查业务结果（审计/`maintenance.*.deleted` 与 `ok=false` ERROR），不能只看 Celery `SUCCESS`
- **审计日志**：`AuditLog` 记录关键操作（开通、停用、改密、自批、清理）
- **失败重试**：Celery 任务 10/20/30s 退避最多 3 次；5xx/对象未确认删除会重试，最终失败记 ERROR

## 13. 安全与合规基线（上线测试必查）
- 全站 HTTPS（nginx TLS 终止 + 应用 `SECURE_SSL_REDIRECT`）
- 登录接口限流（nginx `limit_req` 已配 `/api/v1/auth/`）
- CSP 默认严格（script/style 仅 `'self'`，无 inline/unsafe-eval）
- 敏感联系信息 PII 加密；初中生属敏感个人信息，严禁公开成绩排名
- 会话族整族撤销；强制改密；运行身份与迁移身份分离

## 14. 上线测试检查清单（Gate）
见 `../上线就绪审计报告.md` 第 4 节。核心：部署制品就位 → CI 移至仓库根 → 按本手册 bootstrap → nginx/TLS/限流生效 → 建议加就绪探针与 LOGGING → CI 补 `manage.py test tests` → 真实 S3/Redis 联调。
