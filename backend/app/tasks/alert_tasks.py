"""
告警检测和处理任务
"""
import asyncio
import json
import re
import threading
import time
import urllib.request
import uuid
from celery import shared_task
from sqlalchemy import or_
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional

from app.database import SessionLocal
from app.models import AlertRule, AlertHistory, AlertSilence, Circuit, Device, SyslogEvent
from app.utils import influx_client, notification_manager, redis_client
from app.utils.asternos_exporter_client import asternos_exporter_client
from app.config import settings
from app.core import get_logger

logger = get_logger(__name__)
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
CHECK_ALERTS_LOCK_KEY = "alerts:check_alerts:lock"
FAST_CHECK_ALERTS_LOCK_KEY = "alerts:check_fast_alerts:lock"
PROTOCOL_CHECK_ALERTS_LOCK_KEY = "alerts:check_protocol_alerts:lock"
DEVICE_HEALTH_CHECK_ALERTS_LOCK_KEY = "alerts:check_device_health_alerts:lock"
CHECK_ALERTS_LOCK_TTL_SECONDS = 900
FAST_CHECK_ALERTS_LOCK_TTL_SECONDS = 60
PROTOCOL_CHECK_ALERTS_LOCK_TTL_SECONDS = 180
DEVICE_HEALTH_CHECK_ALERTS_LOCK_TTL_SECONDS = 120
EXPORTER_SCRAPE_CACHE_TTL_SECONDS = 5
ROBOT_NOTIFICATION_INTERVAL_SECONDS = 2
PENDING_ALERT_TTL_SECONDS = 7200
_EXPORTER_SCRAPE_CACHE: Dict[int, Dict[str, Any]] = {}
_EXPORTER_SCRAPE_CACHE_LOCK = threading.Lock()
EXPORTER_DELTA_CACHE_TTL_SECONDS = 86400
RULE_STATUS_PREWARM_URL = "http://api:8000/api/v1/alerts/rules/{rule_id}/status?limit=500"
RULE_STATUS_PREWARM_LOCK_KEY = "alerts:rule_status_prewarm:lock"
RULE_STATUS_PREWARM_CURSOR_KEY = "alerts:rule_status_prewarm:cursor"


def _is_asternos_vendor(vendor: Optional[str]) -> bool:
    vendor_value = (vendor or "").strip().lower()
    return any(marker in vendor_value for marker in ["asternos", "asterfusion", "asteros", "星融元"])


def _is_hillstone_vendor(vendor: Optional[str]) -> bool:
    vendor_value = (vendor or "").strip().lower()
    return any(marker in vendor_value for marker in ["hillstone", "山石"])


def _get_effective_monitor_source(device: Device) -> str:
    return "asternos_exporter" if _is_asternos_vendor(device.vendor) else "snmp"

INTERFACE_METRIC_TYPES = {
    "interface_oper_status",
    "interface_admin_up_oper_down",
    "interface_in_errors_delta",
    "interface_out_errors_delta",
    "interface_in_discards_delta",
    "interface_out_discards_delta",
    "interface_in_broadcast_pps",
    "interface_out_broadcast_pps",
    "optical_rx_power",
    "optical_tx_power",
}

CIRCUIT_METRIC_TYPES = {
    "internet_circuit_traffic_floor",
    "private_line_circuit_traffic_floor",
}

DEVICE_REACHABILITY_METRIC_TYPES = {
    "device_reachability",
}

PROTOCOL_METRIC_TYPES = {
    "bgp_peer_state",
    "ospf_neighbor_state",
    "bfd_session_state",
}

SNMP_TARGET_METRIC_TYPES = {
    "snmp_storage_usage",
    "snmp_fan_status",
    "snmp_power_status",
    "snmp_pak_buffer_usage",
    "snmp_ipsec_tunnel_status",
    "snmp_snat_resource_usage",
    "snmp_dnat_server_status",
    "snmp_slb_virtual_server_status",
}

SNMP_DEVICE_METRIC_TYPES = {
    "snmp_temperature",
    "snmp_session_usage",
    "snmp_session_queue_full_drop_delta",
    "snmp_ha_status",
}

COMMON_DEVICE_METRIC_TYPES = {
    "snmp_cpu",
    "snmp_memory",
    "device_temperature",
}

HILLSTONE_ONLY_METRIC_TYPES = {
    "snmp_session_usage",
    "snmp_session_queue_full_drop_delta",
    "snmp_ha_status",
    "snmp_pak_buffer_usage",
    "snmp_ipsec_tunnel_status",
    "snmp_snat_resource_usage",
    "snmp_dnat_server_status",
    "snmp_slb_virtual_server_status",
}

EXPORTER_METRIC_TYPES = {
    "exporter_metric",
}

PERCENT_METRIC_TYPES = {
    "snmp_cpu",
    "snmp_memory",
    "snmp_session_usage",
    "snmp_storage_usage",
    "snmp_pak_buffer_usage",
    "snmp_snat_resource_usage",
}

NUMERIC_DETAIL_METRIC_TYPES = {
    "snmp_cpu",
    "snmp_memory",
    "device_temperature",
    "snmp_temperature",
    "snmp_session_usage",
    "snmp_session_queue_full_drop_delta",
    "snmp_storage_usage",
    "snmp_fan_status",
    "snmp_power_status",
    "snmp_ha_status",
    "snmp_pak_buffer_usage",
    "snmp_ipsec_tunnel_status",
    "snmp_snat_resource_usage",
    "snmp_dnat_server_status",
    "snmp_slb_virtual_server_status",
    "optical_rx_power",
    "optical_tx_power",
    "exporter_metric",
}

METRIC_VALUE_LABELS = {
    "snmp_cpu": "当前CPU使用率",
    "snmp_memory": "当前内存使用率",
    "device_temperature": "当前温度",
    "snmp_temperature": "当前温度",
    "snmp_session_usage": "当前会话使用率",
    "snmp_session_queue_full_drop_delta": "当前会话队列满丢包增长",
    "snmp_storage_usage": "当前存储使用率",
    "snmp_fan_status": "当前风扇状态",
    "snmp_power_status": "当前电源状态",
    "snmp_ha_status": "当前HA状态",
    "snmp_pak_buffer_usage": "当前Packet Buffer使用率",
    "snmp_ipsec_tunnel_status": "当前IPSec隧道状态",
    "snmp_snat_resource_usage": "当前SNAT资源使用率",
    "snmp_dnat_server_status": "当前DNAT服务器状态",
    "snmp_slb_virtual_server_status": "当前SLB虚拟服务状态",
    "optical_rx_power": "当前收光功率",
    "optical_tx_power": "当前发光功率",
}

FAST_ALERT_METRIC_TYPES = {
    "interface_admin_up_oper_down",
}

DEVICE_HEALTH_ALERT_METRIC_TYPES = {
    "snmp_cpu",
    "snmp_memory",
    "device_temperature",
    "snmp_temperature",
}

SEVERITY_LABELS = {
    "critical": "P0",
    "warning": "P1",
    "info": "P2",
    "P0": "P0",
    "P1": "P1",
    "P2": "P2",
}


def _parse_notification_timestamp(value: Any) -> Optional[datetime]:
    """兼容历史通知记录里的多种时间格式。"""
    if not value:
        return None
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        normalized = value.strip()
        if not normalized:
            return None
        if normalized.endswith("Z"):
            normalized = normalized[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(normalized)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            return None
    return None


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _to_local_datetime(value: Optional[datetime]) -> Optional[datetime]:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TIMEZONE)


def _format_local_time(value: Optional[datetime]) -> str:
    local_value = _to_local_datetime(value)
    if not local_value:
        return "-"
    return local_value.strftime("%Y/%m/%d %H:%M:%S")


def _normalize_severity_label(value: Optional[str]) -> str:
    return SEVERITY_LABELS.get((value or "").strip(), "P1")


def _build_short_alarm_id(alert: AlertHistory) -> str:
    base_time = alert.started_at or _utc_now()
    return f"A{base_time.strftime('%m%d')}-{alert.id % 100000:05d}"


def _ensure_alarm_id(db: Session, alert: AlertHistory) -> str:
    if alert.alarm_id and not str(alert.alarm_id).startswith("ALM"):
        return alert.alarm_id
    alert.alarm_id = _build_short_alarm_id(alert)
    db.commit()
    db.refresh(alert)
    return alert.alarm_id


def _get_mention_users(rule: AlertRule) -> List[str]:
    extra_config = rule.extra_config or {}
    value = extra_config.get("mention_users") or extra_config.get("mention_targets") or []
    if isinstance(value, str):
        users = [item.strip() for item in value.split(",") if item.strip()]
        return users
    if isinstance(value, list):
        return [str(item).strip() for item in value if str(item).strip()]
    return []


def _build_detail_url(alert: AlertHistory) -> str:
    base = settings.FRONTEND_PUBLIC_URL.rstrip("/")
    return f"{base}/alerts/history?alert_id={alert.id}"


def _get_recent_occurrence_count(db: Session, alert: AlertHistory) -> int:
    one_hour_ago = _utc_now() - timedelta(hours=1)
    query = db.query(AlertHistory).filter(
        AlertHistory.rule_id == alert.rule_id,
        AlertHistory.device_id == alert.device_id,
        AlertHistory.started_at >= one_hour_ago,
    )
    if alert.alert_target_key:
        query = query.filter(AlertHistory.alert_target_key == alert.alert_target_key)
    else:
        query = query.filter(AlertHistory.alert_target_key.is_(None))
    return query.count()


def _target_label(rule: AlertRule, alert: AlertHistory) -> str:
    if alert.alert_target_name:
        if rule.metric_type in INTERFACE_METRIC_TYPES:
            return f"接口：{alert.alert_target_name}"
        if rule.metric_type in CIRCUIT_METRIC_TYPES:
            return f"线路接口：{alert.alert_target_name}"
        return f"对象：{alert.alert_target_name}"
    return ""


def _device_datacenter_text(device: Device) -> str:
    datacenter = getattr(device, "datacenter_ref", None)
    if not datacenter:
        return "-"
    if datacenter.code:
        return f"{datacenter.name}（{datacenter.code}）"
    return datacenter.name or "-"


def _current_handler_text(alert: AlertHistory) -> str:
    if alert.status == "acknowledged" and alert.acknowledged_by:
        return alert.acknowledged_by
    if alert.status == "ignored" and alert.ignored_by:
        return alert.ignored_by
    if alert.status == "resolved" and (alert.resolved_by or alert.acknowledged_by):
        return alert.resolved_by or alert.acknowledged_by or "-"
    mention_users = _get_mention_users(alert.rule) if alert.rule else []
    return "、".join(mention_users) if mention_users else "待分配"


def _format_duration(started_at: Optional[datetime], ended_at: Optional[datetime]) -> str:
    if not started_at or not ended_at:
        return "-"
    total_seconds = max(int((ended_at - started_at).total_seconds()), 0)
    hours, remainder = divmod(total_seconds, 3600)
    minutes, seconds = divmod(remainder, 60)
    parts = []
    if hours:
        parts.append(f"{hours}小时")
    if minutes:
        parts.append(f"{minutes}分钟")
    if seconds or not parts:
        parts.append(f"{seconds}秒")
    return "".join(parts)


def _build_notification_title(rule: AlertRule, event_type: str, actor: Optional[str]) -> str:
    severity = _normalize_severity_label(rule.severity)
    mentions = _get_mention_users(rule)
    mention_suffix = f"@{'、'.join(mentions)}" if mentions else ""
    if event_type == "ignored":
        return f"{actor or '有人'}忽略了1条故障"
    if event_type == "auto_resolved":
        if rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
            return f"{severity}-设备重新可达，已恢复{mention_suffix}"
        return f"{severity}-自动恢复通知{mention_suffix}"
    return f"{severity}-故障通知{mention_suffix}"


def _build_notification_content(
    db: Session,
    alert: AlertHistory,
    event_type: str = "firing",
    actor: Optional[str] = None,
) -> str:
    rule = alert.rule
    device = alert.device
    if not rule or not device:
        return alert.message or "告警详情不可用"

    alarm_id = _ensure_alarm_id(db, alert)
    occurrence_count = _get_recent_occurrence_count(db, alert)
    detail_url = _build_detail_url(alert)
    fault_title = rule.name or rule.metric_type
    target_line = _target_label(rule, alert)
    started_at_text = _format_local_time(alert.started_at)

    lines = []
    if event_type == "ignored":
        lines.append(f"【{fault_title}】")
    else:
        lines.append(f"故障标题：【{fault_title}】")
    lines.append(f"交换机：{device.name}")
    lines.append(f"管理地址：{device.ip_address}")
    if target_line:
        lines.append(target_line)
    numeric_detail = _build_numeric_detail_row(alert)
    if numeric_detail:
        lines.append(f"{numeric_detail['label']}：{numeric_detail['value']}")
    if rule.metric_type in {"interface_oper_status", "interface_admin_up_oper_down"}:
        occurrence_label = "过去1小时down次数"
    elif rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
        occurrence_label = "过去1小时不可达次数"
    elif rule.metric_type in CIRCUIT_METRIC_TYPES:
        occurrence_label = "过去1小时掉底次数"
    else:
        occurrence_label = "过去1小时触发次数"
    lines.append(f"{occurrence_label}：{occurrence_count}次")
    lines.append(f"Alarm ID：{alarm_id}")

    if event_type == "firing":
        lines.append(f"发生时间：{started_at_text}")
        lines.append(f"当前处理人：{_current_handler_text(alert)}")
        lines.append("")
        lines.append(f"故障详情：{detail_url}")
    elif event_type == "ignored":
        if actor:
            lines.insert(0, f"{actor}忽略了1条故障：")
    elif event_type == "auto_resolved":
        resolved_at_text = _format_local_time(alert.resolved_at)
        if rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
            lines[0] = "故障标题：【设备重新可达，已恢复】"
        lines.append(f"发生时间：{started_at_text}")
        lines.append(f"恢复时间：{resolved_at_text}")
        lines.append(f"持续时间：{_format_duration(alert.started_at, alert.resolved_at)}")
        lines.append(f"当前处理人：{_current_handler_text(alert)}")
        lines.append("")
        lines.append(f"故障详情：{detail_url}")

    return "\n".join(lines)


def _build_notification_card_data(
    db: Session,
    alert: AlertHistory,
    event_type: str = "firing",
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    rule = alert.rule
    device = alert.device
    if not rule or not device:
        return {}

    alarm_id = _ensure_alarm_id(db, alert)
    occurrence_count = _get_recent_occurrence_count(db, alert)
    severity = _normalize_severity_label(rule.severity)
    started_at_text = _format_local_time(alert.started_at)
    resolved_at_text = _format_local_time(alert.resolved_at)

    if rule.metric_type in {"interface_oper_status", "interface_admin_up_oper_down"}:
        occurrence_label = "过去1小时down次数"
    elif rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
        occurrence_label = "过去1小时不可达次数"
    elif rule.metric_type in CIRCUIT_METRIC_TYPES:
        occurrence_label = "过去1小时掉底次数"
    else:
        occurrence_label = "过去1小时触发次数"

    rows = [
        {"label": "故障标题", "value": f"【{rule.name or rule.metric_type}】"},
        {"label": "交换机", "value": device.name},
        {"label": "管理地址", "value": device.ip_address},
    ]
    if alert.alert_target_name:
        target_label = "接口" if rule.metric_type in INTERFACE_METRIC_TYPES else "对象"
        if rule.metric_type in CIRCUIT_METRIC_TYPES:
            target_label = "线路接口"
        rows.append({"label": target_label, "value": alert.alert_target_name})
    numeric_detail = _build_numeric_detail_row(alert)
    if numeric_detail:
        rows.append(numeric_detail)
    rows.extend(
        [
            {"label": occurrence_label, "value": f"{occurrence_count}次"},
            {"label": "Alarm ID", "value": alarm_id},
        ]
    )

    if event_type == "firing":
        rows.extend(
            [
                {"label": "发生时间", "value": started_at_text},
                {"label": "当前处理人", "value": _current_handler_text(alert)},
            ]
        )
    elif event_type == "ignored":
        rows.insert(0, {"label": "处理动作", "value": f"{actor or '有人'}忽略了1条故障"})
    elif event_type == "auto_resolved":
        if rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
            rows[0] = {"label": "故障标题", "value": "【设备重新可达，已恢复】"}
        rows.extend(
            [
                {"label": "发生时间", "value": started_at_text},
                {"label": "恢复时间", "value": resolved_at_text},
                {"label": "持续时间", "value": _format_duration(alert.started_at, alert.resolved_at)},
                {"label": "当前处理人", "value": _current_handler_text(alert)},
            ]
        )

    return {
        "severity": severity,
        "title": _build_notification_title(rule, event_type, actor),
        "headline": severity,
        "summary": f"{rule.metric_type} / {device.ip_address}",
        "subtitle": started_at_text if event_type != "ignored" else _format_local_time(datetime.now(timezone.utc)),
        "rows": rows,
        "detail_url": _build_detail_url(alert),
        "event_type": event_type,
    }


def _build_channel_config(channel: Dict[str, Any], mention_users: List[str]) -> Dict[str, Any]:
    config = dict(channel.get("config", {}) or {})
    if mention_users:
        config.setdefault("mentioned_list", mention_users)
        mobile_targets = [item for item in mention_users if item.isdigit()]
        if mobile_targets:
            config.setdefault("at_mobiles", mobile_targets)
    return config


async def _wait_for_notification_slot(channel_type: Optional[str], config: Dict[str, Any]) -> None:
    if channel_type not in {"feishu", "wechat", "dingtalk"}:
        return
    webhook = str(config.get("webhook") or "default")
    lock_key = f"alerts:notify:{channel_type}:{webhook[-24:]}"
    for _ in range(60):
        if redis_client.set(lock_key, "1", ex=ROBOT_NOTIFICATION_INTERVAL_SECONDS, nx=True):
            return
        await asyncio.sleep(1)


def _get_last_notification_time(alert: AlertHistory) -> Optional[datetime]:
    notifications = alert.notifications_sent or []
    last_time: Optional[datetime] = None
    for item in notifications:
        if not isinstance(item, dict):
            continue
        item_time = _parse_notification_timestamp(
            item.get("sent_at") or item.get("timestamp") or item.get("time")
        )
        if item_time and (last_time is None or item_time > last_time):
            last_time = item_time
    return last_time


def _should_repeat_notify(alert: AlertHistory, rule: AlertRule) -> bool:
    """
    已触发但未恢复的告警，按 suppress_duration 作为重复通知间隔。
    acknowledged 视为人工确认后暂停重复通知，避免持续打扰。
    """
    if alert.status != "firing":
        return False

    interval_seconds = max(int(rule.suppress_duration or 0), 0)
    if interval_seconds <= 0:
        return False

    now = _utc_now()
    last_notification_time = _get_last_notification_time(alert)
    if last_notification_time is None:
        started_at = alert.started_at or now
        return (now - started_at).total_seconds() >= interval_seconds

    return (now - last_notification_time).total_seconds() >= interval_seconds


def _snooze_until(alert: AlertHistory) -> Optional[datetime]:
    """
    人工点击“解决”不代表故障已经恢复。这里把它作为一小时复查暂停：
    resolved_at 记录点击时间，status=snoozed 表示暂停告警检查/通知。
    """
    if alert.status != "snoozed" or not alert.resolved_at:
        return None
    return alert.resolved_at + timedelta(hours=1)


def _is_snoozed_now(alert: AlertHistory) -> bool:
    until = _snooze_until(alert)
    return bool(until and until > _utc_now())


def _is_targeted_metric(metric_type: str) -> bool:
    return (
        metric_type in INTERFACE_METRIC_TYPES
        or metric_type in PROTOCOL_METRIC_TYPES
        or metric_type in CIRCUIT_METRIC_TYPES
        or metric_type in SNMP_TARGET_METRIC_TYPES
        or metric_type == "device_temperature"
        or metric_type in EXPORTER_METRIC_TYPES
        or metric_type == "syslog_keyword"
    )


def _pending_alert_key(rule: AlertRule, device: Device, target: Dict[str, Any]) -> str:
    target_key = str(target.get("target_key") or "device")
    return f"alerts:pending:{rule.id}:{device.id}:{target_key}"


def _clear_pending_alert(rule: AlertRule, device: Device, target: Dict[str, Any]) -> None:
    redis_client.delete(_pending_alert_key(rule, device, target))


def _duration_confirmed(rule: AlertRule, device: Device, target: Dict[str, Any], value: float) -> bool:
    duration_seconds = max(int(rule.duration or 0), 0)
    if duration_seconds <= 0:
        return True

    now = time.time()
    key = _pending_alert_key(rule, device, target)
    pending_raw = redis_client.get(key)
    first_seen = now
    if pending_raw:
        try:
            first_seen = float(json.loads(pending_raw).get("first_seen") or now)
        except (TypeError, ValueError, json.JSONDecodeError):
            first_seen = now
    else:
        redis_client.set(
            key,
            json.dumps(
                {
                    "first_seen": first_seen,
                    "rule_id": rule.id,
                    "device_id": device.id,
                    "target_key": target.get("target_key"),
                    "target_name": target.get("target_name"),
                    "value": value,
                },
                ensure_ascii=False,
            ),
            ex=max(duration_seconds * 3, duration_seconds + 60, PENDING_ALERT_TTL_SECONDS),
        )
        logger.info(
            "告警进入持续时间确认",
            rule_id=rule.id,
            device_id=device.id,
            target=target.get("target_name"),
            duration_seconds=duration_seconds,
            value=value,
        )

    return (now - first_seen) >= duration_seconds


def _silence_matches(silence: AlertSilence, rule: AlertRule, device: Device, target: Dict[str, Any]) -> bool:
    now = _utc_now()

    def _matches_pattern(source: str, pattern: Optional[str]) -> bool:
        if not pattern:
            return True
        source_text = source or ""
        candidate = pattern.strip()
        if not candidate:
            return True
        import re
        try:
            return re.search(candidate, source_text) is not None
        except re.error:
            return candidate in source_text

    def _matches_ip_value(source: str, candidate: str) -> bool:
        source_text = (source or "").strip()
        candidate_text = (candidate or "").strip()
        if not source_text or not candidate_text:
            return False
        if source_text == candidate_text:
            return True
        if "/" not in candidate_text:
            return False
        try:
            import ipaddress
            return ipaddress.ip_address(source_text) in ipaddress.ip_network(candidate_text, strict=False)
        except ValueError:
            return False

    def _evaluate_condition(field_name: str, field_value: str, operator: str, raw_value: str) -> bool:
        source_text = field_value or ""
        values = [item.strip() for item in re.split(r"[,，;；\n\r]+", raw_value or "") if item.strip()]
        op = (operator or "contains").strip()

        if not values:
            return True

        if field_name in {"ip", "device_ip"}:
            if op in {"contains", "equals"}:
                return any(_matches_ip_value(source_text, candidate) for candidate in values)
            if op in {"not_contains", "not_equals"}:
                return all(not _matches_ip_value(source_text, candidate) for candidate in values)

        if op == "contains":
            return any(candidate in source_text for candidate in values)
        if op == "not_contains":
            return all(candidate not in source_text for candidate in values)
        if op == "equals":
            return any(source_text == candidate for candidate in values)
        if op == "not_equals":
            return all(source_text != candidate for candidate in values)
        if op == "regex":
            return any(_matches_pattern(source_text, candidate) for candidate in values)
        if op == "not_regex":
            return all(not _matches_pattern(source_text, candidate) for candidate in values)
        return True

    if silence.starts_at and silence.starts_at > now:
        return False
    if silence.expires_at and silence.expires_at < now:
        return False
    if silence.rule_id and silence.rule_id != rule.id:
        return False
    if silence.device_id and silence.device_id != device.id:
        return False
    device_ip = device.ip_address or ""
    interface_text = " ".join(
        [
            str(target.get("target_name") or ""),
            str(target.get("target_key") or ""),
        ]
    ).strip()
    message_text = _build_alert_message(rule, device, float(target.get("value") or 0.0), target)
    searchable_message_text = " ".join(
        [
            message_text,
            interface_text,
            device_ip,
            device.name or "",
        ]
    ).strip()
    if not _matches_pattern(device_ip, silence.include_device_ip):
        return False
    if not _matches_pattern(interface_text, silence.include_interface):
        return False
    if not _matches_pattern(searchable_message_text, silence.include_message):
        return False
    if silence.exclude_device_ip and _matches_pattern(device_ip, silence.exclude_device_ip):
        return False
    if silence.exclude_interface and _matches_pattern(interface_text, silence.exclude_interface):
        return False
    if silence.exclude_message and _matches_pattern(searchable_message_text, silence.exclude_message):
        return False
    if silence.target_pattern:
        target_text = " ".join(
            [
                device.ip_address or "",
                target.get("target_name") or "",
                target.get("target_key") or "",
            ]
        )
        if not _matches_pattern(target_text, silence.target_pattern):
            return False
    if silence.conditions:
        field_map = {
            "ip": device_ip,
            "device_ip": device_ip,
            "interface": interface_text,
            "message": searchable_message_text,
            "content": searchable_message_text,
            "alarm_id": str(target.get("alarm_id") or ""),
        }
        for condition in silence.conditions:
            field_name = str((condition or {}).get("field") or "").strip()
            operator = str((condition or {}).get("operator") or "contains").strip()
            value = str((condition or {}).get("value") or "").strip()
            if not field_name or not value:
                continue
            if not _evaluate_condition(field_name, field_map.get(field_name, ""), operator, value):
                return False
    return True


def _is_silenced(db: Session, rule: AlertRule, device: Device, target: Dict[str, Any]) -> bool:
    silences = db.query(AlertSilence).filter(AlertSilence.enabled == 1).all()
    for silence in silences:
        if _silence_matches(silence, rule, device, target):
            return True
    return False


def _run_alert_checks(
    *,
    lock_key: str,
    lock_ttl_seconds: int,
    metric_types: Optional[set[str]] = None,
    exclude_metric_types: Optional[set[str]] = None,
    task_label: str = "告警检查",
) -> Dict[str, Any]:
    started_at = time.time()
    lock_value = f"{started_at}:{uuid.uuid4()}"
    lock_acquired = bool(
        redis_client.set(
            lock_key,
            lock_value,
            ex=lock_ttl_seconds,
            nx=True,
        )
    )
    if not lock_acquired:
        logger.info(f"上一轮{task_label}仍在执行，本轮跳过")
        return {"skipped": True, "reason": f"{task_label} already running"}

    db = SessionLocal()
    try:
        query = db.query(AlertRule).filter(AlertRule.enabled == 1)
        if metric_types:
            query = query.filter(AlertRule.metric_type.in_(list(metric_types)))
        if exclude_metric_types:
            query = query.filter(~AlertRule.metric_type.in_(list(exclude_metric_types)))
        rules = query.order_by(AlertRule.id.asc()).all()
        
        logger.info(f"开始{task_label}，共{len(rules)}条规则")
        
        triggered = 0
        for rule in rules:
            rule_started_at = time.time()
            try:
                result = _check_single_rule(db, rule)
                if result:
                    triggered += 1
                rule_elapsed = time.time() - rule_started_at
                if rule_elapsed >= 3:
                    logger.info(
                        "告警规则检查耗时较长",
                        rule_id=rule.id,
                        rule_name=rule.name,
                        metric_type=rule.metric_type,
                        elapsed_seconds=round(rule_elapsed, 3),
                    )
            except Exception as e:
                logger.error("告警规则检查失败", 
                           rule_id=rule.id, 
                           error=str(e))
        elapsed_seconds = round(time.time() - started_at, 3)
        logger.info(
            f"{task_label}完成",
            total_rules=len(rules),
            triggered=triggered,
            elapsed_seconds=elapsed_seconds,
        )
        return {
            "total_rules": len(rules),
            "triggered": triggered,
            "elapsed_seconds": elapsed_seconds,
        }
        
    except Exception as e:
        logger.error(f"{task_label}失败", error=str(e))
        return {"error": str(e)}
    finally:
        db.close()
        if redis_client.get(lock_key) == lock_value:
            redis_client.delete(lock_key)


@shared_task
def check_fast_alerts():
    """
    高频检查接口类关键告警，避免被全量规则拖慢。
    """
    return _run_alert_checks(
        lock_key=FAST_CHECK_ALERTS_LOCK_KEY,
        lock_ttl_seconds=FAST_CHECK_ALERTS_LOCK_TTL_SECONDS,
        metric_types=FAST_ALERT_METRIC_TYPES,
        task_label="快速告警检查",
    )


@shared_task
def check_protocol_alerts():
    """
    独立检查协议邻居类告警，避免被端口、光模块、Exporter 等慢规则拖住。
    """
    return _run_alert_checks(
        lock_key=PROTOCOL_CHECK_ALERTS_LOCK_KEY,
        lock_ttl_seconds=PROTOCOL_CHECK_ALERTS_LOCK_TTL_SECONDS,
        metric_types=PROTOCOL_METRIC_TYPES,
        task_label="协议邻居告警检查",
    )


@shared_task
def check_device_health_alerts():
    """
    独立检查设备基础健康告警，避免CPU/内存/温度恢复被全量慢规则延迟。
    """
    return _run_alert_checks(
        lock_key=DEVICE_HEALTH_CHECK_ALERTS_LOCK_KEY,
        lock_ttl_seconds=DEVICE_HEALTH_CHECK_ALERTS_LOCK_TTL_SECONDS,
        metric_types=DEVICE_HEALTH_ALERT_METRIC_TYPES,
        task_label="设备健康告警检查",
    )


@shared_task
def check_alerts():
    """
    检查常规告警规则
    由Celery Beat定时调度
    """
    return _run_alert_checks(
        lock_key=CHECK_ALERTS_LOCK_KEY,
        lock_ttl_seconds=CHECK_ALERTS_LOCK_TTL_SECONDS,
        exclude_metric_types=FAST_ALERT_METRIC_TYPES | PROTOCOL_METRIC_TYPES | DEVICE_HEALTH_ALERT_METRIC_TYPES,
        task_label="常规告警检查",
    )


@shared_task
def prewarm_alert_rule_status_cache(limit: int = 500, batch_size: int = 8):
    """分批预热告警规则详情缓存，减少首次点击等待且避免打满后台。"""
    lock_value = f"{time.time()}:{uuid.uuid4()}"
    lock_acquired = bool(
        redis_client.set(
            RULE_STATUS_PREWARM_LOCK_KEY,
            lock_value,
            ex=55,
            nx=True,
        )
    )
    if not lock_acquired:
        return {"skipped": True, "reason": "prewarm already running"}

    db = SessionLocal()
    started_at = time.time()
    warmed = 0
    failed = 0
    try:
        rule_ids = [
            rule_id for (rule_id,) in db.query(AlertRule.id)
            .filter(AlertRule.enabled == 1)
            .order_by(AlertRule.id.asc())
            .all()
        ]
        if not rule_ids:
            return {"total_rules": 0, "warmed": 0, "failed": 0}

        cursor_raw = redis_client.get(RULE_STATUS_PREWARM_CURSOR_KEY)
        cursor = int(cursor_raw or 0) if str(cursor_raw or "").isdigit() else 0
        cursor = cursor % len(rule_ids)
        selected_rule_ids = [
            rule_ids[(cursor + offset) % len(rule_ids)]
            for offset in range(min(max(int(batch_size), 1), len(rule_ids)))
        ]

        for rule_id in selected_rule_ids:
            url = RULE_STATUS_PREWARM_URL.format(rule_id=rule_id)
            if limit != 500:
                url = f"http://api:8000/api/v1/alerts/rules/{rule_id}/status?limit={int(limit)}"
            try:
                with urllib.request.urlopen(url, timeout=6) as response:
                    response.read(256)
                warmed += 1
            except Exception as exc:
                failed += 1
                logger.warning("预热告警规则状态缓存失败", rule_id=rule_id, error=str(exc))

        redis_client.set(RULE_STATUS_PREWARM_CURSOR_KEY, (cursor + len(selected_rule_ids)) % len(rule_ids))
        elapsed_seconds = round(time.time() - started_at, 3)
        logger.info(
            "预热告警规则状态缓存完成",
            total_rules=len(rule_ids),
            batch_size=len(selected_rule_ids),
            warmed=warmed,
            failed=failed,
            elapsed_seconds=elapsed_seconds,
        )
        return {
            "total_rules": len(rule_ids),
            "batch_size": len(selected_rule_ids),
            "warmed": warmed,
            "failed": failed,
            "elapsed_seconds": elapsed_seconds,
        }
    finally:
        db.close()
        if redis_client.get(RULE_STATUS_PREWARM_LOCK_KEY) == lock_value:
            redis_client.delete(RULE_STATUS_PREWARM_LOCK_KEY)


def _check_single_rule(db: Session, rule: AlertRule) -> bool:
    """
    检查单个告警规则
    
    Returns:
        是否触发告警
    """
    # 获取规则适用的设备
    devices = []
    if rule.device_group_id:
        devices = db.query(Device).filter(
            Device.group_id == rule.device_group_id
        ).all()
    elif rule.device_ids:
        devices = db.query(Device).filter(
            Device.id.in_(rule.device_ids)
        ).all()
    else:
        # 应用到所有设备
        devices = db.query(Device).all()

    if rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
        devices = [
            device for device in devices
            if device.is_monitored and device.status in {"active", "online"} and device.ip_address
        ]
    elif rule.metric_type in INTERFACE_METRIC_TYPES or rule.metric_type in CIRCUIT_METRIC_TYPES:
        devices = [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and (
                _get_effective_monitor_source(device) == "asternos_exporter"
                or device.snmp_version
            )
        ]
    elif rule.metric_type in EXPORTER_METRIC_TYPES:
        devices = [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and _get_effective_monitor_source(device) == "asternos_exporter"
        ]
    elif rule.metric_type in PROTOCOL_METRIC_TYPES:
        devices = [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and (
                _get_effective_monitor_source(device) == "asternos_exporter"
                or device.snmp_version
            )
        ]
    elif rule.metric_type in COMMON_DEVICE_METRIC_TYPES:
        devices = [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and (
                _get_effective_monitor_source(device) == "asternos_exporter"
                or device.snmp_version
            )
        ]
    elif rule.metric_type in SNMP_TARGET_METRIC_TYPES or rule.metric_type in SNMP_DEVICE_METRIC_TYPES:
        devices = [
            device for device in devices
            if device.is_monitored
            and device.snmp_version
            and _get_effective_monitor_source(device) == "snmp"
            and (rule.metric_type not in HILLSTONE_ONLY_METRIC_TYPES or _is_hillstone_vendor(device.vendor))
            and device.status in {"active", "online"}
        ]
    elif _is_targeted_metric(rule.metric_type):
        devices = [
            device for device in devices
            if device.is_monitored
            and device.snmp_version
            and _get_effective_monitor_source(device) == "snmp"
            and device.status in {"active", "online"}
        ]
    else:
        devices = [
            device for device in devices
            if device.is_monitored and device.status in {"active", "online"}
        ]
    
    triggered = False
    
    for device in devices:
        targets = _get_metric_targets(db, device, rule.metric_type, rule.extra_config or {})
        if rule.metric_type in PROTOCOL_METRIC_TYPES:
            _resolve_disappeared_target_alerts(
                db,
                rule,
                device,
                {str(target.get("target_key")) for target in targets if target.get("target_key")},
            )
        if not targets:
            continue

        for target in targets:
            value = target.get("value")
            if value is None:
                continue

            should_alert = _evaluate_rule_condition(rule, float(value))

            existing_query = db.query(AlertHistory).filter(
                AlertHistory.rule_id == rule.id,
                AlertHistory.device_id == device.id,
                AlertHistory.status.in_(["firing", "acknowledged", "ignored", "snoozed"])
            )
            if target.get("target_key"):
                existing_query = existing_query.filter(AlertHistory.alert_target_key == target["target_key"])
            else:
                existing_query = existing_query.filter(AlertHistory.alert_target_key.is_(None))

            existing = existing_query.first()

            if should_alert:
                if _is_silenced(db, rule, device, target):
                    _clear_pending_alert(rule, device, target)
                    if existing:
                        existing.alert_value = float(value)
                        existing.updated_at = _utc_now()
                        existing.message = _build_alert_message(rule, device, float(value), target)
                        existing.alert_target_type = target.get("target_type")
                        existing.alert_target_key = target.get("target_key")
                        existing.alert_target_name = target.get("target_name")
                        if existing.status in {"firing", "acknowledged"}:
                            existing.status = "ignored"
                            existing.ignored_by = "alert_silence"
                            existing.ignored_at = _utc_now()
                        db.commit()
                    continue
                if existing:
                    existing.alert_value = float(value)
                    existing.updated_at = _utc_now()
                    existing.message = _build_alert_message(rule, device, float(value), target)
                    existing.alert_target_type = target.get("target_type")
                    existing.alert_target_key = target.get("target_key")
                    existing.alert_target_name = target.get("target_name")
                    if existing.status == "snoozed" and _is_snoozed_now(existing):
                        db.commit()
                        continue
                    was_ignored = existing.status == "ignored"
                    was_snoozed_expired = existing.status == "snoozed"
                    if was_ignored or was_snoozed_expired:
                        existing.status = "firing"
                        existing.started_at = _utc_now()
                        existing.ignored_by = None
                        existing.ignored_at = None
                        existing.resolved_by = None
                        existing.resolved_at = None
                        existing.resolution_note = None
                    db.commit()
                    if was_ignored or was_snoozed_expired:
                        _send_alert_notification.delay(existing.id)
                        logger.info(
                            "暂停/屏蔽解除后告警重新触发",
                            rule_id=rule.id,
                            alert_id=existing.id,
                            device_id=device.id,
                            target=target.get("target_name"),
                            value=value,
                        )
                    elif _should_repeat_notify(existing, rule):
                        _send_alert_notification.delay(existing.id)
                        logger.info(
                            "持续告警重复通知",
                            rule_id=rule.id,
                            alert_id=existing.id,
                            device_id=device.id,
                            target=target.get("target_name"),
                            value=value,
                            interval_seconds=rule.suppress_duration,
                        )
                else:
                    if not _duration_confirmed(rule, device, target, float(value)):
                        continue
                    _clear_pending_alert(rule, device, target)
                    alert = AlertHistory(
                        rule_id=rule.id,
                        device_id=device.id,
                        alert_value=float(value),
                        threshold=rule.threshold,
                        message=_build_alert_message(rule, device, float(value), target),
                        alert_target_type=target.get("target_type"),
                        alert_target_key=target.get("target_key"),
                        alert_target_name=target.get("target_name"),
                        status="firing",
                        started_at=_utc_now()
                    )
                    db.add(alert)
                    db.commit()
                    db.refresh(alert)
                    _ensure_alarm_id(db, alert)
                    _send_alert_notification.delay(alert.id)

                    logger.info(
                        "告警触发",
                        rule_id=rule.id,
                        device_id=device.id,
                        target=target.get("target_name"),
                        value=value,
                        threshold=rule.threshold
                    )
                triggered = True
            else:
                _clear_pending_alert(rule, device, target)
                if existing and existing.status in {"firing", "acknowledged", "ignored", "snoozed"}:
                    existing.alert_value = float(value)
                    existing.message = _build_alert_message(rule, device, float(value), target)
                    existing.alert_target_type = target.get("target_type")
                    existing.alert_target_key = target.get("target_key")
                    existing.alert_target_name = target.get("target_name")
                    existing.status = "resolved"
                    existing.resolved_at = _utc_now()
                    existing.resolved_by = "system"
                    db.commit()
                    _send_alert_event_notification.delay(existing.id, "auto_resolved", "system")
                    logger.info(
                        "告警恢复",
                        rule_id=rule.id,
                        device_id=device.id,
                        target=target.get("target_name"),
                    )
    
    return triggered


def _resolve_disappeared_target_alerts(
    db: Session,
    rule: AlertRule,
    device: Device,
    active_target_keys: set[str],
) -> None:
    """
    协议邻居配置被删除后，SNMP walk 不再返回该 peer。
    这种场景不应继续保持“协议 Down”告警，而应认为监控目标已消失并自动恢复。
    """
    active_alerts = db.query(AlertHistory).filter(
        AlertHistory.rule_id == rule.id,
        AlertHistory.device_id == device.id,
        AlertHistory.status.in_(["firing", "acknowledged", "ignored", "snoozed"]),
        AlertHistory.alert_target_key.isnot(None),
    ).all()

    for alert in active_alerts:
        if alert.alert_target_key in active_target_keys:
            continue
        alert.status = "resolved"
        alert.resolved_at = _utc_now()
        alert.resolved_by = "system"
        alert.resolution_note = "协议邻居已不在当前采集结果中，自动恢复"
        db.commit()
        _send_alert_event_notification.delay(alert.id, "auto_resolved", "system")
        logger.info(
            "协议目标消失，自动恢复告警",
            rule_id=rule.id,
            device_id=device.id,
            alert_id=alert.id,
            target_key=alert.alert_target_key,
        )


def _build_influx_last_value_query(
    measurement: str,
    device_id: int,
    field: str,
    start: str = "-10m",
    tag_filters: Optional[Dict[str, str]] = None,
) -> str:
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {start})
      |> filter(fn: (r) => r._measurement == "{measurement}")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => r._field == "{field}")
    '''
    for key, value in (tag_filters or {}).items():
        if value:
            escaped_value = str(value).replace('"', '\\"')
            flux += f'  |> filter(fn: (r) => r.{key} == "{escaped_value}")\n'
    flux += '  |> last()'
    return flux


def _build_influx_last_fields_query(
    measurement: str,
    device_id: int,
    fields: List[str],
    start: str = "-10m",
    tag_filters: Optional[Dict[str, str]] = None,
) -> str:
    field_filters = " or ".join([f'r._field == "{field}"' for field in fields])
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {start})
      |> filter(fn: (r) => r._measurement == "{measurement}")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => {field_filters})
    '''
    for key, value in (tag_filters or {}).items():
        if value:
            escaped_value = str(value).replace('"', '\\"')
            flux += f'  |> filter(fn: (r) => r.{key} == "{escaped_value}")\n'
    flux += '  |> group(columns: ["_field"])\n'
    flux += '  |> last()'
    return flux


def _build_influx_grouped_last_query(
    measurement: str,
    device_id: int,
    field: str,
    group_columns: List[str],
    start: str = "-10m",
    tag_filters: Optional[Dict[str, str]] = None,
) -> str:
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {start})
      |> filter(fn: (r) => r._measurement == "{measurement}")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => r._field == "{field}")
    '''
    for key, value in (tag_filters or {}).items():
        if value:
            escaped_value = str(value).replace('"', '\\"')
            flux += f'  |> filter(fn: (r) => r.{key} == "{escaped_value}")\n'
    columns = ", ".join([f'"{column}"' for column in group_columns])
    flux += f"  |> group(columns: [{columns}])\n"
    flux += "  |> last()"
    return flux


def _get_syslog_match_count(
    db: Session,
    device_id: int,
    keyword: str,
    lookback_seconds: int,
    severity_lte: Optional[int] = None,
) -> float:
    started_at = _utc_now() - timedelta(seconds=lookback_seconds)
    query = db.query(SyslogEvent).filter(
        SyslogEvent.device_id == device_id,
        SyslogEvent.created_at >= started_at,
        SyslogEvent.message.ilike(f"%{keyword}%"),
    )
    if severity_lte is not None:
        query = query.filter(SyslogEvent.severity.isnot(None), SyslogEvent.severity <= severity_lte)
    return float(query.count())


def _matches_text_filter(value: Optional[str], exact: Optional[str], pattern: Optional[str], excludes: Optional[str]) -> bool:
    text = value or ""
    if exact and text != exact:
        return False
    import re
    if pattern and not re.search(pattern, text):
        return False
    if excludes and re.search(excludes, text):
        return False
    return True


def _get_interface_last_fields(
    device_id: int,
    interface_name: str,
    fields: List[str],
    time_range: str,
) -> Dict[str, Optional[float]]:
    flux = _build_influx_last_fields_query(
        measurement="interface_monitoring",
        device_id=device_id,
        fields=fields,
        start=time_range,
        tag_filters={"interface_name": interface_name},
    )
    results = influx_client.query(flux)
    value_map: Dict[str, Optional[float]] = {field: None for field in fields}
    for item in results:
        field_name = item.get("field") or item.get("_field")
        if field_name in value_map and item.get("value") is not None:
            value_map[field_name] = float(item["value"])
    return value_map


def _get_circuit_targets(
    db: Session,
    device: Device,
    metric_type: str,
    extra_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    line_type = "internet" if metric_type == "internet_circuit_traffic_floor" else "private_line"
    default_time_range = "-90s" if metric_type in PROTOCOL_METRIC_TYPES else "-10m"
    time_range = str(extra_config.get("time_range") or default_time_range)
    query = db.query(Circuit).filter(
        Circuit.line_type == line_type,
        Circuit.status == "active",
        or_(Circuit.primary_device_id == device.id, Circuit.secondary_device_id == device.id),
    )

    if extra_config.get("circuit_id"):
        query = query.filter(Circuit.id == int(extra_config["circuit_id"]))
    if extra_config.get("circuit_name"):
        query = query.filter(Circuit.name == str(extra_config["circuit_name"]))
    if extra_config.get("datacenter_id"):
        query = query.filter(Circuit.datacenter_id == int(extra_config["datacenter_id"]))
    if extra_config.get("customer_id"):
        query = query.filter(Circuit.customer_id == int(extra_config["customer_id"]))

    targets: List[Dict[str, Any]] = []
    for circuit in query.all():
        endpoints = [
            ("primary", circuit.primary_device_id, circuit.primary_port_name),
            ("secondary", circuit.secondary_device_id, circuit.secondary_port_name),
        ]
        for role, endpoint_device_id, endpoint_port_name in endpoints:
            if endpoint_device_id != device.id or not endpoint_port_name:
                continue
            in_bps: Optional[float] = None
            out_bps: Optional[float] = None
            if _get_effective_monitor_source(device) == "asternos_exporter":
                try:
                    stats = _get_exporter_interface_stats_cached(device, endpoint_port_name)
                    in_bps = float(stats.get("in_bps")) if stats.get("in_bps") is not None else None
                    out_bps = float(stats.get("out_bps")) if stats.get("out_bps") is not None else None
                except Exception as exc:
                    logger.error(
                        "Exporter线路流量获取失败",
                        device_id=device.id,
                        circuit_id=circuit.id,
                        interface=endpoint_port_name,
                        error=str(exc),
                    )
            else:
                fields = _get_interface_last_fields(device.id, endpoint_port_name, ["in_bps", "out_bps"], time_range)
                in_bps = fields.get("in_bps")
                out_bps = fields.get("out_bps")
            if in_bps is None and out_bps is None:
                continue
            traffic_floor_value = round(max(in_bps or 0.0, out_bps or 0.0) / 1_000_000, 2)
            targets.append(
                {
                    "target_type": "circuit_port",
                    "target_key": f"circuit:{circuit.id}:{role}",
                    "target_name": f"{circuit.name} / {endpoint_port_name}",
                    "value": float(traffic_floor_value),
                }
            )
    return targets


def _normalize_interface_name(value: Optional[str]) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _find_interface_circuits(db: Session, device_id: int, interface_name: Optional[str]) -> List[Dict[str, Any]]:
    normalized_interface = _normalize_interface_name(interface_name)
    if not normalized_interface:
        return []
    circuits = db.query(Circuit).filter(
        Circuit.status == "active",
        or_(Circuit.primary_device_id == device_id, Circuit.secondary_device_id == device_id),
    ).all()
    matches: List[Dict[str, Any]] = []
    for circuit in circuits:
        endpoints = [
            ("primary", circuit.primary_device_id, circuit.primary_port_name),
            ("secondary", circuit.secondary_device_id, circuit.secondary_port_name),
        ]
        for role, endpoint_device_id, endpoint_port_name in endpoints:
            if endpoint_device_id != device_id:
                continue
            if _normalize_interface_name(endpoint_port_name) != normalized_interface:
                continue
            matches.append({
                "id": circuit.id,
                "name": circuit.name,
                "line_type": circuit.line_type,
                "role": role,
                "port_name": endpoint_port_name,
                "bandwidth_mbps": circuit.bandwidth_mbps,
                "customer_name": circuit.customer_ref.name if circuit.customer_ref else None,
                "operator_name": circuit.operator_name,
            })
    return matches


def _line_type_label(line_type: Optional[str]) -> str:
    return "专线" if line_type == "private_line" else "公网"


def _enrich_interface_target_with_resources(db: Session, device: Device, target: Dict[str, Any]) -> Dict[str, Any]:
    if target.get("target_type") != "interface":
        return target
    circuits = _find_interface_circuits(db, device.id, target.get("target_name"))
    if not circuits:
        return target
    resource_names = [
        f"{_line_type_label(item.get('line_type'))}【{item.get('name')}】"
        for item in circuits
        if item.get("name")
    ]
    role_names = {"primary": "主用端口", "secondary": "备用端口"}
    target["resources"] = circuits
    target["resource_names"] = resource_names
    target["target_name"] = f"{target.get('target_name')} / {'、'.join(resource_names)}"
    resource_parts = []
    for item in circuits:
        role = item.get("role")
        role_text = role_names.get(str(role), str(role)) if role else ""
        resource_parts.append(
            f"{_line_type_label(item.get('line_type'))}【{item.get('name')}】"
            f"{f'（{role_text}）' if role_text else ''}"
        )
    target["resource_text"] = "；".join(resource_parts)
    return target


def _label_state_to_float(value: Any, value_map: Optional[Dict[str, Any]] = None) -> Optional[float]:
    if value is None:
        return None
    normalized = str(value).strip()
    if not normalized:
        return None

    custom_map = {str(key).lower(): mapped for key, mapped in (value_map or {}).items()}
    default_map = {
        "up": 1.0,
        "ok": 1.0,
        "normal": 1.0,
        "enabled": 1.0,
        "active": 1.0,
        "established": 1.0,
        "true": 1.0,
        "1": 1.0,
        "down": 0.0,
        "error": 0.0,
        "failed": 0.0,
        "disabled": 0.0,
        "inactive": 0.0,
        "false": 0.0,
        "0": 0.0,
    }
    state_map = {**default_map, **custom_map}
    mapped = state_map.get(normalized.lower())
    if mapped is None:
        return None
    try:
        return float(mapped)
    except Exception:
        return None


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _protocol_state_is_up(protocol: str, state_text: str, value: Optional[float]) -> bool:
    text = (state_text or "").strip().lower()
    if protocol == "bgp":
        return "established" in text or (value is not None and value >= 1)
    if protocol == "ospf":
        return "full" in text or (value is not None and value >= 1)
    return value is not None and value >= 1


def _get_exporter_protocol_targets(device: Device, protocol: str, extra_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    try:
        metrics = _scrape_exporter_metrics_cached(device)
    except Exception as exc:
        logger.error("Exporter协议指标抓取失败", device_id=device.id, protocol=protocol, error=str(exc))
        return []

    metric_base = {
        "bgp": "bgp_status",
        "ospf": "ospf_status",
        "bfd": "bfd_status",
    }.get(protocol)
    if not metric_base:
        return []

    targets: List[Dict[str, Any]] = []
    for row in asternos_exporter_client._rows(metrics, metric_base):
        labels = row.get("metric", {}) or {}
        if protocol == "ospf":
            peer = str(labels.get("Neighbor") or labels.get("neighbor") or labels.get("peer") or labels.get("Address") or "").strip()
            state_text = str(labels.get("State") or labels.get("state") or labels.get("status") or "").strip()
        else:
            peer = str(labels.get("peer") or labels.get("neighbor") or labels.get("Neighbor") or "").strip()
            state_text = str(labels.get("status") or labels.get("state") or labels.get("State") or "").strip()

        if not peer:
            continue
        if extra_config.get("peer") and str(peer) != str(extra_config.get("peer")):
            continue
        if extra_config.get("peer_regex") and not re.search(str(extra_config.get("peer_regex")), peer):
            continue

        raw_value = _safe_float(row.get("value"))
        value = 1.0 if _protocol_state_is_up(protocol, state_text, raw_value) else 0.0
        targets.append({
            "target_type": "protocol_peer",
            "target_key": f"{protocol}:{peer}",
            "target_name": f"{protocol.upper()} 邻居 {peer}",
            "value": value,
            "state_text": state_text or "-",
        })

    return targets


def _matches_label_filters(labels: Dict[str, Any], extra_config: Dict[str, Any]) -> bool:
    for key, expected in (extra_config.get("label_filters") or {}).items():
        if str(labels.get(str(key), "")) != str(expected):
            return False

    for key, pattern in (extra_config.get("label_regex") or {}).items():
        if not re.search(str(pattern), str(labels.get(str(key), ""))):
            return False

    include_pattern = extra_config.get("include_label_regex")
    if include_pattern:
        label_text = " ".join(f"{key}={value}" for key, value in labels.items())
        if not re.search(str(include_pattern), label_text):
            return False

    exclude_pattern = extra_config.get("exclude_label_regex")
    if exclude_pattern:
        label_text = " ".join(f"{key}={value}" for key, value in labels.items())
        if re.search(str(exclude_pattern), label_text):
            return False

    return True


def _scrape_exporter_metrics_cached(device: Device) -> Dict[str, List[Dict[str, Any]]]:
    now = time.monotonic()
    with _EXPORTER_SCRAPE_CACHE_LOCK:
        cached = _EXPORTER_SCRAPE_CACHE.get(device.id)
        if cached and now - float(cached.get("created_at", 0.0)) < EXPORTER_SCRAPE_CACHE_TTL_SECONDS:
            return cached.get("metrics", [])

    try:
        metrics = asyncio.run(asternos_exporter_client.scrape(device))
    except Exception as exc:
        logger.warning(
            "AsterNOS Exporter抓取失败，短时间内跳过重复请求",
            device_id=device.id,
            ip_address=device.ip_address,
            error=str(exc),
        )
        metrics = {}
    with _EXPORTER_SCRAPE_CACHE_LOCK:
        _EXPORTER_SCRAPE_CACHE[device.id] = {
            "created_at": now,
            "metrics": metrics,
        }
    return metrics


def _list_exporter_interfaces_cached(device: Device) -> List[Dict[str, Any]]:
    metrics = _scrape_exporter_metrics_cached(device)
    interfaces: List[Dict[str, Any]] = []
    for position, row in enumerate(asternos_exporter_client._rows(metrics, "interface_info"), start=1):
        labels = row.get("metric", {}) or {}
        interface_name = labels.get("device")
        if not interface_name:
            continue
        try:
            index = int(labels.get("index") or position)
        except ValueError:
            index = position
        speed_bps = None
        speed_mbps = labels.get("speed")
        if speed_mbps not in (None, ""):
            try:
                speed_bps = float(speed_mbps) * 1_000_000
            except ValueError:
                speed_bps = None
        interfaces.append(
            {
                "index": index,
                "name": interface_name,
                "description": labels.get("description") or labels.get("alias") or interface_name,
                "alias": labels.get("alias") or None,
                "admin_status": "up" if labels.get("admin_status") == "up" else "down",
                "oper_status": "up" if labels.get("operational_status") == "up" else "down",
                "speed_bps": speed_bps,
            }
        )
    interfaces.sort(key=lambda item: item["index"])
    return interfaces


def _get_exporter_interface_stats_cached(device: Device, interface_name: str) -> Dict[str, Any]:
    metrics = _scrape_exporter_metrics_cached(device)
    info = asternos_exporter_client._by_base_metric_label(metrics, "interface_info", "device", interface_name)
    labels = (info or {}).get("metric", {}) or {}
    result: Dict[str, Any] = {
        "name": interface_name,
        "description": labels.get("description") or labels.get("alias") or interface_name,
        "alias": labels.get("alias") or None,
    }
    if labels:
        result["admin_status"] = "up" if labels.get("admin_status") == "up" else "down"
        result["oper_status"] = "up" if labels.get("operational_status") == "up" else "down"
        if labels.get("speed"):
            result["speed_bps"] = float(labels["speed"]) * 1_000_000

    metric_map = {
        "in_octets": "interface_receive_bytes_total",
        "out_octets": "interface_transmit_bytes_total",
        "in_bps": "interface_receive_rate_bps",
        "out_bps": "interface_transmit_rate_bps",
        "in_errors": "interface_receive_errs_total",
        "out_errors": "interface_transmit_errs_total",
        "in_discards": "interface_receive_drops_total",
        "out_discards": "interface_transmit_drops_total",
        "in_utilization_percent": "interface_receive_util",
        "out_utilization_percent": "interface_transmit_util",
    }
    for field, base_name in metric_map.items():
        row = asternos_exporter_client._by_base_metric_label(metrics, base_name, "device", interface_name)
        if row:
            result[field] = row.get("value")

    for field, base_name in {
        "rx_power": "dom_optic_rx_power",
        "tx_power": "dom_optic_tx_power",
        "optic_temperature": "dom_optic_tempt",
    }.items():
        row = asternos_exporter_client._by_base_metric_label(metrics, base_name, "interface", interface_name)
        if row:
            result[field] = row.get("value")

    return result


def _get_exporter_device_metrics_cached(device: Device) -> Dict[str, Any]:
    metrics = _scrape_exporter_metrics_cached(device)
    device_info = (asternos_exporter_client._rows(metrics, "device_info") or [{}])[0].get("metric", {})
    return {
        "info": device_info,
        "cpu_usage": asternos_exporter_client._first(metrics, "device_cpu_usage"),
        "memory_usage": asternos_exporter_client._first(metrics, "device_memory_usage"),
        "system_status": asternos_exporter_client._first(metrics, "device_system_status"),
        "uptime": asternos_exporter_client._first(metrics, "device_up_time"),
    }


def _delta_cache_key(device_id: int, metric_base: str, target_key: str) -> str:
    return f"alerts:delta:{device_id}:{metric_base}:{target_key}"


def _counter_delta(device_id: int, metric_base: str, target_key: str, current_value: float) -> float:
    cache_key = _delta_cache_key(device_id, metric_base, target_key)
    previous_raw = redis_client.get(cache_key)
    redis_client.setex(
        cache_key,
        EXPORTER_DELTA_CACHE_TTL_SECONDS,
        json.dumps({"value": current_value, "time": _utc_now().isoformat()}),
    )
    if not previous_raw:
        return 0.0
    try:
        previous_value = float(json.loads(previous_raw).get("value"))
    except Exception:
        return 0.0
    delta = current_value - previous_value
    return delta if delta >= 0 else current_value


def _get_exporter_metric_targets(device: Device, extra_config: Dict[str, Any]) -> List[Dict[str, Any]]:
    metric_base = str(extra_config.get("metric_base") or extra_config.get("metric_name") or "").strip()
    if not metric_base:
        return []
    if metric_base.startswith(asternos_exporter_client.ASTERNOS_PREFIX):
        metric_base = metric_base.removeprefix(asternos_exporter_client.ASTERNOS_PREFIX)

    try:
        metrics = _scrape_exporter_metrics_cached(device)
    except Exception as exc:
        logger.error("Exporter指标抓取失败", device_id=device.id, metric_base=metric_base, error=str(exc))
        return []

    rows = asternos_exporter_client._rows(metrics, metric_base)
    value_label = extra_config.get("value_label")
    value_map = extra_config.get("value_map") if isinstance(extra_config.get("value_map"), dict) else None
    target_label_keys = extra_config.get("target_label_keys") or []
    if isinstance(target_label_keys, str):
        target_label_keys = [item.strip() for item in target_label_keys.split(",") if item.strip()]

    targets: List[Dict[str, Any]] = []
    for position, row in enumerate(rows, start=1):
        labels = row.get("metric", {}) or {}
        if not _matches_label_filters(labels, extra_config):
            continue

        if value_label:
            value = _label_state_to_float(labels.get(str(value_label)), value_map)
        else:
            raw_value = row.get("value")
            try:
                value = float(raw_value) if raw_value is not None else None
            except Exception:
                value = None
        if value is None:
            continue

        target_parts = [
            f"{key}={labels.get(str(key))}"
            for key in target_label_keys
            if labels.get(str(key)) not in (None, "")
        ]
        if not target_parts:
            for fallback_key in ["device", "interface", "port", "queue", "peer", "neighbor", "resource", "sensor", "name", "process_name", "docker_name", "vni"]:
                if labels.get(fallback_key) not in (None, ""):
                    target_parts.append(f"{fallback_key}={labels[fallback_key]}")
        target_name = " / ".join(target_parts) if target_parts else metric_base
        target_key = f"{metric_base}:{target_name or position}"
        if extra_config.get("use_delta"):
            value = _counter_delta(device.id, metric_base, target_key, float(value))

        targets.append(
            {
                "target_type": "exporter_metric",
                "target_key": target_key,
                "target_name": target_name,
                "value": float(value),
            }
        )

    return targets


def _get_metric_targets(db: Session, device: Device, metric_type: str, extra_config: Optional[Dict[str, Any]] = None) -> List[Dict[str, Any]]:
    extra_config = extra_config or {}
    targets: List[Dict[str, Any]] = []

    if metric_type in EXPORTER_METRIC_TYPES:
        return _get_exporter_metric_targets(device, extra_config)

    if metric_type == "syslog_keyword":
        value = _get_metric_value(db, device.id, metric_type, extra_config)
        if value is None:
            return []
        keyword = str(extra_config.get("keyword") or extra_config.get("pattern") or "").strip()
        return [{
            "target_type": "syslog",
            "target_key": keyword or device.ip_address,
            "target_name": f"日志关键字 {keyword}" if keyword else "日志关键字",
            "value": value,
        }]

    if metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
        value = _get_metric_value(db, device.id, metric_type, extra_config)
        if value is None:
            return []
        return [{
            "target_type": "device",
            "target_key": str(device.id),
            "target_name": None,
            "value": value,
        }]

    if metric_type in CIRCUIT_METRIC_TYPES:
        return _get_circuit_targets(db, device, metric_type, extra_config)

    if metric_type == "device_temperature":
        if _get_effective_monitor_source(device) == "asternos_exporter":
            exporter_config = {
                "metric_base": "device_sensor_tempt",
                "target_label_keys": ["name", "sensor"],
                **extra_config,
            }
            return _get_exporter_metric_targets(device, exporter_config)

        flux = _build_influx_grouped_last_query(
            measurement="snmp_temperature",
            device_id=device.id,
            field="temperature",
            group_columns=["sensor"],
            start=str(extra_config.get("time_range") or "-10m"),
        )
        for item in influx_client.query(flux):
            value = item.get("value")
            if value is None:
                continue
            sensor = str(item.get("sensor") or "temperature")
            targets.append({
                "target_type": "temperature_sensor",
                "target_key": f"temperature:{sensor}",
                "target_name": sensor,
                "value": float(value),
            })
        return targets

    snmp_target_map = {
        "snmp_storage_usage": ("snmp_storage", "usage_percent", ["storage"], None),
        "snmp_fan_status": ("snmp_hardware", "up", ["component_type", "component"], "fan"),
        "snmp_power_status": ("snmp_hardware", "up", ["component_type", "component"], "power"),
        "snmp_pak_buffer_usage": ("snmp_pak_buffer", "usage_percent", ["buffer"], None),
        "snmp_ipsec_tunnel_status": ("snmp_ipsec_tunnel", "up", ["tunnel", "peer"], None),
        "snmp_snat_resource_usage": ("snmp_snat_resource", "usage_percent", ["rule", "protocol"], None),
        "snmp_dnat_server_status": ("snmp_dnat_server", "up", ["server"], None),
        "snmp_slb_virtual_server_status": ("snmp_slb_virtual_server", "up", ["virtual_server"], None),
    }

    if metric_type in snmp_target_map:
        measurement, field, group_columns, component_type = snmp_target_map[metric_type]
        tag_filters = {"component_type": component_type} if component_type else None
        result = _build_influx_grouped_last_query(
            measurement=measurement,
            device_id=device.id,
            field=field,
            group_columns=group_columns,
            start=str(extra_config.get("time_range") or "-10m"),
            tag_filters=tag_filters,
        )
        for item in influx_client.query(result):
            value = item.get("value")
            if value is None:
                continue
            if metric_type == "snmp_storage_usage":
                target_name = str(item.get("storage") or "storage")
                target_key = f"storage:{target_name}"
                target_type = "storage"
            elif metric_type == "snmp_pak_buffer_usage":
                target_name = f"Packet Buffer {item.get('buffer') or '-'}"
                target_key = f"pak_buffer:{item.get('buffer') or target_name}"
                target_type = "pak_buffer"
            elif metric_type == "snmp_ipsec_tunnel_status":
                target_name = str(item.get("tunnel") or "-")
                peer = item.get("peer")
                if peer:
                    target_name = f"{target_name} ({peer})"
                target_key = f"ipsec:{item.get('tunnel') or target_name}:{peer or ''}"
                target_type = "ipsec_tunnel"
            elif metric_type == "snmp_snat_resource_usage":
                rule_name = str(item.get("rule") or "-")
                protocol = str(item.get("protocol") or "-").upper()
                target_name = f"SNAT {rule_name} {protocol}"
                target_key = f"snat:{rule_name}:{protocol}"
                target_type = "snat_resource"
            elif metric_type == "snmp_dnat_server_status":
                target_name = str(item.get("server") or "-")
                target_key = f"dnat:{target_name}"
                target_type = "dnat_server"
            elif metric_type == "snmp_slb_virtual_server_status":
                target_name = str(item.get("virtual_server") or "-")
                target_key = f"slb_vs:{target_name}"
                target_type = "slb_virtual_server"
            else:
                component = str(item.get("component") or "-")
                target_name = f"{'风扇' if component_type == 'fan' else '电源'} {component}"
                target_key = f"{component_type}:{component}"
                target_type = component_type or "hardware"
            targets.append({
                "target_type": target_type,
                "target_key": target_key,
                "target_name": target_name,
                "value": float(value),
            })
        return targets

    measurement_map = {
        "interface_oper_status": ("interface_monitoring", "oper_status"),
        "interface_admin_up_oper_down": ("interface_monitoring", "admin_up_oper_down"),
        "interface_in_errors_delta": ("interface_monitoring", "in_errors_delta"),
        "interface_out_errors_delta": ("interface_monitoring", "out_errors_delta"),
        "interface_in_discards_delta": ("interface_monitoring", "in_discards_delta"),
        "interface_out_discards_delta": ("interface_monitoring", "out_discards_delta"),
        "interface_in_broadcast_pps": ("interface_monitoring", "in_broadcast_pps"),
        "interface_out_broadcast_pps": ("interface_monitoring", "out_broadcast_pps"),
        "optical_rx_power": ("optical_monitoring", "rx_power"),
        "optical_tx_power": ("optical_monitoring", "tx_power"),
        "bgp_peer_state": ("protocol_status", "state_up"),
        "ospf_neighbor_state": ("protocol_status", "state_up"),
        "bfd_session_state": ("protocol_status", "state_up"),
    }

    if metric_type not in measurement_map:
        value = _get_metric_value(db, device.id, metric_type, extra_config)
        if value is None:
            return []
        return [{
            "target_type": "device",
            "target_key": str(device.id),
            "target_name": device.name,
            "value": value,
        }]

    measurement, field = measurement_map[metric_type]
    time_range = str(extra_config.get("time_range") or "-10m")
    targets: List[Dict[str, Any]] = []

    if metric_type in INTERFACE_METRIC_TYPES:
        if _get_effective_monitor_source(device) == "asternos_exporter":
            try:
                interfaces = _list_exporter_interfaces_cached(device)
            except Exception as exc:
                logger.error("Exporter接口列表获取失败", device_id=device.id, error=str(exc))
                return targets

            metric_field_map = {
                "interface_oper_status": "oper_status",
                "interface_admin_up_oper_down": "admin_up_oper_down",
                "interface_in_errors_delta": "in_errors",
                "interface_out_errors_delta": "out_errors",
                "interface_in_discards_delta": "in_discards",
                "interface_out_discards_delta": "out_discards",
                "interface_in_broadcast_pps": "in_broadcast_pps",
                "interface_out_broadcast_pps": "out_broadcast_pps",
                "optical_rx_power": "rx_power",
                "optical_tx_power": "tx_power",
            }

            for interface in interfaces:
                interface_name = interface.get("name")
                interface_index = interface.get("index")
                if not _matches_text_filter(
                    interface_name,
                    str(extra_config.get("interface_name")) if extra_config.get("interface_name") else None,
                    str(extra_config.get("interface_regex")) if extra_config.get("interface_regex") else None,
                    str(extra_config.get("exclude_interface_regex")) if extra_config.get("exclude_interface_regex") else None,
                ):
                    continue
                if extra_config.get("interface_index") and str(interface_index) != str(extra_config.get("interface_index")):
                    continue
                if interface_name and any(skip in interface_name.lower() for skip in ["loopback", "null", "vlanif"]) and not extra_config.get("include_logical_interfaces"):
                    continue
                try:
                    stats = _get_exporter_interface_stats_cached(device, interface_name)
                except Exception as exc:
                    logger.error(
                        "Exporter接口指标获取失败",
                        device_id=device.id,
                        interface=interface_name,
                        error=str(exc),
                    )
                    continue

                if metric_type == "interface_admin_up_oper_down":
                    value = 1.0 if stats.get("admin_status") == "up" and stats.get("oper_status") == "down" else 0.0
                elif metric_type == "interface_oper_status":
                    value = 1.0 if stats.get("oper_status") == "up" else 0.0
                else:
                    metric_field = metric_field_map.get(metric_type)
                    raw_value = stats.get(metric_field) if metric_field else None
                    if raw_value is None:
                        continue
                    value = float(raw_value)
                    if metric_type in {
                        "interface_in_errors_delta",
                        "interface_out_errors_delta",
                        "interface_in_discards_delta",
                        "interface_out_discards_delta",
                    }:
                        value = _counter_delta(
                            device.id,
                            metric_type,
                            str(interface_index or interface_name),
                            value,
                        )

                targets.append(_enrich_interface_target_with_resources(db, device, {
                    "target_type": "interface",
                    "target_key": str(interface_index or interface_name),
                    "target_name": interface_name or f"if{interface_index}",
                    "value": float(value),
                }))
            return targets

        flux = _build_influx_grouped_last_query(
            measurement=measurement,
            device_id=device.id,
            field=field,
            group_columns=["interface_index", "interface_name"],
            start=time_range,
        )
        result = influx_client.query(flux)
        for item in result:
            interface_name = item.get("interface_name")
            interface_index = item.get("interface_index")
            if not _matches_text_filter(
                interface_name,
                str(extra_config.get("interface_name")) if extra_config.get("interface_name") else None,
                str(extra_config.get("interface_regex")) if extra_config.get("interface_regex") else None,
                str(extra_config.get("exclude_interface_regex")) if extra_config.get("exclude_interface_regex") else None,
            ):
                continue
            if extra_config.get("interface_index") and str(interface_index) != str(extra_config.get("interface_index")):
                continue
            if interface_name and any(skip in interface_name.lower() for skip in ["loopback", "null", "vlanif"]) and not extra_config.get("include_logical_interfaces"):
                continue
            value = item.get("value")
            if value is None:
                continue
            targets.append(_enrich_interface_target_with_resources(db, device, {
                "target_type": "interface",
                "target_key": str(interface_index or interface_name),
                "target_name": interface_name or f"if{interface_index}",
                "value": float(value),
            }))
        return targets

    if metric_type in PROTOCOL_METRIC_TYPES:
        protocol = {
            "bgp_peer_state": "bgp",
            "ospf_neighbor_state": "ospf",
            "bfd_session_state": "bfd",
        }[metric_type]

        if _get_effective_monitor_source(device) == "asternos_exporter":
            return _get_exporter_protocol_targets(device, protocol, extra_config)

        flux = _build_influx_grouped_last_query(
            measurement=measurement,
            device_id=device.id,
            field=field,
            group_columns=["protocol", "peer", "state_text"],
            start=time_range,
            tag_filters={"protocol": protocol},
        )
        result = influx_client.query(flux)
        for item in result:
            peer = item.get("peer")
            if extra_config.get("peer") and str(peer) != str(extra_config.get("peer")):
                continue
            value = item.get("value")
            if value is None:
                continue
            targets.append({
                "target_type": "protocol_peer",
                "target_key": f"{protocol}:{peer}",
                "target_name": f"{protocol.upper()} 邻居 {peer}",
                "value": float(value),
            })
        return targets

    return targets


def _get_metric_value(db: Session, device_id: int, metric_type: str, extra_config: Optional[Dict[str, Any]] = None) -> Optional[float]:
    """获取指标值"""
    extra_config = extra_config or {}
    try:
        device = db.query(Device).filter(Device.id == device_id).first()
        if device and _get_effective_monitor_source(device) == "asternos_exporter":
            try:
                summary = _get_exporter_device_metrics_cached(device)
                metric_value_map = {
                    "snmp_cpu": summary.get("cpu_usage"),
                    "snmp_memory": summary.get("memory_usage"),
                    "device_status": summary.get("system_status"),
                }
                value = metric_value_map.get(metric_type)
                if value is not None:
                    return float(value)
            except Exception as exc:
                logger.error("AsterNOS Exporter设备指标查询失败", device_id=device_id, metric_type=metric_type, error=str(exc))

        if metric_type in INTERFACE_METRIC_TYPES and (extra_config.get("interface_name") or extra_config.get("interface_index")):
            if device and _get_effective_monitor_source(device) == "asternos_exporter":
                interface_name = extra_config.get("interface_name")
                if not interface_name and extra_config.get("interface_index"):
                    try:
                        interfaces = _list_exporter_interfaces_cached(device)
                        target_interface = next(
                            (item for item in interfaces if str(item.get("index")) == str(extra_config.get("interface_index"))),
                            None,
                        )
                        interface_name = target_interface.get("name") if target_interface else None
                    except Exception as exc:
                        logger.error("Exporter接口索引解析失败", device_id=device_id, error=str(exc))
                        interface_name = None
                if interface_name:
                    try:
                        stats = _get_exporter_interface_stats_cached(device, str(interface_name))
                        metric_value_map = {
                            "interface_oper_status": 1.0 if stats.get("oper_status") == "up" else 0.0,
                            "interface_admin_up_oper_down": 1.0 if stats.get("admin_status") == "up" and stats.get("oper_status") == "down" else 0.0,
                            "interface_in_errors_delta": float(stats.get("in_errors") or 0.0),
                            "interface_out_errors_delta": float(stats.get("out_errors") or 0.0),
                            "interface_in_discards_delta": float(stats.get("in_discards") or 0.0),
                            "interface_out_discards_delta": float(stats.get("out_discards") or 0.0),
                            "optical_rx_power": float(stats.get("rx_power")) if stats.get("rx_power") is not None else None,
                            "optical_tx_power": float(stats.get("tx_power")) if stats.get("tx_power") is not None else None,
                        }
                        exporter_value = metric_value_map.get(metric_type)
                        if exporter_value is not None:
                            return exporter_value
                    except Exception as exc:
                        logger.error("Exporter接口指标查询失败", device_id=device_id, interface=interface_name, error=str(exc))

        # 映射metric_type到InfluxDB查询
        measurement_map = {
            "snmp_cpu": ("snmp_metrics", "cpu", "usage"),
            "snmp_memory": ("snmp_metrics", "memory", "usage_percent"),
            "device_temperature": ("snmp_temperature", None, "temperature"),
            "snmp_temperature": ("snmp_temperature", None, "temperature"),
            "snmp_session_usage": ("snmp_sessions", None, "usage_percent"),
            "snmp_ha_status": ("snmp_system", None, "ha_status"),
            "snmp_session_queue_full_drop_delta": ("snmp_system", None, "pending_session_queue_full_drop"),
            "device_status": ("device_status", None, "status"),
            "device_reachability": ("device_reachability", None, "reachable"),
            "interface_oper_status": ("interface_monitoring", None, "oper_status"),
            "interface_admin_up_oper_down": ("interface_monitoring", None, "admin_up_oper_down"),
            "interface_in_errors_delta": ("interface_monitoring", None, "in_errors_delta"),
            "interface_out_errors_delta": ("interface_monitoring", None, "out_errors_delta"),
            "interface_in_discards_delta": ("interface_monitoring", None, "in_discards_delta"),
            "interface_out_discards_delta": ("interface_monitoring", None, "out_discards_delta"),
            "interface_in_broadcast_pps": ("interface_monitoring", None, "in_broadcast_pps"),
            "interface_out_broadcast_pps": ("interface_monitoring", None, "out_broadcast_pps"),
            "bgp_peer_state": ("protocol_status", None, "state_up"),
            "ospf_neighbor_state": ("protocol_status", None, "state_up"),
            "bfd_session_state": ("protocol_status", None, "state_up"),
            "optical_rx_power": ("optical_monitoring", None, "rx_power"),
            "optical_tx_power": ("optical_monitoring", None, "tx_power"),
        }

        if metric_type == "syslog_keyword":
            keyword = str(extra_config.get("keyword") or extra_config.get("pattern") or "").strip()
            if not keyword:
                return None
            lookback_seconds = int(extra_config.get("lookback_seconds") or 300)
            severity_lte = extra_config.get("severity_lte")
            severity_value = int(severity_lte) if severity_lte is not None and str(severity_lte).isdigit() else None
            return _get_syslog_match_count(db, device_id, keyword, lookback_seconds, severity_value)

        if metric_type not in measurement_map:
            # 尝试从InfluxDB获取最新值
            return influx_client.get_last_value(
                measurement="snmp_metrics",
                device_id=device_id,
                field="value"
            )

        measurement, metric, field = measurement_map[metric_type]

        tag_filters: Dict[str, str] = {}
        time_range = str(extra_config.get("time_range") or "-10m")
        if metric:
            tag_filters["metric_type"] = metric
        if metric_type.startswith("interface_"):
            if extra_config.get("interface_name"):
                tag_filters["interface_name"] = str(extra_config["interface_name"])
            elif extra_config.get("interface_index"):
                tag_filters["interface_index"] = str(extra_config["interface_index"])
        if metric_type in {"bgp_peer_state", "ospf_neighbor_state", "bfd_session_state"}:
            protocol_map = {
                "bgp_peer_state": "bgp",
                "ospf_neighbor_state": "ospf",
                "bfd_session_state": "bfd",
            }
            tag_filters["protocol"] = protocol_map[metric_type]
            if extra_config.get("peer"):
                tag_filters["peer"] = str(extra_config["peer"])
        if metric_type in {"optical_rx_power", "optical_tx_power"}:
            if extra_config.get("interface_name"):
                tag_filters["interface_name"] = str(extra_config["interface_name"])
            elif extra_config.get("interface_index"):
                tag_filters["interface_index"] = str(extra_config["interface_index"])

        flux = _build_influx_last_value_query(
            measurement=measurement,
            device_id=device_id,
            field=field,
            start=time_range,
            tag_filters=tag_filters,
        )

        result = influx_client.query(flux)
        if result:
            value = float(result[0].get('value', 0))
            if metric_type == "snmp_session_queue_full_drop_delta":
                return _counter_delta(device_id, metric_type, "device", value)
            return value
        
        return None
        
    except Exception as e:
        logger.error("获取指标值失败", 
                    device_id=device_id, 
                    metric_type=metric_type,
                    error=str(e))
        return None


def _evaluate_condition(value: float, condition: str, threshold: float) -> bool:
    """评估告警条件"""
    if condition == ">":
        return value > threshold
    elif condition == ">=":
        return value >= threshold
    elif condition == "<":
        return value < threshold
    elif condition == "<=":
        return value <= threshold
    elif condition == "==":
        return value == threshold
    elif condition == "!=":
        return value != threshold
    return False


def _normalize_percent_metric_value(value: float) -> float:
    """百分比指标兼容 0.85 和 85 两种写法。"""
    if 0 <= value <= 1:
        return value * 100
    return value


def _evaluate_rule_condition(rule: AlertRule, value: float) -> bool:
    """按指标类型评估告警条件。"""
    threshold = rule.threshold
    if threshold is None:
        return False

    compare_value = float(value)
    compare_threshold = float(threshold)
    if rule.metric_type in PERCENT_METRIC_TYPES:
        compare_value = _normalize_percent_metric_value(compare_value)
        compare_threshold = _normalize_percent_metric_value(compare_threshold)

    return _evaluate_condition(compare_value, rule.condition, compare_threshold)


def _exporter_metric_base(rule: AlertRule) -> str:
    extra_config = rule.extra_config or {}
    metric_base = str(extra_config.get("metric_base") or extra_config.get("metric_name") or "").strip()
    if metric_base.startswith(asternos_exporter_client.ASTERNOS_PREFIX):
        metric_base = metric_base.removeprefix(asternos_exporter_client.ASTERNOS_PREFIX)
    return metric_base.lower()


def _numeric_detail_label(rule: AlertRule) -> Optional[str]:
    extra_config = rule.extra_config or {}
    if rule.metric_type in METRIC_VALUE_LABELS:
        return METRIC_VALUE_LABELS[rule.metric_type]
    if rule.metric_type != "exporter_metric":
        return None

    explicit_label = str(extra_config.get("value_label_text") or extra_config.get("metric_label") or "").strip()
    metric_base = _exporter_metric_base(rule)
    normalized = f"{explicit_label} {metric_base}".lower()

    if any(token in normalized for token in ["cpu", "processor"]):
        return "当前CPU使用率"
    if any(token in normalized for token in ["memory", "mem", "内存"]):
        return "当前内存使用率"
    if any(token in normalized for token in ["temperature", "tempt", "temp", "温度"]):
        return "当前温度"
    if any(token in normalized for token in ["rx_power", "receive_power", "收光"]):
        return "当前收光功率"
    if any(token in normalized for token in ["tx_power", "transmit_power", "发光"]):
        return "当前发光功率"
    if any(token in normalized for token in ["buffer", "缓存"]):
        return "当前Buffer使用量"
    if any(token in normalized for token in ["queue", "队列"]):
        return "当前队列指标"
    if explicit_label:
        return explicit_label if explicit_label.startswith("当前") else f"当前{explicit_label}"
    return None


def _numeric_detail_unit(rule: AlertRule) -> str:
    extra_config = rule.extra_config or {}
    explicit_unit = str(extra_config.get("unit") or extra_config.get("value_unit") or "").strip()
    if explicit_unit:
        return explicit_unit
    if rule.metric_type in PERCENT_METRIC_TYPES:
        return "%"
    if rule.metric_type in {"device_temperature", "snmp_temperature"}:
        return "℃"
    if rule.metric_type == "snmp_session_queue_full_drop_delta":
        return "包"
    if rule.metric_type in {"optical_rx_power", "optical_tx_power"}:
        return "dBm"
    if rule.metric_type == "exporter_metric":
        metric_base = _exporter_metric_base(rule)
        label = str(extra_config.get("metric_label") or "").lower()
        normalized = f"{label} {metric_base}"
        if any(token in normalized for token in ["buffer", "queue", "队列", "缓存"]):
            return "包"
        if any(token in normalized for token in ["cpu", "memory", "mem", "util", "usage", "percent", "ratio", "使用率"]):
            return "%"
        if any(token in normalized for token in ["temperature", "tempt", "temp", "温度"]):
            return "℃"
        if any(token in normalized for token in ["rx_power", "tx_power", "optic", "optical", "power", "光功率", "光衰"]):
            return "dBm"
    return ""


def _format_numeric_detail_number(rule: AlertRule, value: float) -> str:
    unit = _numeric_detail_unit(rule)
    if unit == "%":
        return f"{_normalize_percent_metric_value(float(value)):.1f}%"
    if unit == "℃":
        return f"{float(value):.1f}℃"
    if unit == "dBm":
        return f"{float(value):.2f}dBm"
    if unit:
        if float(value).is_integer():
            return f"{int(value)}{unit}"
        return f"{float(value):.2f}{unit}"
    if abs(float(value)) >= 100:
        return f"{float(value):.2f}".rstrip("0").rstrip(".")
    return f"{float(value):.2f}".rstrip("0").rstrip(".")


def _format_numeric_detail_threshold(rule: AlertRule, threshold: Optional[float] = None) -> str:
    if threshold is None:
        threshold = rule.threshold
    if threshold is None:
        return "-"
    unit = _numeric_detail_unit(rule)
    threshold_value = float(threshold)
    if unit == "%":
        return f"{rule.condition} {_normalize_percent_metric_value(threshold_value):.1f}%"
    if unit == "℃":
        return f"{rule.condition} {threshold_value:.1f}℃"
    if unit == "dBm":
        return f"{rule.condition} {threshold_value:.2f}dBm"
    if unit:
        if threshold_value.is_integer():
            return f"{rule.condition} {int(threshold_value)}{unit}"
        return f"{rule.condition} {threshold_value:.2f}{unit}"
    return f"{rule.condition} {threshold_value:g}"


def _build_numeric_detail_row(alert: AlertHistory) -> Optional[Dict[str, str]]:
    rule = alert.rule
    if not rule or alert.alert_value is None:
        return None
    label = _numeric_detail_label(rule)
    if not label:
        return None
    return {
        "label": label,
        "value": _format_numeric_detail_number(rule, float(alert.alert_value)),
    }


def _format_alert_value(rule: AlertRule, value: float) -> str:
    if _numeric_detail_label(rule):
        return _format_numeric_detail_number(rule, float(value))
    if rule.metric_type in PERCENT_METRIC_TYPES:
        return f"{_normalize_percent_metric_value(float(value)):.1f}%"
    return str(value)


def _format_alert_threshold(rule: AlertRule) -> str:
    if rule.threshold is None:
        return "-"
    if _numeric_detail_label(rule):
        return _format_numeric_detail_threshold(rule)
    if rule.metric_type in PERCENT_METRIC_TYPES:
        return f"{rule.condition} {_normalize_percent_metric_value(float(rule.threshold)):.1f}%"
    return f"{rule.condition} {rule.threshold}"


def _build_alert_message(rule: AlertRule, device: Device, value: float, target: Optional[Dict[str, Any]] = None) -> str:
    """构建告警消息"""
    extra_config = rule.extra_config or {}
    target_parts = []
    if target and target.get("target_name"):
        target_parts.append(str(target["target_name"]))
    elif extra_config.get("interface_name"):
        target_parts.append(f"接口 {extra_config['interface_name']}")
    elif extra_config.get("peer"):
        target_parts.append(f"邻居 {extra_config['peer']}")
    elif extra_config.get("keyword"):
        target_parts.append(f"关键字 {extra_config['keyword']}")
    target_text = f"（{' / '.join(target_parts)}）" if target_parts else ""
    metric_label = {
        "device_reachability": "设备可达状态",
        "exporter_metric": str(extra_config.get("metric_label") or extra_config.get("metric_base") or "Exporter 指标"),
        "device_temperature": "设备温度",
        "snmp_session_usage": "会话使用率",
        "snmp_session_queue_full_drop_delta": "会话队列满丢包增长",
        "snmp_storage_usage": "存储使用率",
        "snmp_fan_status": "风扇状态",
        "snmp_power_status": "电源状态",
        "snmp_ha_status": "HA状态",
        "snmp_pak_buffer_usage": "Packet Buffer使用率",
        "snmp_ipsec_tunnel_status": "IPSec隧道状态",
        "snmp_snat_resource_usage": "SNAT资源使用率",
        "snmp_dnat_server_status": "DNAT服务器状态",
        "snmp_slb_virtual_server_status": "SLB虚拟服务状态",
        "internet_circuit_traffic_floor": "公网线路流量掉底",
        "private_line_circuit_traffic_floor": "专线流量掉底",
    }.get(rule.metric_type, rule.metric_type)
    resource_line = ""
    if target and target.get("resource_text"):
        resource_line = f"\n关联资源: {target['resource_text']}"
    state_line = ""
    if target and target.get("state_text"):
        state_line = f"\n协议状态: {target['state_text']}"
    return (
        f"设备 {device.name} ({device.ip_address}){target_text} 触发告警\n"
        f"规则: {rule.name}\n"
        f"指标: {metric_label}\n"
        f"当前值: {_format_alert_value(rule, value)}\n"
        f"阈值: {_format_alert_threshold(rule)}"
        f"{state_line}"
        f"{resource_line}"
    )


@shared_task
def _send_alert_notification(alert_id: int):
    return _send_alert_event_notification(alert_id, "firing", None)


@shared_task
def _send_alert_event_notification(alert_id: int, event_type: str = "firing", actor: Optional[str] = None):
    """发送告警通知"""
    import asyncio
    
    db = SessionLocal()
    try:
        alert = db.query(AlertHistory).filter(AlertHistory.id == alert_id).first()
        if not alert:
            return
        
        rule = alert.rule
        if not rule or not rule.notification_channels:
            return
        title = _build_notification_title(rule, event_type, actor)
        datacenter_text = _device_datacenter_text(alert.device) if alert.device else "-"
        if datacenter_text and datacenter_text != "-":
            title = f"{title}【{datacenter_text}】"
        content = _build_notification_content(db, alert, event_type, actor)
        card_data = _build_notification_card_data(db, alert, event_type, actor)
        mention_users = _get_mention_users(rule)
        
        # 异步发送通知
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _send_all():
                results_inner = []
                for channel in rule.notification_channels:
                    channel_type = channel.get("type")
                    config = _build_channel_config(channel, mention_users)
                    await _wait_for_notification_slot(channel_type, config)
                    result = await notification_manager.send_notification(
                        channel_type,
                        config,
                        title,
                        content,
                        card_data,
                    )
                    results_inner.append(result)
                return results_inner

            results = loop.run_until_complete(_send_all())
            
            # 追加通知记录，供持续告警判断最近一次发送时间。
            sent_at = _utc_now().isoformat()
            history = list(alert.notifications_sent or [])
            history.extend(
                {
                    "channel": ch["type"],
                    "success": result,
                    "sent_at": sent_at,
                    "event_type": event_type,
                    "title": title,
                }
                for ch, result in zip(rule.notification_channels, results)
            )
            alert.notifications_sent = history
            alert.updated_at = _utc_now()
            db.commit()
            
        finally:
            loop.close()
        
    except Exception as e:
        logger.error("发送告警通知失败", alert_id=alert_id, error=str(e))
    finally:
        db.close()


@shared_task
def resolve_stale_alerts():
    """
    告警不能仅因为持续时间超过阈值就标记为已解决。

    “已解决”必须来自指标真实恢复，或人工点击解决后的 snooze 到期重新探测。
    这里保留任务入口，避免 beat 配置引用失效，但不再批量恢复仍存在的故障。
    """
    logger.info("跳过按持续时间自动恢复告警，等待指标真实恢复")
    return {"resolved": 0, "reason": "stale auto resolve disabled"}
