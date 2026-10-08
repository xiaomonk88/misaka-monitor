# Misaka 监控 · 部署指南

## 一键安装（推荐）

```bash
curl -fsSL https://raw.githubusercontent.com/xiaomonk88/misaka-monitor/main/install.sh | bash
```

脚本会：

1. 检查 Docker 与 compose 插件（未安装时询问是否通过 get.docker.com 安装）
2. 创建 `/opt/misaka-monitor`，下载 `docker-compose.yml`
3. 首次安装时生成 `.env`（随机 `APP_SECRET_KEY`，权限 600）；再次运行保留现有 `.env`
4. 拉取 `ghcr.io/xiaomonk88/misaka-monitor` 镜像；拉取失败时自动下载源码本地构建
5. 启动容器并等待 `/healthz` 通过，打印访问地址

首次访问 `http://服务器IP:8000`，用 `admin / admin` 登录，按提示设置新密码后进入系统。

## 目录结构（服务器）

```
/opt/misaka-monitor/
├─ docker-compose.yml   # 由安装脚本下载，升级时自动更新
├─ .env                 # 配置与会话密钥（升级时保留）
└─ data/                # SQLite 数据库与文件日志（升级时保留）
```

## 升级与版本

- 升级：重新运行安装命令，或 `docker compose pull && docker compose up -d`
- 锁定版本：在 `.env` 中设置 `MISAKA_VERSION=1.0.0`，再执行上面的命令
- 回滚：把 `MISAKA_VERSION` 改回旧版本号后 `docker compose up -d`
- 查看当前版本：网页左下角，或 `curl http://127.0.0.1:8000/healthz`

数据库结构变化由程序启动时自动迁移（只增加列，不删除数据）。升级前建议先备份 `data/`。

## 配置项（.env）

| 变量 | 默认 | 说明 |
| --- | --- | --- |
| `APP_SECRET_KEY` | 安装时随机生成 | 会话签名密钥，修改后所有人需重新登录 |
| `MISAKA_PORT` | `8000` | 宿主机端口 |
| `MISAKA_VERSION` | `latest` | 镜像版本 |
| `DISPLAY_TZ` | `Asia/Shanghai` | 初始展示时区（之后以后台设置为准） |
| `CATALOG_SYNC_HOURS` | `24` | 初始目录同步周期（之后以后台设置为准） |
| `MIN_INTERVAL_SECONDS` | `15` | 初始最低轮询间隔（之后以后台设置为准） |
| `MISAKA_API_BASE` | `https://app.misaka.io/api` | 一般无需修改 |

修改 `.env` 后执行 `docker compose up -d` 生效。

## 迁移已有数据

把旧环境的 `data/misaka.db` 复制到新服务器的 `/opt/misaka-monitor/data/`，然后 `docker compose restart`。数据库包含账号密码、Telegram Token、任务与事件历史。

## 忘记密码

```bash
cd /opt/misaka-monitor
docker compose exec misaka-monitor python -m app.reset_admin
```

账号恢复为 `admin / admin`，下次登录强制设置新密码。

## 安全建议

- 防火墙只对自己的 IP 放行端口，或前置 Nginx / Caddy 反代 + HTTPS（镜像已开启 `--proxy-headers`）
- `.env` 与 `data/` 含密钥与 Token，勿公开
- 只运行一个实例：调度器与 SQLite 不支持多副本或 `--workers`

## 不用安装脚本

```bash
mkdir -p /opt/misaka-monitor && cd /opt/misaka-monitor
curl -fsSLO https://raw.githubusercontent.com/xiaomonk88/misaka-monitor/main/docker-compose.yml
curl -fsSL https://raw.githubusercontent.com/xiaomonk88/misaka-monitor/main/.env.example -o .env
sed -i "s|^APP_SECRET_KEY=.*|APP_SECRET_KEY=$(openssl rand -hex 32)|" .env
docker compose up -d
```
