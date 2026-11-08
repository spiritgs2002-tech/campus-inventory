# 目标环境实测清单（上线前 Runbook）

- **用途**：本沙箱已完成应用级全绿验证（个人单账号冒烟 PASS、G0 30/14/32、266 tests OK），但本机**缺 docker / nginx / redis-server / 真实云凭证**，以下项目必须在**你的目标环境**实测后才可拍板上线。
- **受众**：负责部署与上线拍板的运维 / 技术负责人。
- **铁律**：本清单只做"实测 + 拍板"，不动业务代码；所有密钥在目标环境生成，严禁明文进仓库。

---

## 0. 前置条件核查（开始实测前打勾）

- [ ] **目标主机**：Linux（推荐 Ubuntu 22.04+ / Rocky 9），已装 Docker 与 docker-compose v2；或愿意用 systemd 裸机部署。
- [ ] **PostgreSQL 16**：目标库已就绪（托管 RDS / 容器 / 裸机均可），能建角色、建库。
- [ ] **域名 + TLS 证书**：已申请 `your-domain`（如 `inventory.school.edu.cn`），证书 `fullchain.pem` + `privkey.pem` 就绪。
- [ ] **对象存储**：云 S3（或兼容 S3 的 OSS/COS/MinIO），已建桶、拿到 IAM AccessKey/Secret 与 Region/Endpoint。
- [ ] **Redis**：目标环境有可用的 Redis 7（容器 / 托管 / 裸机）。
- [ ] **本地已交付物**：`inventory-v4/` 整树（含 `deploy/` 骨架、`django/` 应用、`scripts/` 验证脚本、根 `schema-v7.sql`）。

> 角色分离约定（宪法级）：迁移身份 `inventory_app`（有 `CREATEDB`/`CREATEROLE`）、运行身份 `inv_run`（角色 `inventory_runtime` 成员，NOLOGIN 组 `inventory_runtime`）。**运行身份永不执行迁移，迁移身份永不作为 Web 运行身份。**

---

## 阶段 A：密钥生成与角色分离 Bootstrap

**A.1 生成密钥（在目标主机，不落仓库）**
```bash
export SECRET_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
export AUTH_SIGNING_KEY=$(python3 -c "import secrets;print(secrets.token_urlsafe(48))")
export PII_ENCRYPTION_KEYS=$(python3 -c "import secrets;print(secrets.token_urlsafe(32))")
# 数据库两身份密码（迁移 / 运行分离）
export MIGRATION_DB_PASSWORD=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
export RUN_DB_PASSWORD=$(python3 -c "import secrets;print(secrets.token_urlsafe(24))")
```

**A.2 填写 `deploy/env.production.template` → 落地为 `.env`（生产）**
关键变量（以 `settings.py` 为准，模板为权威样本）：
- `DEBUG=false`、`ALLOWED_HOSTS=your-domain,127.0.0.1`
- `DB_NAME` / `DB_HOST` / `DB_PORT` / `DB_USER=inventory_app` / `MIGRATION_DB_PASSWORD`
- `RUN_DB_USER=inv_run` / `RUN_DB_PASSWORD`（compose 与 systemd 均显式用运行身份）
- `REDIS_URL=redis://<host>:6379/0`（CELERY broker/result 同源）
- `SECRET_KEY` / `AUTH_SIGNING_KEY` / `PII_ENCRYPTION_KEYS`
- `ATTACHMENT_STORAGE_BACKEND=s3` / `ATTACHMENT_S3_BUCKET` / `ATTACHMENT_S3_REGION` / `ATTACHMENT_S3_ENDPOINT`
- `PICKUP_ANONYMOUS_PER_MINUTE=60`（每校每类免密操作每分钟上限，按校情调）
- `ANONYMOUS_SCAN_TENANTS`（免密扫码开关：`*` 全开 / 空值全关 / 租户 ID 列表）；`PICKUP_ANONYMOUS_MAX_QTY`、`ANONYMOUS_PER_EMPLOYEE_PER_HOUR`、`ANONYMOUS_NEW_IDENTITIES_PER_HOUR` 按校情调
- `TRUST_X_FORWARDED_PROTO=true`（仅在 nginx/CLB 前置时；compose 已为 web 固定开启）
- `CSRF_TRUSTED_ORIGINS=https://your-domain`

**A.3 建库 + 两角色（用迁移身份 `inventory_app` 连接）**
- 建角色 `inventory_app`（CREATEDB, CREATEROLE）、`inventory_runtime`（NOLOGIN 组）、`inv_run`（IN ROLE inventory_runtime）；
- `CREATE DATABASE inventory OWNER inventory_app;`；`ALTER SCHEMA public OWNER TO inventory_app;`（PG15+ public 默认收紧，须显式赋权，否则 apply_schema 报 `permission denied for schema public`）。
- 真实 `.env` 与证书务必进 `.gitignore`，不入库。

---

## 阶段 B：数据库与 Schema 落地（G0 基线）

> 以**迁移身份**执行（compose/web 容器内的 migrate 入口或手动）。

- [ ] `python manage.py apply_schema` → 应用根 `schema-v7.sql`，**含 P8a/b/c 增量**（G0 期望 **30 表 / 14 函数 / 32 触发器**）。
- [ ] `python manage.py verify_schema` → 输出 `G0 = (30,14,32)`，无回归。
- [ ] `python manage.py verify_runtime` → `inventory_runtime` ACL 通过（运行身份权限闸门）。
- [ ] `python manage.py sync_schema_snapshot --check` → `fingerprint_valid:true` 且 `snapshot_matches:true`（根 schema 指纹自洽闸门）。
- [ ] **开通真实租户**：`python manage.py provision_tenant --code <SCHOOL> --name "示范学校" --admin-username admin --idempotency-key <唯一键>`；密码取 `INITIAL_ADMIN_PASSWORD` 或隐藏输入。**该命令拒绝运行身份执行**（防越权）。

> 若 `verify_schema` 报 G0 不符，先 `precheck_sql`（美元引号平衡）自查，再核对根 `schema-v7.sql` 与 `django/sql/schema-v7-current.sql` 是否同步（CI 已加 `sync_schema_snapshot --check` 门禁）。

---

## 阶段 C：Redis + Celery 端到端（本机未测，必须在此验证）

- [ ] 目标环境启动 Redis 7，确认 `REDIS_URL` 可达（`redis-cli ping` → PONG）。
- [ ] 启动 worker：`celery -A campus_inventory worker -Q celery`；启动 beat：`celery -A campus_inventory beat`（或 compose 内 `beat` 服务 / systemd `campus-inventory-beat.service`）。
- [ ] 运行 `python scripts/verify_p7.py` → 覆盖 O1 会话清理 / O2 证据对象清理+孤儿回收 / O3 beat 调度 / O4 运维说明；**此前本沙箱因无 redis 未能跑通，此处必须全过**。
- [ ] **业务联动实测**：在 UI 走一笔「请购驳回→驳回通知」「采购入库→到货通知」，确认通知由 Celery 异步投递（铃铛未读+站内通知落地）；确认 P7 维护任务（会话/证据清理）按 `CELERY_BEAT_SCHEDULE` 周期性执行。
- [ ] `/health/ready/` 此时应由 503 转为 **200**（依赖 redis 探活）；`/health/live/` 维持 200。

---

## 阶段 D：部署骨架实测（compose / systemd / nginx）

**D.1 docker-compose 路径**：`inventory-v4/deploy/docker-compose.yml`（postgres 固定 `inventory_app`；web/worker/beat 显式 `DB_USER=inv_run` + `RUN_DB_PASSWORD`）。
- [ ] `docker compose up -d` 拉起 web+worker+beat+pg16+redis7+nginx；观测各容器 `healthy`。
- [ ] 容器内 `curl -k http://localhost/health/live/` → 200；`/health/ready/` → 200。

**D.2 systemd 裸机路径**（三件套）：`deploy/systemd/campus-inventory-{web,worker,beat}.service`
- [ ] `systemctl enable --now campus-inventory-web` 等；`journalctl -u campus-inventory-web` 无报错；gunicorn 以 `inv_run` 身份 boot 探活 200。

**D.3 nginx（TLS + 限流）**：`deploy/nginx.conf`（TLS 就绪、静态托管、`/auth/login/` 登录限流）。
- [ ] 放置证书，确认 80→443 跳转；`https://your-domain/health/live/` → 200（**测试期若遇 80→443 死循环，用 .env `DEBUG=true` 暂时绕开，正式环境必须 DEBUG=false**）。
- [ ] 连续错登触发限流（返回 429）验证生效。

---

## 阶段 E：真实云 S3 + 托管 Redis 联调（B8）

- [ ] `ATTACHMENT_STORAGE_BACKEND=s3`：上传一张附件（维修照片 / 单据扫描）→ 确认写入目标桶；下载回读字节一致；删除后对象按 `ATTACHMENT_RETENTION_DAYS` 策略处理。
- [ ] IAM 最小权限：仅该桶的 `s3:PutObject/GetObject/DeleteObject`，不含 `s3:*`；Endpoint 走内网/私有链接优先。
- [ ] 若用**托管 Redis**（如云 Redis 7）：把 `REDIS_URL` 换成 `rediss://<host>:6379/0`（ TLS），重跑阶段 C 的 worker/beat/verify_p7，确认 broker 连通。

---

## 阶段 F：多角色深度走查 + 回归

- [ ] **全量测试门禁**：`python manage.py test tests --no-input` → **266 tests OK**（160→266 含 P8a/b/c，无回归）。
- [ ] **阶段验收脚本全跑**：`verify_p1` … `verify_p8c` + `verify_readiness` + `verify_ci_delivery`（CI 已含，目标环境复跑一次保险）。
- [ ] **多角色人工走查**（用真实租户账号）：
  - 管理员：主数据（部门/用户/物资/分类/库位/供应商）、入库/出库/报废、请购审批、报修派单、学校设置、附件。
  - 教师（普通用户 / 双轨轻量身份）：移动端查库存、扫码一键领用（本人领取）、缺货请购、报修上报、站内通知铃铛。
  - 越权校验：普通用户访问 `/manage/*` 写操作应 403；跨部门/跨租户数据隔离生效。

---

## 阶段 G：上线拍板门禁 + 回滚预案

**拍板前必须全绿**：
- [ ] 阶段 A–F 全部打勾；`/health/ready/` 与 `/health/live/` 均 200。
- [ ] 密钥未入库、角色分离生效（运行身份无 CREATEDB/CREATEROLE、迁移身份不作为 web 运行身份）。
- [ ] G0=(30,14,32) 三处一致 + 指纹自洽；`manage.py test tests` 全量通过（以当次 CI 输出的用例数为准）；verify_p1–p8c 全过。
- [ ] 备份就绪：`pg_dump` 全量 + 证据桶按 P7 要求**保持从未启用版本控制**（`Enabled`/`Suspended` 均会令证据清理拒绝运行），对象备份改用跨桶复制或定期离线同步；镜像标签固化。

**回滚预案**：
- 应用回滚：compose 切回上一镜像标签 / systemd 重启旧版 artifact。
- 数据回滚：SQL-first 迁移**不可逆**（`RunSQLFile.database_backwards` 显式抛出 IrreversibleError，增量 SQL 不带 DOWN），回滚只能恢复上线前校验过的 `pg_dump`；对象存储从上述跨桶副本/离线同步恢复，不依赖桶版本化。
- 紧急熔断：应用**没有维护页开关**（`MAINTENANCE_ENABLED` 只控制 P7 自动清理/对账调度，设为 `false` 并重启 worker/beat 可停止清理）。如需对外停服，在 nginx 层返回维护页或停止 web 服务；需要关闭扫码免密入口时设置 `ANONYMOUS_SCAN_TENANTS=` 后重启 web。

---

## 一键核查清单（拍板前复核）

| # | 项 | 命令 / 路径 | 期望 |
|---|---|---|---|
| 1 | Schema 落地 | `apply_schema` | 成功，含 P8a/b/c |
| 2 | G0 基线 | `verify_schema` | (30,14,32) |
| 3 | 运行身份闸门 | `verify_runtime` | PASS |
| 4 | 指纹自洽 | `sync_schema_snapshot --check` | valid+matches:true |
| 5 | 租户开通 | `provision_tenant` | 真实租户就绪 |
| 6 | Celery 端到端 | `verify_p7.py` + worker/beat | 全过，通知异步投递 |
| 7 | 就绪探针 | `/health/ready/` | 200（依赖 redis） |
| 8 | 部署骨架 | compose/systemd/nginx | 各服务 healthy，TLS+限流生效 |
| 9 | 云联调 | S3 上传下载 + 托管 Redis | 对象可读写、broker 通 |
| 10 | 回归 | `test tests` | 266 OK |
| 11 | 阶段验收 | `verify_p1`–`verify_p8c` | 全过 |
| 12 | 多角色走查 | 人工 | admin/教师/越权隔离正常 |

---

## 交付物索引（本仓库内）

- 部署骨架：`inventory-v4/deploy/`（Dockerfile / docker-compose.yml / nginx.conf / systemd×3 / env.production.template / ops-runbook.md）
- 验证脚本：`inventory-v4/django/scripts/{verify_p1..p8c,verify_readiness,verify_ci_delivery,sync_schema_snapshot}.py`
- 管理命令：`apps/core/management/commands/{apply_schema,verify_schema,verify_runtime,provision_tenant}.py`
- 宪法 SQL：`inventory-v4/django/sql/schema-v7.sql`（根权威）+ `schema-v7-current.sql`（同步快照）
- 关联文档：`云端部署与上线测试执行手册.md`、`个人单账号冒烟实测报告.md`、`使用手册.md`、`部署骨架修正说明.md`（均 `docs/过程记录/` 或项目根）
