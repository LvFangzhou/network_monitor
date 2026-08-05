"""
Syslog 事件辅助告警引擎。

周期 SNMP/Telemetry 更适合判断持续状态，但接口瞬断、BGP 邻居短时抖动
经常在两个采样点之间完成恢复。这里把已经入库的设备 Syslog 转成事件型告警，
用于补齐这类“短窗口事件”。
"""
from __future__ import annotations

import re
import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.config import settings
from app.core import get_logger
from app.models import AlertHistory, AlertRule, Device, SyslogEvent
from app.tasks.alert_tasks import (
    _ensure_alarm_id,
    _is_silenced,
    enqueue_alert_notification,
    reset_optical_interface_baselines,
)
from app.tasks.system_tasks import _detect_webhook_provider
from app.utils import redis_client

logger = get_logger(__name__)

ACTIVE_ALERT_STATUSES = ["firing", "acknowledged", "ignored", "snoozed"]
SYSLOG_ALERT_RULE_DEFINITIONS: List[Dict[str, Any]] = [
    {
        "name": "【H3C】Syslog接口物理Down/瞬断",
        "metric_type": "syslog_interface_phy_down",
        "severity": "P2",
        "description": "由 H3C 设备 Syslog 物理接口状态变化自动生成，用于补齐周期采集无法捕获的短时接口瞬断。",
        "category": "interface_phy",
    },
    {
        "name": "【H3C】Syslog BGP邻居状态变化",
        "metric_type": "syslog_bgp_state_change",
        "severity": "P1",
        "description": "由 H3C 设备 Syslog BGP 邻居状态变化自动生成，用于补齐周期采集无法捕获的短时邻居中断。",
        "category": "bgp_neighbor",
    },
    {
        "name": "【H3C】Syslog BFD会话状态变化",
        "metric_type": "syslog_bfd_state_change",
        "severity": "P1",
        "description": "由 H3C 设备 Syslog BFD 会话状态变化自动生成，用于捕获周期采集可能遗漏的短时 BFD down/up。",
        "category": "bfd_session",
    },
    {
        "name": "【H3C】Syslog光模块拔插/异常",
        "metric_type": "syslog_optical_module_event",
        "severity": "P2",
        "description": "由 H3C 设备 Syslog 光模块拔插或异常事件自动生成。",
        "category": "optical_module",
    },
    {
        "name": "【H3C】Syslog光模块收光功率突变",
        "metric_type": "syslog_optical_rx_power_change",
        "severity": "P2",
        "description": "由 H3C 设备 Syslog 光模块收光功率突变事件自动生成；恢复 Syslog 优先，周期光功率采集连续正常时兜底恢复。",
        "category": "optical_rx_power_change",
    },
    {
        "name": "【H3C】Syslog电源模块异常",
        "metric_type": "syslog_power_event",
        "severity": "P0",
        "description": "由 H3C 设备 Syslog 电源模块异常/恢复事件自动生成。",
        "category": "power",
    },
    {
        "name": "【H3C】Syslog风扇异常",
        "metric_type": "syslog_fan_event",
        "severity": "P0",
        "description": "由 H3C 设备 Syslog 风扇异常/恢复事件自动生成。",
        "category": "fan",
    },
    {
        "name": "【H3C】Syslog温度异常",
        "metric_type": "syslog_temperature_event",
        "severity": "P1",
        "description": "由 H3C 设备 Syslog 温度异常/恢复事件自动生成。",
        "category": "temperature",
    },
    {
        "name": "【H3C】Syslog设备主动严重异常",
        "metric_type": "syslog_device_critical_event",
        "severity": "P1",
        "description": "由设备主动上报的 emergency/alert/critical 级别 Syslog 自动生成。",
        "category": "device_exception",
    },
]

PHY_UPDOWN_RE = re.compile(
    r"PHY_UPDOWN:\s*Physical state on the interface\s+(?P<interface>.+?)\s+changed to\s+(?P<state>down|up)\b",
    re.IGNORECASE,
)
LINK_UPDOWN_RE = re.compile(
    r"LINK_UPDOWN:\s*Line protocol state on the interface\s+(?P<interface>.+?)\s+changed to\s+(?P<state>down|up)\b",
    re.IGNORECASE,
)
INTERFACE_DOWN_RE = re.compile(
    r"INTERFACE_DOWN:\s*(?P<interface>\S+)\s+went down\.\s*Reason:\s*(?P<reason>.*)$",
    re.IGNORECASE,
)
BGP_STATE_RE = re.compile(
    r"BGP_STATE_CHANGED_REASON:\s*BGP\s*(?P<context>.*?):\s*"
    r"(?P<peer>[0-9A-Fa-f:.]+)\s+state has changed from\s+(?P<from_state>\S+)\s+to\s+(?P<to_state>\S+)\."
    r"(?:\s*\(Reason:\s*(?P<reason>.*)\))?",
    re.IGNORECASE,
)
IP_RE = re.compile(r"\b(?:\d{1,3}\.){3}\d{1,3}\b")
INTERFACE_NAME_RE = re.compile(
    r"\b(?:FourHundredGigE|HundredGigE|Ten-GigabitEthernet|FortyGigE|GigabitEthernet|M-GigabitEthernet|WGE|Eth-Trunk|Bridge-Aggregation)\s*\d+(?:/\d+){1,3}\b",
    re.IGNORECASE,
)
INTERNAL_LINK_RE = re.compile(r"\bINTERNALLINK_(?P<event>[A-Z0-9_]+)\b", re.IGNORECASE)
INTERNAL_LINK_REASON_RE = re.compile(r"\bReason\s*=\s*(?P<reason>[^)]+)", re.IGNORECASE)
GENERIC_RECOVERY_RE = re.compile(
    r"(?:_[A-Z0-9]*CLEAR\b|_[A-Z0-9]*RECOVER(?:ED)?\b|"
    r"\balarm\s+(?:was\s+)?cleared\b|\brecovered\s+from\b|"
    r"告警(?:已)?清除|(?:已经|已)?恢复正常)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class ParsedSyslogAlert:
    category: str
    state: str
    target_type: str
    target_key: str
    target_name: str
    rule_name: str
    metric_type: str
    severity: str
    description: str
    value: float = 1.0
    threshold: float = 1.0
    reason: Optional[str] = None
    context: Optional[str] = None
    from_state: Optional[str] = None
    to_state: Optional[str] = None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _event_time(event: SyslogEvent) -> datetime:
    value = event.created_at or _utc_now()
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value


def _normalize_interface_key(interface_name: str) -> str:
    return re.sub(r"\s+", "", str(interface_name or "")).lower()


def _short_text_key(value: str, max_len: int = 120) -> str:
    text = re.sub(r"\s+", " ", str(value or "")).strip().lower()
    text = re.sub(r"[^a-z0-9一-龥:._/ -]+", "", text)
    return text[:max_len] or "unknown"


def _extract_interface_name(text: str) -> Optional[str]:
    match = INTERFACE_NAME_RE.search(text or "")
    if match:
        return re.sub(r"\s+", "", match.group(0))
    return None


def _extract_bfd_target(text: str) -> str:
    ips = IP_RE.findall(text or "")
    if len(ips) >= 2:
        return f"{ips[0]}->{ips[1]}"
    if ips:
        return ips[0]
    match = re.search(r"BFD\s+(?:session|Sess|会话)?\s*([^,，。;；\n\r]+)", text or "", re.IGNORECASE)
    if match:
        return match.group(1).strip()
    return "BFD会话"


def _keyword_state(text: str, firing_keywords: List[str], resolved_keywords: List[str]) -> Optional[str]:
    lower = str(text or "").lower()
    if any(keyword.lower() in lower for keyword in resolved_keywords):
        return "resolved"
    if any(keyword.lower() in lower for keyword in firing_keywords):
        return "firing"
    return None


def _contains_hardware_marker(text: str, markers: List[str]) -> bool:
    """
    Match hardware keywords as real words/tokens.

    H3C command audit Syslog can contain words like ``temporary``.  A loose
    substring match for ``TEMP`` would turn a CLI command failure into a false
    temperature alert, so ASCII markers are matched on token boundaries.
    """
    source = str(text or "")
    for marker in markers:
        marker_text = str(marker or "").strip()
        if not marker_text:
            continue
        if re.fullmatch(r"[A-Za-z0-9_/-]+", marker_text):
            if re.search(rf"(?<![A-Za-z0-9]){re.escape(marker_text)}(?![A-Za-z0-9])", source, re.IGNORECASE):
                return True
        elif marker_text.lower() in source.lower():
            return True
    return False


def _webhook_channels() -> List[Dict[str, Any]]:
    channels: List[Dict[str, Any]] = []
    for webhook_url in [
        settings.SYSTEM_ALERT_WEBHOOK_URL,
        settings.WECHAT_WEBHOOK_URL,
        settings.DINGTALK_WEBHOOK_URL,
    ]:
        value = (webhook_url or "").strip()
        if not value:
            continue
        channel_type = _detect_webhook_provider(value)
        config_key = "url" if channel_type == "webhook" else "webhook"
        if any(item.get("type") == channel_type and item.get("config", {}).get(config_key) == value for item in channels):
            continue
        channels.append({"type": channel_type, "config": {config_key: value}})
    return channels


def _ensure_syslog_rule(db: Session, parsed: ParsedSyslogAlert) -> AlertRule:
    rule = db.query(AlertRule).filter(
        AlertRule.metric_type == parsed.metric_type,
        AlertRule.name == parsed.rule_name,
    ).first()
    if rule:
        return rule

    rule = AlertRule(
        name=parsed.rule_name,
        description=parsed.description,
        rule_type="event",
        metric_type=parsed.metric_type,
        condition="==",
        threshold=parsed.threshold,
        duration=0,
        severity=parsed.severity,
        suppress_duration=300,
        enabled=1,
        device_ids=[],
        notification_channels=_webhook_channels(),
        extra_config={
            "vendor": "H3C",
            "source": "syslog",
            "event_category": parsed.category,
        },
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def ensure_default_syslog_alert_rules(db: Session) -> Dict[str, int]:
    """提前创建 Syslog 事件型告警规则；可重复执行。"""
    created = 0
    existing = 0
    for item in SYSLOG_ALERT_RULE_DEFINITIONS:
        parsed = ParsedSyslogAlert(
            category=item["category"],
            state="firing",
            target_type="device_event",
            target_key="template",
            target_name="template",
            rule_name=item["name"],
            metric_type=item["metric_type"],
            severity=item["severity"],
            description=item["description"],
        )
        before = db.query(AlertRule).filter(
            AlertRule.metric_type == parsed.metric_type,
            AlertRule.name == parsed.rule_name,
        ).first()
        _ensure_syslog_rule(db, parsed)
        if before:
            existing += 1
        else:
            created += 1
    return {"created": created, "existing": existing, "total": len(SYSLOG_ALERT_RULE_DEFINITIONS)}


def parse_syslog_alert(message: str, severity: Optional[int] = None) -> Optional[ParsedSyslogAlert]:
    text = str(message or "")
    match = PHY_UPDOWN_RE.search(text)
    if match:
        interface_name = match.group("interface").strip().rstrip(".")
        state = match.group("state").lower()
        return ParsedSyslogAlert(
            category="interface_phy",
            state="resolved" if state == "up" else "firing",
            target_type="interface",
            target_key=f"syslog:h3c:interface:{_normalize_interface_key(interface_name)}",
            target_name=interface_name,
            rule_name="【H3C】Syslog接口物理Down/瞬断",
            metric_type="syslog_interface_phy_down",
            severity="P2",
            description="由 H3C 设备 Syslog 物理接口状态变化自动生成，用于补齐周期采集无法捕获的短时接口瞬断。",
            reason="Physical state changed to down" if state == "down" else "Physical state changed to up",
            context="PHY_UPDOWN",
        )

    match = INTERFACE_DOWN_RE.search(text)
    if match:
        interface_name = match.group("interface").strip().rstrip(".")
        return ParsedSyslogAlert(
            category="interface_phy",
            state="firing",
            target_type="interface",
            target_key=f"syslog:h3c:interface:{_normalize_interface_key(interface_name)}",
            target_name=interface_name,
            rule_name="【H3C】Syslog接口物理Down/瞬断",
            metric_type="syslog_interface_phy_down",
            severity="P2",
            description="由 H3C 设备 Syslog 接口Down原因事件自动生成，用于补齐周期采集无法捕获的短时接口瞬断。",
            reason=(match.group("reason") or "").strip() or None,
            context="INTERFACE_DOWN",
        )

    # LINK_UPDOWN 通常会和 PHY_UPDOWN 同时出现。这里仅把 up 作为恢复兜底，避免 down 事件重复弹两次。
    match = LINK_UPDOWN_RE.search(text)
    if match:
        state = match.group("state").lower()
        if state != "up":
            return None
        interface_name = match.group("interface").strip().rstrip(".")
        return ParsedSyslogAlert(
            category="interface_phy",
            state="resolved",
            target_type="interface",
            target_key=f"syslog:h3c:interface:{_normalize_interface_key(interface_name)}",
            target_name=interface_name,
            rule_name="【H3C】Syslog接口物理Down/瞬断",
            metric_type="syslog_interface_phy_down",
            severity="P2",
            description="由 H3C 设备 Syslog 链路协议恢复事件自动生成，用作接口恢复兜底。",
            reason="Line protocol changed to up",
            context="LINK_UPDOWN",
        )

    # H3C internal-link events describe a physical/remote-fault condition on
    # an interface.  A *_CLEAR event is a recovery notification even though
    # its Syslog severity can still be critical (2), so it must be parsed
    # before the generic critical-event fallback below.
    internal_link_match = INTERNAL_LINK_RE.search(text)
    if internal_link_match:
        interface_name = _extract_interface_name(text) or "内部链路"
        event_name = (internal_link_match.group("event") or "").upper()
        state = "resolved" if (
            "CLEAR" in event_name
            or "RECOVER" in event_name
            or re.search(r"\balarm\s+(?:was\s+)?cleared\b|\brecovered\s+from\b", text, re.IGNORECASE)
        ) else "firing"
        reason_match = INTERNAL_LINK_REASON_RE.search(text)
        reason = (reason_match.group("reason") or "").strip() if reason_match else text
        return ParsedSyslogAlert(
            category="interface_phy",
            state=state,
            target_type="interface",
            target_key=f"syslog:h3c:interface:{_normalize_interface_key(interface_name)}",
            target_name=interface_name,
            rule_name="【H3C】Syslog接口物理Down/瞬断",
            metric_type="syslog_interface_phy_down",
            severity="P2",
            description="由 H3C 设备内部链路异常/恢复 Syslog 自动生成，用于补齐周期采集无法捕获的接口远端故障。",
            reason=reason or None,
            context="INTERNALLINK_ALARM",
        )

    match = BGP_STATE_RE.search(text)
    if match:
        peer = (match.group("peer") or "").strip()
        from_state = (match.group("from_state") or "").strip().upper()
        to_state = (match.group("to_state") or "").strip().upper().rstrip(".")
        reason = (match.group("reason") or "").strip()
        if to_state == "ESTABLISHED":
            state = "resolved"
        elif from_state == "ESTABLISHED" or to_state in {"IDLE", "ACTIVE", "CONNECT", "DOWN"}:
            state = "firing"
        else:
            return None
        return ParsedSyslogAlert(
            category="bgp_neighbor",
            state=state,
            target_type="bgp_neighbor",
            target_key=f"syslog:h3c:bgp:{peer.lower()}",
            target_name=f"BGP邻居 {peer}",
            rule_name="【H3C】Syslog BGP邻居状态变化",
            metric_type="syslog_bgp_state_change",
            severity="P1",
            description="由 H3C 设备 Syslog BGP 邻居状态变化自动生成，用于补齐周期采集无法捕获的短时邻居中断。",
            reason=reason or None,
            context=(match.group("context") or "").strip() or None,
            from_state=from_state,
            to_state=to_state,
        )

    if "BFD" in text.upper():
        state = _keyword_state(
            text,
            firing_keywords=[
                " to down", "-> down", "session down", "state down", "bfd down", "down trap",
                "changed from up to down", "changed to down",
            ],
            resolved_keywords=[
                " to up", "-> up", "session up", "state up", "bfd up", "up trap", "recover", "recovered",
                "changed from down to up", "changed to up",
            ],
        )
        if state:
            target_name = _extract_bfd_target(text)
            return ParsedSyslogAlert(
                category="bfd_session",
                state=state,
                target_type="bfd_session",
                target_key=f"syslog:h3c:bfd:{_short_text_key(target_name)}",
                target_name=f"BFD会话 {target_name}",
                rule_name="【H3C】Syslog BFD会话状态变化",
                metric_type="syslog_bfd_state_change",
                severity="P1",
                description="由 H3C 设备 Syslog BFD 会话状态变化自动生成，用于捕获周期采集可能遗漏的短时 BFD down/up。",
                reason=text,
                context="BFD",
            )

    optical_markers = ["TRANSCEIVER", "OPTICAL", "SFP", "QSFP", "光模块"]
    if any(marker.lower() in text.lower() for marker in optical_markers):
        is_rx_power_change = bool(
            re.search(r"(?:Rx|receive)\s+power\s+change", text, re.IGNORECASE)
            or "收光功率变化" in text
            or "收光功率突变" in text
        )
        state = _keyword_state(
            text,
            firing_keywords=[
                "removed", "remove", "absent", "not present", "unplug", "pulled", "fault", "failed", "alarm", "down", "拔出", "不在位", "异常", "故障",
            ],
            resolved_keywords=[
                "inserted", "insert", "present", "plug", "normal", "recover", "recovered", "clear", "ok", "插入", "在位", "恢复", "正常",
            ],
        )
        if state:
            interface_name = _extract_interface_name(text) or "光模块"
            if is_rx_power_change:
                return ParsedSyslogAlert(
                    category="optical_rx_power_change",
                    state=state,
                    target_type="optical_module",
                    target_key=f"syslog:h3c:optical-rx-change:{_normalize_interface_key(interface_name)}",
                    target_name=interface_name,
                    rule_name="【H3C】Syslog光模块收光功率突变",
                    metric_type="syslog_optical_rx_power_change",
                    severity="P2",
                    description="由 H3C 设备 Syslog 光模块收光功率突变事件自动生成；恢复 Syslog 优先，周期光功率采集连续正常时兜底恢复。",
                    reason=text,
                    context="OPTICAL_RX_POWER_CHANGE",
                )
            return ParsedSyslogAlert(
                category="optical_module",
                state=state,
                target_type="optical_module",
                target_key=f"syslog:h3c:optical:{_normalize_interface_key(interface_name)}",
                target_name=interface_name,
                rule_name="【H3C】Syslog光模块拔插/异常",
                metric_type="syslog_optical_module_event",
                severity="P2",
                description="由 H3C 设备 Syslog 光模块拔插或异常事件自动生成。",
                reason=text,
                context="OPTICAL",
            )

    hardware_definitions = [
        (
            "power",
            ["POWER", "PSU", "电源"],
            "【H3C】Syslog电源模块异常",
            "syslog_power_event",
            "P0",
            "电源模块",
        ),
        (
            "fan",
            ["FAN", "风扇"],
            "【H3C】Syslog风扇异常",
            "syslog_fan_event",
            "P0",
            "风扇",
        ),
        (
            "temperature",
            ["TEMP", "TEMPERATURE", "SENSOR", "温度"],
            "【H3C】Syslog温度异常",
            "syslog_temperature_event",
            "P1",
            "温度传感器",
        ),
    ]
    for category, markers, rule_name, metric_type, rule_severity, target_label in hardware_definitions:
        if not _contains_hardware_marker(text, markers):
            continue
        state = _keyword_state(
            text,
            firing_keywords=[
                "fail", "failed", "fault", "faulty", "alarm", "critical", "warning", "down", "absent", "removed", "over", "high", "low",
                "异常", "故障", "告警", "过高", "过低", "不在位", "拔出",
            ],
            resolved_keywords=["normal", "recover", "recovered", "clear", "ok", "present", "inserted", "恢复", "正常", "清除", "在位"],
        )
        if not state:
            continue
        interface_name = _extract_interface_name(text)
        target_name = interface_name or target_label
        return ParsedSyslogAlert(
            category=category,
            state=state,
            target_type="hardware",
            target_key=f"syslog:h3c:{category}:{_short_text_key(target_name)}",
            target_name=target_name,
            rule_name=rule_name,
            metric_type=metric_type,
            severity=rule_severity,
            description=f"由 H3C 设备 Syslog {target_label}异常/恢复事件自动生成。",
            reason=text,
            context=category.upper(),
        )

    # Recovery messages must never create a new generic critical alarm.  A
    # specifically parsed recovery above can resolve an existing alert; an
    # unknown recovery message remains available in raw Syslog for auditing.
    if GENERIC_RECOVERY_RE.search(text):
        return None

    if severity is not None and severity <= 2:
        return ParsedSyslogAlert(
            category="device_exception",
            state="firing",
            target_type="device_event",
            target_key=f"syslog:h3c:critical:{_short_text_key(text)}",
            target_name="设备严重Syslog事件",
            rule_name="【H3C】Syslog设备主动严重异常",
            metric_type="syslog_device_critical_event",
            severity="P1",
            description="由设备主动上报的 emergency/alert/critical 级别 Syslog 自动生成。",
            reason=text,
            context="CRITICAL_SYSLOG",
        )

    return None


def _active_alert_query(db: Session, rule: AlertRule, device: Device, parsed: ParsedSyslogAlert):
    return db.query(AlertHistory).filter(
        AlertHistory.rule_id == rule.id,
        AlertHistory.device_id == device.id,
        AlertHistory.alert_target_key == parsed.target_key,
        AlertHistory.status.in_(ACTIVE_ALERT_STATUSES),
    )


def _active_interface_alert_for_syslog_target(
    db: Session,
    device: Device,
    parsed: ParsedSyslogAlert,
) -> Optional[AlertHistory]:
    """Find either the original Syslog alert or an upgraded sustained interface alert."""
    normalized_interface = _normalize_interface_key(parsed.target_name)
    rows = (
        db.query(AlertHistory, AlertRule)
        .join(AlertRule, AlertRule.id == AlertHistory.rule_id)
        .filter(
            AlertHistory.device_id == device.id,
            AlertHistory.status.in_(ACTIVE_ALERT_STATUSES),
            AlertHistory.alert_target_type == "interface",
            AlertRule.metric_type.in_(["syslog_interface_phy_down", "interface_admin_up_oper_down"]),
        )
        .order_by(AlertHistory.started_at.desc())
        .limit(80)
        .all()
    )
    for alert, _rule in rows:
        if alert.alert_target_key == parsed.target_key:
            return alert
        for value in (alert.alert_target_name, alert.alert_target_key):
            normalized_value = _normalize_interface_key(str(value or ""))
            if normalized_value == normalized_interface or normalized_value.endswith(f":{normalized_interface}"):
                return alert
    return None


def _find_recent_interface_context(db: Session, device: Device, event: SyslogEvent) -> Optional[str]:
    event_time = _event_time(event)
    started_at = event_time - timedelta(seconds=90)
    rows = db.query(SyslogEvent).filter(
        SyslogEvent.device_id == device.id,
        SyslogEvent.created_at >= started_at,
        SyslogEvent.id != event.id,
    ).order_by(SyslogEvent.created_at.desc()).limit(30).all()
    for row in rows:
        parsed = parse_syslog_alert(row.message or row.raw_message or "", row.severity)
        if parsed and parsed.category == "interface_phy" and parsed.state == "firing":
            return parsed.target_name
    return None


def _build_firing_message(device: Device, event: SyslogEvent, parsed: ParsedSyslogAlert, correlated_interface: Optional[str]) -> str:
    lines = [
        f"设备 {device.name} ({device.ip_address}) 收到关键 Syslog 事件",
        f"事件类型：{parsed.rule_name}",
        f"对象：{parsed.target_name}",
    ]
    if parsed.from_state or parsed.to_state:
        lines.append(f"状态变化：{parsed.from_state or '-'} -> {parsed.to_state or '-'}")
    if parsed.reason:
        lines.append(f"原因：{parsed.reason}")
    if correlated_interface:
        lines.append(f"关联接口：{correlated_interface}")
    lines.extend([
        f"Syslog时间：{_event_time(event).astimezone().strftime('%Y/%m/%d %H:%M:%S')}",
        f"原始日志：{event.message}",
    ])
    return "\n".join(lines)


def _build_resolution_note(event: SyslogEvent, parsed: ParsedSyslogAlert) -> str:
    parts = [f"Syslog恢复事件：{parsed.target_name}"]
    if parsed.from_state or parsed.to_state:
        parts.append(f"状态变化 {parsed.from_state or '-'} -> {parsed.to_state or '-'}")
    if parsed.reason:
        parts.append(parsed.reason)
    parts.append(event.message)
    return " | ".join(parts)


def _resets_optical_baseline(parsed: ParsedSyslogAlert, message: str) -> bool:
    """Return true only for a real interface/module session boundary."""
    if parsed.category == "interface_phy":
        return True
    if parsed.category != "optical_module":
        return False
    return bool(re.search(
        r"(?:MODULE_(?:OUT|IN)|OPTICAL_(?:REMOVED|INSERTED)|"
        r"transceiver\s+(?:was\s+)?(?:removed|inserted)|module\s+(?:removed|inserted))",
        str(message or ""),
        re.IGNORECASE,
    ))


def process_syslog_alert_event(db: Session, event: SyslogEvent, device: Optional[Device]) -> None:
    """将单条 Syslog 事件转换为告警/恢复。调用方应已完成 SyslogEvent 入库。"""
    if not device or not event:
        return
    parsed = parse_syslog_alert(event.message or event.raw_message or "", event.severity)
    if not parsed:
        return
    source_message = event.message or event.raw_message or ""
    if _resets_optical_baseline(parsed, source_message) and parsed.target_name:
        deleted_keys = reset_optical_interface_baselines(device.id, parsed.target_name)
        logger.info(
            "接口/模块会话变化，已重置光功率与FEC基线",
            device_id=device.id,
            target=parsed.target_name,
            deleted_keys=len(deleted_keys),
        )
    # Some devices emit the same hardware event repeatedly in one burst. Keep
    # every raw Syslog row for audit, but only mutate the alert once per burst.
    fingerprint_source = "|".join([
        str(device.id),
        parsed.category,
        parsed.target_key,
        parsed.state,
        event.message or event.raw_message or "",
    ])
    fingerprint = hashlib.sha256(fingerprint_source.encode("utf-8", errors="ignore")).hexdigest()
    try:
        if not redis_client.set(f"syslog:alert:dedupe:{fingerprint}", "1", ex=5, nx=True):
            return
    except Exception:
        pass

    rule = _ensure_syslog_rule(db, parsed)
    target = {
        "target_type": parsed.target_type,
        "target_key": parsed.target_key,
        "target_name": parsed.target_name,
        "value": parsed.value,
    }
    existing = _active_alert_query(db, rule, device, parsed).order_by(AlertHistory.started_at.desc()).first()
    if not existing and parsed.category == "interface_phy":
        existing = _active_interface_alert_for_syslog_target(db, device, parsed)

    if parsed.state == "resolved":
        if not existing:
            logger.info("Syslog恢复事件未匹配到活动告警", device_id=device.id, target=parsed.target_name, event_id=event.id)
            return
        existing.status = "resolved"
        existing.resolved_by = "syslog"
        existing.resolved_at = _event_time(event)
        existing.resolution_note = _build_resolution_note(event, parsed)
        existing.updated_at = _utc_now()
        db.commit()
        enqueue_alert_notification(existing.id, "auto_resolved", "syslog")
        logger.info("Syslog告警已自动恢复", alert_id=existing.id, device_id=device.id, target=parsed.target_name)
        return

    correlated_interface = None
    if parsed.category == "bgp_neighbor":
        correlated_interface = _find_recent_interface_context(db, device, event)
    message = _build_firing_message(device, event, parsed, correlated_interface)

    if _is_silenced(db, rule, device, target):
        if existing:
            existing.status = "ignored"
            existing.ignored_by = "alert_silence"
            existing.ignored_at = _utc_now()
            existing.message = message
            existing.updated_at = _utc_now()
        else:
            existing = AlertHistory(
                rule_id=rule.id,
                device_id=device.id,
                alert_value=parsed.value,
                threshold=parsed.threshold,
                message=message,
                alert_target_type=parsed.target_type,
                alert_target_key=parsed.target_key,
                alert_target_name=parsed.target_name,
                status="ignored",
                ignored_by="alert_silence",
                ignored_at=_utc_now(),
                started_at=_event_time(event),
            )
            db.add(existing)
        db.commit()
        db.refresh(existing)
        _ensure_alarm_id(db, existing)
        logger.info("Syslog事件命中屏蔽规则，已记录为忽略", alert_id=existing.id, device_id=device.id, target=parsed.target_name)
        return

    if existing:
        existing.alert_value = parsed.value
        existing.message = message
        existing.alert_target_type = parsed.target_type
        existing.alert_target_name = parsed.target_name
        existing.updated_at = _utc_now()
        db.commit()
        logger.info("Syslog持续告警已更新", alert_id=existing.id, device_id=device.id, target=parsed.target_name)
        return

    alert = AlertHistory(
        rule_id=rule.id,
        device_id=device.id,
        alert_value=parsed.value,
        threshold=parsed.threshold,
        message=message,
        alert_target_type=parsed.target_type,
        alert_target_key=parsed.target_key,
        alert_target_name=parsed.target_name,
        status="firing",
        started_at=_event_time(event),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    _ensure_alarm_id(db, alert)
    enqueue_alert_notification(alert.id)
    logger.info("Syslog事件已转换为告警", alert_id=alert.id, device_id=device.id, target=parsed.target_name)
