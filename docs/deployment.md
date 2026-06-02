# Network Monitor 部署说明

本文档用于从 GitHub 拉取并部署网络监控系统。项目采用 Docker Compose 编排，包含前端、后端、数据库、时序库、缓存、任务队列、定时任务、流量采集和 TACACS+ 服务。

## 1. 环境要求

推荐部署环境：

- Ubuntu 22.04 或更新版本
- Docker Engine 24+
- Docker Compose v2
- 至少 4 核 CPU、8 GB 内存
- 持久化数据目录建议放在项目目录外，例如 `/opt/network_monitor_data`
- 服务器需要能访问被监控设备的管理网段

需要开放的常用端口：

| 端口 | 协议 | 作用 |
| --- | --- | --- |
| 8080 | TCP | Web 前端 HTTP |
| 8443 | TCP | Web 前端 HTTPS |
| 8000 | TCP | 后端 API |
| 49 | TCP/UDP | TACACS+，由 host network 暴露 |
| 5514 | UDP | Syslog |
| 2055 | UDP | NetFlow |
| 6343 | UDP | sFlow |
| 5432 | TCP | PostgreSQL，可按需关闭外部访问 |
| 6379 | TCP | Redis，可按需关闭外部访问 |
| 8086 | TCP | InfluxDB，可按需关闭外部访问 |
| 15672 | TCP | RabbitMQ 管理界面，可按需关闭外部访问 |

## 2. 获取代码

```bash
cd /opt/AI_python
git clone https://github.com/LvFangzhou/network_monitor.git
cd network_monitor
```

如果服务器已经有旧代码：

```bash
cd /opt/AI_python/network_monitor
git pull
```

## 3. 配置环境变量

复制示例配置：

```bash
cp .env.example .env
```

重点修改以下变量：

```bash
SECRET_KEY=请改成足够随机的字符串
DATA_ROOT=/opt/network_monitor_data
BACKUP_ROOT=/opt/network_monitor_backups
SYSTEM_ALERT_WEBHOOK_URL=
TACACS_WEBHOOK_URL=
```

生产环境建议同步修改以下默认口令或 Token：

```bash
POSTGRES_PASSWORD=
INFLUXDB_TOKEN=
RABBITMQ_PASSWORD=
```

注意：`docker-compose.yml` 中目前也包含部分默认值。正式生产部署时，建议统一改为从 `.env` 注入，避免默认口令长期使用。

## 4. 准备持久化目录

```bash
sudo mkdir -p /opt/network_monitor_data
sudo mkdir -p /opt/network_monitor_backups
sudo chown -R "$USER:$USER" /opt/network_monitor_data /opt/network_monitor_backups
```

项目的重要运行数据会写入：

| 路径 | 作用 |
| --- | --- |
| `${DATA_ROOT}/postgres` | PostgreSQL 数据 |
| `${DATA_ROOT}/influxdb` | InfluxDB 时序数据 |
| `${DATA_ROOT}/redis` | Redis 数据 |
| `${DATA_ROOT}/rabbitmq` | RabbitMQ 数据 |
| `${DATA_ROOT}/tacacs` | TACACS+ 配置和日志 |
| `${BACKUP_ROOT}/influxdb` | InfluxDB 自动备份 |

不要把 `data/`、`.env`、日志、数据库文件提交到 GitHub。

## 5. TACACS+ 操作说明

系统已经内置 TACACS+ 镜像构建文件，不再需要手工上传或导入 `my_build_tacacs.tar`。

Compose 文件中 `tacacs` 服务会从仓库内的 Dockerfile 构建：

```text
tacacs/Dockerfile
```

构建后的镜像名仍为：

```text
my_build_tacacs:latest
```

### 5.1 构建镜像

单独构建 TACACS+ 镜像：

```bash
docker compose build tacacs
```

全量构建系统时也会自动构建 TACACS+ 镜像：

```bash
docker compose up -d --build
```

### 5.2 启动和重启

启动或重建 TACACS+ 容器：

```bash
docker compose up -d tacacs
```

只重启 TACACS+ 容器：

```bash
docker compose restart tacacs
```

Web 前端的 `Tacacs管理 -> 配置管理 -> 重启Tacacs容器` 按钮也会执行容器重启。

### 5.3 配置文件和日志

系统会通过 Web 界面生成并维护 TACACS+ 配置文件。

| 宿主机路径 | 容器路径 | 作用 |
| --- | --- | --- |
| `${DATA_ROOT}/tacacs/tac_plus.cfg` | `/etc/tacacs+/tac_plus.cfg` | TACACS+ 配置文件 |
| `${DATA_ROOT}/tacacs/logs` | `/var/log/tacacs+` | TACACS+ 日志目录 |

默认路径示例：

```text
/opt/network_monitor_data/tacacs/tac_plus.cfg
/opt/network_monitor_data/tacacs/logs/tacacs.log
```

### 5.4 Web 界面操作

进入 `Tacacs管理 -> 配置管理` 后，可以维护：

- 账号、密码、所属组
- 用户组和权限级别
- 允许/拒绝的命令规则
- 机器人通知 Webhook

保存配置后，系统会生成 `tac_plus.cfg`。如果修改了账号、组、Key、命令权限等会影响认证的配置，需要重启 TACACS+ 容器后生效。

进入 `Tacacs管理 -> 操作日志` 后，可以查询：

- 操作时间
- 设备 IP
- 用户
- 命令

### 5.5 交换机侧配置要点

交换机侧需要确认：

- TACACS+ server 地址指向本系统服务器 IP
- TACACS+ key 与 Web 界面生成的配置一致
- 设备到服务器 TCP/UDP 49 端口可达
- 已开启 command authorization / command accounting，命令审计才会有日志

### 5.6 验证命令

查看 TACACS+ 容器状态：

```bash
docker compose ps tacacs
```

查看 TACACS+ 容器日志：

```bash
docker compose logs -f tacacs
```

查看 TACACS+ accounting 日志：

```bash
tail -f /opt/network_monitor_data/tacacs/logs/tacacs.log
```

确认 `tac_plus` 进程：

```bash
docker exec nm-tacacs ps -ef | grep tac_plus
```

确认服务监听 49 端口：

```bash
sudo ss -lntup | grep ':49'
```

## 6. 启动系统

首次启动或代码更新后重建：

```bash
docker compose up -d --build
```

查看容器状态：

```bash
docker compose ps
```

查看日志：

```bash
docker compose logs -f api
docker compose logs -f celery-worker
docker compose logs -f celery-beat
docker compose logs -f frontend
docker compose logs -f tacacs
```

访问系统：

```text
http://服务器IP:8080
```

当前环境示例：

```text
http://172.18.16.92:8080
```

默认管理员账号由系统初始化逻辑创建，当前项目默认是：

```text
用户名：admin
密码：admin123
```

首次部署后建议立即修改管理员密码。

## 7. 初始化和健康检查

后端启动后会自动初始化数据库表和基础数据。可以使用下面命令检查服务健康：

```bash
docker compose ps
docker exec nm-postgres pg_isready -U network_monitor
docker exec nm-redis redis-cli ping
docker exec nm-influxdb influx ping
```

检查 API：

```bash
curl http://127.0.0.1:8000/health
```

如果前端可打开但没有数据，优先检查：

- `nm-api` 是否正常
- `nm-celery-worker` 是否正常执行采集任务
- `nm-influxdb` 是否可写入
- 被监控设备 SNMP、Exporter、sFlow/NetFlow 是否可达

## 8. 常用维护命令

重建并启动：

```bash
docker compose up -d --build
```

只重启后端：

```bash
docker compose restart api celery-worker celery-beat
```

只重启前端：

```bash
docker compose restart frontend
```

重启 TACACS+：

```bash
docker compose restart tacacs
```

停止系统：

```bash
docker compose down
```

注意：不要使用 `docker compose down -v`，否则会删除 Docker volume。当前项目主要使用绑定目录持久化，但仍建议避免带 `-v` 的破坏性操作。

## 9. 数据备份

项目已经包含 `influxdb-backup` 服务，会每天执行 InfluxDB 备份，并按保留天数清理旧备份。

手工备份 PostgreSQL：

```bash
mkdir -p /opt/network_monitor_backups/postgres
docker exec nm-postgres pg_dump -U network_monitor network_monitor > /opt/network_monitor_backups/postgres/network_monitor-$(date +%Y%m%d%H%M%S).sql
```

手工备份 TACACS+ 配置和日志：

```bash
tar czf /opt/network_monitor_backups/tacacs-$(date +%Y%m%d%H%M%S).tar.gz -C /opt/network_monitor_data tacacs
```

建议把 `/opt/network_monitor_backups` 同步到异地存储。

## 10. 更新部署流程

```bash
cd /opt/AI_python/network_monitor
git pull
docker compose up -d --build
docker compose ps
```

如果只修改了文档，不需要重建 Docker。

如果修改了前端、后端、采集任务、告警逻辑或 Docker 配置，必须执行：

```bash
docker compose up -d --build
```

## 11. 重要目录说明

| 目录/文件 | 作用 | 是否建议修改 |
| --- | --- | --- |
| `frontend/` | React 前端 | 可修改 |
| `backend/app/` | FastAPI 后端、采集、告警、任务逻辑 | 可修改 |
| `backend/alembic/` | 数据库迁移 | 谨慎修改 |
| `backend/scripts/` | 初始化和规则生成脚本 | 谨慎修改 |
| `scripts/safe_sync_to_remote.sh` | 安全同步脚本 | 谨慎修改 |
| `docker-compose.yml` | 服务编排 | 谨慎修改 |
| `.env.example` | 环境变量示例 | 可修改 |
| `.env` | 本机真实环境变量 | 不提交 |
| `${DATA_ROOT}` | 运行数据 | 不提交、不随意删除 |
| `${BACKUP_ROOT}` | 备份数据 | 不提交、不随意删除 |

## 12. 故障排查

容器起不来：

```bash
docker compose ps
docker compose logs --tail=200 服务名
```

端口被占用：

```bash
sudo ss -lntup | grep -E '8080|8443|8000|49|2055|6343|5514'
```

SNMP 无数据：

- 确认设备管理 IP 可达
- 确认 Community 或 SNMPv3 参数正确
- 确认设备 ACL 放行服务器 IP
- 确认系统内设备厂商、型号、接口索引识别正常

sFlow/NetFlow 无数据：

- 确认交换机 collector 指向服务器 IP
- 确认 UDP 6343 或 2055 到达服务器
- 使用 `tcpdump` 检查：

```bash
sudo tcpdump -ni any udp port 6343
sudo tcpdump -ni any udp port 2055
```

TACACS+ 不可用：

- 按第 5 节确认镜像、容器、配置文件、key、49 端口和日志。
- 如果前端保存了配置但认证不生效，重启 TACACS+ 容器。
- 如果命令审计没有日志，确认交换机已经开启 command accounting。

```bash
docker compose ps tacacs
docker compose logs -f tacacs
tail -f /opt/network_monitor_data/tacacs/logs/tacacs.log
```

## 13. 安全建议

- 不要把 GitHub Token、Webhook、设备密码写入代码仓库
- `.env`、`data/`、日志和备份目录必须保持在 Git 忽略范围内
- 生产环境请修改默认数据库、InfluxDB、RabbitMQ、管理员账号密码
- 对外只开放必要端口
- 定期验证备份是否可恢复
- GitHub Token 使用后及时撤销或最小化权限
