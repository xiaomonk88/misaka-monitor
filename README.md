# Misaka 监控

自托管的 [Misaka](https://app.misaka.io) IaaS 补货监控后台：定时比对全站区域与套餐，发现补货、售罄、价格 / 规格变化和上下架时，通过 Telegram 推送通知。

![version](https://img.shields.io/github/v/tag/xiaomonk88/misaka-monitor?label=version)
![docker](https://img.shields.io/github/actions/workflow/status/xiaomonk88/misaka-monitor/docker.yml?label=docker)

## 功能

- **产品目录**：全站区域 / 套餐 / 价格 / 流量 / 实时库存，跨区域勾选套餐一键创建监控
- **监控任务**：单个套餐、整个区域或全站上新；独立轮询周期与订阅事件；一键探测并展示结果表
- **Telegram 通知**：补货、售罄、价格与规格变化、上下架；监控目标下架 / 恢复自动提醒
- **事件日志**：监控事件与同步日志分开记录，按分类、事件类型、产品、任务筛选，产品时间线，CSV 导出，一键清空
- **安全**：默认账号 `admin / admin`，首次登录强制修改密码；单文件 SQLite，数据全部在本地

## 一键安装（Docker）

在 Linux 服务器上执行（需 root 或 sudo；未安装 Docker 时脚本会询问是否自动安装）：

```bash
curl -fsSL https://raw.githubusercontent.com/xiaomonk88/misaka-monitor/main/install.sh | bash
```

完成后访问 `http://服务器IP:8000`，用 **`admin / admin`** 登录并设置新密码。

可选参数写在 `bash` 前，例如改端口：

```bash
curl -fsSL https://raw.githubusercontent.com/xiaomonk88/misaka-monitor/main/install.sh | MISAKA_PORT=9000 bash
```

| 变量 | 默认值 | 说明 |
| --- | --- | --- |
| `MISAKA_DIR` | `/opt/misaka-monitor` | 安装目录（`.env` 配置与 `data/` 数据都在这里） |
| `MISAKA_PORT` | `8000` | 对外访问端口 |
| `MISAKA_VERSION` | `latest` | 镜像版本，可锁定为如 `1.0.0` |

## 升级

重新运行一键安装命令即可：保留 `.env` 与 `data/`，拉取最新镜像后重启。也可以手动：

```bash
cd /opt/misaka-monitor
docker compose pull && docker compose up -d
```

当前版本显示在网页左下角，或访问 `/healthz`。更新内容见 [CHANGELOG.md](CHANGELOG.md)。

## 常用命令

```bash
cd /opt/misaka-monitor
docker compose logs -f                                         # 实时日志
docker compose restart                                         # 重启
docker compose down                                            # 停止（数据保留在 ./data）
docker compose exec misaka-monitor python -m app.reset_admin   # 忘记密码：重置为 admin / admin
```

## 首次使用

1. **通知渠道**：添加 Telegram Bot Token 与 Chat ID，点「测试」确认能收到消息
2. **产品目录**：点「同步目录」拉取全站数据（约 20 秒，首次同步只建立比对基线）
3. 在目录中勾选套餐（可跨区域多选）→ 创建监控任务

## 数据与备份

- 全部数据（SQLite 数据库 + 文件日志）在安装目录的 `data/`，备份即复制该目录
- `.env` 与 `data/` 含会话密钥和 Telegram Token，请勿公开
- 建议防火墙只对自己的 IP 放行端口，或前置 Nginx / Caddy 反向代理并启用 HTTPS

## 从源码运行

```bash
git clone https://github.com/xiaomonk88/misaka-monitor.git && cd misaka-monitor
cp .env.example .env            # 修改 APP_SECRET_KEY

# Docker 本地构建
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build

# 或直接用 Python 3.12 运行
python -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/uvicorn app.main:app --port 8000
```

> 调度器与 SQLite 要求单进程单实例运行，不要使用 `--workers` 或启动多个副本。

## 版本发布

版本号只在 [`app/__init__.py`](app/__init__.py) 中维护。发布新版本：

1. 修改 `__version__`，并在 `CHANGELOG.md` 记录变更
2. 提交后打同名标签并推送：`git tag v1.0.1 && git push origin main v1.0.1`
3. GitHub Actions 自动构建 `amd64 / arm64` 镜像并发布到 `ghcr.io/xiaomonk88/misaka-monitor`（`1.0.1`、`1.0`、`latest`）

推送 `main` 分支会发布 `edge` 开发版镜像，不影响 `latest`。

更多架构与开发细节见 [DEVELOPMENT.md](DEVELOPMENT.md)，部署细节见 [DEPLOY.md](DEPLOY.md)。
