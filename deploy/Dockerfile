# 学校总务库存系统 · 测试/预发部署镜像
# 基础镜像与项目一致：Python 3.13 + Django 5.2 + PostgreSQL 16 客户端
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_COMPILE_BYTECODE=1

WORKDIR /app

# 构建依赖：libpq（psycopg 需要）、curl（装 uv）
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev curl ca-certificates \
    && curl -LsSf https://astral.sh/uv/install.sh | sh \
    && ln -sf /root/.local/bin/uv /usr/local/bin/uv \
    && rm -rf /var/lib/apt/lists/*

# 先装依赖（利用层缓存）：仅复制锁文件相关
COPY pyproject.toml uv.lock ./
RUN uv sync --frozen --no-dev

# 复制应用源码
COPY . .

# 入口由 compose/systemd 指定；此处默认给 web
# 注意：collectstatic 在运行时执行（需要 SECRET_KEY 等环境变量）
EXPOSE 8000

# 默认命令（可被 compose 的 command 覆盖用于 worker/beat）
CMD ["uv", "run", "gunicorn", "campus_inventory.wsgi:application", \
     "--bind", "0.0.0.0:8000", "--workers", "3", "--timeout", "60", "--access-logfile", "-"]
