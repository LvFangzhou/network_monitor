# AsterNOS 与 H3C 交换机监控项对照

生成时间：2026-05-28

## 说明

这份清单用于梳理当前系统里 AsterNOS 已经能采集到的数据含义，并对照 H3C 交换机在 MIB/CHM 文档中可落地的中文监控方向，方便选择后续要展示或告警的监控项。

当前结论分三类：

- **已接入**：系统已经采集、入库或已用于页面/告警。
- **可接入**：H3C MIB/通用 MIB 通常支持，但还需要按设备型号和 `snmpwalk` 验证 OID。
- **待确认**：AsterNOS 已有 exporter 指标，但 H3C CHM 中的精确 OID 需要继续提取 CHM 或现场 walk 后确认。

> 注意：你上传的 H3C CHM 文件当前环境只能解出 CHM 文件本体，暂时没有可用的 CHM 内容解析工具。因此 H3C 中文项以当前系统代码、标准 MIB、H3C 私有 OID 方向和交换机常见 MIB 模块来整理；精确 OID 未验证的地方都明确标了“待确认”。

## 设备基础状态

| AsterNOS 指标 | 当前中文含义 | 值/单位 | 当前系统状态 | H3C 对应中文监控项 | H3C 当前状态 | 建议 |
|---|---|---:|---|---|---|---|
| `AsterNOS_device_info` | 设备基础信息、型号、版本等标签 | 标签 | 已接入 | 设备实体信息、型号、系统描述 | 已接入一部分，来自 `sysDescr`、实体表 | 展示 |
| `AsterNOS_device_system_status` | 设备系统状态 | 0/1 或状态值 | 已接入 | 设备可达性、SNMP 可达性、系统状态 | 已接入可达性 | 告警 |
| `AsterNOS_device_up_time` | 设备运行时长 | 秒 | 可接入 | 系统运行时间 `sysUpTime` | 可接入 | 展示/重启告警 |
| `AsterNOS_device_cpu_usage` | CPU 使用率 | % | 已接入 | CPU 使用率 | 已接入 H3C 私有 OID | 展示/告警 |
| `AsterNOS_device_memory_usage` | 内存使用率 | % | 已接入 | 内存使用率 | 已接入 H3C 私有 OID | 展示/告警 |
| `AsterNOS_device_sensor_tempt` | 设备温度传感器 | 摄氏度 | 已接入 | 温度传感器、实体温度 | 已接入 H3C 私有温度 OID | 展示/告警 |
| `AsterNOS_device_fan_operational_status` | 风扇运行状态 | 0/1 | 已接入告警 | 风扇状态 | H3C 可接入，需确认实体表状态映射 | 告警 |
| `AsterNOS_device_fan_available_status` | 风扇是否在位 | 0/1 | 已接入告警 | 风扇在位状态 | H3C 可接入，需确认实体表状态映射 | 告警 |
| `AsterNOS_psu_power_input` | 电源输入/电源状态 | 状态/功率 | 已接入摘要 | 电源模块状态、输入状态 | H3C 可接入，需确认实体表状态映射 | 告警 |

## 接口与流量

| AsterNOS 指标 | 当前中文含义 | 值/单位 | 当前系统状态 | H3C 对应中文监控项 | H3C 当前状态 | 建议 |
|---|---|---:|---|---|---|---|
| `AsterNOS_interface_info` | 接口基础信息、接口名、描述 | 标签 | 已接入 | 接口名称、接口描述、接口索引 | 已接入，通用 IF-MIB | 展示 |
| `AsterNOS_interface_admin_status` | 接口管理状态 | up/down | 已接入 | 接口管理状态 `ifAdminStatus` | 已接入 | 告警辅助条件 |
| `AsterNOS_interface_operational_status` | 接口物理运行状态 | up/down | 已接入 | 接口运行状态 `ifOperStatus` | 已接入 | 告警 |
| `AsterNOS_interface_speed_bytes` | 接口速率 | bytes/s 或换算速率 | 已接入 | 接口速率 `ifHighSpeed`/`ifSpeed` | 已接入 | 展示/使用率计算 |
| `AsterNOS_interface_receive_bytes_total` | 接口入方向累计字节 | bytes | 已接入 | 入方向累计字节 `ifHCInOctets` | 已接入 | 计算速率 |
| `AsterNOS_interface_transmit_bytes_total` | 接口出方向累计字节 | bytes | 已接入 | 出方向累计字节 `ifHCOutOctets` | 已接入 | 计算速率 |
| `AsterNOS_interface_receive_rate_bps` | 接口入方向速率 | bps | 已接入 | 入方向速率，由 `ifHCInOctets` 差值计算 | 已接入 | 折线图 |
| `AsterNOS_interface_transmit_rate_bps` | 接口出方向速率 | bps | 已接入 | 出方向速率，由 `ifHCOutOctets` 差值计算 | 已接入 | 折线图 |
| `AsterNOS_interface_receive_util` | 入方向带宽使用率 | % | 已接入 | 入方向带宽使用率 | 已接入，按速率/接口带宽计算 | 展示/告警 |
| `AsterNOS_interface_transmit_util` | 出方向带宽使用率 | % | 已接入 | 出方向带宽使用率 | 已接入，按速率/接口带宽计算 | 展示/告警 |
| `AsterNOS_interface_receive_errs_total` | 入方向错误包累计 | 包 | 已接入 | 入方向错误包 `ifInErrors` | 已接入 | 增长告警 |
| `AsterNOS_interface_transmit_errs_total` | 出方向错误包累计 | 包 | 已接入 | 出方向错误包 `ifOutErrors` | 已接入 | 增长告警 |
| `AsterNOS_interface_receive_drops_total` | 入方向丢弃包累计 | 包 | 已接入 | 入方向丢弃包 `ifInDiscards` | 已接入 | 增长告警 |
| `AsterNOS_interface_transmit_drops_total` | 出方向丢弃包累计 | 包 | 已接入 | 出方向丢弃包 `ifOutDiscards` | 已接入 | 增长告警 |
| 无直接 AsterNOS 对照 | 接口出队列长度 | 个 | H3C 侧已可读通用项 | 出方向队列长度 `ifOutQLen` | 可接入，但只是接口汇总队列 | 展示，不建议单独强告警 |

## 光模块与物理层

| AsterNOS 指标 | 当前中文含义 | 值/单位 | 当前系统状态 | H3C 对应中文监控项 | H3C 当前状态 | 建议 |
|---|---|---:|---|---|---|---|
| `AsterNOS_dom_optic_rx_power` | 光模块接收光功率 | dBm 或设备原始单位 | 已接入 | 光模块 RX 接收光功率 | 可接入，H3C 精确 OID 待确认 | 告警 |
| `AsterNOS_dom_optic_tx_power` | 光模块发送光功率 | dBm 或设备原始单位 | 已接入 | 光模块 TX 发送光功率 | 可接入，H3C 精确 OID 待确认 | 告警 |
| `AsterNOS_dom_optic_tempt` | 光模块温度 | 摄氏度 | 已接入 | 光模块温度 | 可接入，H3C 精确 OID 待确认 | 展示/告警 |
| 待扩展 | 光模块电压 | V/mV | 未接入 | 光模块电压 | 待 CHM/OID 确认 | 展示 |
| 待扩展 | 光模块偏置电流 | mA/uA | 未接入 | 光模块偏置电流 | 待 CHM/OID 确认 | 展示/告警 |

## 协议状态

| AsterNOS 指标 | 当前中文含义 | 值/单位 | 当前系统状态 | H3C 对应中文监控项 | H3C 当前状态 | 建议 |
|---|---|---:|---|---|---|---|
| `AsterNOS_bgp_status` | BGP 邻居状态 | established/active/数字状态 | 已接入 | BGP 邻居状态 `bgpPeerState` 和 H3C 私有 BGP 表 | 已接入 | 告警 |
| `AsterNOS_ospf_status` | OSPF 邻居状态 | full/down/数字状态 | 已接入 | OSPF 邻居状态 `ospfNbrState` | 已接入 | 告警 |
| `AsterNOS_mclag_status_info` | MCLAG 状态 | 状态值 | 已接入告警方向 | 链路聚合/多机框聚合状态 | H3C 对应项待确认 | 告警 |
| 待扩展 | BFD 会话状态 | up/down | 部分业务字段存在 | BFD 会话状态 | 待 CHM/OID 确认 | 告警 |
| 待扩展 | LACP 聚合成员状态 | up/down/selected | 未完整接入 | 链路聚合成员状态 | 可接入，OID 待确认 | 告警 |

## 队列、PFC、ECN 与 RoCE

| AsterNOS 指标 | 当前中文含义 | 值/单位 | 当前系统状态 | H3C 对应中文监控项 | H3C 当前状态 | 建议 |
|---|---|---:|---|---|---|---|
| `AsterNOS_queue_egress_dropped_pkts` | 队列出方向丢包累计 | 包 | 已接入队列级图表/告警 | QoS 队列出方向丢包 | 待 CHM/OID 确认 | 告警 |
| `AsterNOS_queue_ingress_dropped_pkts` | 队列入方向丢包累计 | 包 | 已接入队列级图表/告警 | QoS 队列入方向丢包 | 待 CHM/OID 确认 | 告警 |
| `AsterNOS_queue_egress_buffer_used_bytes` | 队列出方向 buffer 占用 | bytes | 已接入告警规则方向 | 出方向队列缓存占用 | 待 CHM/OID 确认 | 展示/告警 |
| `AsterNOS_queue_ingress_buffer_used_bytes` | 队列入方向 buffer 占用 | bytes | 已接入告警规则方向 | 入方向队列缓存占用 | 待 CHM/OID 确认 | 展示/告警 |
| `AsterNOS_pfc_rx_pkts` | PFC 入方向 pause 包累计 | 包 | 已接入队列级图表/告警 | PFC 接收 pause 包 | 待 CHM/OID 确认 | 告警 |
| `AsterNOS_pfc_tx_pkts` | PFC 出方向 pause 包累计 | 包 | 已接入队列级图表/告警 | PFC 发送 pause 包 | 待 CHM/OID 确认 | 告警 |
| `AsterNOS_ecn_marked_pkts` | ECN 标记包累计 | 包 | 已接入队列级图表/告警 | ECN 标记报文数 | 待 CHM/OID 确认 | 告警 |

队列类指标建议使用“增长值”而不是累计值直接告警。例如最近 5 分钟队列丢包增长超过阈值、PFC pause 包快速增长、ECN marked 包快速增长。

## 资源表与芯片资源

| AsterNOS 指标 | 当前中文含义 | 值/单位 | 当前系统状态 | H3C 对应中文监控项 | H3C 当前状态 | 建议 |
|---|---|---:|---|---|---|---|
| `AsterNOS_crm_resource_percent` | CRM/芯片资源使用率 | % 或 0-1 | 已接入告警 | ACL、FDB、路由、邻居等硬件资源使用率 | 待 CHM/OID 确认 | 展示/告警 |
| 待扩展 | MAC 地址表使用量 | 条/% | 未接入 | MAC/FDB 表项使用量 | 待 CHM/OID 确认 | 展示/告警 |
| 待扩展 | ARP/ND 表项使用量 | 条/% | 未接入 | ARP/ND 表项使用量 | 待 CHM/OID 确认 | 展示/告警 |
| 待扩展 | 路由表使用量 | 条/% | 未接入 | IPv4/IPv6 路由表项使用量 | 待 CHM/OID 确认 | 展示/告警 |
| 待扩展 | ACL/TCAM 资源 | 条/% | 未接入 | ACL/TCAM 资源使用量 | 待 CHM/OID 确认 | 展示/告警 |

## Exporter 与采集器健康

| AsterNOS 指标 | 当前中文含义 | 值/单位 | 当前系统状态 | H3C 对应中文监控项 | H3C 当前状态 | 建议 |
|---|---|---:|---|---|---|---|
| `AsterNOS_interface_collector_success` | 接口采集器是否成功 | 0/1 | 已接入告警方向 | SNMP 接口采集成功率 | 系统任务状态可实现 | 告警 |
| `AsterNOS_device_collector_success` | 设备采集器是否成功 | 0/1 | 已接入告警方向 | SNMP 设备基础采集成功率 | 系统任务状态可实现 | 告警 |
| `AsterNOS_queue_collector_success` | 队列采集器是否成功 | 0/1 | 已接入告警方向 | H3C 队列采集成功率 | 待队列 OID 接入后实现 | 告警 |
| `AsterNOS_crm_collector_success` | CRM 采集器是否成功 | 0/1 | 已接入告警方向 | H3C 资源表采集成功率 | 待资源 OID 接入后实现 | 告警 |
| `AsterNOS_bgp_scrape_collector_success` | BGP 采集器是否成功 | 0/1 | 已接入告警方向 | BGP SNMP 采集成功率 | 可实现 | 告警 |
| `AsterNOS_ospf_scrape_collector_success` | OSPF 采集器是否成功 | 0/1 | 已接入告警方向 | OSPF SNMP 采集成功率 | 可实现 | 告警 |
| `AsterNOS_pfc_collector_success` | PFC 采集器是否成功 | 0/1 | 已接入告警方向 | PFC 采集成功率 | 待 H3C OID 接入后实现 | 告警 |
| `AsterNOS_ecn_collector_success` | ECN 采集器是否成功 | 0/1 | 已接入告警方向 | ECN 采集成功率 | 待 H3C OID 接入后实现 | 告警 |
| `AsterNOS_roce_collector_success` | RoCE 采集器是否成功 | 0/1 | 已接入告警方向 | RoCE/PFC/ECN 采集成功率 | 待 H3C OID 接入后实现 | 告警 |
| `AsterNOS_mclag_scrape_collector_success` | MCLAG 采集器是否成功 | 0/1 | 已接入告警方向 | 聚合/M-LAG 采集成功率 | 待 H3C OID 接入后实现 | 告警 |

## 当前最适合作为监控项的清单

### 第一优先级：强烈建议直接展示和告警

| 中文监控项 | AsterNOS | H3C | 告警建议 |
|---|---|---|---|
| 设备不可达/采集失败 | 已接入 | 已接入 | P1 |
| CPU 使用率 | 已接入 | 已接入 | P2/P1，按持续时间判断 |
| 内存使用率 | 已接入 | 已接入 | P2/P1，按持续时间判断 |
| 设备温度 | 已接入 | 已接入 | P1/P2 |
| 风扇异常 | 已接入 | 可接入 | P1 |
| 电源异常 | 已接入 | 可接入 | P1 |
| 接口 Admin Up 但 Oper Down | 已接入 | 已接入 | P1/P2 |
| 接口入/出流量 | 已接入 | 已接入 | 展示为主 |
| 接口带宽使用率 | 已接入 | 已接入 | 超阈值持续告警 |
| 接口错误包增长 | 已接入 | 已接入 | P2 |
| 接口丢弃包增长 | 已接入 | 已接入 | P2 |
| BGP 邻居 Down | 已接入 | 已接入 | P1 |
| OSPF 邻居 Down | 已接入 | 已接入 | P1 |
| 光模块 RX/TX 光功率异常 | 已接入 | 可接入，OID 待确认 | P1/P2 |

### 第二优先级：适合数据中心场景重点补齐

| 中文监控项 | AsterNOS | H3C | 用途 |
|---|---|---|---|
| 队列级丢包增长 | 已接入 | 待 CHM/OID 确认 | 判断拥塞发生在哪个队列 |
| 队列 buffer 占用 | 已接入告警方向 | 待 CHM/OID 确认 | 判断瞬时拥塞和微突发 |
| PFC RX/TX 增长 | 已接入 | 待 CHM/OID 确认 | RoCE/无损网络拥塞定位 |
| ECN 标记增长 | 已接入 | 待 CHM/OID 确认 | 拥塞提前预警 |
| CRM/芯片资源使用率 | 已接入 | 待 CHM/OID 确认 | 防止表项耗尽 |
| BFD 会话状态 | 待扩展 | 待 CHM/OID 确认 | 专线/互联快速检测 |
| LACP 成员状态 | 待扩展 | 可接入 | 聚合链路异常定位 |

## H3C 侧建议继续从 CHM 精确确认的中文项

后续如果要把 H3C 的监控能力做得更完整，建议优先在 CHM 或现场 `snmpwalk` 中确认这些表：

| 中文项 | 需要确认的内容 | 目的 |
|---|---|---|
| 光模块诊断信息 | RX 光功率、TX 光功率、温度、电压、偏置电流、告警阈值 | 光衰和光模块健康告警 |
| 实体传感器表 | 温度传感器、风扇、电源、板卡状态 | 硬件健康告警 |
| QoS 队列统计 | 每接口、每队列的入/出丢包、buffer 占用、队列深度 | 队列级拥塞监控 |
| PFC 统计 | 每接口、每优先级 RX/TX pause 包 | 无损网络监控 |
| ECN 统计 | 每接口、每队列 ECN marked 包 | 拥塞趋势监控 |
| CRM/资源表 | MAC、ARP、ND、路由、ACL、TCAM 等资源使用率 | 芯片资源容量监控 |
| BFD 会话表 | 会话 peer、状态、绑定接口、诊断原因 | 专线探测告警 |
| 聚合状态表 | 聚合组、成员端口、选中状态、LACP 状态 | 聚合链路稳定性监控 |

## 交换机侧人工核对命令参考

这些命令用于在告警或图表异常时人工核对，不同 H3C 版本命令可能略有差异：

```text
display interface brief
display interface <interface>
display counters inbound interface <interface>
display counters outbound interface <interface>
display bgp peer
display ospf peer
display environment
display fan
display power
display transceiver interface <interface>
display transceiver diagnosis interface <interface>
display qos queue-statistics interface <interface>
```

## 建议落地顺序

1. 先稳定现有通用项：CPU、内存、温度、接口状态、流量、使用率、错误包、丢弃包、BGP、OSPF。
2. 再补 H3C 光模块诊断 OID：RX/TX 光功率、温度、电压、偏置电流。
3. 然后补 H3C 队列级 OID：队列丢包、buffer、PFC、ECN。
4. 最后补芯片资源和协议细项：CRM/表项资源、BFD、LACP/M-LAG。

这样做的好处是先覆盖最常见故障，再逐步增强数据中心拥塞和容量类监控。
