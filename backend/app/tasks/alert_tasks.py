"""
告警检测和处理任务
"""
import asyncio
import contextvars
import json
import re
import threading
import time
import urllib.request
import uuid
from urllib.error import URLError
from celery import shared_task
from sqlalchemy import or_
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from typing import List, Dict, Any, Optional

from app.database import SessionLocal
from app.models import AlertRule, AlertHistory, AlertSilence, Circuit, Device, QualityProbeTarget, SyslogEvent
from app.utils import influx_client, notification_manager, redis_client
from app.utils.asternos_exporter_client import asternos_exporter_client
from app.utils.interface_scope import is_interface_monitored
from app.utils.interface_scope import get_interface_scope
from app.utils.monitor_profile import device_feature_enabled, get_device_monitor_profile
from app.config import settings
from app.core import get_logger

logger = get_logger(__name__)
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
CHECK_ALERTS_LOCK_KEY = "alerts:check_alerts:lock"
FAST_CHECK_ALERTS_LOCK_KEY = "alerts:check_fast_alerts:lock"
REACHABILITY_CHECK_ALERTS_LOCK_KEY = "alerts:check_reachability_alerts:lock"
PROTOCOL_CHECK_ALERTS_LOCK_KEY = "alerts:check_protocol_alerts:lock"
DEVICE_HEALTH_CHECK_ALERTS_LOCK_KEY = "alerts:check_device_health_alerts:lock"
OPTICAL_CHECK_ALERTS_LOCK_KEY = "alerts:check_optical_alerts:lock"
INTERFACE_ALERT_RECOVERY_LOCK_KEY = "alerts:resolve_interface_alerts_quick:lock"
HILLSTONE_BFD_RECOVERY_LOCK_KEY = "alerts:reconcile_hillstone_bfd_traps:lock"
CHECK_ALERTS_LOCK_TTL_SECONDS = 900
FAST_CHECK_ALERTS_LOCK_TTL_SECONDS = 300
REACHABILITY_CHECK_ALERTS_LOCK_TTL_SECONDS = 180
PROTOCOL_CHECK_ALERTS_LOCK_TTL_SECONDS = 180
DEVICE_HEALTH_CHECK_ALERTS_LOCK_TTL_SECONDS = 120
OPTICAL_CHECK_ALERTS_LOCK_TTL_SECONDS = 240
INTERFACE_ALERT_RECOVERY_LOCK_TTL_SECONDS = 45
EXPORTER_SCRAPE_CACHE_TTL_SECONDS = 60
ROBOT_NOTIFICATION_INTERVAL_SECONDS = 2
PENDING_ALERT_TTL_SECONDS = 7200
INTERFACE_ADMIN_UP_OPER_DOWN_MIN_TRIGGER_SECONDS = 120
INTERFACE_ADMIN_UP_OPER_DOWN_MAX_SAMPLE_AGE_SECONDS = 180
REACHABILITY_MAX_SAMPLE_AGE_SECONDS = 180
INTERFACE_MAX_SAMPLE_AGE_SECONDS = 180
OPTICAL_MAX_SAMPLE_AGE_SECONDS = 420
PROTOCOL_MAX_SAMPLE_AGE_SECONDS = 420
CIRCUIT_MAX_SAMPLE_AGE_SECONDS = 180
GENERAL_METRIC_MAX_SAMPLE_AGE_SECONDS = 420
_EXPORTER_SCRAPE_CACHE: Dict[int, Dict[str, Any]] = {}
_EXPORTER_SCRAPE_CACHE_LOCK = threading.Lock()
_ALERT_RUN_EXPORTER_CACHE: contextvars.ContextVar[Optional[Dict[int, Dict[str, List[Dict[str, Any]]]]]] = (
    contextvars.ContextVar("alert_run_exporter_cache", default=None)
)
EXPORTER_DELTA_CACHE_TTL_SECONDS = 86400
RULE_STATUS_PREWARM_URL = "http://api:8000/api/v1/alerts/rules/{rule_id}/status?limit={limit}&max_runtime_seconds={max_runtime_seconds}"
RULE_STATUS_PREWARM_LOCK_KEY = "alerts:rule_status_prewarm:lock"
RULE_STATUS_PREWARM_CURSOR_KEY = "alerts:rule_status_prewarm:cursor"
NOTIFICATION_QUEUE = "notification"
NOTIFICATION_DEDUP_SECONDS = {
    "firing": 300,
    "auto_resolved": 600,
    "ignored": 600,
}
NOTIFICATION_EXPIRES_SECONDS = {
    "firing": 180,
    "auto_resolved": 600,
    "ignored": 600,
}


def _notification_key(prefix: str, alert_id: int, event_type: str) -> str:
    return f"alerts:notification:{prefix}:{int(alert_id)}:{event_type}"


def enqueue_alert_notification(
    alert_id: int,
    event_type: str = "firing",
    actor: Optional[str] = None,
) -> bool:
    """幂等提交告警通知；相同告警事件在窗口内只允许存在一个任务。"""
    event_type = str(event_type or "firing")
    dedup_seconds = NOTIFICATION_DEDUP_SECONDS.get(event_type, 300)
    expires_seconds = NOTIFICATION_EXPIRES_SECONDS.get(event_type, 300)
    queued_key = _notification_key("queued", alert_id, event_type)
    token = uuid.uuid4().hex
    if not redis_client.set(queued_key, token, ex=dedup_seconds, nx=True):
        logger.info("跳过重复通知入队", alert_id=alert_id, event_type=event_type)
        return False
    try:
        _send_alert_event_notification.apply_async(
            args=[int(alert_id), event_type, actor],
            queue=NOTIFICATION_QUEUE,
            expires=expires_seconds,
        )
        return True
    except Exception:
        if redis_client.get(queued_key) == token:
            redis_client.delete(queued_key)
        raise


def _parse_hillstone_bfd_sessions(output: str) -> Dict[tuple[str, str], str]:
    """解析 ``show bfd session``，返回 (本端, 对端) -> 状态。"""
    sessions: Dict[tuple[str, str], str] = {}
    for line in str(output or "").splitlines():
        match = re.match(
            r"^\s*([0-9a-fA-F:.]+)\s+([0-9a-fA-F:.]+)\s+([A-Za-z][A-Za-z-]*)\b",
            line,
        )
        if not match:
            continue
        sessions[(match.group(1).lower(), match.group(2).lower())] = match.group(3).lower()
    return sessions


def _hillstone_bfd_alert_endpoints(alert: AlertHistory) -> Optional[tuple[str, str]]:
    text = "\n".join(
        str(value or "")
        for value in (alert.alert_target_key, alert.alert_target_name, alert.message)
    )
    local_match = re.search(r"\blocal\s*=\s*([^|\s]+)", text, re.IGNORECASE)
    neighbor_match = re.search(r"\bneighbor\s*=\s*([^|\s]+)", text, re.IGNORECASE)
    if not local_match:
        local_match = re.search(r"\blocal\s*:\s*([0-9a-fA-F:.]+)(?=\s|$)", text, re.IGNORECASE)
    if not neighbor_match:
        neighbor_match = re.search(r"\bneighbor\s*:\s*([0-9a-fA-F:.]+)(?=\s|$)", text, re.IGNORECASE)
    if not local_match or not neighbor_match:
        return None
    return local_match.group(1).lower(), neighbor_match.group(1).lower()


def _collect_hillstone_bfd_sessions(device: Device) -> Dict[tuple[str, str], str]:
    """通过山石 CLI 查询当前 BFD 状态；查询失败时抛错，不误恢复告警。"""
    from netmiko import ConnectHandler
    from app.tasks.config_backup_tasks import _netmiko_device_type

    username = str(device.ssh_username or "").strip()
    if not username or not device.ssh_password:
        raise RuntimeError("设备未配置可用的 SSH 用户名/密码")
    connection = ConnectHandler(
        device_type=_netmiko_device_type(device),
        host=device.ip_address,
        port=int(device.ssh_port or 22),
        username=username,
        password=device.ssh_password,
        timeout=20,
        conn_timeout=15,
        banner_timeout=15,
        auth_timeout=15,
        fast_cli=False,
    )
    try:
        output = connection.send_command(
            "show bfd session",
            read_timeout=30,
            strip_prompt=True,
            strip_command=True,
        )
    finally:
        connection.disconnect()
    return _parse_hillstone_bfd_sessions(output)


@shared_task(name="app.tasks.alert_tasks.reconcile_hillstone_bfd_trap_alerts", time_limit=50, soft_time_limit=45)
def reconcile_hillstone_bfd_trap_alerts():
    """山石只发送 BFD Down Trap；定期用当前 CLI 状态补齐真实恢复。"""
    if not redis_client.set(HILLSTONE_BFD_RECOVERY_LOCK_KEY, uuid.uuid4().hex, ex=55, nx=True):
        return {"skipped": "locked"}

    db = SessionLocal()
    resolved_ids: List[int] = []
    checked_devices = 0
    try:
        active = (
            db.query(AlertHistory)
            .join(AlertRule, AlertRule.id == AlertHistory.rule_id)
            .filter(
                AlertRule.metric_type == "snmp_trap",
                AlertRule.name.ilike("%BFD%Down%"),
                AlertHistory.status.in_(["firing", "acknowledged", "ignored", "snoozed"]),
            )
            .order_by(AlertHistory.device_id, AlertHistory.started_at)
            .all()
        )
        alerts_by_device: Dict[int, List[AlertHistory]] = {}
        for alert in active:
            alerts_by_device.setdefault(int(alert.device_id), []).append(alert)

        for device_id, alerts in alerts_by_device.items():
            device = db.query(Device).filter(Device.id == device_id).first()
            if not device or not _is_hillstone_vendor(device.vendor):
                continue
            try:
                sessions = _collect_hillstone_bfd_sessions(device)
                checked_devices += 1
            except Exception as exc:
                logger.warning(
                    "山石BFD恢复回查失败，保留原告警",
                    device_id=device_id,
                    ip=getattr(device, "ip_address", None),
                    error=str(exc),
                )
                continue

            now = _utc_now()
            for alert in alerts:
                endpoints = _hillstone_bfd_alert_endpoints(alert)
                if not endpoints:
                    continue
                state = sessions.get(endpoints)
                if state not in {"up", "established"}:
                    continue
                alert.status = "resolved"
                alert.resolved_by = "hillstone_bfd_cli"
                alert.resolved_at = now
                alert.updated_at = now
                alert.resolution_note = (
                    f"设备当前 BFD 会话 {endpoints[0]} -> {endpoints[1]} 状态已恢复为 Up（CLI核验）"
                )
                resolved_ids.append(int(alert.id))
            if resolved_ids:
                db.commit()

        for alert_id in resolved_ids:
            enqueue_alert_notification(alert_id, "auto_resolved", "hillstone_bfd_cli")
        return {"checked_devices": checked_devices, "resolved": len(resolved_ids)}
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
        redis_client.delete(HILLSTONE_BFD_RECOVERY_LOCK_KEY)




def _normalize_vendor_text(value: Optional[str]) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if any(marker in text for marker in ["ruijie", "锐捷", "rgos"]):
        return "ruijie 锐捷 rgos"
    if any(marker in text for marker in ["h3c", "华三", "新华三", "comware"]):
        return "h3c 华三 新华三 comware"
    if any(marker in text for marker in ["hillstone", "山石"]):
        return "hillstone 山石"
    if any(marker in text for marker in ["aster", "asternos", "asterfusion", "星融元"]):
        return "aster asternos asterfusion 星融元"
    return text

def _normalize_vendor_list(values: Any) -> List[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",")]
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item or "").strip()]

def _vendor_matches_any(raw_vendor: Optional[str], allowed_vendors: Any) -> bool:
    allowed = _normalize_vendor_list(allowed_vendors)
    if not allowed:
        return True
    normalized_vendor = _normalize_vendor_text(raw_vendor)
    for value in allowed:
        normalized_allowed = _normalize_vendor_text(value)
        if normalized_allowed and (normalized_allowed.split()[0] in normalized_vendor or str(value).strip().lower() in normalized_vendor):
            return True
    return False

def _rule_applicable_vendors(rule: AlertRule) -> List[str]:
    extra_config = rule.extra_config or {}
    return _normalize_vendor_list(extra_config.get("applicable_vendors") or extra_config.get("vendors"))


def _device_matches_model_filter(device: Device, extra_config: Optional[Dict[str, Any]]) -> bool:
    config = extra_config or {}
    model_value = str(getattr(device, "model", "") or "").strip()
    model_keyword = str(config.get("model") or config.get("model_keyword") or "").strip().lower()
    model_regex = str(config.get("model_regex") or "").strip()
    exclude_model_regex = str(config.get("exclude_model_regex") or "").strip()
    models = config.get("models") or []

    if isinstance(models, str):
        models = [item.strip() for item in models.split(",") if item.strip()]
    if isinstance(models, list) and models:
        normalized_model = re.sub(r"[^a-z0-9]+", "", model_value.lower())
        normalized_allowed = [re.sub(r"[^a-z0-9]+", "", str(item).lower()) for item in models if str(item or "").strip()]
        if normalized_allowed and not any(
            allowed == normalized_model or allowed in normalized_model or normalized_model in allowed
            for allowed in normalized_allowed
        ):
            return False

    if model_keyword and model_keyword not in model_value.lower():
        return False
    if model_regex:
        try:
            if not re.search(model_regex, model_value, re.IGNORECASE):
                return False
        except re.error:
            logger.warning("告警规则型号正则无效", model_regex=model_regex)
            return False
    if exclude_model_regex:
        try:
            if re.search(exclude_model_regex, model_value, re.IGNORECASE):
                return False
        except re.error:
            logger.warning("告警规则排除型号正则无效", exclude_model_regex=exclude_model_regex)
            return False
    return True


def _device_matches_monitoring_scope(device: Device, extra_config: Optional[Dict[str, Any]]) -> bool:
    config = extra_config or {}
    profiles = config.get("monitor_profiles") or []
    excluded_profiles = config.get("exclude_monitor_profiles") or []
    required_features = config.get("required_features") or []
    if isinstance(profiles, str):
        profiles = [item.strip() for item in profiles.split(",") if item.strip()]
    if isinstance(excluded_profiles, str):
        excluded_profiles = [item.strip() for item in excluded_profiles.split(",") if item.strip()]
    if isinstance(required_features, str):
        required_features = [item.strip() for item in required_features.split(",") if item.strip()]

    profile = get_device_monitor_profile(device)
    if profiles and profile not in profiles:
        return False
    if excluded_profiles and profile in excluded_profiles:
        return False
    return all(device_feature_enabled(device, feature) for feature in required_features)

def _is_asternos_vendor(vendor: Optional[str]) -> bool:
    vendor_value = (vendor or "").strip().lower()
    return any(marker in vendor_value for marker in ["asternos", "asterfusion", "asteros", "aster", "星融元"])


def _is_hillstone_vendor(vendor: Optional[str]) -> bool:
    vendor_value = (vendor or "").strip().lower()
    return any(marker in vendor_value for marker in ["hillstone", "山石"])


def _get_effective_monitor_source(device: Device) -> str:
    return "asternos_exporter" if _is_asternos_vendor(device.vendor) else "snmp"

OPTICAL_METRIC_TYPES = {
    "optical_rx_power",
    "optical_tx_power",
    "optical_lane_power_delta",
    "optical_rx_power_drop_24h",
    "optical_rx_fec_correlation",
}

INTERFACE_METRIC_TYPES = {
    "interface_oper_status",
    "interface_admin_up_oper_down",
    "interface_in_errors_delta",
    "interface_out_errors_delta",
    "interface_in_discards_delta",
    "interface_out_discards_delta",
    "interface_in_broadcast_pps",
    "interface_out_broadcast_pps",
} | OPTICAL_METRIC_TYPES
DEFAULT_SKIPPED_INTERFACE_MARKERS = (
    "loopback",
    "null",
    "m-gigabitethernet",
    "mgigabitethernet",
    "mge",
    "vlanif",
    "vlan-interface",
    "vlan interface",
)

CIRCUIT_METRIC_TYPES = {
    "internet_circuit_traffic_floor",
    "private_line_circuit_traffic_floor",
}

DEVICE_REACHABILITY_METRIC_TYPES = {
    "device_reachability",
    "snmp_reachability",
    "exporter_reachability",
    "telemetry_reachability",
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
    "quality_packet_loss",
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
    "optical_lane_power_delta",
    "optical_rx_power_drop_24h",
    "optical_rx_fec_correlation",
    "exporter_metric",
    "quality_packet_loss",
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
    "optical_lane_power_delta": "当前Lane收光功率差",
    "optical_rx_power_drop_24h": "24小时收光衰减",
    "optical_rx_fec_correlation": "当前FEC纠错包增长",
    "quality_packet_loss": "最近5分钟丢包率",
}

FAST_ALERT_METRIC_TYPES = {
    "interface_admin_up_oper_down",
    "interface_in_discards_delta",
    "interface_out_discards_delta",
}

REACHABILITY_ALERT_METRIC_TYPES = {
    "device_reachability",
    "snmp_reachability",
    "exporter_reachability",
    "telemetry_reachability",
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
    "P3": "P3",
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


def _is_operation_notification(rule: Optional[AlertRule]) -> bool:
    """P3 当前用于配置变更等操作记录类事件，不按故障通知展示。"""
    return _normalize_severity_label(rule.severity if rule else None) == "P3"


def _operation_notification_name(rule: Optional[AlertRule]) -> str:
    text = " ".join(
        str(value or "")
        for value in (
            getattr(rule, "name", None),
            getattr(rule, "metric_type", None),
        )
    ).lower()
    if any(marker in text for marker in ("登录失败", "ssh", "login")):
        return "SSH登录失败记录"
    if any(marker in text for marker in ("配置变更", "config")):
        return "配置变更记录"
    return "操作记录"


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


def _quality_target_notification(rule: AlertRule, alert: AlertHistory) -> Dict[str, Any]:
    if rule.metric_type != "quality_packet_loss" or alert.alert_target_type != "quality_probe":
        return {}
    target_notifications = (rule.extra_config or {}).get("target_notifications") or {}
    return target_notifications.get(str(alert.alert_target_key or "")) or {}


def _notification_channels_for_alert(rule: AlertRule, alert: AlertHistory) -> List[Dict[str, Any]]:
    target_notification = _quality_target_notification(rule, alert)
    if target_notification:
        webhook_url = str(target_notification.get("webhook_url") or "").strip()
        if not target_notification.get("enabled") or not webhook_url:
            return []
        channel_type = str(target_notification.get("channel_type") or "webhook")
        config_key = "url" if channel_type == "webhook" else "webhook"
        return [{
            "type": channel_type,
            "config": {
                config_key: webhook_url,
                "mention_users": target_notification.get("mention_users") or [],
            },
        }]
    return list(rule.notification_channels or [])


def _notification_mentions_for_alert(rule: AlertRule, alert: AlertHistory) -> List[str]:
    target_notification = _quality_target_notification(rule, alert)
    if target_notification:
        return [str(item).strip() for item in (target_notification.get("mention_users") or []) if str(item).strip()]
    return _get_mention_users(rule)


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
    resolved_by = None if alert.resolved_by == "rule_disabled" else alert.resolved_by
    if alert.status == "acknowledged" and alert.acknowledged_by:
        return alert.acknowledged_by
    if alert.status == "ignored" and alert.ignored_by:
        return alert.ignored_by
    if alert.status == "resolved" and (resolved_by or alert.acknowledged_by):
        return resolved_by or alert.acknowledged_by or "-"
    mention_users = _notification_mentions_for_alert(alert.rule, alert) if alert.rule else []
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


def _reachability_recovery_fault_title(rule: AlertRule) -> str:
    """Keep the original reachability type visible in recovery notices."""
    original_title = str(getattr(rule, "name", None) or "").strip()
    if original_title:
        return original_title if "恢复" in original_title else f"{original_title}，已恢复"
    metric_title = {
        "device_reachability": "Ping不可达，已恢复",
        "snmp_reachability": "SNMP不可达，已恢复",
        "exporter_reachability": "Exporter不可达，已恢复",
        "telemetry_reachability": "Telemetry不可达，已恢复",
    }
    return metric_title.get(rule.metric_type, "设备不可达，已恢复")


def _build_notification_title(rule: AlertRule, event_type: str, actor: Optional[str]) -> str:
    severity = _normalize_severity_label(rule.severity)
    mentions = _get_mention_users(rule)
    mention_suffix = f"@{'、'.join(mentions)}" if mentions else ""
    if _is_operation_notification(rule):
        operation_name = _operation_notification_name(rule)
        if event_type == "ignored":
            return f"{actor or '有人'}忽略了1条{operation_name}"
        return f"{severity}-{operation_name}{mention_suffix}"
    if event_type == "ignored":
        return f"{actor or '有人'}忽略了1条故障"
    if rule.metric_type == "quality_packet_loss":
        if event_type == "auto_resolved":
            return f"{severity}-公网链路质量恢复{mention_suffix}"
        return f"{severity}-公网链路质量下降{mention_suffix}"
    if event_type == "auto_resolved":
        if rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
            return f"{severity}-{_reachability_recovery_fault_title(rule)}{mention_suffix}"
        return f"{severity}-自动恢复通知{mention_suffix}"
    return f"{severity}-故障通知{mention_suffix}"


def _quality_probe_context(db: Session, alert: AlertHistory) -> Dict[str, Any]:
    """Build a stable, readable context for quality-probe robot messages."""
    try:
        target_id = int(str(alert.alert_target_key or "").strip())
    except (TypeError, ValueError):
        target_id = 0
    target = db.query(QualityProbeTarget).filter(QualityProbeTarget.id == target_id).first() if target_id else None
    rule = alert.rule
    extra_config = (rule.extra_config or {}) if rule else {}
    message = str(alert.message or "")
    resolution_note = str(alert.resolution_note or "")
    match = re.search(r"连续\s*(\d+)\s*个探测周期", message)
    consecutive = int(match.group(1)) if match else 0
    target_notification = (extra_config.get("target_notifications") or {}).get(str(target_id)) or {}
    required_match = re.search(r"告警要求连续\s*(\d+)\s*个周期", message)
    required_count = int(required_match.group(1)) if required_match else int(
        target_notification.get("consecutive_samples") or extra_config.get("consecutive_samples") or 5
    )
    recovery_count_match = re.search(r"当前连续异常周期\s*(\d+)/(\d+)", resolution_note)
    current_consecutive = int(recovery_count_match.group(1)) if recovery_count_match else consecutive
    io_match = re.search(r"本轮收发[：:]\s*(\d+)\s*/\s*(\d+)", message)
    received = int(io_match.group(1)) if io_match else None
    sent = int(io_match.group(2)) if io_match else None
    latency_match = re.search(r"当前延迟[：:]\s*([\d.]+)\s*ms", message, re.IGNORECASE)
    loss = float(alert.alert_value or 0)
    threshold = float(alert.threshold or (rule.threshold if rule else 0) or 0)
    latency = float(latency_match.group(1)) if latency_match else getattr(target, "last_avg_latency_ms", None)
    datacenter = getattr(target, "datacenter_ref", None)
    datacenter_text = getattr(datacenter, "name", None) or "-"
    operator = getattr(target, "operator_name", None) or "-"
    interval_seconds = int(getattr(target, "interval_seconds", None) or 60)
    packet_count = int(getattr(target, "packet_count", None) or 5)
    timeout_ms = int(getattr(target, "timeout_ms", None) or 1000)
    name = getattr(target, "name", None) or alert.alert_target_name or "-"
    address = getattr(target, "target", None) or "-"
    if not target and " / " in str(alert.alert_target_name or ""):
        name, address = str(alert.alert_target_name).split(" / ", 1)
    return {
        "name": name,
        "address": address,
        "datacenter": datacenter_text,
        "operator": operator,
        "latency": "-" if latency is None else f"{float(latency):.2f} ms",
        "loss": loss,
        "threshold": threshold,
        "consecutive": consecutive,
        "current_consecutive": current_consecutive,
        "required_count": required_count,
        "probe_result": (
            f"发送 {sent} / 收到 {received} / 丢失 {max(sent - received, 0)}"
            if sent is not None and received is not None else "-"
        ),
        "recovery_reason": resolution_note.split("：", 1)[-1] if resolution_note else "触发条件已不再满足",
        "sampling": f"每 {interval_seconds} 秒 / 每次 {packet_count} 包 / 超时 {timeout_ms} ms",
    }


def _quality_impact_text(loss_percent: float) -> str:
    if loss_percent >= 100:
        return "目标完全不可达，相关公网访问可能已经中断"
    if loss_percent >= 10:
        return "严重丢包，TCP 重传及实时业务体验可能明显受影响"
    if loss_percent >= 3:
        return "链路质量下降，可能出现访问变慢、卡顿或重传"
    return "出现持续丢包，请关注链路稳定性"


def _quality_action_text() -> str:
    return "先在质量查询执行 MTR，再核对同机房公网出口流量及 BGP 状态"


def _extract_trap_content_from_message(message: Optional[str]) -> str:
    text = (message or "").strip()
    marker = "Trap内容:"
    if marker in text:
        return text.split(marker, 1)[1].strip()
    return text


def _clean_operation_detail(alert: AlertHistory) -> str:
    detail = _extract_trap_content_from_message(alert.message)
    device_name = (alert.device.name if alert.device else "") or ""
    if device_name and detail.startswith(device_name):
        detail = detail[len(device_name):].lstrip(" /")
    if " / " in detail:
        parts = [part.strip() for part in detail.split(" / ") if part.strip()]
        detail = next((part for part in parts if part != device_name), parts[-1] if parts else detail)
    if device_name and detail == device_name:
        detail = ""
    if not detail and alert.alert_target_name and alert.alert_target_name != device_name:
        detail = alert.alert_target_name
    return (detail or "配置变更").strip()


def _build_notification_content(
    db: Session,
    alert: AlertHistory,
    event_type: str = "firing",
    actor: Optional[str] = None,
) -> str:
    rule = alert.rule
    device = alert.device
    if rule and rule.metric_type == "quality_packet_loss" and not device:
        alarm_id = _ensure_alarm_id(db, alert)
        started_at_text = _format_local_time(alert.started_at)
        context = _quality_probe_context(db, alert)
        is_recovery = event_type == "auto_resolved"
        lines = [
            f"告警事件：{'公网链路质量恢复' if is_recovery else '公网链路质量下降'}",
            f"探测名称：{context['name']}",
            f"目标地址：{context['address']}",
            f"所属机房：{context['datacenter']}",
            f"运营商：{context['operator']}",
            f"{'恢复时延迟' if is_recovery else '当前延迟'}：{context['latency']}",
        ]
        if is_recovery:
            lines.extend([
                f"恢复原因：{context['recovery_reason']}",
                f"恢复时5分钟丢包率：{context['loss']:.2f}% / 阈值 {context['threshold']:.2f}%",
                f"当前连续异常周期：{context['current_consecutive']} / {context['required_count']}",
            ])
        else:
            lines.extend([
                "触发原因：5分钟丢包率超阈值，并且连续异常周期达到要求（两个条件同时满足）",
                f"5分钟丢包率：{context['loss']:.2f}% / 阈值 {context['threshold']:.2f}%",
                f"连续异常周期：{context['consecutive']} / {context['required_count']}",
                f"本轮探测：{context['probe_result']}",
                f"影响判断：{_quality_impact_text(context['loss'])}",
                f"处理建议：{_quality_action_text()}",
            ])
        lines.extend([f"采样方式：{context['sampling']}", f"Alarm ID：{alarm_id}"])
        if event_type == "auto_resolved":
            lines.extend([
                f"发生时间：{started_at_text}",
                f"恢复时间：{_format_local_time(alert.resolved_at)}",
                f"持续时间：{_format_duration(alert.started_at, alert.resolved_at)}",
            ])
        else:
            lines.extend([f"发生时间：{started_at_text}", f"当前处理人：{_current_handler_text(alert)}"])
        lines.extend(["", f"故障详情：{_build_detail_url(alert)}"])
        return "\n".join(lines)
    if not rule or not device:
        return alert.message or "告警详情不可用"

    alarm_id = _ensure_alarm_id(db, alert)
    occurrence_count = _get_recent_occurrence_count(db, alert)
    detail_url = _build_detail_url(alert)
    fault_title = rule.name or rule.metric_type
    is_operation = _is_operation_notification(rule)
    target_line = _target_label(rule, alert)
    started_at_text = _format_local_time(alert.started_at)

    lines = []
    if event_type == "ignored":
        lines.append(f"【{fault_title}】")
    elif is_operation:
        lines.append(f"记录类型：【{fault_title}】")
    else:
        lines.append(f"故障标题：【{fault_title}】")
    lines.append(f"交换机：{device.name}")
    lines.append(f"管理地址：{device.ip_address}")
    if target_line and not is_operation:
        lines.append(target_line)
    if is_operation:
        lines.append(f"变更内容：{_clean_operation_detail(alert)}")
    numeric_detail = _build_numeric_detail_row(alert)
    if numeric_detail:
        lines.append(f"{numeric_detail['label']}：{numeric_detail['value']}")
    for metadata in _alert_message_metadata(alert):
        lines.append(f"{metadata['label']}：{metadata['value']}")
    if rule.metric_type in {"interface_oper_status", "interface_admin_up_oper_down"}:
        occurrence_label = "过去1小时down次数"
    elif rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
        occurrence_label = "过去1小时不可达次数"
    elif rule.metric_type in CIRCUIT_METRIC_TYPES:
        occurrence_label = "过去1小时掉底次数"
    else:
        occurrence_label = "过去1小时触发次数"
    if not is_operation:
        lines.append(f"{occurrence_label}：{occurrence_count}次")
    lines.append(f"Alarm ID：{alarm_id}")

    if event_type == "firing":
        lines.append(f"{'记录时间' if is_operation else '发生时间'}：{started_at_text}")
        if not is_operation:
            lines.append(f"当前处理人：{_current_handler_text(alert)}")
        lines.append("")
        lines.append(f"{'记录详情' if is_operation else '故障详情'}：{detail_url}")
    elif event_type == "ignored":
        if actor:
            lines.insert(0, f"{actor}忽略了1条{'操作记录' if is_operation else '故障'}：")
    elif event_type == "auto_resolved":
        resolved_at_text = _format_local_time(alert.resolved_at)
        if is_operation:
            return ""
        elif rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
            lines[0] = f"故障标题：【{_reachability_recovery_fault_title(rule)}】"
        lines.append(f"发生时间：{started_at_text}")
        lines.append(f"恢复时间：{resolved_at_text}")
        lines.append(f"持续时间：{_format_duration(alert.started_at, alert.resolved_at)}")
        lines.append(f"当前处理人：{_current_handler_text(alert)}")
        lines.append("")
        lines.append(f"{'操作详情' if is_operation else '故障详情'}：{detail_url}")

    return "\n".join(lines)


def _build_notification_card_data(
    db: Session,
    alert: AlertHistory,
    event_type: str = "firing",
    actor: Optional[str] = None,
) -> Dict[str, Any]:
    rule = alert.rule
    device = alert.device
    if rule and rule.metric_type == "quality_packet_loss" and not device:
        alarm_id = _ensure_alarm_id(db, alert)
        severity = _normalize_severity_label(rule.severity)
        context = _quality_probe_context(db, alert)
        is_recovery = event_type == "auto_resolved"
        rows = [
            {"label": "告警事件", "value": "公网链路质量恢复" if is_recovery else "公网链路质量下降"},
            {"label": "探测名称", "value": context["name"]},
            {"label": "目标地址", "value": context["address"]},
            {"label": "所属机房", "value": context["datacenter"]},
            {"label": "运营商", "value": context["operator"]},
            {"label": "恢复时延迟" if is_recovery else "当前延迟", "value": context["latency"]},
        ]
        if is_recovery:
            rows.extend([
                {"label": "恢复原因", "value": context["recovery_reason"]},
                {"label": "恢复时5分钟丢包率", "value": f"{context['loss']:.2f}% / 阈值 {context['threshold']:.2f}%"},
                {"label": "当前连续异常周期", "value": f"{context['current_consecutive']} / {context['required_count']}"},
            ])
        else:
            rows.extend([
                {"label": "触发原因", "value": "5分钟丢包率超阈值 + 连续异常周期达标（同时满足）"},
                {"label": "5分钟丢包率", "value": f"{context['loss']:.2f}% / 阈值 {context['threshold']:.2f}%"},
                {"label": "连续异常周期", "value": f"{context['consecutive']} / {context['required_count']}"},
                {"label": "本轮探测", "value": context["probe_result"]},
                {"label": "影响判断", "value": _quality_impact_text(context["loss"])},
                {"label": "处理建议", "value": _quality_action_text()},
            ])
        rows.extend([
            {"label": "采样方式", "value": context["sampling"]},
            {"label": "Alarm ID", "value": alarm_id},
        ])
        if event_type == "auto_resolved":
            rows.extend([
                {"label": "发生时间", "value": _format_local_time(alert.started_at)},
                {"label": "恢复时间", "value": _format_local_time(alert.resolved_at)},
                {"label": "持续时间", "value": _format_duration(alert.started_at, alert.resolved_at)},
            ])
        else:
            rows.extend([
                {"label": "发生时间", "value": _format_local_time(alert.started_at)},
                {"label": "当前处理人", "value": _current_handler_text(alert)},
            ])
        return {
            "severity": severity,
            "title": _build_notification_title(rule, event_type, actor),
            "headline": severity,
            "summary": f"quality_packet_loss / {alert.alert_target_name or '-'}",
            "subtitle": _format_local_time(alert.resolved_at if event_type == "auto_resolved" else alert.started_at),
            "rows": rows,
            "detail_url": _build_detail_url(alert),
            "event_type": event_type,
            "notification_kind": "alert",
        }
    if not rule or not device:
        return {}

    alarm_id = _ensure_alarm_id(db, alert)
    occurrence_count = _get_recent_occurrence_count(db, alert)
    severity = _normalize_severity_label(rule.severity)
    is_operation = _is_operation_notification(rule)
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
        {"label": "记录类型" if is_operation else "故障标题", "value": f"【{rule.name or rule.metric_type}】"},
        {"label": "交换机", "value": device.name},
        {"label": "管理地址", "value": device.ip_address},
    ]
    if is_operation:
        rows.append({"label": "变更内容", "value": _clean_operation_detail(alert)})
    elif alert.alert_target_name:
        target_label = "变更内容" if is_operation else ("接口" if rule.metric_type in INTERFACE_METRIC_TYPES else "对象")
        if rule.metric_type in CIRCUIT_METRIC_TYPES:
            target_label = "线路接口"
        rows.append({"label": target_label, "value": alert.alert_target_name})
    numeric_detail = _build_numeric_detail_row(alert)
    if numeric_detail:
        rows.append(numeric_detail)
    rows.extend(_alert_message_metadata(alert))
    if not is_operation:
        rows.append({"label": occurrence_label, "value": f"{occurrence_count}次"})
    rows.append({"label": "Alarm ID", "value": alarm_id})

    if event_type == "firing":
        rows.append({"label": "记录时间" if is_operation else "发生时间", "value": started_at_text})
        if not is_operation:
            rows.append({"label": "当前处理人", "value": _current_handler_text(alert)})
    elif event_type == "ignored":
        rows.insert(0, {"label": "处理动作", "value": f"{actor or '有人'}忽略了1条{'操作记录' if is_operation else '故障'}"})
    elif event_type == "auto_resolved":
        if is_operation:
            return {}
        elif rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
            rows[0] = {
                "label": "故障标题",
                "value": f"【{_reachability_recovery_fault_title(rule)}】",
            }
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
        "notification_kind": "operation" if is_operation else "alert",
    }


def _build_channel_config(channel: Dict[str, Any], mention_users: List[str]) -> Dict[str, Any]:
    config = dict(channel.get("config", {}) or {})
    if mention_users:
        normalized_targets = []
        for item in mention_users:
            raw_text = str(item).strip()
            if not raw_text:
                continue
            normalized_targets.append("@all" if raw_text.lower() == "@all" else raw_text.lstrip("@"))
        mobile_targets = [item for item in normalized_targets if item.isdigit()]
        user_targets = [item for item in normalized_targets if not item.isdigit()]
        if user_targets:
            config.setdefault("mentioned_list", user_targets)
        if mobile_targets:
            config.setdefault("at_mobiles", mobile_targets)
            config.setdefault("mentioned_mobile_list", mobile_targets)
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


def _pending_recovery_key(rule: AlertRule, device: Device, target: Dict[str, Any]) -> str:
    target_key = str(target.get("target_key") or "device")
    return f"alerts:recovery_pending:{rule.id}:{device.id}:{target_key}"


def _clear_pending_alert(rule: AlertRule, device: Device, target: Dict[str, Any]) -> None:
    redis_client.delete(_pending_alert_key(rule, device, target))


def _clear_pending_recovery(rule: AlertRule, device: Device, target: Dict[str, Any]) -> None:
    redis_client.delete(_pending_recovery_key(rule, device, target))


def _parse_row_time(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        try:
            parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        except Exception:
            return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _row_age_seconds(row: Dict[str, Any]) -> Optional[float]:
    row_time = _parse_row_time(row.get("_time") or row.get("time"))
    if not row_time:
        return None
    return max((_utc_now() - row_time).total_seconds(), 0.0)


def _sample_max_age_seconds(rule: AlertRule, target: Dict[str, Any]) -> int:
    explicit = target.get("max_sample_age_seconds")
    rule_extra_config = getattr(rule, "extra_config", None)
    if explicit is None and isinstance(rule_extra_config, dict):
        explicit = rule_extra_config.get("max_sample_age_seconds")
    if explicit is not None:
        try:
            return max(int(explicit), 1)
        except (TypeError, ValueError):
            pass
    if rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
        return REACHABILITY_MAX_SAMPLE_AGE_SECONDS
    if rule.metric_type in OPTICAL_METRIC_TYPES:
        return OPTICAL_MAX_SAMPLE_AGE_SECONDS
    if rule.metric_type in INTERFACE_METRIC_TYPES:
        return INTERFACE_MAX_SAMPLE_AGE_SECONDS
    if rule.metric_type in PROTOCOL_METRIC_TYPES:
        return PROTOCOL_MAX_SAMPLE_AGE_SECONDS
    if rule.metric_type in CIRCUIT_METRIC_TYPES:
        return CIRCUIT_MAX_SAMPLE_AGE_SECONDS
    return GENERAL_METRIC_MAX_SAMPLE_AGE_SECONDS


def _sample_is_fresh(rule: AlertRule, device: Device, target: Dict[str, Any], phase: str) -> bool:
    sample_age = target.get("sample_age_seconds")
    if sample_age is None:
        return True
    try:
        sample_age_value = float(sample_age)
    except (TypeError, ValueError):
        return True
    max_age = _sample_max_age_seconds(rule, target)
    if sample_age_value <= max_age:
        return True
    logger.info(
        "跳过过期告警样本",
        phase=phase,
        rule_id=rule.id,
        metric_type=rule.metric_type,
        device_id=device.id,
        target=target.get("target_name"),
        sample_time=target.get("sample_time"),
        sample_age_seconds=round(sample_age_value, 2),
        max_sample_age_seconds=max_age,
    )
    return False


def _duration_confirmed(rule: AlertRule, device: Device, target: Dict[str, Any], value: float) -> bool:
    duration_seconds = max(int(rule.duration or 0), 0)
    rule_extra_config = getattr(rule, "extra_config", None)
    extra_config = rule_extra_config if isinstance(rule_extra_config, dict) else {}
    required_samples = int(
        target.get("required_samples")
        or extra_config.get("required_samples")
        or (3 if rule.metric_type in OPTICAL_METRIC_TYPES else 1)
    )
    required_samples = max(required_samples, 1)
    if rule.metric_type == "interface_admin_up_oper_down":
        duration_seconds = max(duration_seconds, INTERFACE_ADMIN_UP_OPER_DOWN_MIN_TRIGGER_SECONDS)

    if not _sample_is_fresh(rule, device, target, "trigger"):
        return False

    if duration_seconds <= 0 and required_samples <= 1:
        return True

    sample_time = str(target.get("sample_time") or "")
    sample_age = target.get("sample_age_seconds")
    if rule.metric_type == "interface_admin_up_oper_down" and sample_age is not None:
        try:
            if float(sample_age) > INTERFACE_ADMIN_UP_OPER_DOWN_MAX_SAMPLE_AGE_SECONDS:
                logger.info(
                    "跳过过期接口AdminUp物理Down样本",
                    rule_id=rule.id,
                    device_id=device.id,
                    target=target.get("target_name"),
                    sample_time=sample_time,
                    sample_age_seconds=sample_age,
                )
                return False
        except (TypeError, ValueError):
            pass

    now = time.time()
    key = _pending_alert_key(rule, device, target)
    pending_raw = redis_client.get(key)
    first_seen = now
    first_sample_time = sample_time
    latest_sample_time = sample_time
    sample_count = 1
    if pending_raw:
        try:
            pending_payload = json.loads(pending_raw)
            first_seen = float(pending_payload.get("first_seen") or now)
            first_sample_time = str(pending_payload.get("first_sample_time") or sample_time)
            latest_sample_time = str(pending_payload.get("latest_sample_time") or first_sample_time or sample_time)
            sample_count = max(int(pending_payload.get("sample_count") or 1), 1)
        except (TypeError, ValueError, json.JSONDecodeError):
            first_seen = now
            first_sample_time = sample_time
            latest_sample_time = sample_time
            sample_count = 1
        if sample_time and sample_time != latest_sample_time:
            latest_sample_time = sample_time
            sample_count += 1
            redis_client.set(
                key,
                json.dumps(
                    {
                        "first_seen": first_seen,
                        "first_sample_time": first_sample_time,
                        "latest_sample_time": latest_sample_time,
                        "sample_count": sample_count,
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
    else:
        redis_client.set(
            key,
            json.dumps(
                {
                    "first_seen": first_seen,
                    "first_sample_time": first_sample_time,
                    "latest_sample_time": latest_sample_time,
                    "sample_count": sample_count,
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
            required_samples=required_samples,
        )

    if sample_time and first_sample_time == latest_sample_time:
        # 不能因为同一个异常样本被规则任务反复读取，就把瞬时异常升级为持续故障。
        # 只有看到至少两个独立异常采样点后，持续时间才具有实际意义。
        return False
    if sample_count < required_samples:
        return False
    return (now - first_seen) >= duration_seconds


def _requires_recovery_confirmation(rule: AlertRule) -> bool:
    return (
        rule.metric_type == "interface_admin_up_oper_down"
        or rule.metric_type in PROTOCOL_METRIC_TYPES
        or rule.metric_type in CIRCUIT_METRIC_TYPES
    )


def _recovery_confirmed(rule: AlertRule, device: Device, target: Dict[str, Any], value: float) -> bool:
    if not _sample_is_fresh(rule, device, target, "recovery"):
        return False
    if not _requires_recovery_confirmation(rule):
        return True

    duration_seconds = max(int(rule.duration or 0), 0)
    if rule.metric_type == "interface_admin_up_oper_down":
        # 接口 AdminUp/物理Down 依赖高频接口采集。根因修复后如果再额外等待 60s，
        # 实际恢复会变成“采集轮次 + 60s”，现场观感通常超过 1 分钟。
        confirm_seconds = max(15, min(duration_seconds, 30) if duration_seconds else 15)
    else:
        confirm_seconds = max(60, duration_seconds)
    now = time.time()
    key = _pending_recovery_key(rule, device, target)
    pending_raw = redis_client.get(key)
    first_seen = now
    sample_time = str(target.get("sample_time") or "")
    first_sample_time = sample_time
    latest_sample_time = sample_time
    if pending_raw:
        try:
            pending_payload = json.loads(pending_raw)
            first_seen = float(pending_payload.get("first_seen") or now)
            first_sample_time = str(pending_payload.get("first_sample_time") or sample_time)
            latest_sample_time = str(pending_payload.get("latest_sample_time") or first_sample_time or sample_time)
        except (TypeError, ValueError, json.JSONDecodeError):
            first_seen = now
            first_sample_time = sample_time
            latest_sample_time = sample_time
        if sample_time and sample_time != latest_sample_time:
            latest_sample_time = sample_time
            redis_client.set(
                key,
                json.dumps(
                    {
                        "first_seen": first_seen,
                        "first_sample_time": first_sample_time,
                        "latest_sample_time": latest_sample_time,
                        "rule_id": rule.id,
                        "device_id": device.id,
                        "target_key": target.get("target_key"),
                        "target_name": target.get("target_name"),
                        "value": value,
                    },
                    ensure_ascii=False,
                ),
                ex=max(confirm_seconds * 3, confirm_seconds + 60, PENDING_ALERT_TTL_SECONDS),
            )
    else:
        redis_client.set(
            key,
            json.dumps(
                {
                    "first_seen": first_seen,
                    "first_sample_time": first_sample_time,
                    "latest_sample_time": latest_sample_time,
                    "rule_id": rule.id,
                    "device_id": device.id,
                    "target_key": target.get("target_key"),
                    "target_name": target.get("target_name"),
                    "value": value,
                },
                ensure_ascii=False,
            ),
            ex=max(confirm_seconds * 3, confirm_seconds + 60, PENDING_ALERT_TTL_SECONDS),
        )
        logger.info(
            "告警进入恢复确认",
            rule_id=rule.id,
            device_id=device.id,
            target=target.get("target_name"),
            confirm_seconds=confirm_seconds,
            value=value,
        )

    if sample_time and first_sample_time == latest_sample_time:
        return False
    return (now - first_seen) >= confirm_seconds


def _silence_matches(silence: AlertSilence, rule: AlertRule, device: Device, target: Dict[str, Any]) -> bool:
    from app.utils.ip_match import ip_value_matches

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
        return ip_value_matches(source, candidate)

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
            if field_name == "interface":
                interface_values = [
                    str(target.get("target_name") or ""),
                    str(target.get("target_key") or ""),
                ]
                if operator in {"not_contains", "not_equals", "not_regex"}:
                    condition_matches = all(
                        _evaluate_condition(field_name, item, operator, value)
                        for item in interface_values
                    )
                else:
                    condition_matches = any(
                        _evaluate_condition(field_name, item, operator, value)
                        for item in interface_values
                    )
            else:
                condition_matches = _evaluate_condition(
                    field_name,
                    field_map.get(field_name, ""),
                    operator,
                    value,
                )
            if not condition_matches:
                return False
    return True


def _is_silenced(db: Session, rule: AlertRule, device: Device, target: Dict[str, Any]) -> bool:
    silences = db.query(AlertSilence).filter(AlertSilence.enabled == 1).all()
    for silence in silences:
        if _silence_matches(silence, rule, device, target):
            return True
    return False


def _target_from_alert_history(alert: AlertHistory) -> Dict[str, Any]:
    return {
        "target_type": alert.alert_target_type,
        "target_key": alert.alert_target_key,
        "target_name": alert.alert_target_name,
        "value": alert.alert_value,
        "alarm_id": alert.alarm_id,
    }


def _has_successful_firing_notification(alert: AlertHistory) -> bool:
    for item in alert.notifications_sent or []:
        if not isinstance(item, dict):
            continue
        if item.get("event_type") == "firing" and item.get("success"):
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
    exporter_cache_token = _ALERT_RUN_EXPORTER_CACHE.set({})
    try:
        _resolve_alerts_for_disabled_rules(db)
        if not metric_types or metric_types & INTERFACE_METRIC_TYPES:
            resolve_interface_alerts_quick()
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
        _ALERT_RUN_EXPORTER_CACHE.reset(exporter_cache_token)
        db.close()
        if redis_client.get(lock_key) == lock_value:
            redis_client.delete(lock_key)


def _resolve_alerts_for_disabled_rules(db: Session) -> int:
    """Resolve active alerts whose rule has been disabled."""
    active_alerts = (
        db.query(AlertHistory)
        .join(AlertRule, AlertRule.id == AlertHistory.rule_id)
        .filter(
            AlertRule.enabled != 1,
            AlertHistory.status.in_(["firing", "acknowledged", "ignored", "snoozed"]),
        )
        .all()
    )
    if not active_alerts:
        return 0

    now = _utc_now()
    for alert in active_alerts:
        alert.status = "resolved"
        alert.resolved_at = now
        alert.resolved_by = None
        alert.resolution_note = "告警规则已停用，系统静默恢复活动告警"
        alert.updated_at = now
    db.commit()
    logger.info("已自动恢复停用规则关联的活动告警", count=len(active_alerts))
    return len(active_alerts)


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
def check_reachability_alerts():
    """
    独立检查设备可达性告警，避免 Ping/SNMP/Exporter/Telemetry 不可达被全量慢规则延迟。
    """
    return _run_alert_checks(
        lock_key=REACHABILITY_CHECK_ALERTS_LOCK_KEY,
        lock_ttl_seconds=REACHABILITY_CHECK_ALERTS_LOCK_TTL_SECONDS,
        metric_types=REACHABILITY_ALERT_METRIC_TYPES,
        task_label="可达性告警检查",
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
def check_optical_alerts():
    """独立检查光模块质量，避免被慢速 Exporter 常规规则拖过任务上限。"""
    return _run_alert_checks(
        lock_key=OPTICAL_CHECK_ALERTS_LOCK_KEY,
        lock_ttl_seconds=OPTICAL_CHECK_ALERTS_LOCK_TTL_SECONDS,
        metric_types=OPTICAL_METRIC_TYPES,
        task_label="光模块质量告警检查",
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
        exclude_metric_types=FAST_ALERT_METRIC_TYPES | REACHABILITY_ALERT_METRIC_TYPES | PROTOCOL_METRIC_TYPES | DEVICE_HEALTH_ALERT_METRIC_TYPES | OPTICAL_METRIC_TYPES,
        task_label="常规告警检查",
    )


@shared_task
def prewarm_alert_rule_status_cache(limit: int = 100, batch_size: int = 2, max_runtime_seconds: int = 4):
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
            url = RULE_STATUS_PREWARM_URL.format(
                rule_id=rule_id,
                limit=int(limit),
                max_runtime_seconds=int(max_runtime_seconds),
            )
            try:
                headers = {"X-Internal-Token": settings.INTERNAL_API_TOKEN} if settings.INTERNAL_API_TOKEN else {}
                request = urllib.request.Request(url, headers=headers)
                with urllib.request.urlopen(request, timeout=max(int(max_runtime_seconds) + 1, 3)) as response:
                    response.read(256)
                warmed += 1
            except (Exception, URLError) as exc:
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


@shared_task(name="app.tasks.alert_tasks.prewarm_alert_silence_match_counts", time_limit=240, soft_time_limit=210)
def prewarm_alert_silence_match_counts(silence_id: int, include_total: bool = False):
    """后台计算告警屏蔽命中数量，避免列表页同步扫库拖慢 API。"""
    db = SessionLocal()
    try:
        silence = db.query(AlertSilence).filter(AlertSilence.id == silence_id).first()
        if not silence:
            return {"silence_id": silence_id, "missing": True}

        # Runtime import avoids making alert route imports part of Celery module
        # initialization. The route module owns the exact matching/cache helpers,
        # so the list count and detail count remain consistent.
        from app.routers.alerts import _count_silence_matches_with_lock

        active = _count_silence_matches_with_lock(db, silence, active_only=True)
        total = _count_silence_matches_with_lock(db, silence, active_only=False) if include_total else {
            "count": None,
            "cached": False,
            "pending": True,
            "exact": False,
        }
        return {"silence_id": silence_id, "active": active, "total": total}
    except Exception as exc:
        logger.warning("告警屏蔽命中数量后台统计失败", silence_id=silence_id, error=str(exc))
        return {"silence_id": silence_id, "error": str(exc)}
    finally:
        db.close()


def _check_single_rule(db: Session, rule: AlertRule) -> bool:
    """
    检查单个告警规则
    
    Returns:
        是否触发告警
    """
    # 质量探测没有关联网络设备，由 quality_tasks 在每次 Ping 后直接评估。
    if rule.metric_type == "quality_packet_loss":
        return False

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

    applicable_vendors = _rule_applicable_vendors(rule)
    if not applicable_vendors:
        logger.warning(
            "跳过未配置适用厂商的告警规则",
            rule_id=rule.id,
            rule_name=rule.name,
            metric_type=rule.metric_type,
        )
        return False
    devices = [device for device in devices if _vendor_matches_any(device.vendor, applicable_vendors)]
    devices = [device for device in devices if _device_matches_model_filter(device, rule.extra_config or {})]
    devices = [device for device in devices if _device_matches_monitoring_scope(device, rule.extra_config or {})]

    if rule.metric_type == "device_reachability":
        devices = [
            device for device in devices
            if device.is_monitored and device.status in {"active", "online"} and device.ip_address
        ]
    elif rule.metric_type == "snmp_reachability":
        devices = [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and device.ip_address
            and device.snmp_version
            and (device.monitor_source == "snmp" or device.monitor_source is None)
        ]
    elif rule.metric_type == "exporter_reachability":
        devices = [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and device.ip_address
            and _get_effective_monitor_source(device) == "asternos_exporter"
        ]
    elif rule.metric_type == "telemetry_reachability":
        devices = [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and device.ip_address
            and int(device.gnmi_enabled or 0) == 1
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
        targets = _get_metric_targets(db, device, rule.metric_type, rule.extra_config or {}, rule)
        if rule.metric_type in OPTICAL_METRIC_TYPES:
            _resolve_optical_alerts_on_inactive_interfaces(db, rule, device)
        if rule.metric_type in PROTOCOL_METRIC_TYPES or rule.metric_type in INTERFACE_METRIC_TYPES:
            _resolve_disappeared_target_alerts(
                db,
                rule,
                device,
                {
                    str(value)
                    for target in targets
                    for value in (target.get("target_key"), target.get("target_name"))
                    if value
                },
            )
        if not targets:
            continue
        active_alerts = db.query(AlertHistory).filter(
            AlertHistory.rule_id == rule.id,
            AlertHistory.device_id == device.id,
            AlertHistory.status.in_(["firing", "acknowledged", "ignored", "snoozed"]),
        ).all()
        active_alerts_by_target = {
            str(alert.alert_target_key or ""): alert
            for alert in active_alerts
        }

        for target in targets:
            value = target.get("value")
            if value is None:
                continue

            effective_threshold = _effective_rule_threshold(rule, target)
            should_alert = _evaluate_rule_condition(rule, float(value), target)

            target_alert_key = str(target.get("target_key") or "")
            existing = active_alerts_by_target.get(target_alert_key)

            if should_alert:
                _clear_pending_recovery(rule, device, target)
                if _is_silenced(db, rule, device, target):
                    if existing:
                        _clear_pending_alert(rule, device, target)
                        existing.alert_value = float(value)
                        existing.threshold = effective_threshold
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
                    else:
                        if not _duration_confirmed(rule, device, target, float(value)):
                            continue
                        _clear_pending_alert(rule, device, target)
                        alert = AlertHistory(
                            rule_id=rule.id,
                            device_id=device.id,
                            alert_value=float(value),
                            threshold=effective_threshold,
                            message=_build_alert_message(rule, device, float(value), target),
                            alert_target_type=target.get("target_type"),
                            alert_target_key=target.get("target_key"),
                            alert_target_name=target.get("target_name"),
                            status="ignored",
                            ignored_by="alert_silence",
                            ignored_at=_utc_now(),
                            started_at=_utc_now(),
                        )
                        db.add(alert)
                        db.commit()
                        db.refresh(alert)
                        active_alerts_by_target[target_alert_key] = alert
                        _ensure_alarm_id(db, alert)
                        logger.info(
                            "告警命中屏蔽规则，已记录为忽略",
                            rule_id=rule.id,
                            alert_id=alert.id,
                            device_id=device.id,
                            target=target.get("target_name"),
                            value=value,
                        )
                    continue
                if existing:
                    existing.alert_value = float(value)
                    existing.threshold = effective_threshold
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
                        enqueue_alert_notification(existing.id)
                        logger.info(
                            "暂停/屏蔽解除后告警重新触发",
                            rule_id=rule.id,
                            alert_id=existing.id,
                            device_id=device.id,
                            target=target.get("target_name"),
                            value=value,
                        )
                    elif _should_repeat_notify(existing, rule):
                        enqueue_alert_notification(existing.id)
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
                        threshold=effective_threshold,
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
                    active_alerts_by_target[target_alert_key] = alert
                    _ensure_alarm_id(db, alert)
                    enqueue_alert_notification(alert.id)

                    logger.info(
                        "告警触发",
                        rule_id=rule.id,
                        device_id=device.id,
                        target=target.get("target_name"),
                        value=value,
                        threshold=effective_threshold,
                        threshold_source=target.get("threshold_source"),
                    )
                triggered = True
            else:
                _clear_pending_alert(rule, device, target)
                if existing and existing.status in {"firing", "acknowledged", "ignored", "snoozed"}:
                    if not _recovery_confirmed(rule, device, target, float(value)):
                        continue
                    _clear_pending_recovery(rule, device, target)
                    was_silenced_ignored = existing.status == "ignored" and existing.ignored_by == "alert_silence"
                    existing.alert_value = float(value)
                    existing.threshold = effective_threshold
                    existing.message = _build_alert_message(rule, device, float(value), target)
                    existing.alert_target_type = target.get("target_type")
                    existing.alert_target_key = target.get("target_key")
                    existing.alert_target_name = target.get("target_name")
                    existing.status = "resolved"
                    existing.resolved_at = _utc_now()
                    existing.resolved_by = "system"
                    db.commit()
                    if not was_silenced_ignored:
                        enqueue_alert_notification(existing.id, "auto_resolved", "system")
                    logger.info(
                        "告警恢复",
                        rule_id=rule.id,
                        device_id=device.id,
                        target=target.get("target_name"),
                        was_silenced_ignored=was_silenced_ignored,
                    )
    
    return triggered


def _resolve_disappeared_target_alerts(
    db: Session,
    rule: AlertRule,
    device: Device,
    active_target_keys: set[str],
) -> None:
    """
    监控目标被删除/不再采集后，SNMP walk 不再返回该 target。
    这种场景不应继续保持活动告警，而应认为监控目标已消失并自动恢复。
    """
    # 接口/协议目标偶尔会因为一次 SNMP walk 不完整、Exporter 短暂超时或分页截断
    # 暂时不在本轮结果中。“没有采集到”不等于“状态已恢复”。这些状态类告警必须
    # 明确采到正常值并通过恢复确认，不能仅凭目标消失就发送恢复通知。
    if rule.metric_type in INTERFACE_METRIC_TYPES or rule.metric_type in PROTOCOL_METRIC_TYPES:
        return

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
        alert.resolution_note = "监控目标已不在当前采集结果中，自动恢复"
        db.commit()
        enqueue_alert_notification(alert.id, "auto_resolved", "system")
        logger.info(
            "监控目标消失，自动恢复告警",
            rule_id=rule.id,
            device_id=device.id,
            alert_id=alert.id,
            target_key=alert.alert_target_key,
        )


def _is_default_skipped_interface(interface_name: Optional[str]) -> bool:
    normalized = str(interface_name or "").strip().lower()
    return bool(normalized and any(marker in normalized for marker in DEFAULT_SKIPPED_INTERFACE_MARKERS))


def _resolve_active_interface_alert(
    db: Session,
    alert: AlertHistory,
    reason: str,
    value: Optional[float] = None,
    notify: bool = True,
) -> None:
    """Resolve one active interface alert with a consistent audit trail."""
    now = _utc_now()
    if value is not None:
        alert.alert_value = float(value)
    alert.status = "resolved"
    alert.resolved_at = now
    alert.resolved_by = "system"
    alert.resolution_note = reason
    alert.updated_at = now
    db.commit()
    if notify:
        enqueue_alert_notification(alert.id, "auto_resolved", "system")


def _latest_interface_values_by_device(device_id: int, field: str, start: str = "-5m") -> Dict[str, float]:
    """Return latest interface field values keyed by both interface index and name."""
    flux = _build_influx_grouped_last_query(
        measurement="interface_monitoring",
        device_id=int(device_id),
        field=field,
        group_columns=["interface_index", "interface_name"],
        start=start,
    )
    latest: Dict[str, float] = {}
    try:
        for row in influx_client.query(flux):
            value = row.get("value")
            if value is None:
                continue
            numeric_value = float(value)
            for key in (row.get("interface_index"), row.get("interface_name")):
                if key is not None and str(key).strip():
                    latest[str(key)] = numeric_value
    except Exception as exc:
        logger.error("接口告警快速恢复读取最新指标失败", device_id=device_id, field=field, error=str(exc))
    return latest


@shared_task
def resolve_interface_alerts_quick() -> Dict[str, Any]:
    """
    快速恢复接口类活动告警。

    目的：
    1. 端口被改为不监控/排除后，历史已触发的接口告警不再等待慢速全量规则扫描。
    2. 设备退出监控后，接口类活动告警立即静默恢复。

    注意：最新值恢复只处理配置了端口范围/未监控的候选设备，避免扫描全网 1 万+ 活动告警。
    """
    started_at = time.time()
    lock_value = f"{started_at}:{uuid.uuid4()}"
    lock_acquired = bool(
        redis_client.set(
            INTERFACE_ALERT_RECOVERY_LOCK_KEY,
            lock_value,
            ex=INTERFACE_ALERT_RECOVERY_LOCK_TTL_SECONDS,
            nx=True,
        )
    )
    if not lock_acquired:
        return {"skipped": True, "reason": "interface alert recovery already running"}

    db = SessionLocal()
    checked = 0
    resolved_scope = 0
    resolved_value = 0
    try:
        candidate_devices = db.query(Device).filter(
            or_(Device.is_monitored == False, Device.custom_fields.isnot(None))  # noqa: E712
        ).all()
        candidate_device_map = {}
        for device in candidate_devices:
            scope = get_interface_scope(device)
            if (not device.is_monitored) or (scope.get("mode") != "all"):
                candidate_device_map[int(device.id)] = device

        if not candidate_device_map:
            return {"checked": 0, "resolved_scope": 0, "elapsed_seconds": round(time.time() - started_at, 3)}

        active_rows = (
            db.query(AlertHistory, AlertRule)
            .join(AlertRule, AlertRule.id == AlertHistory.rule_id)
            .filter(
                AlertHistory.device_id.in_(list(candidate_device_map.keys())),
                AlertHistory.status.in_(["firing", "acknowledged", "ignored", "snoozed"]),
                AlertHistory.alert_target_type == "interface",
            )
            .all()
        )

        scoped_admin_alerts_by_device: Dict[int, List[tuple[AlertHistory, AlertRule, Device]]] = {}
        for alert, rule in active_rows:
            device = candidate_device_map.get(int(alert.device_id or 0))
            if not device:
                continue
            checked += 1
            if not device.is_monitored:
                _resolve_active_interface_alert(
                    db,
                    alert,
                    "设备已设置为未监控，系统自动恢复接口告警",
                    notify=not (alert.status == "ignored" and alert.ignored_by == "alert_silence"),
                )
                resolved_scope += 1
                continue

            if _is_default_skipped_interface(alert.alert_target_name):
                _resolve_active_interface_alert(
                    db,
                    alert,
                    "逻辑接口默认不参与接口物理状态监控，系统自动恢复活动告警",
                    notify=not (alert.status == "ignored" and alert.ignored_by == "alert_silence"),
                )
                resolved_scope += 1
                continue

            if not is_interface_monitored(device, alert.alert_target_name, alert.alert_target_key):
                _resolve_active_interface_alert(
                    db,
                    alert,
                    "端口已设置为不监控，系统自动恢复活动告警",
                    notify=not (alert.status == "ignored" and alert.ignored_by == "alert_silence"),
                )
                resolved_scope += 1
                continue

            if rule.metric_type == "interface_admin_up_oper_down":
                scoped_admin_alerts_by_device.setdefault(int(device.id), []).append((alert, rule, device))

        for device_id, rows in scoped_admin_alerts_by_device.items():
            latest_values = _latest_interface_values_by_device(device_id, "admin_up_oper_down", "-5m")
            if not latest_values:
                continue
            for alert, rule, device in rows:
                value = None
                for key in (alert.alert_target_key, alert.alert_target_name):
                    if key is not None and str(key) in latest_values:
                        value = latest_values[str(key)]
                        break
                if value is None:
                    continue
                if not _evaluate_rule_condition(rule, float(value)):
                    _clear_pending_recovery(rule, device, _target_from_alert_history(alert))
                    _resolve_active_interface_alert(
                        db,
                        alert,
                        "接口最新采集值已恢复，系统快速恢复活动告警",
                        value=float(value),
                        notify=not (alert.status == "ignored" and alert.ignored_by == "alert_silence"),
                    )
                    resolved_value += 1

        elapsed_seconds = round(time.time() - started_at, 3)
        if resolved_scope or resolved_value or elapsed_seconds >= 3:
            logger.info(
                "接口告警快速恢复完成",
                checked=checked,
                resolved_scope=resolved_scope,
                resolved_value=resolved_value,
                elapsed_seconds=elapsed_seconds,
            )
        return {
            "checked": checked,
            "resolved_scope": resolved_scope,
            "resolved_value": resolved_value,
            "elapsed_seconds": elapsed_seconds,
        }
    except Exception as exc:
        logger.error("接口告警快速恢复失败", error=str(exc))
        return {"error": str(exc)}
    finally:
        db.close()
        if redis_client.get(INTERFACE_ALERT_RECOVERY_LOCK_KEY) == lock_value:
            redis_client.delete(INTERFACE_ALERT_RECOVERY_LOCK_KEY)


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
) -> Dict[str, Any]:
    flux = _build_influx_last_fields_query(
        measurement="interface_monitoring",
        device_id=device_id,
        fields=fields,
        start=time_range,
        tag_filters={"interface_name": interface_name},
    )
    results = influx_client.query(flux)
    value_map: Dict[str, Any] = {field: None for field in fields}
    newest_sample_time: Optional[datetime] = None
    for item in results:
        field_name = item.get("field") or item.get("_field")
        if field_name in value_map and item.get("value") is not None:
            value_map[field_name] = float(item["value"])
            row_time = _parse_row_time(item.get("_time") or item.get("time"))
            if row_time and (newest_sample_time is None or row_time > newest_sample_time):
                newest_sample_time = row_time
    value_map["_sample_time"] = newest_sample_time.isoformat() if newest_sample_time else None
    value_map["_sample_age_seconds"] = (
        max((_utc_now() - newest_sample_time).total_seconds(), 0.0)
        if newest_sample_time else None
    )
    return value_map


def _normalize_interface_identity(value: Any) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _load_json_cache(key: str) -> Dict[str, Any]:
    raw = redis_client.get(key)
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _optical_interface_states(device_id: int) -> Dict[str, Dict[str, Any]]:
    payload = _load_json_cache(f"monitor:cache:interfaces:{device_id}")
    states: Dict[str, Dict[str, Any]] = {}
    for row in payload.get("interfaces") or payload.get("items") or []:
        if not isinstance(row, dict):
            continue
        for value in (row.get("index"), row.get("interface_index"), row.get("name"), row.get("interface_name")):
            key = _normalize_interface_identity(value)
            if key:
                states[key] = row
    return states


def _optical_interface_state(item: Dict[str, Any], states: Dict[str, Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    for value in (item.get("interface_index"), item.get("interface_name")):
        state = states.get(_normalize_interface_identity(value))
        if state:
            return state
    return None


def _interface_state_is_up(state: Optional[Dict[str, Any]]) -> bool:
    if not state:
        return False
    oper = str(state.get("oper_status") or state.get("if_oper_status") or "").strip().lower()
    admin = str(state.get("admin_status") or state.get("if_admin_status") or "").strip().lower()
    return oper in {"up", "1", "true"} and admin not in {"down", "2", "false"}


def _resolve_optical_alerts_on_inactive_interfaces(
    db: Session,
    rule: AlertRule,
    device: Device,
) -> int:
    """接口明确Down时，无光测量下限不应保持为光功率故障。"""
    if rule.metric_type not in OPTICAL_METRIC_TYPES:
        return 0
    extra_config = rule.extra_config if isinstance(rule.extra_config, dict) else {}
    if not extra_config.get("require_interface_up", True):
        return 0
    states = _optical_interface_states(device.id)
    if not states:
        return 0
    alerts = db.query(AlertHistory).filter(
        AlertHistory.rule_id == rule.id,
        AlertHistory.device_id == device.id,
        AlertHistory.status.in_(["firing", "acknowledged", "ignored", "snoozed"]),
    ).all()
    resolved = 0
    for alert in alerts:
        state = (
            states.get(_normalize_interface_identity(alert.alert_target_key))
            or states.get(_normalize_interface_identity(alert.alert_target_name))
        )
        if not state or _interface_state_is_up(state):
            continue
        oper = str(state.get("oper_status") or state.get("if_oper_status") or "unknown").lower()
        admin = str(state.get("admin_status") or state.get("if_admin_status") or "unknown").lower()
        now = _utc_now()
        alert.status = "resolved"
        alert.resolved_at = now
        alert.updated_at = now
        alert.resolved_by = "system"
        alert.resolution_note = f"接口当前为Down（admin={admin}, oper={oper}），无光读数不参与光模块功率告警"
        db.commit()
        resolved += 1
        logger.info(
            "接口Down，自动关闭光模块功率告警",
            rule_id=rule.id,
            alert_id=alert.id,
            device_id=device.id,
            interface=alert.alert_target_name,
            admin_status=admin,
            oper_status=oper,
        )
    return resolved


def _optical_number(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not (-60 <= number <= 30):
        return None
    return number


def _module_threshold(item: Dict[str, Any], direction: str, condition: str, severity: str) -> tuple[Optional[float], Optional[str]]:
    side = "low" if condition in {"<", "<="} else "high"
    severity_text = str(severity or "").upper()
    preferred = "alarm" if severity_text in {"P0", "CRITICAL"} else "warning"
    alternate = "warning" if preferred == "alarm" else "alarm"
    for level in (preferred, alternate):
        key = f"{direction}_{side}_{level}_dbm"
        value = _optical_number(item.get(key))
        if value is not None:
            return value, f"设备DDM {direction.upper()} {side.upper()} {level.upper()}阈值"
    return None, None


def _module_profile_bounds(item: Dict[str, Any], state: Optional[Dict[str, Any]]) -> Optional[Dict[str, tuple[float, float]]]:
    text = " ".join(str(item.get(key) or "") for key in ("transceiver_type", "hardware_type", "vendor_name")).upper()
    profiles = [
        (("400G", "SR8"), {"rx": (-8.4, 4.0), "tx": (-6.5, 4.0)}),
        (("400G", "DR4"), {"rx": (-5.9, 4.0), "tx": (-2.9, 4.0)}),
        (("400G", "FR4"), {"rx": (-7.3, 4.4), "tx": (-3.2, 4.4)}),
        (("400G", "LR4"), {"rx": (-9.0, 5.1), "tx": (-2.7, 5.1)}),
        (("400G", "LR8"), {"rx": (-9.1, 5.3), "tx": (-2.8, 5.3)}),
        (("200G", "SR4"), {"rx": (-8.4, 4.0), "tx": (-6.5, 4.0)}),
        (("200G", "FR4"), {"rx": (-8.2, 4.7), "tx": (-4.2, 4.7)}),
        (("100G", "CWDM4"), {"rx": (-11.5, 2.5), "tx": (-6.5, 2.5)}),
        (("100G", "LR4"), {"rx": (-10.6, 4.5), "tx": (-4.3, 4.5)}),
        (("100G", "SR4"), {"rx": (-10.3, 2.4), "tx": (-8.4, 2.4)}),
        (("25G", "SR"), {"rx": (-10.3, 2.4), "tx": (-8.4, 2.4)}),
        (("10G", "LR"), {"rx": (-14.4, 0.5), "tx": (-8.2, 0.5)}),
        (("10G", "SR"), {"rx": (-9.9, -1.0), "tx": (-7.3, -1.0)}),
    ]
    for markers, bounds in profiles:
        if all(marker in text for marker in markers):
            return bounds

    speed_bps = None
    if state:
        try:
            speed_bps = float(state.get("speed_bps") or 0)
        except (TypeError, ValueError):
            speed_bps = None
    try:
        speed_mbps = float(item.get("speed_mbps") or 0)
    except (TypeError, ValueError):
        speed_mbps = 0
    speed_gbps = speed_mbps / 1000 if speed_mbps else (speed_bps or 0) / 1_000_000_000
    if speed_gbps >= 390:
        return {"rx": (-9.1, 5.3), "tx": (-6.5, 5.3)}
    if speed_gbps >= 190:
        return {"rx": (-8.4, 4.7), "tx": (-6.5, 4.7)}
    if speed_gbps >= 90:
        return {"rx": (-20.9, 4.8), "tx": (-9.4, 6.5)}
    if speed_gbps >= 20:
        return {"rx": (-13.3, 2.4), "tx": (-8.4, 2.4)}
    if speed_gbps >= 9:
        return {"rx": (-24.0, 0.5), "tx": (-8.2, 4.0)}
    return None


def _optical_effective_threshold(
    rule: AlertRule,
    item: Dict[str, Any],
    state: Optional[Dict[str, Any]],
) -> tuple[float, str]:
    direction = "rx" if rule.metric_type == "optical_rx_power" else "tx"
    module_value, module_source = _module_threshold(item, direction, rule.condition, rule.severity)
    if module_value is not None:
        return module_value, module_source or "设备DDM阈值"
    bounds = _module_profile_bounds(item, state)
    if bounds and direction in bounds:
        low, high = bounds[direction]
        return (low if rule.condition in {"<", "<="} else high), "厂商/速率/模块类型阈值"
    return float(rule.threshold), "规则默认阈值"


def _optical_history_key(device_id: int, interface_key: str) -> str:
    return f"alerts:optical_rx_history:{device_id}:{interface_key}"


def _update_optical_rx_history(
    device_id: int,
    interface_key: str,
    sample_time: Any,
    current_rx: float,
) -> Dict[str, Optional[float]]:
    parsed_time = _parse_row_time(sample_time) or _utc_now()
    timestamp = parsed_time.timestamp()
    key = _optical_history_key(device_id, interface_key)
    payload = _load_json_cache(key)
    points = []
    for point in payload.get("points") or []:
        try:
            point_time = float(point[0])
            point_value = float(point[1])
        except (TypeError, ValueError, IndexError):
            continue
        if timestamp - point_time <= 49 * 3600:
            points.append([point_time, point_value])
    latest_sample = str(payload.get("latest_sample_time") or "")
    sample_text = parsed_time.isoformat()
    if sample_text != latest_sample:
        if not points or timestamp - float(points[-1][0]) >= 30 * 60:
            points.append([timestamp, float(current_rx)])
        payload = {"points": points[-100:], "latest_sample_time": sample_text}
        redis_client.set(key, json.dumps(payload, ensure_ascii=False), ex=72 * 3600)

    def drop_for(window_seconds: int, minimum_age_seconds: int) -> Optional[float]:
        candidates = [point for point in points if timestamp - float(point[0]) >= minimum_age_seconds]
        if not candidates:
            return None
        target_time = timestamp - window_seconds
        baseline = min(candidates, key=lambda point: abs(float(point[0]) - target_time))
        return round(float(baseline[1]) - float(current_rx), 4)

    return {
        "drop_1h": drop_for(3600, 45 * 60),
        "drop_24h": drop_for(24 * 3600, 23 * 3600),
    }


def _fec_rows(device_id: int) -> tuple[Dict[str, Dict[str, Any]], Optional[str]]:
    payload = _load_json_cache(f"monitor:cache:telemetry_lossless:{device_id}:ifmgr_iffecdata")
    collected_at = str(payload.get("collected_at") or "") or None
    rows: Dict[str, Dict[str, Any]] = {}
    for row in payload.get("rows") or []:
        if not isinstance(row, dict):
            continue
        key = _normalize_interface_identity(row.get("interface_name"))
        if key:
            rows[key] = row
    return rows, collected_at


def _fec_counter_delta(device_id: int, interface_key: str, row: Dict[str, Any], sample_time: str) -> Optional[Dict[str, float]]:
    try:
        correctable = float(row.get("fec_correctable_packets") or 0)
        uncorrectable = float(row.get("fec_uncorrectable_packets") or 0)
    except (TypeError, ValueError):
        return None
    key = f"alerts:fec_counter:{device_id}:{interface_key}"
    previous = _load_json_cache(key)
    if str(previous.get("sample_time") or "") == str(sample_time or ""):
        if previous.get("correctable_delta") is None:
            return None
        return {
            "correctable_delta": float(previous.get("correctable_delta") or 0),
            "uncorrectable_delta": float(previous.get("uncorrectable_delta") or 0),
        }
    previous_correctable = previous.get("correctable")
    previous_uncorrectable = previous.get("uncorrectable")
    result = None
    if previous_correctable is not None and previous_uncorrectable is not None:
        result = {
            "correctable_delta": max(correctable - float(previous_correctable), 0.0),
            "uncorrectable_delta": max(uncorrectable - float(previous_uncorrectable), 0.0),
        }
    redis_client.set(
        key,
        json.dumps({
            "sample_time": sample_time,
            "correctable": correctable,
            "uncorrectable": uncorrectable,
            "correctable_delta": result.get("correctable_delta") if result else None,
            "uncorrectable_delta": result.get("uncorrectable_delta") if result else None,
        }),
        ex=3 * 3600,
    )
    return result


def _get_optical_targets(
    db: Session,
    device: Device,
    metric_type: str,
    extra_config: Dict[str, Any],
    rule: Optional[AlertRule] = None,
) -> List[Dict[str, Any]]:
    payload = _load_json_cache(f"monitor:cache:optical_modules:{device.id}")
    states = _optical_interface_states(device.id)
    circuit_map = _device_interface_circuit_map(db, device.id)
    fec_by_interface, fec_sample_time = _fec_rows(device.id) if metric_type == "optical_rx_fec_correlation" else ({}, None)
    targets: List[Dict[str, Any]] = []
    for item in payload.get("items") or []:
        if not isinstance(item, dict):
            continue
        interface_name = str(item.get("interface_name") or "").strip()
        interface_index = item.get("interface_index")
        if not interface_name:
            continue
        if not _matches_text_filter(
            interface_name,
            str(extra_config.get("interface_name")) if extra_config.get("interface_name") else None,
            str(extra_config.get("interface_regex")) if extra_config.get("interface_regex") else None,
            str(extra_config.get("exclude_interface_regex")) if extra_config.get("exclude_interface_regex") else None,
        ):
            continue
        if extra_config.get("interface_index") and str(interface_index) != str(extra_config.get("interface_index")):
            continue
        if _is_default_skipped_interface(interface_name) and not extra_config.get("include_logical_interfaces"):
            continue
        if not is_interface_monitored(device, interface_name, interface_index):
            continue
        state = _optical_interface_state(item, states)
        if extra_config.get("require_interface_up", True) and not _interface_state_is_up(state):
            continue

        sample_time = str(item.get("collected_at") or payload.get("collected_at") or "") or None
        sample_age = _row_age_seconds({"_time": sample_time}) if sample_time else None
        interface_key = str(interface_index or _normalize_interface_identity(interface_name))
        rx_power = _optical_number(item.get("rx_power_dbm"))
        value: Optional[float] = None
        details: List[str] = []
        target: Dict[str, Any] = {
            "target_type": "interface",
            "target_key": interface_key,
            "target_name": interface_name,
            "sample_time": sample_time,
            "sample_age_seconds": sample_age,
            "required_samples": int(extra_config.get("required_samples") or 3),
        }

        history = None
        if rx_power is not None:
            history = _update_optical_rx_history(device.id, interface_key, sample_time, rx_power)

        if metric_type in {"optical_rx_power", "optical_tx_power"}:
            direction = "rx" if metric_type == "optical_rx_power" else "tx"
            lane_values = [
                _optical_number(channel.get(f"{direction}_power_dbm"))
                for channel in item.get("channels") or []
                if isinstance(channel, dict)
            ]
            lane_values = [lane for lane in lane_values if lane is not None]
            if lane_values:
                value = min(lane_values) if rule and rule.condition in {"<", "<="} else max(lane_values)
            else:
                value = _optical_number(item.get(f"{direction}_power_dbm"))
            if value is None:
                continue
            if rule is not None:
                threshold, source = _optical_effective_threshold(rule=rule, item=item, state=state)
                if source.startswith("设备DDM"):
                    lane_label = "最低" if rule.condition in {"<", "<="} else "最高"
                    details.append(f"设备DDM阈值优先；当前{lane_label}Lane：{value:.2f}dBm")
                target["effective_threshold"] = threshold
                target["threshold_source"] = source
        elif metric_type == "optical_lane_power_delta":
            lane_values = [
                _optical_number(channel.get("rx_power_dbm"))
                for channel in item.get("channels") or []
                if isinstance(channel, dict)
            ]
            lane_values = [lane for lane in lane_values if lane is not None]
            if len(lane_values) < 2:
                continue
            value = round(max(lane_values) - min(lane_values), 4)
            details.append(f"Lane最大/最小收光：{max(lane_values):.2f}/{min(lane_values):.2f}dBm")
        elif metric_type == "optical_rx_power_drop_24h":
            value = float(history.get("drop_24h") or 0.0) if history else 0.0
            details.append(f"当前收光：{rx_power:.2f}dBm" if rx_power is not None else "当前收光：-")
        elif metric_type == "optical_rx_fec_correlation":
            fec_row = fec_by_interface.get(_normalize_interface_identity(interface_name))
            if not fec_row or not fec_sample_time or not history:
                continue
            fec_delta = _fec_counter_delta(device.id, interface_key, fec_row, fec_sample_time)
            if fec_delta is None:
                continue
            drop_1h = float(history.get("drop_1h") or 0.0)
            corrected = float(fec_delta.get("correctable_delta") or 0.0)
            uncorrected = float(fec_delta.get("uncorrectable_delta") or 0.0)
            minimum_drop = float(extra_config.get("rx_drop_db") or 1.0)
            value = max(corrected, 1_000_000_000.0 if uncorrected > 0 else 0.0) if drop_1h >= minimum_drop else 0.0
            target["sample_time"] = fec_sample_time
            target["sample_age_seconds"] = _row_age_seconds({"_time": fec_sample_time})
            details.extend([
                f"近1小时收光下降：{drop_1h:.2f}dB",
                f"本周期FEC可纠错/不可纠错增长：{corrected:.0f}/{uncorrected:.0f}",
            ])
        if value is None:
            continue
        target["value"] = float(value)
        if details:
            target["diagnostic_text"] = "；".join(details)
        targets.append(_enrich_interface_target_with_resources(db, device, target, circuit_map))
    return targets


def _get_circuit_targets(
    db: Session,
    device: Device,
    metric_type: str,
    extra_config: Dict[str, Any],
) -> List[Dict[str, Any]]:
    line_type = "internet" if metric_type == "internet_circuit_traffic_floor" else "private_line"
    time_range = str(extra_config.get("time_range") or "-10m")
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
            sample_time: Optional[str] = None
            sample_age_seconds: Optional[float] = None
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
                sample_time = fields.get("_sample_time")
                sample_age_seconds = fields.get("_sample_age_seconds")
            if in_bps is None and out_bps is None:
                continue
            traffic_floor_value = round(max(in_bps or 0.0, out_bps or 0.0) / 1_000_000, 2)
            targets.append(
                {
                    "target_type": "circuit_port",
                    "target_key": f"circuit:{circuit.id}:{role}",
                    "target_name": f"{circuit.name} / {endpoint_port_name}",
                    "value": float(traffic_floor_value),
                    "sample_time": sample_time,
                    "sample_age_seconds": sample_age_seconds,
                }
            )
    return targets


def _normalize_interface_name(value: Optional[str]) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _find_interface_circuits(db: Session, device_id: int, interface_name: Optional[str]) -> List[Dict[str, Any]]:
    normalized_interface = _normalize_interface_name(interface_name)
    if not normalized_interface:
        return []
    return _device_interface_circuit_map(db, device_id).get(normalized_interface, [])


def _device_interface_circuit_map(db: Session, device_id: int) -> Dict[str, List[Dict[str, Any]]]:
    """Load a device's circuit bindings once instead of once per interface target."""
    circuits = db.query(Circuit).filter(
        Circuit.status == "active",
        or_(Circuit.primary_device_id == device_id, Circuit.secondary_device_id == device_id),
    ).all()
    matches: Dict[str, List[Dict[str, Any]]] = {}
    for circuit in circuits:
        endpoints = [
            ("primary", circuit.primary_device_id, circuit.primary_port_name),
            ("secondary", circuit.secondary_device_id, circuit.secondary_port_name),
        ]
        for role, endpoint_device_id, endpoint_port_name in endpoints:
            if endpoint_device_id != device_id:
                continue
            normalized_port = _normalize_interface_name(endpoint_port_name)
            if not normalized_port:
                continue
            matches.setdefault(normalized_port, []).append({
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


def _enrich_interface_target_with_resources(
    db: Session,
    device: Device,
    target: Dict[str, Any],
    circuit_map: Optional[Dict[str, List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    if target.get("target_type") != "interface":
        return target
    if circuit_map is None:
        circuits = _find_interface_circuits(db, device.id, target.get("target_name"))
    else:
        circuits = circuit_map.get(_normalize_interface_name(target.get("target_name")), [])
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
    run_cache = _ALERT_RUN_EXPORTER_CACHE.get()
    if run_cache is not None and device.id in run_cache:
        return run_cache[device.id]

    now = time.monotonic()
    with _EXPORTER_SCRAPE_CACHE_LOCK:
        cached = _EXPORTER_SCRAPE_CACHE.get(device.id)
        if cached and now - float(cached.get("created_at", 0.0)) < EXPORTER_SCRAPE_CACHE_TTL_SECONDS:
            metrics = cached.get("metrics", {})
            if run_cache is not None:
                run_cache[device.id] = metrics
            return metrics

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
    if run_cache is not None:
        run_cache[device.id] = metrics
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


INFLUX_DEVICE_SAMPLE_MAP: Dict[str, tuple[str, Optional[str], str]] = {
    "snmp_cpu": ("snmp_metrics", "cpu", "usage"),
    "snmp_memory": ("snmp_metrics", "memory", "usage_percent"),
    "device_status": ("device_status", None, "status"),
    "device_reachability": ("device_reachability", None, "reachable"),
    "snmp_reachability": ("snmp_reachability", None, "reachable"),
    "exporter_reachability": ("exporter_reachability", None, "reachable"),
    "telemetry_reachability": ("telemetry_reachability", None, "reachable"),
    "snmp_session_usage": ("snmp_sessions", None, "usage_percent"),
    "snmp_ha_status": ("snmp_system", None, "ha_status"),
    "snmp_session_queue_full_drop_delta": ("snmp_system", None, "pending_session_queue_full_drop"),
}


def _get_influx_device_sample(
    device_id: int,
    metric_type: str,
    extra_config: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    source = INFLUX_DEVICE_SAMPLE_MAP.get(metric_type)
    if not source:
        return None
    measurement, metric, field = source
    tag_filters = {"metric_type": metric} if metric else None
    default_range = "-5m" if metric_type in DEVICE_REACHABILITY_METRIC_TYPES else "-10m"
    flux = _build_influx_last_value_query(
        measurement=measurement,
        device_id=device_id,
        field=field,
        start=str(extra_config.get("time_range") or default_range),
        tag_filters=tag_filters,
    )
    rows = influx_client.query(flux)
    if not rows or rows[0].get("value") is None:
        return None
    row = rows[0]
    sample_time = row.get("_time") or row.get("time")
    return {
        "value": float(row["value"]),
        "sample_time": str(sample_time) if sample_time is not None else None,
        "sample_age_seconds": _row_age_seconds(row),
    }


def _get_metric_targets(
    db: Session,
    device: Device,
    metric_type: str,
    extra_config: Optional[Dict[str, Any]] = None,
    rule: Optional[AlertRule] = None,
) -> List[Dict[str, Any]]:
    extra_config = extra_config or {}
    targets: List[Dict[str, Any]] = []

    if metric_type in EXPORTER_METRIC_TYPES:
        return _get_exporter_metric_targets(device, extra_config)

    if metric_type in OPTICAL_METRIC_TYPES:
        return _get_optical_targets(db, device, metric_type, extra_config, rule)

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
        sample = _get_influx_device_sample(device.id, metric_type, extra_config)
        if not sample:
            return []
        return [{
            "target_type": "device",
            "target_key": str(device.id),
            "target_name": None,
            **sample,
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
                "sample_time": str(item.get("_time") or item.get("time") or "") or None,
                "sample_age_seconds": _row_age_seconds(item),
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
                "sample_time": str(item.get("_time") or item.get("time") or "") or None,
                "sample_age_seconds": _row_age_seconds(item),
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
        if _get_effective_monitor_source(device) != "asternos_exporter":
            sample = _get_influx_device_sample(device.id, metric_type, extra_config)
            if sample:
                return [{
                    "target_type": "device",
                    "target_key": str(device.id),
                    "target_name": device.name,
                    **sample,
                }]
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
    default_time_range = "-7m" if metric_type in PROTOCOL_METRIC_TYPES else "-10m"
    time_range = str(extra_config.get("time_range") or default_time_range)
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
                if _is_default_skipped_interface(interface_name) and not extra_config.get("include_logical_interfaces"):
                    continue
                if not is_interface_monitored(device, interface_name, interface_index):
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
            sample_age_seconds = _row_age_seconds(item)
            sample_time_value = item.get("_time") or item.get("time")
            if not _matches_text_filter(
                interface_name,
                str(extra_config.get("interface_name")) if extra_config.get("interface_name") else None,
                str(extra_config.get("interface_regex")) if extra_config.get("interface_regex") else None,
                str(extra_config.get("exclude_interface_regex")) if extra_config.get("exclude_interface_regex") else None,
            ):
                continue
            if extra_config.get("interface_index") and str(interface_index) != str(extra_config.get("interface_index")):
                continue
            if _is_default_skipped_interface(interface_name) and not extra_config.get("include_logical_interfaces"):
                continue
            if not is_interface_monitored(device, interface_name, interface_index):
                continue
            value = item.get("value")
            if value is None:
                continue
            if (
                metric_type == "interface_admin_up_oper_down"
                and float(value) >= 1.0
                and sample_age_seconds is not None
                and sample_age_seconds > INTERFACE_ADMIN_UP_OPER_DOWN_MAX_SAMPLE_AGE_SECONDS
            ):
                logger.info(
                    "跳过过期接口AdminUp物理Down采样",
                    device_id=device.id,
                    interface=interface_name,
                    interface_index=interface_index,
                    sample_age_seconds=round(sample_age_seconds, 2),
                    sample_time=str(sample_time_value),
                )
                continue
            targets.append(_enrich_interface_target_with_resources(db, device, {
                "target_type": "interface",
                "target_key": str(interface_index or interface_name),
                "target_name": interface_name or f"if{interface_index}",
                "value": float(value),
                "sample_time": str(sample_time_value) if sample_time_value is not None else None,
                "sample_age_seconds": round(sample_age_seconds, 3) if sample_age_seconds is not None else None,
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
                "sample_time": str(item.get("_time") or item.get("time") or "") or None,
                "sample_age_seconds": _row_age_seconds(item),
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
                    if not is_interface_monitored(device, interface_name, extra_config.get("interface_index")):
                        return None
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
            "snmp_reachability": ("snmp_reachability", None, "reachable"),
            "exporter_reachability": ("exporter_reachability", None, "reachable"),
            "telemetry_reachability": ("telemetry_reachability", None, "reachable"),
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
            if device and not is_interface_monitored(
                device,
                extra_config.get("interface_name"),
                extra_config.get("interface_index"),
            ):
                return None
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
            if device and not is_interface_monitored(
                device,
                extra_config.get("interface_name"),
                extra_config.get("interface_index"),
            ):
                return None

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


def _normalize_percent_threshold(value: float) -> float:
    """告警阈值兼容 0.85 和 85 两种写法。"""
    if 0 <= value <= 1:
        return value * 100
    return value


def _effective_rule_threshold(rule: AlertRule, target: Optional[Dict[str, Any]] = None) -> Optional[float]:
    if target and target.get("effective_threshold") is not None:
        try:
            return float(target["effective_threshold"])
        except (TypeError, ValueError):
            pass
    return float(rule.threshold) if rule.threshold is not None else None


def _evaluate_rule_condition(rule: AlertRule, value: float, target: Optional[Dict[str, Any]] = None) -> bool:
    """按指标类型评估告警条件。"""
    threshold = _effective_rule_threshold(rule, target)
    if threshold is None:
        return False

    compare_value = float(value)
    compare_threshold = float(threshold)
    if rule.metric_type in PERCENT_METRIC_TYPES:
        # 采集器写入的 usage/usage_percent 已经是 0~100 的百分数。
        # 不能再把 1.0 当成 100%，否则设备 CPU 恰好为 1% 时会误告警。
        # 只有历史规则阈值允许使用 0.7 表示 70%。
        compare_threshold = _normalize_percent_threshold(compare_threshold)

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
    if rule.metric_type in {"optical_lane_power_delta", "optical_rx_power_drop_24h"}:
        return "dB"
    if rule.metric_type == "optical_rx_fec_correlation":
        return "个/周期"
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
        return f"{float(value):.1f}%"
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
        return f"{rule.condition} {_normalize_percent_threshold(threshold_value):.1f}%"
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


def _alert_message_metadata(alert: AlertHistory) -> List[Dict[str, str]]:
    """Preserve dynamic-threshold and correlation evidence in robot cards."""
    labels = {"阈值来源", "关联判断"}
    rows: List[Dict[str, str]] = []
    for line in str(alert.message or "").splitlines():
        if "：" in line:
            label, value = line.split("：", 1)
        elif ":" in line:
            label, value = line.split(":", 1)
        else:
            continue
        label = label.strip()
        value = value.strip()
        if label in labels and value:
            rows.append({"label": label, "value": value})
    return rows


def _format_alert_value(rule: AlertRule, value: float) -> str:
    if _numeric_detail_label(rule):
        return _format_numeric_detail_number(rule, float(value))
    if rule.metric_type in PERCENT_METRIC_TYPES:
        return f"{float(value):.1f}%"
    return str(value)


def _format_alert_threshold(rule: AlertRule, target: Optional[Dict[str, Any]] = None) -> str:
    threshold = _effective_rule_threshold(rule, target)
    if threshold is None:
        return "-"
    if _numeric_detail_label(rule):
        return _format_numeric_detail_threshold(rule, threshold)
    if rule.metric_type in PERCENT_METRIC_TYPES:
        return f"{rule.condition} {_normalize_percent_threshold(float(threshold)):.1f}%"
    return f"{rule.condition} {threshold}"


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
        "device_reachability": "Ping可达状态",
        "snmp_reachability": "SNMP可达状态",
        "exporter_reachability": "Exporter可达状态",
        "telemetry_reachability": "Telemetry可达状态",
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
        "optical_rx_power": "模块收光功率",
        "optical_tx_power": "模块发光功率",
        "optical_lane_power_delta": "Lane收光功率差",
        "optical_rx_power_drop_24h": "24小时收光衰减",
        "optical_rx_fec_correlation": "光功率与FEC关联异常",
    }.get(rule.metric_type, rule.metric_type)
    resource_line = ""
    if target and target.get("resource_text"):
        resource_line = f"\n关联资源: {target['resource_text']}"
    state_line = ""
    if target and target.get("state_text"):
        state_line = f"\n协议状态: {target['state_text']}"
    threshold_source_line = ""
    if target and target.get("threshold_source"):
        threshold_source_line = f"\n阈值来源: {target['threshold_source']}"
    diagnostic_line = ""
    if target and target.get("diagnostic_text"):
        diagnostic_line = f"\n关联判断: {target['diagnostic_text']}"
    return (
        f"设备 {device.name} ({device.ip_address}){target_text} 触发告警\n"
        f"规则: {rule.name}\n"
        f"指标: {metric_label}\n"
        f"当前值: {_format_alert_value(rule, value)}\n"
        f"阈值: {_format_alert_threshold(rule, target)}"
        f"{threshold_source_line}"
        f"{diagnostic_line}"
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

    event_type = str(event_type or "firing")
    dedup_seconds = NOTIFICATION_DEDUP_SECONDS.get(event_type, 300)
    processing_key = _notification_key("processed", alert_id, event_type)
    if not redis_client.set(processing_key, uuid.uuid4().hex, ex=dedup_seconds, nx=True):
        logger.info("跳过重复通知执行", alert_id=alert_id, event_type=event_type)
        return {"skipped": "duplicate"}

    db = SessionLocal()
    try:
        alert = db.query(AlertHistory).filter(AlertHistory.id == alert_id).first()
        if not alert:
            return
        if event_type == "firing" and alert.status != "firing":
            logger.info(
                "跳过已非触发状态的故障通知",
                alert_id=alert_id,
                status=alert.status,
                event_type=event_type,
            )
            return
        if event_type == "auto_resolved" and not _has_successful_firing_notification(alert):
            logger.info(
                "跳过未发送过故障通知的恢复通知",
                alert_id=alert_id,
                alarm_id=alert.alarm_id,
                status=alert.status,
            )
            return
        
        rule = alert.rule
        if not rule:
            return
        notification_channels = _notification_channels_for_alert(rule, alert)
        if not notification_channels:
            return
        recent_success = False
        now = _utc_now()
        for sent in reversed(alert.notifications_sent or []):
            if sent.get("event_type") != event_type or not sent.get("success"):
                continue
            sent_at = _parse_notification_timestamp(sent.get("sent_at"))
            if sent_at and (now - sent_at).total_seconds() < dedup_seconds:
                recent_success = True
            break
        if recent_success:
            logger.info("跳过近期已成功发送的通知", alert_id=alert_id, event_type=event_type)
            return {"skipped": "recently_sent"}
        if event_type == "auto_resolved" and _is_operation_notification(rule):
            logger.info("跳过P3配置变更记录的恢复通知", alert_id=alert_id, alarm_id=alert.alarm_id)
            return
        if alert.device and _is_silenced(db, rule, alert.device, _target_from_alert_history(alert)):
            logger.info(
                "跳过命中屏蔽规则的告警通知",
                alert_id=alert.id,
                alarm_id=alert.alarm_id,
                event_type=event_type,
                device_id=alert.device_id,
                target=alert.alert_target_name,
            )
            return
        title = _build_notification_title(rule, event_type, actor)
        datacenter_text = _device_datacenter_text(alert.device) if alert.device else "-"
        if datacenter_text and datacenter_text != "-":
            title = f"{title}【{datacenter_text}】"
        content = _build_notification_content(db, alert, event_type, actor)
        card_data = _build_notification_card_data(db, alert, event_type, actor)
        mention_users = _notification_mentions_for_alert(rule, alert)
        
        # 异步发送通知
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _send_all():
                results_inner = []
                for channel in notification_channels:
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
                for ch, result in zip(notification_channels, results)
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
