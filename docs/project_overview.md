# 网络设备监控系统项目说明

## 项目定位

这是一个面向 IDC / 网络运维场景的网络设备监控与资源管理系统，核心目标是把网络设备、端口流量、BGP/OSPF 状态、告警、客户资源、专线资源、IP 流量分析、Tacacs 认证审计集中到一个 Web 系统里统一管理。

系统部署在 Ubuntu 服务器 `/opt/AI_python/network_monitor`，通过 Docker Compose 运行各个组件。

## 核心功能

### 1. 仪表盘

用于展示系统整体状态，包括设备数量、告警情况、资源状态、监控概览等，是进入系统后的总览入口。

### 2. 网络设备管理

支持维护网络设备资产信息，包括：

- 设备名称、管理 IP、厂商、型号、角色、机房
- SNMP / gNMI / Exporter 采集信息
- 在线状态、CPU、内存、温度
- BGP / OSPF 邻居状态
- 设备详情查看
- 设备筛选、排序、导入导出
- 单设备手动刷新采集状态

### 3. 资源管理

包含 IDC 网络资源相关的管理能力：

- 客户管理
- 供应商管理
- 机房管理
- 公网管理
- 专线管理
- IPDB 管理
- 客户公网 IP、内网网段、带宽、链路归属维护
- 专线主备端口、VLAN、本端/对端地址、路由协议、探测方式管理

### 4. 监控中心

监控中心包含设备总览、端口查询、IP 流量查询等模块。

#### 设备总览

展示设备运行状态：

- 连通性
- SNMP / Exporter 可达状态
- CPU
- 内存
- 温度
- BGP / OSPF 邻居数量
- 最近更新时间

#### 端口查询

用于查看交换机端口出入向流量趋势：

- 基于 SNMP / Exporter 采集端口计数器
- 存入 InfluxDB 时序数据库
- 支持过去 10 分钟、30 分钟、1 小时、6 小时、24 小时、3 天、7 天等范围
- 自动按 bps / Kbps / Mbps / Gbps 显示
- 支持折线图、缩放、重置缩放
- 根据时间范围调整展示颗粒度

#### IP 流量查询

用于查看具体公网 IP 的流量：

- 基于 sFlow / NetFlow 数据
- 支持输入任意 IP 查询该 IP 的入向 / 出向流量
- 支持接口维度的 sFlow Top IP 分析
- 可查看某接口上带宽使用最高的 IP 排名
- 适合客户公网 IP 用量分析

### 5. 告警管理

告警模块包括：

- 告警规则
- 告警历史
- 告警屏蔽

支持的告警类型包括：

- 设备 Down
- 接口 Down
- BGP 邻居 Down
- OSPF 邻居异常
- 端口流量异常
- Syslog 告警
- 其他采集指标异常

告警历史支持：

- 确认
- 忽略
- 解决
- 筛选
- 重置筛选
- 告警状态查看

告警屏蔽支持：

- 按 IP、接口、规则、消息等字段匹配
- 正则匹配
- 失效时间
- 启用 / 停用
- 命中告警统计
- 查看命中的告警明细

### 6. Tacacs 管理

系统集成了 Tacacs+ 服务，主要用于网络设备登录认证和命令审计。

包含：

- Tacacs 配置管理
- 用户账号管理
- 用户组管理
- 命令权限配置
- Tacacs 容器重启
- Tacacs 操作日志查看
- 操作日志筛选
- 机器人通知配置
- 飞书、企业微信、钉钉 Webhook 通知

Tacacs 日志会解析为类似表格的数据：

| 时间 | 设备 IP | 用户 | 命令 |
|---|---|---|---|
| 2026-05-21 17:33:07 | 10.242.2.26 | lvfz | display current-configuration |

### 7. 系统设置与权限

系统支持用户与权限控制：

- 登录认证
- JWT Token
- 7 天登录保持
- 管理员账号
- 只读账号
- 菜单权限控制
- 账号最后登录时间
- 在线 / 离线状态
- 敏感信息隐藏
- 只读账号隐藏编辑按钮

## 系统组件

### Docker 服务

| 组件 | 容器名 | 作用 |
|---|---|---|
| PostgreSQL | `nm-postgres` | 关系型数据库，保存设备、资源、用户、告警、配置等业务数据 |
| InfluxDB | `nm-influxdb` | 时序数据库，保存端口流量、IP 流量等监控数据 |
| InfluxDB Backup | `nm-influxdb-backup` | 定期备份 InfluxDB 数据 |
| Redis | `nm-redis` | 缓存、会话、限流、在线状态等 |
| RabbitMQ | `nm-rabbitmq` | Celery 任务队列 |
| FastAPI API | `nm-api` | 后端 API 服务 |
| Celery Beat | `nm-celery-beat` | 定时任务调度器 |
| Celery Worker | `nm-celery-worker` | 后台采集、告警、通知任务执行器 |
| Frontend | `nm-frontend` | 前端 Web 页面 |
| Tacacs | `nm-tacacs` | Tacacs+ 认证与命令审计服务 |

## 使用技术

### 前端

- React 18
- TypeScript
- Vite
- Ant Design 5
- React Router
- Axios
- Zustand
- Recharts
- Dayjs
- React Query

### 后端

- Python
- FastAPI
- SQLAlchemy
- Alembic
- Pydantic
- Celery
- Redis
- RabbitMQ
- Uvicorn

### 监控采集

- SNMP
- gNMI
- Asteros Exporter
- sFlow
- NetFlow
- Syslog
- Ping 探测

### 数据存储

- PostgreSQL：业务数据
- InfluxDB：时序监控数据
- Redis：缓存与状态
- 文件目录：Tacacs 配置、Tacacs 日志、备份数据

### 通知能力

- 飞书机器人
- 企业微信机器人
- 钉钉机器人
- Webhook 通知

## 数据流说明

### 端口流量采集链路

```text
交换机端口计数器
    ↓
SNMP / Exporter 采集
    ↓
Celery Worker 后台任务
    ↓
InfluxDB 时序数据库
    ↓
FastAPI 查询接口
    ↓
前端折线图展示
```

### IP 流量采集链路

```text
交换机 sFlow / NetFlow
    ↓
服务器 UDP 端口接收
    ↓
Flow Listener 解析
    ↓
InfluxDB 写入 IP 流量数据
    ↓
IP 流量查询 / 接口分析器展示
```

### 告警链路

```text
设备指标 / 协议状态 / Syslog / 流量数据
    ↓
告警规则判断
    ↓
生成告警历史
    ↓
屏蔽规则匹配
    ↓
Webhook / 机器人通知
```

### Tacacs 审计链路

```text
交换机登录 / 命令执行
    ↓
Tacacs+ 服务认证与审计
    ↓
tacacs.log
    ↓
系统解析日志
    ↓
操作日志页面展示
    ↓
机器人通知
```

## 主要目录

```text
network_monitor/
├── backend/                 # FastAPI 后端
│   └── app/
│       ├── routers/         # API 路由
│       ├── models/          # 数据库模型
│       ├── schemas/         # Pydantic 数据结构
│       ├── collectors/      # SNMP / gNMI / Ping 采集
│       ├── services/        # Syslog / Flow 服务
│       ├── tasks/           # Celery 定时任务
│       ├── utils/           # 工具函数
│       └── websocket/       # WebSocket 管理
├── frontend/                # React 前端
│   └── src/
│       ├── pages/           # 页面
│       ├── api/             # API 请求封装
│       ├── components/      # 公共组件
│       └── store/           # 状态管理
├── data/                    # 持久化数据目录
│   ├── postgres/            # PostgreSQL 数据
│   ├── influxdb/            # InfluxDB 数据
│   ├── redis/               # Redis 数据
│   ├── rabbitmq/            # RabbitMQ 数据
│   ├── tacacs/              # Tacacs 配置与日志
│   └── backups/             # 备份数据
├── scripts/                 # 运维脚本
└── docker-compose.yml       # Docker 编排文件
```

## 主要端口

| 端口 | 协议 | 作用 |
|---|---|---|
| 8080 | HTTP | 前端访问 |
| 8443 | HTTPS | 前端 HTTPS |
| 8000 | HTTP | 后端 API |
| 8086 | HTTP | InfluxDB |
| 5432 | TCP | PostgreSQL |
| 6379 | TCP | Redis |
| 5672 | TCP | RabbitMQ |
| 15672 | HTTP | RabbitMQ 管理页面 |
| 5514 | UDP | Syslog 接收 |
| 2055 | UDP | NetFlow 接收 |
| 6343 | UDP | sFlow 接收 |

## 项目特点

- 偏向真实网络运维场景，不只是简单设备列表。
- 已经集成 SNMP、Exporter、sFlow、NetFlow、Syslog、Tacacs 多种网络运维数据源。
- 前端已经支持明暗主题。
- 端口流量、IP 流量使用时序数据库保存。
- 告警支持规则、历史、屏蔽、通知闭环。
- Tacacs 已经不是独立工具，而是融合进系统的认证审计模块。
- Docker Compose 一键运行，适合单机部署。
