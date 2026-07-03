"""
SNMP 采集任务 - Celery定时任务
"""
from celery import shared_task
from typing import Dict, Any, List, Optional
from datetime import datetime, timezone
from sqlalchemy import or_
from concurrent.futures import ThreadPoolExecutor, as_completed
import asyncio
import json
import math
import re
import subprocess
import time
import uuid

from app.config import settings
from app.database import SessionLocal
from app.models import Device, Circuit
from app.collectors import snmp_collector
from app.core import get_logger
from app.utils import redis_client, influx_client
from app.utils.asternos_exporter_client import asternos_exporter_client

logger = get_logger(__name__)

SNMP_SCHEDULER_INTERVAL_SECONDS = max(1, int(settings.SNMP_SCHEDULER_INTERVAL_SECONDS))
SNMP_FULL_COLLECTION_INTERVAL_SECONDS = max(
    SNMP_SCHEDULER_INTERVAL_SECONDS,
    int(settings.SNMP_FULL_COLLECTION_INTERVAL_SECONDS),
)
SNMP_INTERFACE_REALTIME_INTERVAL_SECONDS = max(
    SNMP_SCHEDULER_INTERVAL_SECONDS,
    int(settings.SNMP_INTERFACE_REALTIME_INTERVAL_SECONDS),
)
SNMP_BATCH_COUNT = max(
    1,
    math.ceil(SNMP_FULL_COLLECTION_INTERVAL_SECONDS / SNMP_SCHEDULER_INTERVAL_SECONDS),
)
SNMP_INTERFACE_BATCH_COUNT = max(
    1,
    math.ceil(SNMP_INTERFACE_REALTIME_INTERVAL_SECONDS / SNMP_SCHEDULER_INTERVAL_SECONDS),
)
SNMP_MAX_DEVICES_PER_TICK = max(1, int(settings.SNMP_MAX_DEVICES_PER_TICK))
SNMP_TASK_LOCK_TTL_SECONDS = max(180, min(600, SNMP_FULL_COLLECTION_INTERVAL_SECONDS * 3))
ASTERNOS_SCHEDULER_INTERVAL_SECONDS = max(1, int(settings.ASTERNOS_SCHEDULER_INTERVAL_SECONDS))
ASTERNOS_FULL_COLLECTION_INTERVAL_SECONDS = max(
    ASTERNOS_SCHEDULER_INTERVAL_SECONDS,
    int(settings.ASTERNOS_FULL_COLLECTION_INTERVAL_SECONDS),
)
ASTERNOS_BATCH_COUNT = max(
    1,
    math.ceil(ASTERNOS_FULL_COLLECTION_INTERVAL_SECONDS / ASTERNOS_SCHEDULER_INTERVAL_SECONDS),
)
ASTERNOS_MAX_DEVICES_PER_TICK = max(1, int(settings.ASTERNOS_MAX_DEVICES_PER_TICK))
SNMP_VERIFY_OID = "1.3.6.1.2.1.1.3.0"
SNMP_FAILURE_THRESHOLD = 3
SNMP_STATUS_REACHABLE = "reachable"
SNMP_STATUS_UNREACHABLE = "unreachable"
SNMP_STATUS_UNKNOWN = "unknown"
ICMP_PING_PACKETS = 5
ICMP_PING_TIMEOUT_SECONDS = 2
ICMP_PING_INTERVAL_MS = 200
MONITOR_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
ASTERNOS_TASK_LOCK_TTL_SECONDS = max(45, min(180, ASTERNOS_FULL_COLLECTION_INTERVAL_SECONDS * 2))
INTERFACE_REALTIME_LOCK_TTL_SECONDS = 120
INTERFACE_REALTIME_MAX_WORKERS = max(1, int(settings.SNMP_INTERFACE_REALTIME_MAX_WORKERS))
INTERFACE_RATE_CAP_MULTIPLIER = 1.03
ICMP_REACHABILITY_LOCK_TTL_SECONDS = max(25, (ICMP_PING_PACKETS * ICMP_PING_TIMEOUT_SECONDS) + 15)
ASTERNOS_COUNTER_METRICS = [
    {
        "field": "queue_egress_dropped_pkts_delta",
        "metric_base": "queue_egress_dropped_pkts",
        "label": "队列出方向丢包增长",
        "match_label": "port",
        "target_labels": ["port", "queue"],
    },
    {
        "field": "queue_ingress_dropped_pkts_delta",
        "metric_base": "queue_ingress_dropped_pkts",
        "label": "队列入方向丢包增长",
        "match_label": "port",
        "target_labels": ["port", "queue"],
    },
    {
        "field": "pfc_rx_pkts_delta",
        "metric_base": "pfc_rx_pkts",
        "label": "PFC RX包增长",
        "match_label": "port",
        "target_labels": ["port", "prio"],
    },
    {
        "field": "pfc_tx_pkts_delta",
        "metric_base": "pfc_tx_pkts",
        "label": "PFC TX包增长",
        "match_label": "port",
        "target_labels": ["port", "prio"],
    },
    {
        "field": "ecn_marked_pkts_delta",
        "metric_base": "ecn_marked_pkts",
        "label": "ECN标记包增长",
        "match_label": "port",
        "target_labels": ["port", "queue"],
    },
]




def _telemetry_interface_enabled(device: Device) -> bool:
    if bool(getattr(device, "gnmi_enabled", 0)):
        return True
    custom_fields = device.custom_fields or {}
    if not isinstance(custom_fields, dict):
        return False
    monitoring = custom_fields.get("monitoring") or {}
    if not isinstance(monitoring, dict):
        return False
    telemetry = monitoring.get("telemetry") or {}
    if isinstance(telemetry, dict):
        if telemetry.get("interface_stats_primary") is True or telemetry.get("disable_snmp") is True:
            return True
    return False


def _telemetry_primary_enabled(device: Device) -> bool:
    """Whether Telemetry should be treated as the authoritative data source.

    For high-density switches we still keep SNMP credentials configured, but
    SNMP should only fill gaps that are not currently covered by Telemetry
    parsing, such as BGP/OSPF neighbor state during migration.
    """
    if bool(getattr(device, "gnmi_enabled", 0)):
        return True
    custom_fields = device.custom_fields or {}
    if not isinstance(custom_fields, dict):
        return False
    monitoring = custom_fields.get("monitoring") or {}
    if not isinstance(monitoring, dict):
        return False
    telemetry = monitoring.get("telemetry") or {}
    if isinstance(telemetry, dict):
        return telemetry.get("primary") is True or telemetry.get("disable_snmp") is True
    return False


def _telemetry_snmp_disabled(device: Device) -> bool:
    """Whether SNMP collection should be skipped because Telemetry is authoritative.

    Kept as a custom-field switch so we can migrate one device at a time without
    changing the global device monitor_source enum or surprising existing SNMP
    devices.
    """
    custom_fields = device.custom_fields or {}
    if not isinstance(custom_fields, dict):
        return False
    monitoring = custom_fields.get("monitoring") or {}
    if not isinstance(monitoring, dict):
        return False
    telemetry = monitoring.get("telemetry") or {}
    return isinstance(telemetry, dict) and telemetry.get("disable_snmp") is True


def _telemetry_snmp_protocol_fallback_enabled(device: Device) -> bool:
    custom_fields = device.custom_fields or {}
    if not isinstance(custom_fields, dict):
        return True
    monitoring = custom_fields.get("monitoring") or {}
    if not isinstance(monitoring, dict):
        return True
    telemetry = monitoring.get("telemetry") or {}
    if isinstance(telemetry, dict) and telemetry.get("snmp_fallback_protocols") is False:
        return False
    return True


def _telemetry_snmp_optical_fallback_enabled(device: Device) -> bool:
    custom_fields = device.custom_fields or {}
    if not isinstance(custom_fields, dict):
        return False
    monitoring = custom_fields.get("monitoring") or {}
    if not isinstance(monitoring, dict):
        return False
    telemetry = monitoring.get("telemetry") or {}
    return isinstance(telemetry, dict) and telemetry.get("snmp_fallback_optical") is True


def _protocol_summary_has_data(summary: Dict[str, Any]) -> bool:
    for item in (summary or {}).values():
        if isinstance(item, dict) and int(item.get("total") or 0) > 0:
            return True
    return False


def _merge_protocols_into_overview_cache(device: Device, protocols: Dict[str, Any]) -> None:
    if not _protocol_summary_has_data(protocols):
        return
    raw = redis_client.get(_monitor_cache_key("overview", device.id))
    overview: Dict[str, Any] = {}
    if raw:
        try:
            overview = json.loads(raw)
        except Exception:
            overview = {}
    overview.setdefault("connectivity", {
        "type": "telemetry" if _telemetry_primary_enabled(device) else "snmp",
        "status": "reachable",
        "message": "Telemetry 正在推送" if _telemetry_primary_enabled(device) else f"SNMP {device.snmp_version}",
    })
    overview.setdefault("resources", {"cpu_percent": None, "memory_percent": None, "temperature": None, "storage_percent": None})
    overview.setdefault("sessions", {"current": None, "total": None, "usage_percent": None})
    overview.setdefault("hardware", {"fan_total": 0, "fan_down": 0, "fan_status_known": True, "power_total": 0, "power_down": 0, "power_status_known": True})
    overview.setdefault("system_info", {"sys_name": None, "sys_descr": None, "software_version": None, "snmp_model": None, "serial_number": None, "uptime_seconds": None})
    overview["protocols"] = protocols
    overview.setdefault("data_sources", {}).setdefault("protocols", {})
    for protocol in protocols:
        overview["data_sources"]["protocols"][protocol] = "snmp"
    overview["collected_at"] = datetime.now(timezone.utc).isoformat()
    _set_monitor_cache("overview", device.id, overview)


def _merge_snmp_gap_fill_into_overview_cache(device: Device, gap_fill: Dict[str, Any]) -> None:
    """Merge lightweight SNMP-only overview gaps into Telemetry cache.

    Telemetry-primary devices should keep Telemetry as the source for fast
    resources/interfaces, but uptime and fan/PSU state can be filled by SNMP
    until the Telemetry parser supports those paths.
    """
    if not gap_fill:
        return
    raw = redis_client.get(_monitor_cache_key("overview", device.id))
    overview: Dict[str, Any] = {}
    if raw:
        try:
            overview = json.loads(raw)
        except Exception:
            overview = {}
    overview.setdefault("connectivity", {
        "type": "telemetry" if _telemetry_primary_enabled(device) else "snmp",
        "status": "reachable",
        "message": "Telemetry 正在推送" if _telemetry_primary_enabled(device) else f"SNMP {device.snmp_version}",
    })
    overview.setdefault("resources", {"cpu_percent": None, "memory_percent": None, "temperature": None, "storage_percent": None})
    overview.setdefault("sessions", {"current": None, "total": None, "usage_percent": None})
    overview.setdefault("hardware", {"fan_total": 0, "fan_down": 0, "fan_status_known": True, "power_total": 0, "power_down": 0, "power_status_known": True})
    overview.setdefault("protocols", {"bgp": {"total": 0, "up": 0, "down": 0}, "ospf": {"total": 0, "up": 0, "down": 0}})
    overview.setdefault("system_info", {"sys_name": None, "sys_descr": None, "software_version": None, "snmp_model": None, "serial_number": None, "uptime_seconds": None})
    sources = overview.setdefault("data_sources", {})
    sources.setdefault("hardware", {})
    sources.setdefault("system_info", {})

    hardware = gap_fill.get("hardware")
    if isinstance(hardware, dict) and ((hardware.get("fan_total") or 0) > 0 or (hardware.get("power_total") or 0) > 0):
        overview["hardware"] = hardware
        for key in ["fan_total", "fan_down", "fan_status_known", "power_total", "power_down", "power_status_known"]:
            sources["hardware"][key] = "snmp"

    system_info = gap_fill.get("system_info")
    if isinstance(system_info, dict):
        target_system_info = overview.setdefault("system_info", {})
        for key in ["sys_name", "sys_descr", "software_version", "snmp_model", "serial_number", "uptime_seconds"]:
            value = system_info.get(key)
            if value is not None and value != "":
                target_system_info[key] = value
                sources["system_info"][key] = "snmp"

    overview["collected_at"] = datetime.now(timezone.utc).isoformat()
    _set_monitor_cache("overview", device.id, overview)

def _monitor_cache_key(kind: str, device_id: int, suffix: str = "") -> str:
    return f"monitor:cache:{kind}:{device_id}{suffix}"


def _asternos_lock_key(device_id: int) -> str:
    return f"asternos_collect:lock:{device_id}"


def _interface_realtime_lock_key(kind: str = "asternos") -> str:
    return f"interface_realtime_collect:{kind}:lock"


def _bucket_cursor_key(kind: str) -> str:
    return f"collect:bucket_cursor:{kind}"


def _icmp_reachability_lock_key() -> str:
    return "device_reachability_collect:lock"


def _next_round_robin_bucket(kind: str, bucket_count: int) -> int:
    """Return the next bucket using Redis state instead of wall-clock modulo.

    Celery Beat can run with a stable phase (for example always around :15/:45).
    If bucket selection is based on ``int(time.time() // interval) % N``, some
    buckets may be skipped for a long time when task runtime and beat phase line
    up badly. A Redis cursor guarantees every scheduler execution advances to
    the next bucket.
    """
    if bucket_count <= 1:
        return 0
    try:
        return (int(redis_client.incr(_bucket_cursor_key(kind))) - 1) % bucket_count
    except Exception as exc:
        logger.warning("采集分桶游标更新失败，回退到时间分桶", kind=kind, error=str(exc))
        return int(time.time() // SNMP_SCHEDULER_INTERVAL_SECONDS) % bucket_count


def _try_lock_asternos_device(device_id: int) -> bool:
    return bool(redis_client.set(_asternos_lock_key(device_id), "1", ex=ASTERNOS_TASK_LOCK_TTL_SECONDS, nx=True))


def _release_asternos_device_lock(device_id: int) -> None:
    redis_client.delete(_asternos_lock_key(device_id))


def _try_lock_interface_realtime(kind: str = "asternos") -> Optional[str]:
    token = uuid.uuid4().hex
    locked = redis_client.set(
        _interface_realtime_lock_key(kind),
        token,
        ex=INTERFACE_REALTIME_LOCK_TTL_SECONDS,
        nx=True,
    )
    return token if locked else None


def _release_interface_realtime_lock(kind: str = "asternos", token: Optional[str] = None) -> None:
    key = _interface_realtime_lock_key(kind)
    current = redis_client.get(key)
    if isinstance(current, bytes):
        current = current.decode("utf-8", errors="ignore")
    if token and current != token:
        return
    redis_client.delete(key)


def _try_lock_icmp_reachability() -> Optional[str]:
    token = uuid.uuid4().hex
    locked = redis_client.set(
        _icmp_reachability_lock_key(),
        token,
        ex=ICMP_REACHABILITY_LOCK_TTL_SECONDS,
        nx=True,
    )
    return token if locked else None


def _release_icmp_reachability_lock(token: Optional[str] = None) -> None:
    key = _icmp_reachability_lock_key()
    current = redis_client.get(key)
    if isinstance(current, bytes):
        current = current.decode("utf-8", errors="ignore")
    if token and current != token:
        return
    redis_client.delete(key)


def _monitor_cache_ttl_seconds(kind: str) -> int:
    if kind in {"overview", "interfaces", "protocol_neighbors"}:
        return MONITOR_CACHE_TTL_SECONDS
    return 180


def _set_monitor_cache(kind: str, device_id: int, payload: Any, suffix: str = "") -> None:
    redis_client.setex(
        _monitor_cache_key(kind, device_id, suffix),
        _monitor_cache_ttl_seconds(kind),
        json.dumps(payload, ensure_ascii=False, default=str),
    )


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _normalize_percent(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value * 100, 1) if 0 <= value <= 1 else round(value, 1)


def _max_metric_value(rows: List[Dict[str, Any]]) -> Optional[float]:
    values = [_safe_float(row.get("value")) for row in rows]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _label_text(row: Dict[str, Any], keys: List[str]) -> str:
    labels = row.get("metric") or {}
    normalized_labels = {str(key).lower(): value for key, value in labels.items()}
    for key in keys:
        value = normalized_labels.get(key.lower())
        if value is not None:
            return str(value).lower()
    return ""


def _summarize_exporter_protocol(rows: List[Dict[str, Any]], up_keywords: List[str]) -> Dict[str, int]:
    total = len(rows)
    up = 0
    for row in rows:
        text = _label_text(row, ["status", "state", "state_text", "session_state", "oper_state"])
        value = _safe_float(row.get("value"))
        if any(keyword in text for keyword in up_keywords) or (value is not None and value > 0):
            up += 1
    return {"total": total, "up": up, "down": max(total - up, 0)}


def _state_is_up(protocol: str, state_text: str, value: Optional[float]) -> bool:
    text = (state_text or "").lower()
    if protocol == "bgp":
        return "established" in text or (value is not None and value >= 1)
    if protocol == "ospf":
        return "full" in text or (value is not None and value >= 1)
    return value is not None and value >= 1


def _parse_duration_text(text: str | None) -> Optional[int]:
    if not text:
        return None
    total = 0
    for value, unit in re.findall(r"(\d+)\s*(w|d|h|m|s)", str(text), flags=re.IGNORECASE):
        amount = int(value)
        unit = unit.lower()
        if unit == "w":
            total += amount * 7 * 24 * 3600
        elif unit == "d":
            total += amount * 24 * 3600
        elif unit == "h":
            total += amount * 3600
        elif unit == "m":
            total += amount * 60
        elif unit == "s":
            total += amount
    return total or None


def _exporter_uptime_map(metrics: Dict[str, List[Dict[str, Any]]], base_names: List[str]) -> Dict[str, float]:
    mapping: Dict[str, float] = {}
    for base_name in base_names:
        for row in asternos_exporter_client._rows(metrics, base_name):
            labels = row.get("metric") or {}
            peer = labels.get("peer") or labels.get("neighbor") or labels.get("Neighbor")
            value = _safe_float(row.get("value"))
            if peer and value is not None:
                mapping[str(peer)] = value
    return mapping


def _build_exporter_protocol_neighbors(metrics: Dict[str, List[Dict[str, Any]]]) -> Dict[str, List[Dict[str, Any]]]:
    bgp_uptimes = _exporter_uptime_map(metrics, ["bgp_peer_uptime", "bgp_peer_uptime_seconds"])
    bgp_neighbors = []
    for row in asternos_exporter_client._rows(metrics, "bgp_status"):
        labels = row.get("metric") or {}
        peer = str(labels.get("peer") or labels.get("neighbor") or labels.get("Neighbor") or "")
        state_text = str(labels.get("status") or labels.get("state") or "")
        is_up = _state_is_up("bgp", state_text, _safe_float(row.get("value")))
        bgp_neighbors.append({
            "protocol": "bgp",
            "peer": peer,
            "neighbor": labels.get("neighbor") or labels.get("Neighbor") or peer,
            "remote_as": labels.get("remote_as"),
            "interface": labels.get("interface") or labels.get("Interface"),
            "state": state_text or "-",
            "status": "up" if is_up else "down",
            "duration_seconds": _safe_float(bgp_uptimes.get(peer)),
            "duration_text": None,
            "source": "exporter",
        })

    ospf_neighbors = []
    for row in asternos_exporter_client._rows(metrics, "ospf_status"):
        labels = row.get("metric") or {}
        peer = str(labels.get("Neighbor") or labels.get("neighbor") or labels.get("peer") or labels.get("Address") or "")
        state_text = str(labels.get("State") or labels.get("state") or "")
        is_up = _state_is_up("ospf", state_text, _safe_float(row.get("value")))
        uptime_text = labels.get("Uptime") or labels.get("uptime")
        ospf_neighbors.append({
            "protocol": "ospf",
            "peer": peer,
            "neighbor": peer,
            "remote_as": None,
            "interface": labels.get("Interface") or labels.get("interface"),
            "state": state_text or "-",
            "status": "up" if is_up else "down",
            "duration_seconds": _parse_duration_text(uptime_text),
            "duration_text": uptime_text,
            "source": "exporter",
        })

    return {"bgp": bgp_neighbors, "ospf": ospf_neighbors}


def _counter_cache_key(device_id: int, metric_base: str, target_key: str) -> str:
    return f"monitor:asternos_counter:{device_id}:{metric_base}:{target_key}"


def _sanitize_interface_rates(stats: Dict[str, Any]) -> None:
    speed_bps = stats.get("speed_bps")
    try:
        speed_value = float(speed_bps)
    except (TypeError, ValueError):
        return
    if speed_value <= 0:
        return
    for bps_key, utilization_key in [
        ("in_bps", "in_utilization_percent"),
        ("out_bps", "out_utilization_percent"),
    ]:
        value = stats.get(bps_key)
        try:
            numeric = float(value)
        except (TypeError, ValueError):
            continue
        if numeric < 0:
            stats[bps_key] = None
            stats[utilization_key] = None
        elif numeric > speed_value * INTERFACE_RATE_CAP_MULTIPLIER:
            # Never turn an invalid counter delta into a believable line-rate
            # spike. Missing data is safer than a fabricated 10G/100G point.
            stats[bps_key] = None
            stats[utilization_key] = None


def _build_counter_target_key(labels: Dict[str, Any], target_labels: List[str]) -> str:
    return "|".join(f"{key}={labels.get(key, '')}" for key in target_labels)


def _get_asternos_counter_deltas(device_id: int, metrics: Dict[str, List[Dict[str, Any]]], interface_name: str) -> Dict[str, Any]:
    counters: List[Dict[str, Any]] = []
    totals: Dict[str, float] = {}

    for config in ASTERNOS_COUNTER_METRICS:
        metric_base = str(config["metric_base"])
        field = str(config["field"])
        target_labels = list(config["target_labels"])
        match_label = str(config["match_label"])
        total_delta = 0.0
        for row in asternos_exporter_client._rows(metrics, metric_base):
            labels = row.get("metric", {}) or {}
            if str(labels.get(match_label) or "") != str(interface_name):
                continue
            current = row.get("value")
            if current is None:
                continue
            current_value = float(current)
            target_key = _build_counter_target_key(labels, target_labels)
            cache_key = _counter_cache_key(device_id, metric_base, target_key)
            previous_raw = redis_client.get(cache_key)
            previous_value = None
            if previous_raw:
                try:
                    previous_value = float(json.loads(previous_raw).get("value"))
                except Exception:
                    previous_value = None
            delta = None
            if previous_value is not None:
                raw_delta = current_value - previous_value
                delta = raw_delta if raw_delta >= 0 else current_value
                total_delta += delta
            redis_client.setex(
                cache_key,
                86400,
                json.dumps({"value": current_value, "time": datetime.now(timezone.utc).isoformat()}),
            )
            counters.append({
                "field": field,
                "metric_base": metric_base,
                "label": config["label"],
                "target": target_key,
                "labels": labels,
                "current": current_value,
                "previous": previous_value,
                "delta": delta,
            })
        totals[field] = total_delta

    return {"counters": counters, "totals": totals}


def _build_asternos_interfaces(metrics: Dict[str, List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    rows = asternos_exporter_client._rows(metrics, "interface_info")
    interfaces: List[Dict[str, Any]] = []
    for position, row in enumerate(rows, start=1):
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
        interfaces.append({
            "index": index,
            "name": interface_name,
            "description": labels.get("description") or labels.get("alias") or interface_name,
            "alias": labels.get("alias") or None,
            "admin_status": "up" if labels.get("admin_status") == "up" else "down",
            "oper_status": "up" if labels.get("operational_status") == "up" else "down",
            "speed_bps": speed_bps,
        })
    interfaces.sort(key=lambda item: item["index"])
    return interfaces


def _by_base_metric_label(metrics: Dict[str, List[Dict[str, Any]]], base_name: str, label: str, value: str) -> Optional[Dict[str, Any]]:
    return asternos_exporter_client._by_base_metric_label(metrics, base_name, label, value)


def _build_asternos_interface_stats(
    device_id: int,
    metrics: Dict[str, List[Dict[str, Any]]],
    interface: Dict[str, Any],
    include_counters: bool = True,
) -> Dict[str, Any]:
    interface_name = str(interface["name"])
    result: Dict[str, Any] = dict(interface)
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
    for field, metric_base in metric_map.items():
        row = _by_base_metric_label(metrics, metric_base, "device", interface_name)
        if row:
            result[field] = row.get("value")

    for field, metric_base in {
        "rx_power": "dom_optic_rx_power",
        "tx_power": "dom_optic_tx_power",
        "optic_temperature": "dom_optic_tempt",
    }.items():
        row = _by_base_metric_label(metrics, metric_base, "interface", interface_name)
        if row:
            result[field] = row.get("value")

    if include_counters:
        counter_deltas = _get_asternos_counter_deltas(device_id, metrics, interface_name)
        result["asternos_counters"] = counter_deltas["counters"]
        result.update(counter_deltas["totals"])
    return result


def _octet_rate_cache_key(device_id: int, interface_index: int) -> str:
    return f"monitor:interface_octets:{device_id}:{interface_index}"


def _octet_rate_lock_key(device_id: int, interface_index: int) -> str:
    return f"{_octet_rate_cache_key(device_id, interface_index)}:lock"


def _release_octet_rate_lock(lock_key: str, token: str) -> None:
    redis_client.eval(
        "if redis.call('get', KEYS[1]) == ARGV[1] then "
        "return redis.call('del', KEYS[1]) else return 0 end",
        1,
        lock_key,
        token,
    )


def _apply_octet_rates(device_id: int, stats: Dict[str, Any], timestamp: datetime) -> None:
    interface_index = stats.get("index")
    if interface_index is None:
        return

    current_in = stats.get("in_octets")
    current_out = stats.get("out_octets")
    if current_in is None and current_out is None:
        return

    interface_index = int(interface_index)
    cache_key = _octet_rate_cache_key(device_id, interface_index)
    lock_key = _octet_rate_lock_key(device_id, interface_index)
    lock_token = uuid.uuid4().hex
    if not redis_client.set(lock_key, lock_token, ex=10, nx=True):
        # Another collector owns this baseline. Do not calculate from a value
        # that may be replaced underneath us.
        return

    try:
        sample_time = timestamp.replace(tzinfo=timezone.utc)
        sample_time_text = sample_time.isoformat()
        previous_raw = redis_client.get(cache_key)
        previous = None
        if previous_raw:
            try:
                previous = json.loads(previous_raw)
            except Exception:
                previous = None

        next_cache = {
            "in_octets": current_in,
            "out_octets": current_out,
            "time": sample_time_text,
        }

        if previous:
            try:
                previous_time = datetime.fromisoformat(str(previous.get("time")))
            except Exception:
                previous_time = None

            # A slower concurrent collector may finish after a newer sample.
            # Never let that older result replace the current baseline.
            if previous_time and sample_time <= previous_time:
                return

            for octet_key, bps_key, time_key in [
                ("in_octets", "in_bps", "in_time"),
                ("out_octets", "out_bps", "out_time"),
            ]:
                current_value = stats.get(octet_key)
                previous_value = previous.get(octet_key)
                if current_value is None or previous_value is None:
                    continue
                try:
                    field_previous_time = datetime.fromisoformat(str(previous.get(time_key) or previous.get("time")))
                except Exception:
                    field_previous_time = previous_time
                elapsed = (sample_time - field_previous_time).total_seconds() if field_previous_time else 0.0
                delta = float(current_value) - float(previous_value)
                if 0.5 <= elapsed <= 300:
                    if delta > 0:
                        stats[bps_key] = round((delta * 8) / elapsed, 2)
                        stats.setdefault("_octet_rate_fields", []).append(bps_key)
                        next_cache[time_key] = sample_time_text
                    elif delta == 0:
                        stats[bps_key] = 0.0
                        stats.setdefault("_octet_rate_fields", []).append(bps_key)
                        next_cache[time_key] = sample_time_text
                    else:
                        next_cache[time_key] = sample_time_text
                    stats["sample_seconds"] = round(elapsed, 2)

        if "in_time" not in next_cache:
            next_cache["in_time"] = sample_time_text
        if "out_time" not in next_cache:
            next_cache["out_time"] = sample_time_text

        redis_client.setex(cache_key, 86400, json.dumps(next_cache))
    finally:
        _release_octet_rate_lock(lock_key, lock_token)


def _interface_point(device: Device, stats: Dict[str, Any], timestamp: datetime) -> Optional[Dict[str, Any]]:
    interface_index = stats.get("index")
    if interface_index is None:
        return None

    _apply_octet_rates(device.id, stats, timestamp)
    _sanitize_interface_rates(stats)
    preserve_exporter_rates = str(device.monitor_source or "snmp") == "asternos_exporter"

    return {
        "measurement": "interface_monitoring",
        "tags": {
            "device_id": str(device.id),
            "device_name": device.name,
            "interface_index": str(interface_index),
            "interface_name": stats.get("name"),
        },
        "fields": {
            "in_bps": stats.get("in_bps") if (
                "in_bps" in stats.get("_octet_rate_fields", [])
                or stats.get("in_octets") is None
                or (preserve_exporter_rates and stats.get("in_bps") is not None)
            ) else None,
            "out_bps": stats.get("out_bps") if (
                "out_bps" in stats.get("_octet_rate_fields", [])
                or stats.get("out_octets") is None
                or (preserve_exporter_rates and stats.get("out_bps") is not None)
            ) else None,
            "in_octets": stats.get("in_octets"),
            "out_octets": stats.get("out_octets"),
            "in_utilization_percent": stats.get("in_utilization_percent"),
            "out_utilization_percent": stats.get("out_utilization_percent"),
            "in_discards": stats.get("in_discards"),
            "out_discards": stats.get("out_discards"),
            "in_discards_delta": stats.get("in_discards_delta"),
            "out_discards_delta": stats.get("out_discards_delta"),
            "in_errors": stats.get("in_errors"),
            "out_errors": stats.get("out_errors"),
            "in_errors_delta": stats.get("in_errors_delta"),
            "out_errors_delta": stats.get("out_errors_delta"),
            "queue_egress_dropped_pkts_delta": stats.get("queue_egress_dropped_pkts_delta"),
            "queue_ingress_dropped_pkts_delta": stats.get("queue_ingress_dropped_pkts_delta"),
            "pfc_rx_pkts_delta": stats.get("pfc_rx_pkts_delta"),
            "pfc_tx_pkts_delta": stats.get("pfc_tx_pkts_delta"),
            "ecn_marked_pkts_delta": stats.get("ecn_marked_pkts_delta"),
            "buffer_usage": stats.get("buffer_usage"),
            "queue_length": stats.get("queue_length"),
            "speed_bps": stats.get("speed_bps"),
            "sample_seconds": stats.get("sample_seconds"),
            "admin_status": 1.0 if stats.get("admin_status") == "up" else 0.0,
            "oper_status": 1.0 if stats.get("oper_status") == "up" else 0.0,
            "admin_up_oper_down": 1.0 if stats.get("admin_status") == "up" and stats.get("oper_status") != "up" else 0.0,
        },
        "timestamp": timestamp,
    }


def _circuit_port_names_for_device(db, device_id: int) -> set[str]:
    circuits = db.query(Circuit).filter(Circuit.status == "active").all()
    port_names: set[str] = set()
    for circuit in circuits:
        if circuit.primary_device_id == device_id and circuit.primary_port_name:
            port_names.add(str(circuit.primary_port_name))
        if circuit.secondary_device_id == device_id and circuit.secondary_port_name:
            port_names.add(str(circuit.secondary_port_name))
    return port_names


def _asternos_queue_detail_points(device: Device, stats: Dict[str, Any], timestamp: datetime) -> List[Dict[str, Any]]:
    interface_index = stats.get("index")
    interface_name = stats.get("name")
    if interface_index is None or not interface_name:
        return []

    points: List[Dict[str, Any]] = []
    for counter in stats.get("asternos_counters") or []:
        labels = counter.get("labels") or {}
        metric_base = counter.get("metric_base")
        field = counter.get("field")
        target = counter.get("target")
        if not metric_base or not field or not target:
            continue

        queue = labels.get("queue")
        prio = labels.get("prio")
        points.append({
            "measurement": "queue_monitoring",
            "tags": {
                "device_id": str(device.id),
                "device_name": device.name,
                "interface_index": str(interface_index),
                "interface_name": interface_name,
                "metric_base": str(metric_base),
                "field": str(field),
                "target": str(target),
                "queue": str(queue) if queue is not None else None,
                "prio": str(prio) if prio is not None else None,
            },
            "fields": {
                "current": counter.get("current"),
                "previous": counter.get("previous"),
                "delta": counter.get("delta"),
            },
            "timestamp": timestamp,
        })
    return points


def _cache_interface_stats(device_id: int, stats: Dict[str, Any], collected_at: str) -> None:
    interface_index = stats.get("index")
    if interface_index is None:
        return
    _set_monitor_cache("interface_stats", device_id, {
        "interface": stats,
        "collected_at": collected_at,
    }, suffix=f":{interface_index}")


def _device_lock_key(device_id: int) -> str:
    return f"snmp_collect:lock:{device_id}"


def _device_failure_key(device_id: int) -> str:
    return f"snmp_collect:failure:{device_id}"


def _device_status_key(device_id: int) -> str:
    return f"snmp_collect:status:{device_id}"


def _try_lock_device(device_id: int) -> bool:
    return bool(redis_client.set(_device_lock_key(device_id), "1", ex=SNMP_TASK_LOCK_TTL_SECONDS, nx=True))


def _release_device_lock(device_id: int) -> None:
    redis_client.delete(_device_lock_key(device_id))


def _get_device_status(device_id: int) -> str:
    return redis_client.get(_device_status_key(device_id)) or SNMP_STATUS_UNKNOWN


def _write_snmp_reachability(device: Device, reachable: bool, failures: int = 0) -> None:
    if not device or not device.id or not device.ip_address:
        return
    try:
        influx_client.write_point(
            measurement="snmp_reachability",
            tags={
                "device_id": str(device.id),
                "device_name": device.name,
                "device_ip": device.ip_address,
            },
            fields={
                "reachable": 1.0 if reachable else 0.0,
                "failures": float(failures or 0),
            },
            timestamp=datetime.utcnow(),
            sync=False,
        )
    except Exception as exc:
        logger.warning("写入SNMP可达性指标失败", device_id=getattr(device, "id", None), error=str(exc))


def _write_exporter_reachability(device: Optional[Device], reachable: bool, error: Optional[str] = None) -> None:
    if not device or not device.id or not device.ip_address:
        return
    fields: Dict[str, Any] = {"reachable": 1.0 if reachable else 0.0}
    if error:
        fields["error"] = str(error)[:500]
    try:
        influx_client.write_point(
            measurement="exporter_reachability",
            tags={
                "device_id": str(device.id),
                "device_name": device.name,
                "device_ip": device.ip_address,
            },
            fields=fields,
            timestamp=datetime.utcnow(),
            sync=False,
        )
    except Exception as exc:
        logger.warning("写入Exporter可达性指标失败", device_id=getattr(device, "id", None), error=str(exc))


def _mark_device_reachable(device_id: int) -> None:
    redis_client.set(_device_status_key(device_id), SNMP_STATUS_REACHABLE)
    redis_client.delete(_device_failure_key(device_id))


def _record_device_failure(device_id: int) -> int:
    failures = redis_client.incr(_device_failure_key(device_id))
    redis_client.expire(_device_failure_key(device_id), 86400)
    if failures >= SNMP_FAILURE_THRESHOLD:
        redis_client.set(_device_status_key(device_id), SNMP_STATUS_UNREACHABLE)
    elif _get_device_status(device_id) != SNMP_STATUS_REACHABLE:
        redis_client.set(_device_status_key(device_id), SNMP_STATUS_UNKNOWN)
    return failures


def _clear_device_failure_state(device_id: int) -> None:
    _mark_device_reachable(device_id)


def _clear_device_failure_state_for_device(device: Device) -> None:
    _mark_device_reachable(device.id)
    _write_snmp_reachability(device, True, 0)


def _record_device_failure_for_device(device: Device) -> int:
    failures = _record_device_failure(device.id)
    if failures >= SNMP_FAILURE_THRESHOLD:
        _write_snmp_reachability(device, False, failures)
    else:
        _write_snmp_reachability(device, True, failures)
    return failures


def _snmp_is_reachable(device: Device) -> bool:
    return snmp_collector.snmp_get(device, SNMP_VERIFY_OID) is not None


def _collect_icmp_reachability(device: Device) -> Dict[str, Any]:
    command = [
        "ping",
        "-c",
        str(ICMP_PING_PACKETS),
        "-W",
        str(ICMP_PING_TIMEOUT_SECONDS),
        device.ip_address,
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(ICMP_PING_PACKETS * ICMP_PING_TIMEOUT_SECONDS + 3, 8),
            check=False,
        )
    except Exception as exc:
        logger.warning("ICMP探测执行失败", device_id=device.id, ip=device.ip_address, error=str(exc))
        return {
            "reachable": 0.0,
            "sent_packets": float(ICMP_PING_PACKETS),
            "success_packets": 0.0,
            "packet_loss_percent": 100.0,
            "avg_latency_ms": None,
        }

    output = f"{result.stdout}\n{result.stderr}".strip()
    transmitted = float(ICMP_PING_PACKETS)
    received = 0.0
    loss_percent = 100.0
    avg_latency_ms = None

    match = re.search(r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received", output)
    if match:
        transmitted = float(match.group(1))
        received = float(match.group(2))

    loss_match = re.search(r"(\d+(?:\.\d+)?)%\s+packet loss", output)
    if loss_match:
        loss_percent = float(loss_match.group(1))
    elif transmitted > 0:
        loss_percent = round(((transmitted - received) / transmitted) * 100, 2)

    rtt_match = re.search(r"=\s*([\d.]+)/([\d.]+)/([\d.]+)/", output)
    if rtt_match:
        avg_latency_ms = float(rtt_match.group(2))

    return {
        "reachable": 1.0 if received > 0 and loss_percent < 100 else 0.0,
        "sent_packets": transmitted,
        "success_packets": received,
        "packet_loss_percent": loss_percent,
        "avg_latency_ms": avg_latency_ms,
    }


def _collect_icmp_reachability_batch(devices: List[Device]) -> Dict[int, Dict[str, Any]]:
    targets = [device for device in devices if device.ip_address]
    if not targets:
        return {}

    default_result = {
        "reachable": 0.0,
        "sent_packets": float(ICMP_PING_PACKETS),
        "success_packets": 0.0,
        "packet_loss_percent": 100.0,
        "avg_latency_ms": None,
    }
    metrics_by_device: Dict[int, Dict[str, Any]] = {
        device.id: default_result.copy() for device in targets
    }
    ip_to_device = {device.ip_address: device for device in targets}

    command = [
        "fping",
        "-C",
        str(ICMP_PING_PACKETS),
        "-q",
        "-p",
        str(ICMP_PING_INTERVAL_MS),
        "-t",
        str(ICMP_PING_TIMEOUT_SECONDS * 1000),
        *ip_to_device.keys(),
    ]

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            timeout=max(len(targets), 1) * ICMP_PING_TIMEOUT_SECONDS + 10,
            check=False,
        )
    except FileNotFoundError:
        logger.warning("fping不可用，回退到单设备ping探测")
        return {device.id: _collect_icmp_reachability(device) for device in targets}
    except Exception as exc:
        logger.warning("批量ICMP探测执行失败，回退到单设备ping探测", error=str(exc))
        return {device.id: _collect_icmp_reachability(device) for device in targets}

    output = result.stderr or result.stdout or ""
    for raw_line in output.splitlines():
        line = raw_line.strip()
        if not line or ":" not in line:
            continue
        ip_address, samples_text = line.split(":", 1)
        ip_address = ip_address.strip()
        device = ip_to_device.get(ip_address)
        if not device:
            continue

        sample_tokens = [token for token in samples_text.strip().split() if token]
        latencies: List[float] = []
        for token in sample_tokens:
            if token == "-":
                continue
            try:
                latencies.append(float(token))
            except ValueError:
                continue

        received = float(len(latencies))
        transmitted = float(ICMP_PING_PACKETS)
        loss_percent = 100.0 if transmitted <= 0 else round(((transmitted - received) / transmitted) * 100, 2)
        avg_latency_ms = round(sum(latencies) / len(latencies), 3) if latencies else None

        metrics_by_device[device.id] = {
            "reachable": 1.0 if received > 0 and loss_percent < 100 else 0.0,
            "sent_packets": transmitted,
            "success_packets": received,
            "packet_loss_percent": loss_percent,
            "avg_latency_ms": avg_latency_ms,
        }

    return metrics_by_device


@shared_task(bind=True)
def collect_snmp_for_device(self, device_id: int):
    """
    采集单个设备的SNMP数据
    
    Args:
        device_id: 设备ID
    """
    db = SessionLocal()
    device: Optional[Device] = None
    try:
        if not redis_client.exists(_device_lock_key(device_id)):
            _try_lock_device(device_id)

        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            logger.warning("设备不存在", device_id=device_id)
            return {"error": "设备不存在"}

        if not device.is_monitored or device.status not in {"active", "online"}:
            logger.debug("设备未加入监控或未上线，跳过采集", device_id=device_id)
            return {"skipped": True, "reason": "未加入监控或未上线"}
        if (device.monitor_source or "snmp") != "snmp":
            logger.debug("设备监控方式非SNMP，跳过SNMP采集", device_id=device_id)
            return {"skipped": True, "reason": "监控方式非SNMP"}
        if not device.snmp_version:
            logger.debug("设备未配置SNMP", device_id=device_id)
            return {"skipped": True, "reason": "未配置SNMP"}
        
        logger.debug("开始SNMP采集", device_id=device_id, ip=device.ip_address)
        
        result: Dict[str, Any] = {}
        gap_fill_result: Dict[str, Any] = {}
        protocol_status_result: Dict[str, Any] = {}
        optical_result: Dict[str, Any] = {}
        telemetry_primary = _telemetry_primary_enabled(device) or _telemetry_snmp_disabled(device)

        if telemetry_primary:
            logger.info(
                "设备已启用Telemetry主采集，跳过SNMP资源/接口全量采集，仅保留缺口补采",
                device_id=device_id,
                ip=device.ip_address,
            )
            try:
                gap_fill_result = snmp_collector.collect_overview_gap_fill(device)
            except Exception as exc:
                logger.error("Telemetry设备SNMP缺口补采失败", device_id=device_id, error=str(exc))
        else:
            try:
                result = snmp_collector.collect_device(device)
            except Exception as exc:
                logger.error("设备SNMP指标采集失败", device_id=device_id, error=str(exc))

        if (not telemetry_primary) or _telemetry_snmp_protocol_fallback_enabled(device):
            try:
                protocol_status_result = snmp_collector.collect_protocol_status(device)
            except Exception as exc:
                logger.error("协议状态采集失败", device_id=device_id, error=str(exc))

        if (not telemetry_primary) or _telemetry_snmp_optical_fallback_enabled(device):
            try:
                optical_result = snmp_collector.collect_optical_monitoring(device)
            except Exception as exc:
                logger.error("光模块指标采集失败", device_id=device_id, error=str(exc))

        logger.info(
            "SNMP采集完成",
            device_id=device_id,
            telemetry_primary=telemetry_primary,
            points=result.get("points_written", 0),
            gap_fill_points=gap_fill_result.get("points_written", 0),
            interface_points=0,
            protocol_points=protocol_status_result.get("points_written", 0),
            optical_points=optical_result.get("points_written", 0),
        )

        total_points_written = (
            int(result.get("points_written") or 0)
            + int(gap_fill_result.get("points_written") or 0)
            + int(protocol_status_result.get("points_written") or 0)
            + int(optical_result.get("points_written") or 0)
        )
        if total_points_written == 0 and not _snmp_is_reachable(device):
            failures = _record_device_failure_for_device(device)
            logger.warning(
                "SNMP采集无数据且连通性验证失败",
                device_id=device_id,
                ip=device.ip_address,
                failures=failures,
                status=_get_device_status(device_id),
            )
            return {
                "device_id": device_id,
                "success": False,
                "error": "SNMP无响应或无可采集数据",
                "failures": failures,
                "status": _get_device_status(device_id),
                "points_written": 0,
                "interface_points_written": 0,
                "interfaces_monitored": 0,
                "protocol_points_written": 0,
                "optical_points_written": 0,
            }

        _clear_device_failure_state_for_device(device)

        if telemetry_primary:
            _merge_snmp_gap_fill_into_overview_cache(device, gap_fill_result)
            _merge_protocols_into_overview_cache(device, protocol_status_result.get("protocols") or {})
            return {
                "device_id": device_id,
                "success": True,
                "telemetry_primary": True,
                "points_written": gap_fill_result.get("points_written", 0),
                "gap_fill_points_written": gap_fill_result.get("points_written", 0),
                "hardware_count": gap_fill_result.get("hardware_count", 0),
                "interface_points_written": 0,
                "interfaces_monitored": 0,
                "protocol_points_written": protocol_status_result.get("points_written", 0),
                "optical_points_written": optical_result.get("points_written", 0),
                "snmp_mode": "fallback_only",
            }

        memory = result.get("memory") or {}
        sessions = result.get("sessions") or {}
        overview = {
            "connectivity": {
                "type": "snmp",
                "status": "reachable",
                "message": f"SNMP {device.snmp_version}",
            },
            "resources": {
                "cpu_percent": result.get("cpu"),
                "memory_percent": memory.get("usage_percent") or memory.get("used_percent") or memory.get("percent"),
                "temperature": result.get("temperature"),
                "storage_percent": result.get("storage_percent"),
            },
            "sessions": {
                "current": sessions.get("current"),
                "total": sessions.get("total"),
                "usage_percent": sessions.get("usage_percent"),
            },
            "hardware": result.get("hardware") or {
                "fan_total": 0,
                "fan_down": 0,
                "fan_status_known": True,
                "power_total": 0,
                "power_down": 0,
                "power_status_known": True,
            },
            "protocols": protocol_status_result.get("protocols") or {
                "bgp": {"total": 0, "up": 0, "down": 0},
                "ospf": {"total": 0, "up": 0, "down": 0},
            },
            "system_info": result.get("system_info") or {
                "sys_name": None,
                "sys_descr": None,
                "software_version": None,
                "snmp_model": None,
                "serial_number": None,
                "uptime_seconds": result.get("uptime"),
            },
            "collected_at": datetime.now(timezone.utc).isoformat(),
        }
        _set_monitor_cache("overview", device.id, overview)
        
        return {
            "device_id": device_id,
            "success": True,
            "cpu": result.get("cpu"),
            "memory": result.get("memory"),
            "interfaces": result.get("interfaces_count"),
            "points_written": result.get("points_written"),
            "interface_points_written": 0,
            "interfaces_monitored": 0,
            "protocol_points_written": protocol_status_result.get("points_written", 0),
            "optical_points_written": optical_result.get("points_written", 0),
        }
        
    except Exception as exc:
        logger.error("SNMP采集失败", device_id=device_id, error=str(exc))
        if device is None:
            try:
                device = db.query(Device).filter(Device.id == device_id).first()
            except Exception:
                device = None
        failures = _record_device_failure_for_device(device) if device else _record_device_failure(device_id)
        return {
            "device_id": device_id,
            "success": False,
            "error": str(exc),
            "failures": failures,
            "status": _get_device_status(device_id),
        }
    finally:
        _release_device_lock(device_id)
        db.close()


@shared_task
def collect_all_snmp():
    """
    分批采集所有 SNMP 设备。
    Beat 仍按 10 秒醒一次，但每次只调度一个稳定分桶，避免 128 口设备规模扩大后集中 walk。
    """
    db = SessionLocal()
    try:
        # 获取所有启用了SNMP的设备
        devices = db.query(Device).filter(
            Device.snmp_version.isnot(None),
            Device.status.in_(["active", "online"]),
            Device.is_monitored == True,
            or_(Device.monitor_source == "snmp", Device.monitor_source.is_(None)),
        ).order_by(Device.id.asc()).all()
        telemetry_primary_total = sum(1 for device in devices if _telemetry_primary_enabled(device) or _telemetry_snmp_disabled(device))

        current_bucket = _next_round_robin_bucket("snmp_full", SNMP_BATCH_COUNT)
        devices_in_bucket = [
            device for device in devices
            if int(device.id or 0) % SNMP_BATCH_COUNT == current_bucket
        ]
        if len(devices_in_bucket) > SNMP_MAX_DEVICES_PER_TICK:
            devices_in_bucket = devices_in_bucket[:SNMP_MAX_DEVICES_PER_TICK]

        logger.info(
            "开始批量SNMP采集",
            total_devices=len(devices),
            telemetry_primary_fallback_total=telemetry_primary_total,
            bucket=current_bucket,
            bucket_count=SNMP_BATCH_COUNT,
            devices_in_bucket=len(devices_in_bucket),
            target_interval_seconds=SNMP_FULL_COLLECTION_INTERVAL_SECONDS,
        )
        
        # 为每个设备创建采集任务
        task_ids = []
        skipped_locked = 0
        skipped_unreachable = 0
        for device in devices_in_bucket:
            if _get_device_status(device.id) == SNMP_STATUS_UNREACHABLE:
                skipped_unreachable += 1
                continue
            if not _try_lock_device(device.id):
                skipped_locked += 1
                continue
            task = collect_snmp_for_device.delay(device.id)
            task_ids.append({
                "device_id": device.id,
                "task_id": task.id
            })
        
        return {
            "total_devices": len(devices),
            "telemetry_primary_fallback_total": telemetry_primary_total,
            "bucket": current_bucket,
            "bucket_count": SNMP_BATCH_COUNT,
            "devices_in_bucket": len(devices_in_bucket),
            "target_interval_seconds": SNMP_FULL_COLLECTION_INTERVAL_SECONDS,
            "tasks_created": len(task_ids),
            "skipped_locked": skipped_locked,
            "skipped_unreachable": skipped_unreachable,
        }
        
    except Exception as e:
        logger.error("批量SNMP采集失败", error=str(e))
        return {"error": str(e)}
    finally:
        db.close()


@shared_task
def collect_all_snmp_interface_realtime():
    """高频采集所有受监控 SNMP 设备的接口流量历史，避免端口图被慢速全量轮询拖慢。"""
    lock_token = _try_lock_interface_realtime("snmp")
    if not lock_token:
        return {"skipped": True, "reason": "上一轮SNMP端口高频采集未完成"}

    db = SessionLocal()
    try:
        devices = db.query(Device).filter(
            Device.snmp_version.isnot(None),
            Device.status.in_(["active", "online"]),
            Device.is_monitored == True,
            or_(Device.monitor_source == "snmp", Device.monitor_source.is_(None)),
        ).order_by(Device.id.asc()).all()
        telemetry_interface_skipped_total = sum(1 for device in devices if _telemetry_interface_enabled(device))
        devices = [device for device in devices if not _telemetry_interface_enabled(device)]

        current_bucket = _next_round_robin_bucket("snmp_interface_realtime", SNMP_INTERFACE_BATCH_COUNT)
        devices_in_bucket = [
            device for device in devices
            if int(device.id or 0) % SNMP_INTERFACE_BATCH_COUNT == current_bucket
        ]
        if len(devices_in_bucket) > SNMP_MAX_DEVICES_PER_TICK:
            devices_in_bucket = devices_in_bucket[:SNMP_MAX_DEVICES_PER_TICK]

        collection_started = time.monotonic()
        results: List[Dict[str, Any]] = []
        skipped_unreachable = 0
        workers = min(INTERFACE_REALTIME_MAX_WORKERS, max(1, len(devices_in_bucket)))

        def collect_device_interfaces(device: Device) -> Dict[str, Any]:
            if _get_device_status(device.id) == SNMP_STATUS_UNREACHABLE:
                return {"device_id": device.id, "skipped": "unreachable"}
            if not _try_lock_device(device.id):
                return {"device_id": device.id, "skipped": "locked"}
            try:
                return snmp_collector.collect_interface_monitoring(
                    device,
                    suppress_rate_interface_names=_circuit_port_names_for_device(db, device.id),
                    realtime=True,
                )
            finally:
                _release_device_lock(device.id)

        if devices_in_bucket:
            with ThreadPoolExecutor(max_workers=workers) as executor:
                futures = {executor.submit(collect_device_interfaces, device): device for device in devices_in_bucket}
                for future in as_completed(futures):
                    device = futures[future]
                    try:
                        result = future.result()
                    except Exception as exc:
                        logger.warning(
                            "SNMP端口高频采集设备失败",
                            device_id=device.id,
                            ip_address=device.ip_address,
                            error=str(exc),
                        )
                        continue
                    if result.get("skipped") == "unreachable":
                        skipped_unreachable += 1
                    results.append(result)

        points_written = sum(int(item.get("points_written") or 0) for item in results)
        interfaces_monitored = sum(int(item.get("interfaces_monitored") or 0) for item in results)
        logger.info(
            "SNMP端口高频采集完成",
            total_devices=len(devices),
            telemetry_interface_skipped_total=telemetry_interface_skipped_total,
            bucket=current_bucket,
            bucket_count=SNMP_INTERFACE_BATCH_COUNT,
            devices_in_bucket=len(devices_in_bucket),
            target_interval_seconds=SNMP_INTERFACE_REALTIME_INTERVAL_SECONDS,
            devices_collected=len(results),
            interfaces_monitored=interfaces_monitored,
            points_written=points_written,
            skipped_unreachable=skipped_unreachable,
            elapsed_seconds=round(time.monotonic() - collection_started, 3),
            workers=workers,
        )
        return {
            "total_devices": len(devices),
            "telemetry_interface_skipped_total": telemetry_interface_skipped_total,
            "bucket": current_bucket,
            "bucket_count": SNMP_INTERFACE_BATCH_COUNT,
            "devices_in_bucket": len(devices_in_bucket),
            "target_interval_seconds": SNMP_INTERFACE_REALTIME_INTERVAL_SECONDS,
            "devices_collected": len(results),
            "interfaces_monitored": interfaces_monitored,
            "points_written": points_written,
            "skipped_unreachable": skipped_unreachable,
            "elapsed_seconds": round(time.monotonic() - collection_started, 3),
        }
    except Exception as exc:
        logger.error("SNMP端口高频采集失败", error=str(exc))
        return {"error": str(exc)}
    finally:
        _release_interface_realtime_lock("snmp", lock_token)
        db.close()


@shared_task(bind=True)
def collect_asternos_for_device(self, device_id: int):
    """采集单台 AsterNOS Exporter 设备，并缓存总览/端口/邻居快照。"""
    db = SessionLocal()
    device: Optional[Device] = None
    try:
        if not redis_client.exists(_asternos_lock_key(device_id)):
            _try_lock_asternos_device(device_id)

        device = db.query(Device).filter(Device.id == device_id).first()
        if not device:
            return {"error": "设备不存在"}
        if not device.is_monitored or device.status not in {"active", "online"}:
            return {"skipped": True, "reason": "未加入监控或未上线"}

        metrics = asyncio.run(asternos_exporter_client.scrape(device))
        interfaces = _build_asternos_interfaces(metrics)
        now = datetime.utcnow()
        collected_at = datetime.now(timezone.utc).isoformat()
        points = []

        for interface in interfaces:
            stats = _build_asternos_interface_stats(device.id, metrics, interface)
            _cache_interface_stats(device.id, stats, collected_at)
            point = _interface_point(device, stats, now)
            if point:
                points.append(point)
            points.extend(_asternos_queue_detail_points(device, stats, now))

        if points:
            influx_client.write_points(points, sync=False)

        neighbors = _build_exporter_protocol_neighbors(metrics)
        overview = {
            "connectivity": {
                "type": "exporter",
                "status": "reachable",
                "message": f"http://{device.ip_address}:8101/metrics",
            },
            "resources": {
                "cpu_percent": _normalize_percent(_safe_float(asternos_exporter_client._first(metrics, "device_cpu_usage"))),
                "memory_percent": _normalize_percent(_safe_float(asternos_exporter_client._first(metrics, "device_memory_usage"))),
                "temperature": _max_metric_value(asternos_exporter_client._rows(metrics, "device_sensor_tempt")),
                "storage_percent": None,
            },
            "sessions": {"current": None, "total": None, "usage_percent": None},
            "hardware": {"fan_total": 0, "fan_down": 0, "power_total": 0, "power_down": 0},
            "protocols": {
                "bgp": _summarize_exporter_protocol(
                    asternos_exporter_client._rows(metrics, "bgp_status"),
                    ["established", "up"],
                ),
                "ospf": _summarize_exporter_protocol(
                    asternos_exporter_client._rows(metrics, "ospf_status"),
                    ["full", "established", "up"],
                ),
            },
            "system_info": asternos_exporter_client.system_info(metrics),
            "collected_at": collected_at,
        }
        _set_monitor_cache("interfaces", device.id, {"interfaces": interfaces, "collected_at": collected_at})
        _set_monitor_cache("overview", device.id, overview)
        _set_monitor_cache("protocol_neighbors", device.id, {"neighbors": neighbors, "collected_at": collected_at})
        redis_client.set(f"asternos_collect:status:{device.id}", "reachable")
        _write_exporter_reachability(device, True)

        return {
            "device_id": device.id,
            "success": True,
            "interfaces": len(interfaces),
            "points_written": len(points),
        }
    except Exception as exc:
        logger.error("AsterNOS Exporter采集失败", device_id=device_id, error=str(exc))
        redis_client.set(f"asternos_collect:status:{device_id}", "unreachable")
        if device is None:
            try:
                device = db.query(Device).filter(Device.id == device_id).first()
            except Exception:
                device = None
        _write_exporter_reachability(device, False, str(exc))
        cache_key = _monitor_cache_key("overview", device_id)
        cached_raw = redis_client.get(cache_key)
        cached_overview = None
        if cached_raw:
            try:
                cached_overview = json.loads(cached_raw)
            except Exception:
                cached_overview = None
        if isinstance(cached_overview, dict):
            cached_overview["connectivity"] = {
                "type": "exporter",
                "status": "unreachable",
                "message": f"最近一次采集失败，当前展示上一次有效指标：{exc}",
            }
            _set_monitor_cache("overview", device_id, cached_overview)
        else:
            _set_monitor_cache("overview", device_id, {
                "connectivity": {"type": "exporter", "status": "unreachable", "message": str(exc)},
                "resources": {"cpu_percent": None, "memory_percent": None, "temperature": None, "storage_percent": None},
                "sessions": {"current": None, "total": None, "usage_percent": None},
                "hardware": {"fan_total": 0, "fan_down": 0, "power_total": 0, "power_down": 0},
                "protocols": {"bgp": {"total": 0, "up": 0, "down": 0}, "ospf": {"total": 0, "up": 0, "down": 0}},
                "system_info": {"sys_name": None, "sys_descr": None, "software_version": None, "snmp_model": None, "serial_number": None, "uptime_seconds": None},
                "collected_at": datetime.now(timezone.utc).isoformat(),
            })
        return {"device_id": device_id, "success": False, "error": str(exc)}
    finally:
        _release_asternos_device_lock(device_id)
        db.close()


@shared_task
def collect_all_asternos_exporter():
    """周期采集所有 AsterNOS Exporter 直连设备。"""
    db = SessionLocal()
    try:
        all_devices = db.query(Device).filter(
            Device.status.in_(["active", "online"]),
            Device.is_monitored == True,
        ).order_by(Device.id.asc()).all()
        devices = []
        for device in all_devices:
            vendor = str(device.vendor or "").lower()
            monitor_source = str(device.monitor_source or "")
            if (
                monitor_source == "asternos_exporter"
                or any(marker in vendor for marker in ["asternos", "asterfusion", "asteros", "星融元"])
            ):
                devices.append(device)

        current_bucket = _next_round_robin_bucket("asternos_full", ASTERNOS_BATCH_COUNT)
        devices_in_bucket = [
            device for device in devices
            if int(device.id or 0) % ASTERNOS_BATCH_COUNT == current_bucket
        ]
        if len(devices_in_bucket) > ASTERNOS_MAX_DEVICES_PER_TICK:
            devices_in_bucket = devices_in_bucket[:ASTERNOS_MAX_DEVICES_PER_TICK]

        scheduled = 0
        skipped_locked = 0
        for device in devices_in_bucket:
            if not _try_lock_asternos_device(device.id):
                skipped_locked += 1
                continue
            collect_asternos_for_device.delay(device.id)
            scheduled += 1
        return {
            "total_devices": len(devices),
            "bucket": current_bucket,
            "bucket_count": ASTERNOS_BATCH_COUNT,
            "devices_in_bucket": len(devices_in_bucket),
            "target_interval_seconds": ASTERNOS_FULL_COLLECTION_INTERVAL_SECONDS,
            "scheduled": scheduled,
            "skipped_locked": skipped_locked,
        }
    except Exception as exc:
        logger.error("批量AsterNOS Exporter采集失败", error=str(exc))
        return {"error": str(exc)}
    finally:
        db.close()


@shared_task
def collect_all_asternos_interface_realtime():
    """高频采集所有 AsterNOS 监控设备的端口基础指标，用于端口流量连续曲线。"""
    if not _try_lock_interface_realtime("asternos"):
        return {"skipped": True, "reason": "上一轮AsterNOS端口高频采集未完成"}

    db = SessionLocal()
    try:
        all_devices = db.query(Device).filter(
            Device.status.in_(["active", "online"]),
            Device.is_monitored == True,
        ).all()
        devices = []
        for device in all_devices:
            vendor = str(device.vendor or "").lower()
            monitor_source = str(device.monitor_source or "")
            if (
                monitor_source == "asternos_exporter"
                or any(marker in vendor for marker in ["asternos", "asterfusion", "asteros", "星融元"])
            ):
                devices.append(device)

        now = datetime.utcnow()
        collected_at = datetime.now(timezone.utc).isoformat()
        points: List[Dict[str, Any]] = []
        device_count = 0
        interface_count = 0

        async def scrape_device(device: Device):
            try:
                return device, await asternos_exporter_client.scrape(device), None
            except Exception as exc:
                return device, None, exc

        async def scrape_devices():
            return await asyncio.gather(*(scrape_device(device) for device in devices))

        scrape_results = asyncio.run(scrape_devices()) if devices else []
        for device, metrics, error in scrape_results:
            if error:
                logger.warning("AsterNOS端口高频采集失败", device_id=device.id, ip=device.ip_address, error=str(error))
                continue
            try:
                interfaces = _build_asternos_interfaces(metrics)
                _set_monitor_cache("interfaces", device.id, {"interfaces": interfaces, "collected_at": collected_at})
                device_count += 1

                for interface in interfaces:
                    stats = _build_asternos_interface_stats(device.id, metrics, interface, include_counters=False)
                    point = _interface_point(device, stats, now)
                    if not point:
                        continue
                    points.append(point)
                    interface_count += 1
                    _cache_interface_stats(device.id, stats, collected_at)
            except Exception as exc:
                logger.warning("AsterNOS端口高频采集失败", device_id=device.id, ip=device.ip_address, error=str(exc))

        if points:
            influx_client.write_points(points, sync=False)

        logger.info(
            "AsterNOS端口高频采集完成",
            devices=device_count,
            interfaces=interface_count,
            points_written=len(points),
        )
        return {"devices": device_count, "interfaces": interface_count, "points_written": len(points)}
    except Exception as exc:
        logger.error("AsterNOS端口高频采集批量失败", error=str(exc))
        return {"error": str(exc)}
    finally:
        _release_interface_realtime_lock("asternos")
        db.close()


@shared_task
def collect_circuit_interface_realtime():
    """轻量采集线路绑定端口，避免全设备扫描较慢造成端口历史断点。"""
    lock_token = _try_lock_interface_realtime("circuit")
    if not lock_token:
        return {"skipped": True, "reason": "上一轮线路端口采集未完成"}

    db = SessionLocal()
    try:
        circuits = db.query(Circuit).filter(Circuit.status == "active").all()
        target_map: Dict[int, set[str]] = {}
        for circuit in circuits:
            if circuit.primary_device_id and circuit.primary_port_name:
                target_map.setdefault(circuit.primary_device_id, set()).add(str(circuit.primary_port_name))
            if circuit.access_mode == "dual" and circuit.secondary_device_id and circuit.secondary_port_name:
                target_map.setdefault(circuit.secondary_device_id, set()).add(str(circuit.secondary_port_name))

        if not target_map:
            return {"devices": 0, "points_written": 0}

        devices = db.query(Device).filter(
            Device.id.in_(target_map.keys()),
            Device.status.in_(["active", "online"]),
            Device.is_monitored == True,
        ).all()
        now = datetime.utcnow()
        collected_at = datetime.now(timezone.utc).isoformat()
        collection_started = time.monotonic()

        def collect_device_ports(device: Device) -> Dict[str, Any]:
            monitor_source = str(device.monitor_source or "snmp")
            port_names = target_map.get(device.id, set())
            device_points: List[Dict[str, Any]] = []
            matched = 0
            if not port_names:
                return {"points": device_points, "matched_ports": matched}

            if monitor_source == "asternos_exporter":
                metrics = asyncio.run(asternos_exporter_client.scrape(device))
                interfaces = _build_asternos_interfaces(metrics)
                _set_monitor_cache("interfaces", device.id, {"interfaces": interfaces, "collected_at": collected_at})
                for interface in interfaces:
                    names = {str(interface.get("name") or ""), str(interface.get("description") or ""), str(interface.get("alias") or "")}
                    if not names.intersection(port_names):
                        continue
                    stats = _build_asternos_interface_stats(device.id, metrics, interface)
                    point = _interface_point(device, stats, now)
                    if point:
                        device_points.append(point)
                        device_points.extend(_asternos_queue_detail_points(device, stats, now))
                        matched += 1
                        _cache_interface_stats(device.id, stats, collected_at)
                return {"points": device_points, "matched_ports": matched}

            if not device.snmp_version:
                return {"points": device_points, "matched_ports": matched}
            interfaces = snmp_collector.list_interfaces(device)
            for interface in interfaces:
                names = {str(interface.get("name") or ""), str(interface.get("description") or ""), str(interface.get("alias") or "")}
                if not names.intersection(port_names):
                    continue
                stats = snmp_collector.get_interface_snapshot(device, int(interface["index"]))
                point = _interface_point(device, stats, now)
                if point:
                    device_points.append(point)
                    matched += 1
                    _cache_interface_stats(device.id, stats, collected_at)
            return {"points": device_points, "matched_ports": matched}

        points: List[Dict[str, Any]] = []
        matched_ports = 0
        workers = min(INTERFACE_REALTIME_MAX_WORKERS, max(1, len(devices)))
        with ThreadPoolExecutor(max_workers=workers) as executor:
            futures = {executor.submit(collect_device_ports, device): device for device in devices}
            for future in as_completed(futures):
                device = futures[future]
                try:
                    result = future.result()
                except Exception as exc:
                    logger.warning(
                        "线路绑定端口实时采集设备失败",
                        device_id=device.id,
                        ip_address=device.ip_address,
                        error=str(exc),
                    )
                    continue
                points.extend(result.get("points") or [])
                matched_ports += int(result.get("matched_ports") or 0)

        if points:
            influx_client.write_points(points, sync=False)

        logger.info(
            "线路绑定端口实时采集完成",
            devices=len(devices),
            matched_ports=matched_ports,
            points_written=len(points),
            elapsed_seconds=round(time.monotonic() - collection_started, 3),
            workers=workers,
        )
        return {
            "devices": len(devices),
            "matched_ports": matched_ports,
            "points_written": len(points),
            "elapsed_seconds": round(time.monotonic() - collection_started, 3),
        }
    except Exception as exc:
        logger.error("线路绑定端口实时采集失败", error=str(exc))
        return {"error": str(exc)}
    finally:
        _release_interface_realtime_lock("circuit", lock_token)
        db.close()


@shared_task
def collect_device_reachability():
    """每30秒对已上线设备执行 ICMP 探测并写入时序库。"""
    lock_token = _try_lock_icmp_reachability()
    if not lock_token:
        logger.info("设备ICMP探测跳过：上一轮任务仍在执行")
        return {"skipped": True, "reason": "previous_run_in_progress"}
    db = SessionLocal()
    try:
        devices = db.query(Device).filter(
            Device.status.in_(["active", "online"]),
            Device.is_monitored == True,
        ).all()

        points = []
        now = datetime.utcnow()
        metrics_by_device = _collect_icmp_reachability_batch(devices)
        for device in devices:
            if not device.ip_address:
                continue
            metrics = metrics_by_device.get(device.id) or _collect_icmp_reachability(device)
            points.append({
                "measurement": "device_reachability",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "device_ip": device.ip_address,
                },
                "fields": metrics,
                "timestamp": now,
            })

        if points:
            influx_client.write_points(points, sync=False)

        logger.info("设备ICMP探测完成", total_devices=len(devices), points_written=len(points))
        return {"total_devices": len(devices), "points_written": len(points)}
    except Exception as exc:
        logger.error("设备ICMP探测失败", error=str(exc))
        return {"error": str(exc)}
    finally:
        _release_icmp_reachability_lock(lock_token)
        db.close()


@shared_task(bind=True)
def verify_snmp_reachability(self, device_id: int):
    """轻量验证单台设备SNMP是否可达"""
    db = SessionLocal()
    try:
        if not redis_client.exists(_device_lock_key(device_id)):
            _try_lock_device(device_id)

        device = db.query(Device).filter(Device.id == device_id).first()
        if not device or not device.snmp_version:
            return {"device_id": device_id, "verified": False, "reason": "设备不存在或未配置SNMP"}

        reachable = _snmp_is_reachable(device)
        if reachable:
            _clear_device_failure_state_for_device(device)
            return {"device_id": device_id, "verified": True, "reachable": True}

        failures = _record_device_failure_for_device(device)
        return {
            "device_id": device_id,
            "verified": True,
            "reachable": False,
            "failures": failures,
            "status": _get_device_status(device_id),
        }
    finally:
        _release_device_lock(device_id)
        db.close()


@shared_task
def verify_unreachable_snmp_devices():
    """每分钟只验证已判定不可达的设备是否恢复"""
    db = SessionLocal()
    try:
        devices = db.query(Device).filter(
            Device.snmp_version.isnot(None),
            Device.status.in_(["active", "online"]),
            Device.is_monitored == True,
            or_(Device.monitor_source == "snmp", Device.monitor_source.is_(None)),
        ).all()

        scheduled = 0
        for device in devices:
            if _get_device_status(device.id) != SNMP_STATUS_UNREACHABLE:
                continue
            if not _try_lock_device(device.id):
                continue
            verify_snmp_reachability.delay(device.id)
            scheduled += 1

        return {"scheduled": scheduled}
    finally:
        db.close()


@shared_task
def sync_gnmi_devices():
    """
    同步gNMI设备到gNMI管理器
    由Celery Beat定时调度
    """
    import asyncio
    from app.collectors import gnmi_manager, DeviceGNMIConfig
    
    db = SessionLocal()
    try:
        # 获取所有启用了gNMI的设备
        devices = db.query(Device).filter(
            Device.gnmi_enabled == 1
        ).all()
        
        logger.info(f"同步gNMI设备，共{len(devices)}个设备")
        
        # 构建设备配置列表
        device_configs = []
        for device in devices:
            config = DeviceGNMIConfig(
                device_id=device.id,
                ip_address=device.ip_address,
                port=device.gnmi_port or 57400,
                username=device.gnmi_username,
                password=device.gnmi_password,
                tls_enabled=bool(device.gnmi_tls_enabled),
                tls_cert=device.gnmi_tls_cert,
                skip_verify=bool(device.gnmi_skip_verify),
                subscriptions=device.gnmi_subscriptions or []
            )
            device_configs.append(config)
        
        # 异步同步设备
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            loop.run_until_complete(gnmi_manager.sync_devices(device_configs))
        finally:
            loop.close()
        
        return {
            "total_devices": len(devices),
            "synced": len(device_configs)
        }
        
    except Exception as e:
        logger.error("同步gNMI设备失败", error=str(e))
        return {"error": str(e)}
    finally:
        db.close()


@shared_task
def update_ping_monitor():
    """
    库存模式下不再主动同步Ping监控
    """
    logger.info("库存模式下跳过Ping监控同步")
    return {
        "total_devices": 0,
        "added": 0,
        "removed": 0,
        "skipped": True,
    }
