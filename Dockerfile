FROM python:3.12-slim

# 构建时由 CI 传入（与 app/__init__.py 中的 __version__ 一致）
ARG VERSION=dev

LABEL org.opencontainers.image.title="misaka-monitor" \
      org.opencontainers.image.description="Misaka IaaS 补货监控与 Telegram 通知" \
      org.opencontainers.image.source="https://github.com/xiaomonk88/misaka-monitor" \
      org.opencontainers.image.version="${VERSION}"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    DATA_DIR=/data \
    TZ=Asia/Shanghai

WORKDIR /app

# 先装依赖，利用层缓存
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用代码（模板与静态资源已本地化，运行时无需外网 CDN）
COPY app ./app

# 数据卷：SQLite 数据库与日志
VOLUME ["/data"]
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/healthz').status==200 else 1)" || exit 1

# 单进程运行：调度器与 SQLite 都要求单实例，不要增加 --workers
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips", "*"]
