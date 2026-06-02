"""
Generate alert rules for monitored AsterNOS/Asterfusion switch models.
"""
from __future__ import annotations

import json
from typing import Any, Dict, List

from app.database import SessionLocal
from app.models import AlertHistory, AlertRule


MODELS = ["CX308P-48Y-NF-AC", "CX564P-N", "CX532P-N"]
MERGED_RULE_GENERATOR = "generate_merged_asternos_alert_rules"
LEGACY_RULE_GENERATOR = "generate_model_alert_rules"


def native_rule(
    label: str,
    metric_type: str,
    condition: str,
    threshold: float,
    severity: str,
    description: str,
    duration: int = 60,
    suppress_duration: int = 600,
    enabled: int = 1,
    extra_config: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "label": label,
        "description": description,
        "rule_type": "threshold",
        "metric_type": metric_type,
        "condition": condition,
        "threshold": threshold,
        "duration": duration,
        "severity": severity,
        "suppress_duration": suppress_duration,
        "enabled": enabled,
        "extra_config": extra_config or {},
    }


def exporter_rule(
    label: str,
    metric_base: str,
    condition: str,
    threshold: float,
    severity: str,
    description: str,
    *,
    metric_label: str,
    target_label_keys: List[str] | None = None,
    value_label: str | None = None,
    value_map: Dict[str, float] | None = None,
    label_regex: Dict[str, str] | None = None,
    include_label_regex: str | None = None,
    exclude_label_regex: str | None = None,
    use_delta: bool = False,
    duration: int = 60,
    suppress_duration: int = 600,
    enabled: int = 1,
) -> Dict[str, Any]:
    extra_config: Dict[str, Any] = {
        "metric_base": metric_base,
        "metric_label": metric_label,
        "target_label_keys": target_label_keys or [],
    }
    if value_label:
        extra_config["value_label"] = value_label
    if value_map:
        extra_config["value_map"] = value_map
    if label_regex:
        extra_config["label_regex"] = label_regex
    if include_label_regex:
        extra_config["include_label_regex"] = include_label_regex
    if exclude_label_regex:
        extra_config["exclude_label_regex"] = exclude_label_regex
    if use_delta:
        extra_config["use_delta"] = True
    return native_rule(
        label=label,
        metric_type="exporter_metric",
        condition=condition,
        threshold=threshold,
        severity=severity,
        description=description,
        duration=duration,
        suppress_duration=suppress_duration,
        enabled=enabled,
        extra_config=extra_config,
    )


COMMON_RULES = [
    native_rule("设备不可达", "device_reachability", "<", 1, "P0", "ICMP/SNMP/exporter 可达性失败。"),
    native_rule("CPU使用率高", "snmp_cpu", ">", 0.85, "P1", "设备 CPU 使用率超过 85%。"),
    native_rule("内存使用率高", "snmp_memory", ">", 0.85, "P1", "设备内存使用率超过 85%。"),
    native_rule("系统状态异常", "device_status", "<", 1, "P0", "设备系统状态不是 UP。"),
    native_rule("接口AdminUp但OperDown", "interface_admin_up_oper_down", ">=", 1, "P1", "物理接口管理开启但运行 Down。"),
    native_rule("接口入方向错误", "interface_in_errors_delta", ">", 0, "P2", "接口出现入方向错误计数。"),
    native_rule("接口出方向错误", "interface_out_errors_delta", ">", 0, "P2", "接口出现出方向错误计数。"),
    native_rule("接口入方向丢弃", "interface_in_discards_delta", ">", 0, "P2", "接口出现入方向丢弃计数。"),
    native_rule("接口出方向丢弃", "interface_out_discards_delta", ">", 0, "P2", "接口出现出方向丢弃计数。"),
    native_rule("OSPF邻居Down", "ospf_neighbor_state", "<", 1, "P1", "OSPF 邻居状态非 Full。"),
    native_rule("光模块RX功率过低", "optical_rx_power", "<", -10, "P1", "光模块 RX 功率低于 -10 dBm。"),
    native_rule("光模块RX功率过高", "optical_rx_power", ">", 3, "P1", "光模块 RX 功率高于 3 dBm。"),
    native_rule("光模块TX功率过低", "optical_tx_power", "<", -10, "P1", "光模块 TX 功率低于 -10 dBm。"),
    native_rule("光模块TX功率过高", "optical_tx_power", ">", 3, "P1", "光模块 TX 功率高于 3 dBm。"),
    exporter_rule("设备温度过高", "device_sensor_tempt", ">", 70, "P1", "设备温度传感器超过 70 摄氏度。", metric_label="设备温度", target_label_keys=["name", "sensor"]),
    exporter_rule("风扇未插入", "device_fan_available_status", "<", 1, "P1", "风扇可用状态为未插入。", metric_label="风扇可用状态", target_label_keys=["slot", "name"]),
    exporter_rule("风扇运行异常", "device_fan_operational_status", "<", 1, "P1", "风扇运行状态为 Down。", metric_label="风扇运行状态", target_label_keys=["slot", "name"]),
    exporter_rule("关键进程Down", "device_critical_process", "<", 1, "P1", "关键进程状态不是 Up。", metric_label="关键进程状态", target_label_keys=["docker_name", "process_name"], value_label="process_status"),
    exporter_rule("Docker容器Down", "device_docker_info", "<", 1, "P1", "关键 Docker 容器状态不是 UP。", metric_label="Docker容器状态", target_label_keys=["docker_name"], value_label="status", label_regex={"status": ".+"}),
    exporter_rule("CRM资源使用率高", "crm_resource_percent", ">", 0.8, "P1", "CRM 资源使用率超过 80%。", metric_label="CRM资源使用率", target_label_keys=["resource"]),
    exporter_rule("队列出方向丢包增长高", "queue_egress_dropped_pkts", ">", 1000, "P2", "队列出方向丢包增长超过 1000。", metric_label="队列出方向丢包增长", target_label_keys=["port", "queue"], use_delta=True),
    exporter_rule("队列入方向丢包增长高", "queue_ingress_dropped_pkts", ">", 1000, "P2", "队列入方向丢包增长超过 1000。", metric_label="队列入方向丢包增长", target_label_keys=["port", "queue"], use_delta=True),
    exporter_rule("队列出方向缓存占用高", "queue_egress_buffer_used_bytes", ">", 10_000_000, "P2", "队列出方向 buffer 占用超过 10MB。", metric_label="队列出方向Buffer占用", target_label_keys=["port", "queue"]),
    exporter_rule("队列入方向缓存占用高", "queue_ingress_buffer_used_bytes", ">", 10_000_000, "P2", "队列入方向 buffer 占用超过 10MB。", metric_label="队列入方向Buffer占用", target_label_keys=["port", "queue"]),
    exporter_rule("PFC RX包增长高", "pfc_rx_pkts", ">", 100_000, "P2", "PFC RX 包增长超过 100000。", metric_label="PFC RX包增长", target_label_keys=["port", "prio"], use_delta=True),
    exporter_rule("PFC TX包增长高", "pfc_tx_pkts", ">", 100_000, "P2", "PFC TX 包增长超过 100000。", metric_label="PFC TX包增长", target_label_keys=["port", "prio"], use_delta=True),
    exporter_rule("ECN标记包增长高", "ecn_marked_pkts", ">", 100_000, "P2", "ECN marked 包增长超过 100000。", metric_label="ECN标记包增长", target_label_keys=["port", "queue"], use_delta=True),
    exporter_rule("接口采集器失败", "interface_collector_success", "<", 1, "P1", "接口 exporter collector 失败。", metric_label="接口采集器状态"),
    exporter_rule("设备采集器失败", "device_collector_success", "<", 1, "P1", "设备 exporter collector 失败。", metric_label="设备采集器状态"),
    exporter_rule("队列采集器失败", "queue_collector_success", "<", 1, "P1", "队列 exporter collector 失败。", metric_label="队列采集器状态"),
    exporter_rule("CRM采集器失败", "crm_collector_success", "<", 1, "P1", "CRM exporter collector 失败。", metric_label="CRM采集器状态"),
    exporter_rule("OSPF采集器失败", "ospf_scrape_collector_success", "<", 1, "P1", "OSPF exporter collector 失败。", metric_label="OSPF采集器状态"),
    exporter_rule("PFC采集器失败", "pfc_collector_success", "<", 1, "P1", "PFC exporter collector 失败。", metric_label="PFC采集器状态"),
    exporter_rule("ECN采集器失败", "ecn_collector_success", "<", 1, "P1", "ECN exporter collector 失败。", metric_label="ECN采集器状态"),
    exporter_rule("RoCE采集器失败", "roce_collector_success", "<", 1, "P1", "RoCE exporter collector 失败。", metric_label="RoCE采集器状态"),
]

MODEL_SPECIFIC_RULES = {
    "CX308P-48Y-NF-AC": [
        native_rule("BGP邻居Down", "bgp_peer_state", "<", 1, "P1", "BGP 邻居状态非 Established。"),
        exporter_rule("MCLAG状态异常", "mclag_status_info", "<", 1, "P1", "MCLAG 状态异常。", metric_label="MCLAG状态", target_label_keys=["domain_id", "peer_link"], value_label="operational_status"),
        exporter_rule("SAG状态异常", "sag_operational_status", "<", 1, "P1", "SAG 网关运行状态异常。", metric_label="SAG运行状态", target_label_keys=["interface", "vni"]),
    ],
    "CX532P-N": [
        native_rule("BGP邻居Down", "bgp_peer_state", "<", 1, "P1", "BGP 邻居状态非 Established。"),
        exporter_rule("MCLAG状态异常", "mclag_status_info", "<", 1, "P1", "MCLAG 状态异常。", metric_label="MCLAG状态", target_label_keys=["domain_id", "peer_link"], value_label="operational_status"),
        exporter_rule("SAG状态异常", "sag_operational_status", "<", 1, "P1", "SAG 网关运行状态异常。", metric_label="SAG运行状态", target_label_keys=["interface", "vni"]),
    ],
    "CX564P-N": [
        exporter_rule("BGP采集器失败", "bgp_scrape_collector_success", "<", 1, "P1", "BGP exporter collector 失败。", metric_label="BGP采集器状态"),
        exporter_rule("MCLAG采集器失败", "mclag_scrape_collector_success", "<", 1, "P1", "MCLAG exporter collector 失败。", metric_label="MCLAG采集器状态"),
        exporter_rule("SAG采集器失败", "sag_scrape_collector_success", "<", 1, "P1", "SAG exporter collector 失败。", metric_label="SAG采集器状态"),
    ],
}


def _get_default_notification_channels(db) -> List[Dict[str, Any]]:
    for rule in db.query(AlertRule).order_by(AlertRule.id).all():
        if rule.notification_channels:
            return rule.notification_channels
    return []


def _normalized_extra_config(extra_config: Dict[str, Any] | None) -> Dict[str, Any]:
    normalized = dict(extra_config or {})
    for key in ["generated_by", "model", "models", "mention_users"]:
        normalized.pop(key, None)
    return normalized


def _rule_signature(rule_config: Dict[str, Any]) -> str:
    payload = {
        "metric_type": rule_config["metric_type"],
        "condition": rule_config["condition"],
        "threshold": float(rule_config["threshold"]),
        "severity": rule_config["severity"],
        "extra_config": _normalized_extra_config(rule_config.get("extra_config")),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _db_rule_signature(rule: AlertRule) -> str:
    payload = {
        "metric_type": rule.metric_type,
        "condition": rule.condition,
        "threshold": float(rule.threshold),
        "severity": rule.severity,
        "extra_config": _normalized_extra_config(rule.extra_config),
    }
    return json.dumps(payload, sort_keys=True, ensure_ascii=False)


def _merged_rule_configs() -> List[Dict[str, Any]]:
    merged: Dict[str, Dict[str, Any]] = {}
    for rule_config in COMMON_RULES:
        if rule_config["severity"] in {"P0", "P1", "P2"}:
            merged.setdefault(_rule_signature(rule_config), rule_config)
    for rules in MODEL_SPECIFIC_RULES.values():
        for rule_config in rules:
            if rule_config["severity"] in {"P1", "P2"}:
                merged.setdefault(_rule_signature(rule_config), rule_config)
    return list(merged.values())


def _find_existing_rule(db, rule_config: Dict[str, Any]) -> AlertRule | None:
    by_name = db.query(AlertRule).filter(AlertRule.name == rule_config["label"]).first()
    if by_name:
        return by_name
    expected_signature = _rule_signature(rule_config)
    for rule in db.query(AlertRule).order_by(AlertRule.id).all():
        if _db_rule_signature(rule) == expected_signature:
            return rule
    return None


def upsert_rule(
    db,
    rule_config: Dict[str, Any],
    notification_channels: List[Dict[str, Any]],
) -> AlertRule:
    name = rule_config["label"]
    rule = _find_existing_rule(db, rule_config)
    rule_extra_config = rule_config.get("extra_config") or {}
    channels = [] if rule_extra_config.get("use_delta") else notification_channels
    payload = {
        "description": rule_config["description"],
        "rule_type": rule_config["rule_type"],
        "metric_type": rule_config["metric_type"],
        "condition": rule_config["condition"],
        "threshold": float(rule_config["threshold"]),
        "duration": int(rule_config["duration"]),
        "severity": rule_config["severity"],
        "suppress_duration": int(rule_config["suppress_duration"]),
        "enabled": int(rule_config.get("enabled", 1)),
        "device_group_id": None,
        "device_ids": [],
        "extra_config": {
            **rule_extra_config,
            "generated_by": MERGED_RULE_GENERATOR,
            "models": MODELS,
            "mention_users": [],
        },
        "notification_channels": channels,
    }
    if rule:
        rule.name = name
        for key, value in payload.items():
            setattr(rule, key, value)
        return rule

    rule = AlertRule(name=name, **payload)
    db.add(rule)
    db.flush()
    return rule


def main() -> None:
    db = SessionLocal()
    try:
        created = 0
        updated = 0
        notification_channels = _get_default_notification_channels(db)

        canonical_by_signature: Dict[str, AlertRule] = {}
        for rule_config in _merged_rule_configs():
            existed = _find_existing_rule(db, rule_config) is not None
            rule = upsert_rule(db, rule_config, notification_channels)
            canonical_by_signature[_rule_signature(rule_config)] = rule
            if existed:
                updated += 1
            else:
                created += 1

        db.flush()

        removed = 0
        remapped_histories = 0
        legacy_rules = [
            rule
            for rule in db.query(AlertRule).order_by(AlertRule.id).all()
            if isinstance(rule.extra_config, dict)
            and rule.extra_config.get("generated_by") == LEGACY_RULE_GENERATOR
            and rule.severity in {"P0", "P1", "P2"}
        ]
        for legacy_rule in legacy_rules:
            signature = _db_rule_signature(legacy_rule)
            canonical = canonical_by_signature.get(signature)
            if not canonical or canonical.id == legacy_rule.id:
                continue
            remapped_histories += (
                db.query(AlertHistory)
                .filter(AlertHistory.rule_id == legacy_rule.id)
                .update({AlertHistory.rule_id: canonical.id}, synchronize_session=False)
            )
            db.delete(legacy_rule)
            removed += 1

        db.commit()
        print(
            f"created={created} updated={updated} removed_legacy={removed} "
            f"remapped_histories={remapped_histories} scope=all_monitored_devices"
        )
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
