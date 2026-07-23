# H3C S9867 Telemetry gRPC 与 SNMP 数据能力对照

生成时间：2026-07-20  
适用范围：174 台 H3C S9867-128DH（RoCE Fabric）  
Collector：`192.168.254.175:50051`

## 1. 结论摘要

当前 174 台设备均能通过 Telemetry Dial-out 建立 gRPC 会话并持续上报数据。设备配置中共有约 56 条 Sensor Path，覆盖设备资源、接口、LLDP、BGP、光模块、FEC、PFC、ECN、Buffer、队列、QoS、ARP、路由等数据。

但是，“设备已经配置并通过 gRPC 发送”不等于“平台已经解析并保存”。当前接收器为了保证 174 条公网长连接稳定，只解析并写入缓存的 4 类路径是：

1. `diagnostic/cpuhistory`：CPU 使用率。
2. `diagnostic/memories`：内存使用率。
3. `ifmgr/interfaces`：接口清单、接口名称、速率、Admin/Oper 状态。
4. `device/physicalentities`：型号、序列号、软件版本等实体信息。

`ifmgr/statistics` 已经具备解析接口流量、错误包和丢弃包的代码，但生产环境当前设置为：

```text
TELEMETRY_RECEIVER_WRITE_INTERFACE_HISTORY=false
```

因此不会将 Telemetry 接口历史数据重复写入 InfluxDB，当前接口流量仍以 SNMP 为主。

当前建议的数据源分工是：

| 数据类型 | 推荐主数据源 | 原因 |
|---|---|---|
| 设备存活、运行时间、基础资源、普通接口流量 | SNMP | 当前功能完整、已经稳定运行、厂商通用 |
| 接口名称、状态快速变化 | Telemetry + SNMP兜底 | Telemetry推送更及时，SNMP用于校验和补全 |
| LLDP、BGP、接口IP、描述 | SNMP + CLI | 当前解析和展示逻辑更完整，CLI结果最准确 |
| PFC、ECN、Buffer、Headroom、队列、FEC、Pre-FEC BER | Telemetry | 这是Telemetry相对SNMP最有价值的部分 |
| 配置、命令审计 | CLI、配置备份、Syslog、TACACS | 不属于SNMP或当前Telemetry指标范围 |

## 2. 当前实际解析和缓存情况

对 174 台 S9867 当前 Redis 缓存进行检查：

| Telemetry数据 | 已形成缓存的设备数 | 当前用途 | 注意事项 |
|---|---:|---|---|
| 接口清单与状态 | 174/174 | 设备总览、接口摘要、连接信息基础数据 | 不包含接口IP、MTU和完整LLDP详情 |
| CPU使用率 | 154/174 | 设备资源概览 | 其余设备仍可由SNMP补齐 |
| 内存使用率 | 153/174 | 设备资源概览 | 其余设备仍可由SNMP补齐 |
| 型号、序列号、软件版本 | 150/174 | 系统信息概览 | 部分设备的实体报文尚未形成有效缓存 |
| 运行时间 | 0/174 | 当前不可用 | `physicalentities`没有提供可直接使用的uptime，仍应使用SNMP `sysUpTime` |
| 接口历史流量 | 0/174（Telemetry） | 当前关闭 | 由SNMP写入InfluxDB，避免重复数据 |
| PFC/ECN/Buffer/FEC等RoCE数据 | 0/174（平台解析） | 尚未落库和展示 | 设备已配置相关路径，但接收器尚未增加映射 |

说明：CPU、内存和系统信息未达到 174/174，不代表这些设备 gRPC 未连接，而是对应周期报文没有被当前解析器成功形成缓存，或字段结构与当前解析模板存在差异。

## 3. 设备侧已配置的 Telemetry 数据

以下是当前设备订阅中配置的数据路径。事件型路径只有事件发生时才产生数据；周期型路径按对应采样周期上报。

### 3.1 实时事件型路径

Sensor Group：`sdn_grpc_1_0`

| Sensor Path | 数据含义 | 当前平台处理状态 |
|---|---|---|
| `buffermonitor/portquedropevent` | 端口队列丢包事件 | 已传输，未解析 |
| `buffermonitor/portqueoverrunevent` | 端口队列溢出事件 | 已传输，未解析 |
| `resourcemonitor/resourceevent` | 系统资源异常事件 | 已传输，未解析 |
| `telemetryftrace/genevent` | Telemetry Flow Trace通用事件 | 已传输，未解析 |

### 3.2 每60秒采集

Sensor Group：`sdn_grpc_1_60`

| 类别 | Sensor Path | 数据含义 | 当前平台处理状态 |
|---|---|---|---|
| Buffer | `buffermonitor/commbufferusages` | 公共Buffer使用量 | 未解析 |
| Buffer | `buffermonitor/commheadroomusages` | Headroom使用量 | 未解析 |
| ECN/WRED | `buffermonitor/ecnandwredstatistics` | ECN标记和WRED统计 | 未解析 |
| 丢包 | `buffermonitor/egressdrops` | 出方向丢包 | 未解析 |
| 丢包 | `buffermonitor/ingressdrops` | 入方向丢包 | 未解析 |
| PFC | `buffermonitor/pfcspeeds` | PFC速率/变化情况 | 未解析 |
| PFC | `buffermonitor/pfcstatistics` | PFC统计 | 未解析 |
| 队列 | `buffermonitor/portqueconfigurations` | 端口队列配置 | 未解析 |
| 硬件 | `device/boards` | 板卡信息 | 未解析 |
| 硬件 | `device/extphysicalentities` | 扩展物理实体 | 未解析 |
| 硬件 | `device/physicalentities` | 物理实体、型号、序列号、版本 | **已解析** |
| 资源 | `diagnostic/cpuhistory` | CPU历史/使用率 | **已解析** |
| 资源 | `diagnostic/memories` | 内存使用率 | **已解析** |
| 资源 | `diagnostic/memoryalert` | 内存告警 | 未解析 |
| 接口 | `ifmgr/ethportstatistics` | 以太端口统计 | 未解析 |
| 接口 | `ifmgr/interfaces` | 接口清单、速率、状态 | **已解析** |
| 接口 | `ifmgr/statistics` | 流量、包数、错误、丢弃 | 已有解析代码，但生产落库已关闭 |
| LLDP | `lldp/lldpneighbors` | LLDP邻居 | 未解析 |
| QoS | `mqc/globalcategorypolicyaccount` | 全局分类策略统计 | 未解析 |
| QoS | `mqc/ifcategorypolicyaccount` | 接口分类策略统计 | 未解析 |
| QoS | `mqc/ifpolicyaccount` | 接口QoS策略统计 | 未解析 |
| PFC | `pfc/pfcports/port` | PFC端口状态 | 未解析 |
| PFC | `pfc/pfcports/port/portnodrops/portnodrop` | PFC No-drop优先级 | 未解析 |
| PFC | `pfc/portdeadlocks` | PFC Deadlock | 未解析 |
| 队列 | `qos/interfaces/interface/input/queues/queue/state` | 接口入方向队列状态 | 未解析 |
| 队列 | `qstat/queuestat` | 队列统计 | 未解析 |
| WRED | `wred/dropparameter` | WRED丢弃参数 | 未解析 |
| WRED | `wred/ifapplytable` | 接口WRED应用关系 | 未解析 |
| WRED | `wred/ifqueuewreds/ifqueuewred` | 接口队列WRED状态 | 未解析 |
| WRED | `wred/ifqueuewreds/ifqueuewred/dropparameters/dropparameter` | 接口队列WRED丢弃参数 | 未解析 |
| WRED | `wred/queueparameter` | WRED队列参数 | 未解析 |

### 3.3 每300秒采集

Sensor Group：`sdn_grpc_1_300`

| 类别 | Sensor Path | 数据含义 | 当前平台处理状态 |
|---|---|---|---|
| ACL | `acl/ipv4namedadvancerules` | IPv4高级ACL规则 | 未解析 |
| ARP | `arp/arptable` | ARP表 | 未解析 |
| BGP | `bgp/neighbors` | BGP邻居状态 | 未解析 |
| 光模块 | `components/component/optical-channel/state/esnr` | 光通道ESNR | 未解析 |
| FEC | `components/component/optical-channel/state/pre-fec-ber` | Pre-FEC BER | 未解析 |
| 光模块 | `device/transceivers` | 光模块信息 | 未解析 |
| 光模块 | `device/transceiverschannels` | 光模块通道信息 | 未解析 |
| FEC | `ifmgr/iffecdata` | 接口FEC数据 | 未解析 |
| 聚合 | `lagg/lagggroups` | 聚合组 | 未解析 |
| 聚合 | `lagg/laggmembers` | 聚合成员 | 未解析 |
| QoS | `mqc/rules` | QoS匹配规则 | 未解析 |
| 资源 | `resourcemonitor/monitors` | 资源监控项 | 未解析 |
| 资源 | `resourcemonitor/resources` | 资源状态 | 未解析 |
| 路由 | `route/ipv4routes` | IPv4路由表 | 未解析 |
| 路由 | `route/ipv6routes` | IPv6路由表 | 未解析 |
| FEC | `terminal-device/logical-channels/channel/ethernet/state/pre-fec-ber` | 逻辑通道Pre-FEC BER | 未解析 |

### 3.4 每600秒采集

| Sensor Path | 数据含义 | 当前平台处理状态 |
|---|---|---|
| `ifmgr/ports` | 物理端口属性 | 未解析 |

### 3.5 每1200秒采集

| Sensor Path | 数据含义 | 当前平台处理状态 |
|---|---|---|
| `ipv4address/ipv4addresses` | 接口IPv4地址 | 未解析 |
| `vlan/interfaces` | VLAN与接口关系 | 未解析 |

### 3.6 每3600秒采集

| Sensor Path | 数据含义 | 当前平台处理状态 |
|---|---|---|
| `arp/arptableevent` | ARP表事件 | 未解析 |
| `device/base` | 设备基础信息 | 未解析 |

## 4. Telemetry 与 SNMP 能力对照

| 数据项 | Telemetry设备侧 | 当前Telemetry平台能力 | 当前SNMP/CLI能力 | 推荐方案 |
|---|---|---|---|---|
| gRPC/SNMP可达性 | gRPC长连接状态 | 已记录会话和最后上报 | SNMP `sysUpTime`验证 | 两者分别监控，不互相替代 |
| 设备名称、型号、序列号、版本 | `device/base`、`physicalentities` | 已解析部分字段 | SNMP系统OID已支持 | SNMP主，Telemetry校验 |
| 运行时间 | 未在当前解析路径中获得 | 不可用 | SNMP `1.3.6.1.2.1.1.3.0` 已支持 | SNMP |
| CPU | `diagnostic/cpuhistory` | 已解析最大使用率 | SNMP私有OID已支持 | SNMP主，Telemetry做高频补充 |
| 内存 | `diagnostic/memories` | 已解析最大使用率 | SNMP私有OID已支持 | SNMP主，Telemetry做高频补充 |
| 温度 | 可能包含于物理实体/资源路径 | 尚未解析 | S9867温度OID已适配 | 现阶段SNMP |
| 风扇、电源、模块 | boards/physicalentities/transceivers | 尚未完整解析 | SNMP风扇、电源已支持，模块信息有限 | SNMP基础状态，Telemetry补模块详情 |
| 接口名称、速率、状态 | `ifmgr/interfaces`、`ifmgr/ports` | 接口缓存已覆盖174台 | IF-MIB已支持 | Telemetry快速更新，SNMP校验 |
| 接口描述、MTU | 接口路径可能包含 | 当前没有完整提取 | SNMP + CLI已支持 | SNMP/CLI |
| 接口IP | `ipv4address/ipv4addresses` | 未解析 | SNMP IP-MIB + CLI已支持 | 现阶段SNMP/CLI |
| VLAN/Trunk/PVID | `vlan/interfaces` | 未解析 | 主要依赖SNMP + CLI配置解析 | 现阶段CLI，后续Telemetry补充 |
| 接口流量、PPS、包数 | `ifmgr/statistics` | 有代码但生产落库关闭 | SNMP 64位接口计数器已稳定使用 | 保持SNMP，Telemetry暂不重复写 |
| 接口Errors/Discards | `ifmgr/statistics`、drop路径 | 生产落库关闭 | SNMP IF-MIB已支持 | SNMP基础告警；Telemetry后续做秒级事件 |
| LLDP | `lldp/lldpneighbors` | 未解析 | SNMP + 厂商CLI已适配 | SNMP/CLI为主 |
| BGP邻居 | `bgp/neighbors` | 未解析 | SNMP Context + CLI已支持 | SNMP/CLI为主，Telemetry后续做快速状态变化 |
| LAGG聚合组和成员 | `lagg/lagggroups`、`lagg/laggmembers` | 未解析 | SNMP/CLI可获得部分信息 | Telemetry适合补齐拓扑关系 |
| ARP/路由/ACL全表 | 多条300/3600秒路径 | 未解析 | SNMP/CLI按需查询 | 不建议默认全量长期存储，按需缓存 |
| 光功率 | transceivers相关路径 | 未解析 | SNMP RX/TX功率已支持 | SNMP主 |
| ESNR、Pre-FEC BER、FEC | ESNR、pre-fec-ber、iffecdata | 未解析 | 当前SNMP基本未覆盖 | **优先使用Telemetry** |
| PFC RX/TX、No-drop、Deadlock | pfc与buffermonitor路径 | 未解析 | H3C SNMP当前未形成通用采集 | **优先使用Telemetry** |
| ECN/WRED标记与丢弃 | ecnandwredstatistics、wred路径 | 未解析 | H3C SNMP当前未形成通用采集 | **优先使用Telemetry** |
| Buffer与Headroom | commbufferusages、commheadroomusages | 未解析 | SNMP当前未覆盖 | **优先使用Telemetry** |
| 端口/队列丢包 | portquedropevent、ingressdrops、egressdrops、qstat | 未解析 | SNMP只有接口级Discards，缺少RoCE队列维度 | **优先使用Telemetry** |
| QoS策略命中统计 | mqc相关路径 | 未解析 | SNMP支持有限，CLI可人工查询 | Telemetry适合长期趋势和告警 |

## 5. Telemetry最值得优先落地的指标

### 第一优先级：RoCE链路质量

这些数据是S9867 RoCE网络中最有价值、SNMP最难替代的部分：

1. 每端口、每优先级PFC RX/TX增长。
2. PFC Deadlock事件和持续时间。
3. Buffer与Headroom使用率。
4. 每端口、每队列入/出方向丢包。
5. ECN Marked Packet数量和ECN比例。
6. WRED丢弃统计。
7. FEC Corrected/Uncorrectable错误。
8. Pre-FEC BER和ESNR。

建议落库时统一使用：

```text
measurement: roce_interface_monitoring
tags:
  device_id
  device_name
  interface_name
  priority
  queue
fields:
  pfc_rx_packets
  pfc_tx_packets
  pfc_deadlock
  buffer_usage
  headroom_usage
  ingress_drops
  egress_drops
  ecn_marked_packets
  wred_drops
  fec_corrected_errors
  fec_uncorrectable_errors
  pre_fec_ber
  esnr
```

### 第二优先级：事件型数据

事件型路径不需要高频轮询，数据量通常较小，适合直接形成告警：

- 队列丢包事件。
- 队列溢出事件。
- 资源异常事件。
- PFC Deadlock事件。
- ARP变化事件。

### 第三优先级：拓扑和协议补充

- LLDP邻居。
- BGP邻居状态。
- LAGG组和成员。
- 接口IP和VLAN关系。

这些数据可以降低用户打开设备详情时实时执行CLI的等待时间，但应继续保留SNMP/CLI兜底。

### 暂不建议优先落地

- 全量IPv4/IPv6路由表。
- 全量ARP表周期快照。
- 全量ACL规则。
- 与SNMP完全重复的60秒接口历史流量。

这些数据基数大，会显著增加解析CPU、InfluxDB写入量和存储占用；除非有明确查询场景，否则建议只保留最新快照或按需采集。

## 6. 建议的数据源优先级

### 普通网络监控

```text
SNMP → 主数据源
CLI  → 详情校验和补充
Telemetry → 快速状态变化和新增专项指标
```

### S9867 RoCE Fabric

```text
Telemetry → PFC / ECN / Buffer / Headroom / Queue / FEC / BER 主数据源
SNMP      → 存活、Uptime、CPU、内存、温度、接口基础流量
CLI       → 配置、LLDP/BGP校验、故障排查
```

## 7. 落地顺序建议

1. 先解析 `pfcstatistics`、`portdeadlocks`、`commbufferusages`、`commheadroomusages`。
2. 再解析 `ecnandwredstatistics`、`ingressdrops`、`egressdrops`、`qstat/queuestat`。
3. 再解析 `iffecdata`、`pre-fec-ber`、`esnr`。
4. 为这些指标建立Redis最新值缓存和InfluxDB历史表。
5. 增加按设备、接口、优先级、队列筛选的趋势图。
6. 数据稳定后再建立PFC、ECN、Headroom、FEC和队列丢包告警。
7. 最后再考虑LLDP、BGP、LAGG、IP和VLAN等重复能力迁移。

## 8. 风险与注意事项

1. 当前 `RECORD_MESSAGES=false`、`RECORD_PAYLOADS=false`，平台不会长期保存每条原始Telemetry报文；这有利于控制磁盘使用，但开发新解析器时需要针对少量设备临时抓取样本。
2. 不应一次启用全部56条路径的解析和落库。应该按类别逐批启用，并观察CPU、队列、InfluxDB写入和磁盘增长。
3. 设备到Collector经过公网，偶尔存在约9至10秒重连。事件型数据在重连期间可能丢失，因此关键告警应结合双Collector或SNMP兜底。
4. CPU、内存、接口状态等重复指标必须标记 `source`，避免Telemetry与SNMP互相覆盖造成页面数据跳变。
5. 路由、ARP、ACL等高基数数据应使用“最新快照 + 较短保留期”，不要按照普通时序指标无限保存。

## 9. 最终建议

当前不建议用Telemetry完全替换SNMP。最合理的方案是：

- 保留SNMP作为174台设备的基础监控主数据源。
- 保留CLI作为配置、LLDP、BGP和故障排查的准确数据源。
- 将Telemetry集中用于RoCE网络专项可观测性，优先补齐PFC、ECN、Buffer、Headroom、队列、FEC和Pre-FEC BER。

这样既能利用Telemetry的高价值数据，也不会重复写入大量SNMP已经稳定提供的普通接口数据。
