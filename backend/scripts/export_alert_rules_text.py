from collections import defaultdict
from datetime import datetime

from app.database import SessionLocal
from app.models import AlertRule, Device


METRIC_GROUP = {
    "device_reachability": "设备可达性",
    "snmp_reachability": "设备可达性",
    "exporter_reachability": "设备可达性",
    "telemetry_reachability": "设备可达性",
    "snmp_cpu": "性能与资源",
    "snmp_memory": "性能与资源",
    "device_status": "性能与资源",
    "exporter_metric": "Asteros Exporter专项",
    "interface_admin_up_oper_down": "接口状态与质量",
    "interface_crc_errors_delta": "接口状态与质量",
    "interface_in_errors_delta": "接口状态与质量",
    "interface_out_errors_delta": "接口状态与质量",
    "interface_in_discards_delta": "接口状态与质量",
    "interface_out_discards_delta": "接口状态与质量",
    "optical_rx_power": "光模块",
    "optical_tx_power": "光模块",
    "bgp_peer_state": "路由协议",
    "ospf_neighbor_state": "路由协议",
    "snmp_trap": "SNMP Trap事件",
    "quality_packet_loss": "质量探测",
}
GROUP_ORDER = [
    "设备可达性",
    "性能与资源",
    "接口状态与质量",
    "光模块",
    "路由协议",
    "Asteros Exporter专项",
    "SNMP Trap事件",
    "质量探测",
    "其他",
]
PERCENT_METRICS = {"snmp_cpu", "snmp_memory", "quality_packet_loss"}
CONDITION_LABELS = {">": "高于", ">=": "达到或高于", "<": "低于", "<=": "达到或低于", "==": "等于", "!=": "不等于"}
SEVERITY_LABELS = {"critical": "P0", "warning": "P1", "info": "P2"}
SOURCE_LABELS = {
    "device_reachability": "Ping",
    "snmp_reachability": "SNMP",
    "exporter_reachability": "Exporter",
    "telemetry_reachability": "Telemetry",
}
METRIC_LABELS = {
    "snmp_cpu": "CPU使用率",
    "snmp_memory": "内存使用率",
    "device_status": "系统状态值",
    "interface_crc_errors_delta": "接口CRC/FCS错包增量",
    "interface_in_errors_delta": "接口入方向错包增量",
    "interface_out_errors_delta": "接口出方向错包增量",
    "interface_in_discards_delta": "接口入方向丢弃包增量",
    "interface_out_discards_delta": "接口出方向丢弃包增量",
    "optical_rx_power": "收光功率",
    "optical_tx_power": "发光功率",
}


def format_duration(value, immediate_text="立即"):
    seconds = max(int(value or 0), 0)
    if not seconds:
        return immediate_text
    if seconds % 86400 == 0:
        return f"{seconds // 86400}天"
    if seconds % 3600 == 0:
        return f"{seconds // 3600}小时"
    if seconds % 60 == 0:
        return f"{seconds // 60}分钟"
    return f"{seconds}秒"


def format_threshold(rule):
    value = float(rule.threshold or 0)
    if rule.metric_type in PERCENT_METRICS:
        if rule.metric_type != "quality_packet_loss" and 0 <= value <= 1:
            value *= 100
        return f"{value:g}%"
    if rule.metric_type in {"optical_rx_power", "optical_tx_power"}:
        return f"{value:g} dBm"
    return f"{value:g}"


def trigger_text(rule):
    metric_type = rule.metric_type
    extra_config = rule.extra_config or {}
    duration = format_duration(rule.duration)
    if metric_type in SOURCE_LABELS:
        return f"{SOURCE_LABELS[metric_type]}连续不可达达到{duration}后触发"
    if metric_type == "interface_admin_up_oper_down":
        return f"接口管理状态为Up、物理状态为Down，持续{duration}后触发（系统实际最低确认时间为2分钟）"
    if metric_type == "bgp_peer_state":
        return f"BGP邻居未处于Established状态，持续{duration}后触发"
    if metric_type == "ospf_neighbor_state":
        return f"OSPF邻居未处于Full状态，持续{duration}后触发"
    if metric_type == "quality_packet_loss":
        target_config = extra_config.get("target_notifications") or {}
        return (
            "某个质量探测对象的5分钟丢包率达到该对象独立设置的阈值，"
            f"并连续达到该对象要求的异常周期后触发；已配置对象数：{len(target_config)}"
        )
    if metric_type == "snmp_trap":
        vendor = extra_config.get("vendor") or "对应厂商"
        category = extra_config.get("trap_category") or "指定类型"
        oid = extra_config.get("trap_oid")
        return f"收到{vendor}设备的{category} Trap后立即生成事件告警" + (f"；匹配OID：{oid}" if oid else "")
    if metric_type == "exporter_metric":
        label = extra_config.get("metric_label") or extra_config.get("metric_base") or "指定Exporter指标"
        condition = CONDITION_LABELS.get(rule.condition, rule.condition)
        return f"Exporter指标“{label}”{condition}{format_threshold(rule)}，持续{duration}后触发"
    label = METRIC_LABELS.get(metric_type, metric_type)
    condition = CONDITION_LABELS.get(rule.condition, rule.condition)
    return f"{label}{condition}{format_threshold(rule)}，持续{duration}后触发"


def scope_text(rule, devices):
    extra_config = rule.extra_config or {}
    vendors = extra_config.get("applicable_vendors") or extra_config.get("vendors") or []
    vendor_text = "、".join(map(str, vendors)) if vendors else "未配置厂商"
    models = extra_config.get("models") or []
    if isinstance(models, str):
        models = [models]
    model_text = f"；型号：{'、'.join(map(str, models))}" if models else ""
    device_ids = rule.device_ids or []
    if device_ids:
        names = []
        for device_id in device_ids:
            device = devices.get(int(device_id))
            names.append(f"{device.name}({device.ip_address})" if device else f"设备ID {device_id}")
        device_text = "；仅限设备：" + "、".join(names)
    else:
        device_text = "；范围：该厂商所有已启用监控的设备"
    filters = []
    if extra_config.get("interface_regex"):
        filters.append(f"接口匹配 {extra_config['interface_regex']}")
    if extra_config.get("exclude_interface_regex"):
        filters.append(f"排除接口 {extra_config['exclude_interface_regex']}")
    if extra_config.get("model_regex"):
        filters.append(f"型号匹配 {extra_config['model_regex']}")
    filter_text = "；筛选：" + "，".join(filters) if filters else ""
    return f"厂商：{vendor_text}{model_text}{device_text}{filter_text}"


def main():
    db = SessionLocal()
    try:
        rules = db.query(AlertRule).order_by(AlertRule.id).all()
        devices = {device.id: device for device in db.query(Device).all()}
        groups = defaultdict(list)
        for rule in rules:
            groups[METRIC_GROUP.get(rule.metric_type, "其他")].append(rule)
        enabled_count = sum(bool(rule.enabled) for rule in rules)

        print("# 当前系统告警规则（人工审阅版）")
        print()
        print(f"> 生成时间：{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}（服务器当前时间）  ")
        print(f"> 规则总数：{len(rules)}；启用：{enabled_count}；停用：{len(rules) - enabled_count}。  ")
        print("> 本文直接读取生产数据库，仅用于人工审阅；机器人Webhook地址未输出。")
        print()
        print("## 阅读说明")
        print()
        print("- “持续时间”表示异常条件至少持续多久后才允许触发；部分规则还要求两个不同时间的独立异常采样。")
        print("- “重复通知”表示故障持续未恢复时，再次推送通知的最短间隔。")
        print("- 停用规则不会参与当前告警计算，但仍保留在系统中。")
        print()
        for group in GROUP_ORDER:
            rules_in_group = groups.get(group, [])
            if not rules_in_group:
                continue
            print(f"## {group}（{len(rules_in_group)}条）")
            print()
            for rule in rules_in_group:
                state = "启用" if rule.enabled else "停用"
                severity = SEVERITY_LABELS.get(rule.severity, rule.severity or "P1")
                print(f"### ID {rule.id} · {rule.name}")
                print()
                print(f"- 状态与级别：**{state} / {severity}**")
                print(f"- 适用范围：{scope_text(rule, devices)}")
                print(f"- 触发条件：{trigger_text(rule)}。")
                print(f"- 重复通知：故障持续时，每{format_duration(rule.suppress_duration or 0, '不限制')}最多重复通知一次。")
                if rule.description:
                    print(f"- 原规则备注：{str(rule.description).replace(chr(10), ' ')}")
                print()
    finally:
        db.close()


if __name__ == "__main__":
    main()
