# 并行网络运营平台

并行网络运营平台是一个面向 IDC / 网络运维场景的网络设备监控与资源管理系统，包含 CMDB、设备监控、端口流量、IP 流量分析、告警管理、告警屏蔽、TACACS+ 认证审计等能力。

## 核心能力

- 网络设备、客户、供应商、机房、公网、专线、IPDB 等资源管理
- SNMP、gNMI、Asteros Exporter、sFlow、NetFlow、Syslog、Ping 等采集能力
- 设备总览、端口流量查询、IP 流量查询、sFlow 接口 Top IP 分析
- 告警规则、告警历史、告警屏蔽、机器人通知
- TACACS+ 可视化配置、账号/组/命令权限管理、操作日志审计
- 用户权限、只读账号、菜单权限、敏感信息隐藏

## 技术栈

- 前端：React 18、TypeScript、Vite、Ant Design、Recharts、Zustand
- 后端：FastAPI、SQLAlchemy、Alembic、Celery、Pydantic
- 存储：PostgreSQL、InfluxDB、Redis、RabbitMQ
- 部署：Docker Compose

## 快速启动

```bash
git clone https://github.com/LvFangzhou/network_monitor.git
cd network_monitor
cp .env.example .env
docker compose up -d --build
```

访问：

```text
http://服务器IP:8080
```

默认管理员账号：

```text
用户名：admin
密码：admin123
```

首次部署后请立即修改管理员密码，并修改 `.env` 和 `docker-compose.yml` 中的默认密钥、数据库密码、Token 等配置。

## 文档

- [项目说明](docs/project_overview.md)
- [部署说明](docs/deployment.md)
- [AsterNOS 与 H3C 监控项参考](docs/AsterNOS_H3C_monitoring_items.md)

## 重要提醒

- 不要提交 `.env`、`data/`、日志、数据库文件、备份文件、证书私钥。
- 生产环境建议将 `DATA_ROOT` 和 `BACKUP_ROOT` 放在项目目录外。
- 更新前端、后端、采集任务、告警逻辑或 Docker 配置后，需要执行 `docker compose up -d --build`。
- 文档变更不需要重建 Docker。
