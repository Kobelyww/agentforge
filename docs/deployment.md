# 部署与运维指南

从裸机到对外服务的完整路径。三条路线按投入排序：Docker（推荐）→ systemd + nginx → 本地开发。

## 1. Docker（推荐）

```bash
cp .env.example .env   # 按需填写 LLM Key / AGENTFORGE_ADMIN_PASSWORD / AGENTFORGE_WEBHOOK_URL
docker compose up --build -d
curl localhost:8000/readyz
```

数据（SQLite + 日志 + 备份）全部落在 `agentforge-data` 卷，升级镜像不丢数据。

## 2. systemd + nginx（裸金属/VM）

```bash
# 1) 安装
sudo useradd -r -m agentforge
sudo mkdir -p /opt/agentforge && sudo chown agentforge /opt/agentforge
sudo -u agentforge git clone https://github.com/Kobelyww/agentforge.git /opt/agentforge/app
cd /opt/agentforge/app
sudo -u agentforge python3 -m venv .venv && sudo -u agentforge .venv/bin/pip install -e .
sudo -u agentforge make build-ui   # 或 npm ci && npm run build

# 2) systemd
sudo cp deploy/systemd/agentforge.service /etc/systemd/system/
sudo systemctl daemon-reload && sudo systemctl enable --now agentforge

# 3) nginx 反代（SSE 必须关闭缓冲）
sudo cp deploy/nginx.conf /etc/nginx/sites-available/agentforge
sudo ln -s /etc/nginx/sites-available/agentforge /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

单文件数据库 → systemd 单元保持**单 worker**；横向扩展的路径是接 Postgres
（`db_url` 已是 SQLAlchemy 抽象）+ 会话粘性，而不是对同一 SQLite 文件加 worker。

## 3. 生产加固清单

| 项 | 做法 |
|---|---|
| 认证 | 设 `AGENTFORGE_ADMIN_PASSWORD`（启用 JWT 登录，PBKDF2 存储）和/或 `AGENTFORGE_API_KEY`；两者可并存 |
| HTTPS | nginx TLS 终结（见 deploy/nginx.conf 注释块），或前置网关 |
| 日志 | 默认开启：`data/logs/agentforge.log` 10MB×5 轮转（JSON 行，接 Loki/ELK 直接 tail） |
| 事件外发 | 设 `AGENTFORGE_WEBHOOK_URL`，工单创建即 POST `{event, work_order}` |
| 限流 | `AGENTFORGE_RATE_LIMIT_RPM`（默认 60/客户端，令牌桶） |
| 备份 | `agentforge backup`（SQLite 在线 backup API + 完整性校验），配合 cron：`0 2 * * * agentforge backup` |
| 体检 | `agentforge doctor`（配置/目录/数据库/供应商/工具/认证模式逐项自检） |
| 升级 | `git pull && pip install -e . && systemctl restart agentforge`——启动时自动执行 schema 迁移 |

## 4. 认证模式

三种模式按配置自动生效，可叠加：

| 配置 | 行为 |
|---|---|
| 均未设置 | 开发模式：`/api/*` 开放（仅限本机/内网） |
| `AGENTFORGE_API_KEY` | 机器调用：请求头 `X-API-Key: …` |
| `AGENTFORGE_ADMIN_PASSWORD` | 人工登录：`POST /api/auth/login` → HS256 JWT（PBKDF2 存储，2 小时 TTL）→ `Authorization: Bearer …` |

```bash
TOKEN=$(curl -s localhost:8000/api/auth/login \
  -H 'Content-Type: application/json' \
  -d '{"username":"admin","password":"…"}' | python3 -c "import json,sys;print(json.load(sys.stdin)['access_token'])")
curl localhost:8000/api/sessions -H "Authorization: Bearer $TOKEN"
```

> 跨重启保持 JWT 有效：显式设置 `AGENTFORGE_AUTH_SECRET`（否则每次启动从密码派生，旧 token 失效）。

## 5. 健康检查与监控

- `GET /healthz` — 存活探针（k8s liveness / LB check）
- `GET /readyz` — 就绪探针：数据库、providers、工具、KB chunk 数
- `GET /metrics` — Prometheus（HTTP/LLM/工具/Agent 迭代全维度）
- `GET /api/sessions/{id}/trace` — 单次任务的完整决策链路

Prometheus 抓取配置示例：

```yaml
scrape_configs:
  - job_name: agentforge
    static_configs:
      - targets: ["forgeops.internal:8000"]
```
