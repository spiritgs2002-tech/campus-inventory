#!/usr/bin/env bash
# =============================================================================
# 学校总务库存系统 · 一键部署脚本（局域网 / 单机 docker-compose 栈）
# -----------------------------------------------------------------------------
# 它把"装环境 -> 建角色 -> 落 schema -> 开租户 -> 生成证书 -> 起服务 -> 健康自检"
# 全部一键完成，且幂等（重复运行安全，不会破坏已有数据）。
#
# 用法：
#   ./deploy.sh            # 交互式（首次，按提示回车即可）
#   ./deploy.sh --auto     # 读取 deploy.conf 非交互
#   ./deploy.sh --down     # 停止并移除容器（保留数据库卷与证书）
#
# 前置：Linux 服务器、docker、docker compose 插件、openssl（均会自动检测）。
# 数据全部留在校内；TLS 证书为自签，终端首次访问需信任/忽略警告。
# =============================================================================
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

ENV_FILE="$SCRIPT_DIR/.env"
NGINX_CONF="$SCRIPT_DIR/nginx.conf"
NGINX_BAK="$SCRIPT_DIR/nginx.http.conf.bak"
CERT_DIR="$SCRIPT_DIR/certs"
CONF="${DEPLOY_CONF:-$SCRIPT_DIR/deploy.conf}"
ADMIN_CREDS="$SCRIPT_DIR/admin-credentials.txt"

# ------------------------- 输出工具 -----------------------------------------
info() { printf '\033[1;34m[INFO]\033[0m %s\n' "$*"; }
ok()   { printf '\033[1;32m[ OK ]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[WARN]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[FAIL]\033[0m %s\n' "$*" >&2; exit 1; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "缺少命令：$1，请先在服务器上安装（apt/dnf 或官方脚本）。"; }

# ------------------------- 前置检测 -----------------------------------------
need_cmd docker
docker compose version >/dev/null 2>&1 || die "docker compose 不可用，请安装 docker compose 插件。"
need_cmd openssl

# ------------------------- 参数 ---------------------------------------------
ACTION="deploy"
AUTO=0
for a in "$@"; do
  case "$a" in
    --auto) AUTO=1 ;;
    --down) ACTION="down" ;;
    -h|--help) sed -n '3,18p' "$0"; exit 0 ;;
    *) die "未知参数：$a" ;;
  esac
done

# ------------------------- 读取配置 -----------------------------------------
if [ "$ACTION" = "down" ]; then
  docker compose down || true
  ok "已停止并移除容器（数据库卷 pgdata / miniodata / 证书保留）。"
  exit 0
fi

DOMAIN=""; TENANT_CODE="DEMO-SCHOOL"; TENANT_NAME="示范学校"; ADMIN_USER="admin"
DB_NAME="campus_inventory"; STORAGE_MODE="minio"
EXT_S3_ENDPOINT=""; EXT_S3_BUCKET=""; EXT_AWS_ID=""; EXT_AWS_SECRET=""

if [ -f "$CONF" ] && [ "$AUTO" -eq 1 ]; then
  info "读取配置文件：$CONF"
  set -a; . "$CONF"; set +a
  DOMAIN="${DOMAIN:-}"; TENANT_CODE="${TENANT_CODE:-DEMO-SCHOOL}"; TENANT_NAME="${TENANT_NAME:-示范学校}"
  ADMIN_USER="${ADMIN_USER:-admin}"; DB_NAME="${DB_NAME:-campus_inventory}"; STORAGE_MODE="${STORAGE_MODE:-minio}"
  EXT_S3_ENDPOINT="${ATTACHMENT_S3_ENDPOINT:-}"; EXT_S3_BUCKET="${ATTACHMENT_S3_BUCKET:-}"
  EXT_AWS_ID="${AWS_ACCESS_KEY_ID:-}"; EXT_AWS_SECRET="${AWS_SECRET_ACCESS_KEY:-}"
elif [ "$AUTO" -eq 0 ]; then
  info "交互式配置（直接回车采用 [默认值]）"
  read -r -p "校内访问域名/IP [$DOMAIN]: " v; [ -n "$v" ] && DOMAIN="$v"
  read -r -p "租户代码 [$TENANT_CODE]: " v; [ -n "$v" ] && TENANT_CODE="$v"
  read -r -p "学校名称 [$TENANT_NAME]: " v; [ -n "$v" ] && TENANT_NAME="$v"
  read -r -p "管理员用户名 [$ADMIN_USER]: " v; [ -n "$v" ] && ADMIN_USER="$v"
  read -r -p "数据库名 [$DB_NAME]: " v; [ -n "$v" ] && DB_NAME="$v"
  read -r -p "对象存储 minio/s3 [$STORAGE_MODE]: " v; [ -n "$v" ] && STORAGE_MODE="$v"
fi

[ -n "$DOMAIN" ] || die "必须提供 DOMAIN（校内访问域名或服务器内网 IP）。"
[ -n "$TENANT_CODE" ] && [ -n "$TENANT_NAME" ] && [ -n "$ADMIN_USER" ] || die "租户信息不完整。"

# ------------------------- 载入/生成 .env（幂等，密钥只生成一次）------------
if [ -f "$ENV_FILE" ]; then
  info "复用已有 .env 中的密钥（不覆盖）"
  set -a; . "$ENV_FILE"; set +a
fi

: "${DEBUG:=false}"
: "${DB_NAME:=$DB_NAME}"
: "${MIGRATION_DB_PASSWORD:=$(openssl rand -base64 24 | tr -d '\n')}"
: "${RUN_DB_PASSWORD:=$(openssl rand -base64 24 | tr -d '\n')}"
: "${SECRET_KEY:=$(openssl rand -base64 48 | tr -d '\n')}"
: "${PII_ENCRYPTION_KEYS:=$(openssl rand -base64 32 | tr -d '\n')}"
: "${AUTH_SIGNING_KEY:=$SECRET_KEY}"
: "${ALLOWED_HOSTS:=$DOMAIN,127.0.0.1}"
: "${CSRF_TRUSTED_ORIGINS:=https://$DOMAIN}"
: "${CELERY_KEY_PREFIX:=campus_inv_}"
: "${MAINTENANCE_ENABLED:=false}"
: "${MAINTENANCE_ACTORS_JSON:={}}"
: "${ATTACHMENT_CLEANUP_ENABLED:=false}"
: "${HTTP_PORT:=80}"

if [ "${STORAGE_MODE:-minio}" = "minio" ]; then
  : "${MINIO_ROOT_USER:=invstorage}"
  : "${MINIO_ROOT_PASSWORD:=$(openssl rand -base64 24 | tr -d '\n')}"
  ATTACHMENT_STORAGE_BACKEND=s3
  ATTACHMENT_S3_BUCKET=inventory-attachments
  ATTACHMENT_S3_ENDPOINT=https://minio:9000
  ATTACHMENT_S3_REGION=us-east-1
  AWS_ACCESS_KEY_ID="$MINIO_ROOT_USER"
  AWS_SECRET_ACCESS_KEY="$MINIO_ROOT_PASSWORD"
  AWS_CA_BUNDLE=/ca/minio-ca.crt
  ATTACHMENT_RETENTION_DAYS="${ATTACHMENT_RETENTION_DAYS:-365}"
else
  [ -n "$EXT_AWS_ID" ] && [ -n "$EXT_AWS_SECRET" ] && [ -n "$EXT_S3_ENDPOINT" ] && [ -n "$EXT_S3_BUCKET" ] \
    || die "STORAGE_MODE=s3 时需在 deploy.conf 填写 AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY / ATTACHMENT_S3_ENDPOINT(https) / ATTACHMENT_S3_BUCKET。"
  [ "${EXT_S3_ENDPOINT%%:*}" = "https" ] || die "外部 S3 endpoint 必须为 https（合规与系统要求）。"
  ATTACHMENT_STORAGE_BACKEND=s3
  ATTACHMENT_S3_BUCKET="$EXT_S3_BUCKET"
  ATTACHMENT_S3_ENDPOINT="$EXT_S3_ENDPOINT"
  ATTACHMENT_S3_REGION="${ATTACHMENT_S3_REGION:-us-east-1}"
  AWS_ACCESS_KEY_ID="$EXT_AWS_ID"
  AWS_SECRET_ACCESS_KEY="$EXT_AWS_SECRET"
  ATTACHMENT_RETENTION_DAYS="${ATTACHMENT_RETENTION_DAYS:-365}"
fi

# 写出 .env（base64 不含空格，无需引号）
{
  echo "# 由 deploy.sh 生成，请勿手改密钥；可改 ALLOWED_HOSTS 等"
  echo "DEBUG=$DEBUG"
  echo "SECRET_KEY=$SECRET_KEY"
  echo "AUTH_SIGNING_KEY=$AUTH_SIGNING_KEY"
  echo "ALLOWED_HOSTS=$ALLOWED_HOSTS"
  echo "CSRF_TRUSTED_ORIGINS=$CSRF_TRUSTED_ORIGINS"
  echo "DB_NAME=$DB_NAME"
  echo "DB_USER=inventory_app"
  echo "MIGRATION_DB_PASSWORD=$MIGRATION_DB_PASSWORD"
  echo "RUN_DB_PASSWORD=$RUN_DB_PASSWORD"
  echo "DB_HOST=127.0.0.1"
  echo "DB_PORT=5432"
  echo "REDIS_URL=redis://127.0.0.1:6379/0"
  echo "CELERY_KEY_PREFIX=$CELERY_KEY_PREFIX"
  echo "PII_ENCRYPTION_KEYS=$PII_ENCRYPTION_KEYS"
  echo "ATTACHMENT_STORAGE_BACKEND=$ATTACHMENT_STORAGE_BACKEND"
  echo "ATTACHMENT_S3_BUCKET=$ATTACHMENT_S3_BUCKET"
  echo "ATTACHMENT_S3_ENDPOINT=$ATTACHMENT_S3_ENDPOINT"
  echo "ATTACHMENT_S3_REGION=$ATTACHMENT_S3_REGION"
  echo "ATTACHMENT_RETENTION_DAYS=$ATTACHMENT_RETENTION_DAYS"
  echo "AWS_ACCESS_KEY_ID=$AWS_ACCESS_KEY_ID"
  echo "AWS_SECRET_ACCESS_KEY=$AWS_SECRET_ACCESS_KEY"
  echo "MAINTENANCE_ENABLED=$MAINTENANCE_ENABLED"
  echo "MAINTENANCE_ACTORS_JSON=$MAINTENANCE_ACTORS_JSON"
  echo "ATTACHMENT_CLEANUP_ENABLED=$ATTACHMENT_CLEANUP_ENABLED"
  echo "HTTP_PORT=$HTTP_PORT"
  echo "TRUST_X_FORWARDED_PROTO=true"
  echo "ANONYMOUS_SCAN_TENANTS=${ANONYMOUS_SCAN_TENANTS-*}"
  if [ "${STORAGE_MODE:-minio}" = "minio" ]; then
    echo "MINIO_ROOT_USER=$MINIO_ROOT_USER"
    echo "MINIO_ROOT_PASSWORD=$MINIO_ROOT_PASSWORD"
  fi
} > "$ENV_FILE"
ok ".env 已就绪（DB_NAME=$DB_NAME, 对象存储=$STORAGE_MODE）"

# ------------------------- 生成 TLS 证书 ------------------------------------
mkdir -p "$CERT_DIR"
if [ ! -f "$CERT_DIR/fullchain.pem" ]; then
  info "生成自签域名证书（CN=$DOMAIN）"
  openssl req -x509 -newkey rsa:2048 -nodes \
    -keyout "$CERT_DIR/privkey.pem" -out "$CERT_DIR/fullchain.pem" \
    -days 825 -subj "/CN=$DOMAIN" >/dev/null 2>&1
  ok "域名证书：$CERT_DIR/fullchain.pem"
else
  info "复用已有域名证书"
fi

# MinIO 专用 CA + 服务端证书（仅 minio 模式）
if [ "${STORAGE_MODE:-minio}" = "minio" ]; then
  mkdir -p "$CERT_DIR/minio"
  if [ ! -f "$CERT_DIR/minio/public.crt" ]; then
    info "生成 MinIO 自签 CA 与服务端证书"
    openssl genrsa -out "$CERT_DIR/minio/ca.key" 2048 >/dev/null 2>&1
    openssl req -x509 -new -nodes -key "$CERT_DIR/minio/ca.key" -days 3650 \
      -out "$CERT_DIR/minio/ca.crt" -subj "/CN=inventory-minio-ca" >/dev/null 2>&1
    openssl genrsa -out "$CERT_DIR/minio/private.key" 2048 >/dev/null 2>&1
    openssl req -new -key "$CERT_DIR/minio/private.key" -out "$CERT_DIR/minio/minio.csr" \
      -subj "/CN=minio" >/dev/null 2>&1
    openssl x509 -req -in "$CERT_DIR/minio/minio.csr" -CA "$CERT_DIR/minio/ca.crt" \
      -CAkey "$CERT_DIR/minio/ca.key" -CAcreateserial -out "$CERT_DIR/minio/public.crt" \
      -days 3650 -extfile <(printf "subjectAltName=DNS:minio,IP:127.0.0.1") >/dev/null 2>&1
    rm -f "$CERT_DIR/minio/minio.csr" "$CERT_DIR/minio/ca.srl"
    ok "MinIO 证书：$CERT_DIR/minio/public.crt"
  else
    info "复用已有 MinIO 证书"
  fi
fi

# ------------------------- 写入 nginx TLS 配置（备份原 HTTP 版）-------------
if [ ! -f "$NGINX_BAK" ] && [ -f "$NGINX_CONF" ]; then
  cp "$NGINX_CONF" "$NGINX_BAK"
fi
cat > "$NGINX_CONF" <<'NGINX'
upstream campus_inventory_app { server web:8000; }
limit_req_zone $binary_remote_addr zone=auth_limit:10m rate=10r/m;

server {
    listen 80;
    server_name _;
    # HTTP 统一跳转到 HTTPS（应用层 SECURE_SSL_REDIRECT 也会要求，这里由 nginx 落地）
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl;
    server_name _;
    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;
    client_max_body_size 12m;

    location /static/ {
        alias /static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
    location = /health/live/ { proxy_pass http://campus_inventory_app; proxy_set_header Host $host; }
    location = /health/ready/ { proxy_pass http://campus_inventory_app; proxy_set_header Host $host; }
    location /api/v1/auth/ {
        limit_req zone=auth_limit burst=5 nodelay;
        proxy_pass http://campus_inventory_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
    location / {
        proxy_pass http://campus_inventory_app;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
    }
}
NGINX
ok "nginx 已切换为 TLS（443），原 HTTP 版备份在 $NGINX_BAK"

# ------------------------- 启动依赖：postgres + redis ------------------------
info "启动 postgres + redis 并等待就绪"
docker compose up -d postgres redis
wait_healthy() {
  local svc="$1" i st
  for i in $(seq 1 40); do
    st=$(docker compose ps -q "$svc" 2>/dev/null)
    if [ -n "$st" ] && docker inspect -f '{{.State.Health.Status}}' "$st" 2>/dev/null | grep -q healthy; then return 0; fi
    sleep 3
  done
  return 1
}
wait_healthy postgres || die "postgres 未就绪"
wait_healthy redis || die "redis 未就绪"
ok "postgres + redis 就绪"

# ------------------------- 建角色：inventory_runtime + inv_run ---------------
info "创建运行角色（幂等）"
docker compose exec -T postgres psql -U inventory_app -d "$DB_NAME" -v ON_ERROR_STOP=1 \
  -f /dev/stdin < "$SCRIPT_DIR/../django/sql/grant-runtime.sql" >/dev/null 2>&1 || warn "grant-runtime 有输出（已存在则正常）"

docker compose exec -T postgres psql -U inventory_app -d "$DB_NAME" -v runpw="'$RUN_DB_PASSWORD'" -f - <<'SQL' >/dev/null 2>&1
DO $$ BEGIN
  IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='inv_run') THEN
    CREATE ROLE inv_run LOGIN PASSWORD :runpw
      NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS;
  END IF;
END $$;
GRANT inventory_runtime TO inv_run;
SQL
ok "角色 inventory_runtime + inv_run 就绪"

# ------------------------- 落 schema（迁移身份 inventory_app）---------------
info "执行 apply_schema（G0 校验内嵌）"
docker compose run --rm -e DB_USER=inventory_app -e DB_PASSWORD="$MIGRATION_DB_PASSWORD" \
  -e DB_NAME="$DB_NAME" web uv run python manage.py apply_schema
ok "schema 已落地（verify_schema / verify_runtime 已通过）"

# ------------------------- 开租户（受控运维身份）---------------------------
ADMIN_PW="$(openssl rand -base64 24 | tr -d '\n')"
IDEMPOTENCY_KEY="deploy-provision-$TENANT_CODE"
info "开通租户 $TENANT_CODE（管理员 $ADMIN_USER，首次登录须改密）"
docker compose run --rm -e DB_USER=inventory_app -e DB_PASSWORD="$MIGRATION_DB_PASSWORD" \
  -e DB_NAME="$DB_NAME" -e INITIAL_ADMIN_PASSWORD="$ADMIN_PW" web \
  uv run python manage.py provision_tenant \
  --code "$TENANT_CODE" --name "$TENANT_NAME" --admin-username "$ADMIN_USER" \
  --idempotency-key "$IDEMPOTENCY_KEY"
printf '租户=%s 管理员=%s 初始密码=%s\n（首次登录必须修改；请妥善保存，改密后可删除本文件）\n' \
  "$TENANT_CODE" "$ADMIN_USER" "$ADMIN_PW" > "$ADMIN_CREDS"
chmod 600 "$ADMIN_CREDS"
ok "管理员凭据已写入 $ADMIN_CREDS（权限 600）"

# ------------------------- MinIO 启动 + 建桶 --------------------------------
if [ "${STORAGE_MODE:-minio}" = "minio" ]; then
  info "启动 MinIO 并创建附件桶"
  docker compose up -d minio
  for i in $(seq 1 30); do
    if docker compose run --rm --entrypoint sh mc -c \
      "mc alias set local https://minio:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD >/dev/null 2>&1 && mc ready local >/dev/null 2>&1" >/dev/null 2>&1; then break; fi
    sleep 3
  done
  docker compose run --rm --entrypoint sh mc -c \
    "mc alias set local https://minio:9000 $MINIO_ROOT_USER $MINIO_ROOT_PASSWORD >/dev/null 2>&1 && mc mb --ignore-existing local/$ATTACHMENT_S3_BUCKET >/dev/null 2>&1" \
    || warn "MinIO 建桶失败，请检查 minio 容器日志"
  ok "MinIO 就绪（桶 $ATTACHMENT_S3_BUCKET）"
fi

# ------------------------- 起全栈 -------------------------------------------
info "启动全栈（web/worker/beat/nginx）"
docker compose up -d
sleep 5

# ------------------------- 健康自检 -----------------------------------------
check_url() {
  local path="$1" code i
  for i in $(seq 1 30); do
    code=$(curl -k -s -o /dev/null -w '%{http_code}' "https://127.0.0.1$path" || true)
    [ "$code" = "200" ] && { echo "$code"; return 0; }
    sleep 3
  done
  echo "$code"; return 1
}
LIVE=$(check_url /health/live/)
READY=$(check_url /health/ready/)
[ "$LIVE" = "200" ] || die "/health/live/ 返回 $LIVE（应用未起来，看 docker compose logs web）"
[ "$READY" = "200" ] || die "/health/ready/ 返回 $READY（DB/Redis/MinIO 未连通）"
ok "健康检查通过：live=$LIVE ready=$READY"

# ------------------------- 完成 ---------------------------------------------
cat <<SUMMARY

==================================================================
 部署完成 ✅
==================================================================
 访问地址：  https://$DOMAIN   （自签证书，浏览器首次访问会提示，选择“继续/高级->继续”即可）
 管理员登录：代码 $TENANT_CODE / 用户 $ADMIN_USER
 初始密码：  见 $ADMIN_CREDS  （首次登录必须修改）

 已落地：
   - 数据库角色 inventory_app(迁移) / inventory_runtime / inv_run(运行)
   - Schema（G0 校验内嵌通过）+ 租户 $TENANT_CODE
   - TLS：$CERT_DIR 下证书；nginx 监听 443，HTTP 自动跳 443
   - 对象存储：$STORAGE_MODE（校内 MinIO，数据不出校）

 日常运维：
   ./deploy.sh --down      停止容器（保留数据卷与证书）
   docker compose logs -f web  查看应用日志
   docker compose restart web  重启应用

 注意：
   - 本机无外网映射；校外访问请走学校 VPN（见《部署指南》阶段12）。
   - 证书默认 825 天有效，到期前用同目录证书替换并 docker compose restart nginx。
   - .env / admin-credentials.txt 含密钥，切勿提交或外泄。
==================================================================
SUMMARY
