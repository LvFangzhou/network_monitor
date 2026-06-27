"""
指标查询路由
"""
from fastapi import APIRouter, Depends, HTTPException, Query
from typing import Any, Dict, List, Optional
from datetime import datetime, timezone
import asyncio
import ipaddress
import json
import math
import os
import platform
import re
import socket

import psutil

from app.utils import influx_client
from app.utils.asternos_exporter_client import asternos_exporter_client
from app.utils import redis_client
from app.schemas import MetricQuery, MetricResponse, DashboardStats
from app.database import get_db
from app.models import Device, AlertHistory, AlertRule, Circuit, Customer, Datacenter
from sqlalchemy.orm import Session
from sqlalchemy import func
from app.core import get_logger
from app.config import settings
from app.collectors import snmp_collector
from app.services.flow_listener import flow_listener
from app.utils.snmp_system_info import extract_snmp_model
from app.utils.controller_client import ControllerClient
from app.utils.controller_settings import find_controller_settings

logger = get_logger(__name__)
router = APIRouter()
SNMP_STATUS_KEY_PREFIX = "snmp_collect:status:"
SNMP_FAILURE_KEY_PREFIX = "snmp_collect:failure:"
SNMP_STATUS_REACHABLE = "reachable"
SNMP_STATUS_UNREACHABLE = "unreachable"
SNMP_VERIFY_OID = "1.3.6.1.2.1.1.3.0"
MONITOR_CACHE_STALE_SECONDS = 7 * 24 * 60 * 60
MAX_INTERFACE_HISTORY_SECONDS = 7 * 24 * 60 * 60
RATE_FALLBACK_MAX_SECONDS = 5 * 60
HISTORY_REQUEST_CACHE_SECONDS = 8
DASHBOARD_STATS_CACHE_SECONDS = 300
DEVICE_OVERVIEW_RESPONSE_CACHE_SECONDS = 15 * 60
DEVICE_OVERVIEW_SNAPSHOT_CACHE_PREFIX = "monitor:cache:overview_snapshot"
FRESH_INTERFACE_SAMPLE_LOCK_SECONDS = 8
FRESH_INTERFACE_SAMPLE_MAX_RANGE_SECONDS = 60 * 60
INTERFACE_RATE_CAP_MULTIPLIER = 1.03
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
QUEUE_MONITOR_GROUP_FIELDS = {
    "queueDropGrowth": ["queue_ingress_dropped_pkts_delta", "queue_egress_dropped_pkts_delta"],
    "pfcGrowth": ["pfc_rx_pkts_delta", "pfc_tx_pkts_delta"],
    "ecnGrowth": ["ecn_marked_pkts_delta"],
}
QUEUE_MONITOR_COLORS = [
    "#fa541c",
    "#eb2f96",
    "#722ed1",
    "#13c2c2",
    "#fa8c16",
    "#2f54eb",
    "#52c41a",
    "#f4d000",
    "#1677ff",
    "#a0d911",
]


def _monitor_cache_key(kind: str, device_id: int, suffix: str = "") -> str:
    return f"monitor:cache:{kind}:{device_id}{suffix}"


def _load_monitor_cache(kind: str, device_id: int, suffix: str = "") -> Optional[Any]:
    raw = redis_client.get(_monitor_cache_key(kind, device_id, suffix))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _cache_collected_at(payload: Optional[Dict[str, Any]]) -> Optional[str]:
    if not isinstance(payload, dict):
        return None
    return payload.get("collected_at")


def is_asternos_vendor(vendor: str | None) -> bool:
    vendor_value = (vendor or "").lower()
    return any(marker in vendor_value for marker in ["asternos", "asterfusion", "asteros", "星融元"])


def get_effective_monitor_source(device: Device) -> str:
    if is_asternos_vendor(device.vendor):
        return "asternos_exporter"
    return "snmp"


def _snmp_status_key(device_id: int) -> str:
    return f"{SNMP_STATUS_KEY_PREFIX}{device_id}"


def _snmp_failure_key(device_id: int) -> str:
    return f"{SNMP_FAILURE_KEY_PREFIX}{device_id}"


def _mark_snmp_reachable(device_id: int) -> None:
    redis_client.set(_snmp_status_key(device_id), SNMP_STATUS_REACHABLE)
    redis_client.delete(_snmp_failure_key(device_id))


def _mark_snmp_failure(device_id: int) -> int:
    failures = redis_client.incr(_snmp_failure_key(device_id))
    redis_client.expire(_snmp_failure_key(device_id), 86400)
    if failures >= 3:
        redis_client.set(_snmp_status_key(device_id), SNMP_STATUS_UNREACHABLE)
    return failures


async def ensure_device_snmp_reachable(device: Device) -> None:
    reachable = await asyncio.to_thread(snmp_collector.snmp_get, device, SNMP_VERIFY_OID)
    if reachable is not None:
        _mark_snmp_reachable(device.id)
        return

    failures = _mark_snmp_failure(device.id)
    detail = "SNMP 无法连通，请检查管理IP、community/账号、ACL 或设备配置"
    if failures >= 3:
        detail = "SNMP 已连续 3 次无法连通，设备已标记为不可达，请检查 SNMP 配置或网络"
    raise HTTPException(status_code=400, detail=detail)


def persist_interface_metrics(device: Device, interface_metrics: dict, sync: bool = False) -> None:
    interface_index = interface_metrics.get("index")
    if interface_index is None:
        return

    fields = {field: interface_metrics.get(field) for field in INTERFACE_HISTORY_FIELDS}
    preserve_exporter_rates = get_effective_monitor_source(device) == "asternos_exporter"
    if (
        interface_metrics.get("in_octets") is not None
        and "in_bps" not in interface_metrics.get("_octet_rate_fields", [])
        and not (preserve_exporter_rates and interface_metrics.get("in_bps") is not None)
    ):
        fields["in_bps"] = None
    if (
        interface_metrics.get("out_octets") is not None
        and "out_bps" not in interface_metrics.get("_octet_rate_fields", [])
        and not (preserve_exporter_rates and interface_metrics.get("out_bps") is not None)
    ):
        fields["out_bps"] = None
    if interface_metrics.get("admin_status") is not None:
        fields["admin_status"] = 1.0 if interface_metrics.get("admin_status") == "up" else 0.0
    if interface_metrics.get("oper_status") is not None:
        fields["oper_status"] = 1.0 if interface_metrics.get("oper_status") == "up" else 0.0
    if interface_metrics.get("admin_status") is not None and interface_metrics.get("oper_status") is not None:
        fields["admin_up_oper_down"] = (
            1.0 if interface_metrics.get("admin_status") == "up" and interface_metrics.get("oper_status") != "up" else 0.0
        )
    _sanitize_impossible_interface_rates(fields)

    influx_client.write_point(
        measurement="interface_monitoring",
        tags={
            "device_id": str(device.id),
            "device_name": device.name,
            "interface_index": str(interface_index),
            "interface_name": interface_metrics.get("name"),
        },
        fields=fields,
        timestamp=datetime.utcnow(),
        sync=sync,
    )


def persist_asternos_queue_detail_metrics(device: Device, interface_metrics: dict, sync: bool = False) -> None:
    interface_index = interface_metrics.get("index")
    interface_name = interface_metrics.get("name")
    if interface_index is None or not interface_name:
        return

    points: List[Dict[str, Any]] = []
    now = datetime.utcnow()
    for counter in interface_metrics.get("asternos_counters") or []:
        labels = counter.get("labels") or {}
        metric_base = counter.get("metric_base")
        field = counter.get("field")
        target = counter.get("target")
        if not metric_base or not field or not target:
            continue
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
                "queue": str(labels.get("queue")) if labels.get("queue") is not None else None,
                "prio": str(labels.get("prio")) if labels.get("prio") is not None else None,
            },
            "fields": {
                "current": counter.get("current"),
                "previous": counter.get("previous"),
                "delta": counter.get("delta"),
            },
            "timestamp": now,
        })
    if points:
        influx_client.write_points(points, sync=sync)


INTERFACE_HISTORY_FIELDS = [
    "in_octets",
    "out_octets",
    "in_bps",
    "out_bps",
    "in_utilization_percent",
    "out_utilization_percent",
    "in_discards",
    "out_discards",
    "in_discards_delta",
    "out_discards_delta",
    "in_errors",
    "out_errors",
    "in_errors_delta",
    "out_errors_delta",
    "queue_egress_dropped_pkts_delta",
    "queue_ingress_dropped_pkts_delta",
    "pfc_rx_pkts_delta",
    "pfc_tx_pkts_delta",
    "ecn_marked_pkts_delta",
    "buffer_usage",
    "queue_length",
    "speed_bps",
    "sample_seconds",
]


def _parse_flux_duration_seconds(value: str, default_seconds: int = 600) -> int:
    match = re.fullmatch(r"-?(\d+)(s|m|h|d|w)", str(value or "").strip())
    if not match:
        return default_seconds
    amount = int(match.group(1))
    unit = match.group(2)
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return max(amount * multipliers[unit], 1)


def _flux_duration(seconds: int) -> str:
    return f"{max(int(seconds), 1)}s"


def _flux_string(value: Any) -> str:
    return json.dumps(str(value), ensure_ascii=False)


def _normalize_query_range(value: str, default: str = "-1h") -> str:
    text = str(value or default).strip()
    return text if re.fullmatch(r"-\d+(s|m|h|d|w)", text) else default


def _normalize_query_interval(value: str, default: str = "30s") -> str:
    text = str(value or default).strip()
    return text if re.fullmatch(r"\d+(s|m|h|d|w)", text) else default


def _customer_public_cidrs(customer: Any) -> List[str]:
    cidrs: List[str] = []
    for site in customer.customer_sites or []:
        if not isinstance(site, dict):
            continue
        for entry in site.get("public_address_entries") or []:
            if not isinstance(entry, dict):
                continue
            cidr = entry.get("cidr") or (
                f"{entry.get('prefix')}/{entry.get('mask')}" if entry.get("prefix") and entry.get("mask") else entry.get("prefix")
            )
            if cidr:
                cidrs.append(str(cidr).strip())
    if getattr(customer, "public_addresses", None):
        for item in str(customer.public_addresses).replace("，", ",").replace("；", ",").replace("\n", ",").split(","):
            text = item.strip()
            if text:
                cidrs.append(text.split(":", 1)[-1].strip())
    normalized: List[str] = []
    for cidr in cidrs:
        try:
            normalized.append(str(ipaddress.ip_network(cidr if "/" in cidr else f"{cidr}/32", strict=False)))
        except ValueError:
            continue
    return list(dict.fromkeys(normalized))


def _find_customers_by_public_cidr(db: Session, cidr: str) -> List[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for customer in db.query(Customer).filter(Customer.is_active == True).all():  # noqa: E712
        if cidr in _customer_public_cidrs(customer):
            matches.append({"id": customer.id, "name": customer.name})
    return matches


def _interface_history_filter(interface_index: int, interface_names: Optional[List[str]] = None) -> str:
    predicates = [f'r.interface_index == {_flux_string(interface_index)}']
    for interface_name in interface_names or []:
        if interface_name:
            predicates.append(f'r.interface_name == {_flux_string(interface_name)}')
    return "(" + " or ".join(predicates) + ")"


async def _resolve_asternos_interface_lookup(device: Device, interface_index: int) -> Dict[str, Any]:
    """AsterNOS exposes both an internal index and a visible name like 0/52."""
    candidates = [str(interface_index)]
    if "/" not in str(interface_index):
        candidates.append(f"0/{interface_index}")

    interfaces_cache = _load_monitor_cache("interfaces", device.id)
    interfaces = interfaces_cache.get("interfaces", []) if isinstance(interfaces_cache, dict) else []
    if not interfaces:
        interfaces = await asternos_exporter_client.list_interfaces(device)

    target = next(
        (
            item for item in interfaces
            if item.get("index") == interface_index or str(item.get("name") or "") in candidates
        ),
        None,
    )
    interface_names = [str(target.get("name"))] if target and target.get("name") else []
    return {"target": target, "interface_names": interface_names}


def _parse_history_time(row: Dict[str, Any]) -> Optional[datetime]:
    raw_time = row.get("_time")
    if not raw_time:
        return None
    if isinstance(raw_time, datetime):
        value = raw_time
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    try:
        value = datetime.fromisoformat(str(raw_time).replace("Z", "+00:00"))
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except Exception:
        return None


def _parse_query_datetime(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return parsed.astimezone(timezone.utc)
    except Exception:
        return None


def _flux_time(value: datetime) -> str:
    return f'time(v: "{value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")}")'


def _apply_windowed_octet_rates(rows: List[Dict[str, Any]], window_seconds: int) -> None:
    timed_rows = [(row, _parse_history_time(row)) for row in rows]
    timed_rows = [(row, row_time) for row, row_time in timed_rows if row_time is not None]
    timed_rows.sort(key=lambda item: item[1])

    for octet_key, bps_key in [("in_octets", "in_bps"), ("out_octets", "out_bps")]:
        candidates: List[tuple[datetime, float]] = []
        last_seen: Optional[tuple[datetime, float]] = None
        for row, row_time in timed_rows:
            current_value = _safe_float(row.get(octet_key))
            if current_value is None:
                continue

            cutoff = row_time.timestamp() - window_seconds
            candidates = [(time_value, value) for time_value, value in candidates if time_value.timestamp() >= cutoff]
            previous = candidates[0] if candidates else last_seen
            if previous:
                previous_time, previous_value = previous
                elapsed = (row_time - previous_time).total_seconds()
                delta = current_value - previous_value
                if 0 < elapsed <= RATE_FALLBACK_MAX_SECONDS and delta >= 0:
                    row[bps_key] = round((delta * 8) / elapsed, 2)
                    row["sample_seconds"] = round(elapsed, 2)
            candidates.append((row_time, current_value))
            last_seen = (row_time, current_value)


def _apply_windowed_bps_average(rows: List[Dict[str, Any]], window_seconds: int, interval_seconds: int, range_seconds: int) -> None:
    if interval_seconds >= window_seconds:
        return

    # Short-range views are used for troubleshooting and traffic tests. SNMP
    # already stores a per-sample counter delta in *_bps, so applying another
    # five-minute moving average here makes sudden traffic look like a slow ramp.
    if range_seconds <= 60 * 60:
        return

    timed_rows = [(row, _parse_history_time(row)) for row in rows]
    timed_rows = [(row, row_time) for row, row_time in timed_rows if row_time is not None]
    timed_rows.sort(key=lambda item: item[1])

    for bps_key in ["in_bps", "out_bps"]:
        candidates: List[tuple[datetime, float]] = []
        for row, row_time in timed_rows:
            current_value = _safe_float(row.get(bps_key))
            if current_value is None:
                continue
            cutoff = row_time.timestamp() - window_seconds
            candidates = [(time_value, value) for time_value, value in candidates if time_value.timestamp() >= cutoff]
            candidates.append((row_time, current_value))
            if len(candidates) > 1:
                row[bps_key] = round(sum(value for _, value in candidates) / len(candidates), 2)


def _recalculate_utilization_from_display_rates(rows: List[Dict[str, Any]]) -> None:
    """Keep utilization charts aligned with the displayed traffic rate window."""
    for row in rows:
        _sanitize_impossible_interface_rates(row)
        speed_bps = _safe_float(row.get("speed_bps"))
        if not speed_bps or speed_bps <= 0:
            continue
        in_bps = _safe_float(row.get("in_bps"))
        out_bps = _safe_float(row.get("out_bps"))
        if in_bps is not None:
            row["in_utilization_percent"] = round((in_bps / speed_bps) * 100, 2)
        if out_bps is not None:
            row["out_utilization_percent"] = round((out_bps / speed_bps) * 100, 2)


def _fill_short_rate_gaps(rows: List[Dict[str, Any]], max_gap_seconds: int = 120) -> None:
    timed_rows = [(row, _parse_history_time(row)) for row in rows]
    timed_rows = [(row, row_time) for row, row_time in timed_rows if row_time is not None]
    timed_rows.sort(key=lambda item: item[1])

    for bps_key in ["in_bps", "out_bps"]:
        last_value: Optional[float] = None
        last_time: Optional[datetime] = None
        for row, row_time in timed_rows:
            current_value = _safe_float(row.get(bps_key))
            if current_value is not None:
                last_value = current_value
                last_time = row_time
                continue
            if last_value is not None and last_time is not None:
                elapsed = (row_time - last_time).total_seconds()
                if 0 < elapsed <= max_gap_seconds:
                    row[bps_key] = last_value
                    row.setdefault("sample_seconds", round(elapsed, 2))


def _history_interval_seconds(interval: str) -> int:
    return _parse_flux_duration_seconds(interval, 30)


def _history_rate_window_seconds(interval_seconds: int) -> int:
    return 5 * 60


def _mark_stale_rate_samples(rows: List[Dict[str, Any]], interval_seconds: int, max_sample_seconds: int = 75) -> None:
    """Mark stale collection samples as missing instead of pretending traffic dropped to zero."""
    timed_rows = [(row, _parse_history_time(row)) for row in rows]
    timed_rows = [(row, row_time) for row, row_time in timed_rows if row_time is not None]
    timed_rows.sort(key=lambda item: item[1])

    previous_time: Optional[datetime] = None
    for row, row_time in timed_rows:
        sample_seconds = _safe_float(row.get("sample_seconds"))
        elapsed = (row_time - previous_time).total_seconds() if previous_time else None
        elapsed_is_stale = (
            interval_seconds <= max_sample_seconds and
            elapsed is not None and
            elapsed > max_sample_seconds
        )
        sample_is_stale = sample_seconds is not None and sample_seconds > max_sample_seconds
        if sample_is_stale or elapsed_is_stale:
            for key in ["in_bps", "out_bps", "in_utilization_percent", "out_utilization_percent"]:
                row[key] = None
        previous_time = row_time


async def _persist_fresh_interface_sample(device: Device, interface_index: int, range_seconds: int) -> bool:
    if range_seconds > FRESH_INTERFACE_SAMPLE_MAX_RANGE_SECONDS:
        return False
    lock_key = _monitor_cache_key("interface_fresh_sample_lock", device.id, suffix=f":{interface_index}")
    if not redis_client.set(lock_key, "1", ex=FRESH_INTERFACE_SAMPLE_LOCK_SECONDS, nx=True):
        return False
    try:
        interface_metrics = await collect_current_interface_metrics(device, interface_index, allow_cache=False)
        if interface_metrics and interface_metrics.get("name"):
            persist_interface_metrics(device, interface_metrics, sync=True)
            if get_effective_monitor_source(device) == "asternos_exporter":
                persist_asternos_queue_detail_metrics(device, interface_metrics, sync=True)
            return True
    except Exception as exc:
        logger.warning(
            "端口历史查询实时采集失败",
            device_id=device.id,
            interface_index=interface_index,
            error=str(exc),
        )
    return False


def interface_metrics_to_history_point(interface_metrics: Dict[str, Any]) -> Dict[str, Any]:
    point = {"_time": datetime.now(timezone.utc).isoformat()}
    for field in INTERFACE_HISTORY_FIELDS:
        if interface_metrics.get(field) is not None:
            point[field] = interface_metrics.get(field)
    return point


def _octet_rate_cache_key(device_id: int, interface_index: int) -> str:
    return f"monitor:interface_octets:{device_id}:{interface_index}"


def apply_octet_rates(device_id: int, interface_metrics: Dict[str, Any], timestamp: datetime) -> None:
    interface_index = interface_metrics.get("index")
    if interface_index is None:
        return

    current_in = interface_metrics.get("in_octets")
    current_out = interface_metrics.get("out_octets")
    if current_in is None and current_out is None:
        return

    cache_key = _octet_rate_cache_key(device_id, int(interface_index))
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
        "time": timestamp.replace(tzinfo=timezone.utc).isoformat(),
    }

    if previous:
        try:
            previous_time = datetime.fromisoformat(str(previous.get("time")))
        except Exception:
            previous_time = None
        for octet_key, bps_key, time_key in [("in_octets", "in_bps", "in_time"), ("out_octets", "out_bps", "out_time")]:
            current_value = interface_metrics.get(octet_key)
            previous_value = previous.get(octet_key)
            if current_value is None or previous_value is None:
                continue
            try:
                field_previous_time = datetime.fromisoformat(str(previous.get(time_key) or previous.get("time")))
            except Exception:
                field_previous_time = previous_time
            elapsed = max((timestamp.replace(tzinfo=timezone.utc) - field_previous_time).total_seconds(), 0.0) if field_previous_time else 0.0
            delta = float(current_value) - float(previous_value)
            if 0.5 <= elapsed <= 300:
                if delta > 0:
                    interface_metrics[bps_key] = round((delta * 8) / elapsed, 2)
                    interface_metrics.setdefault("_octet_rate_fields", []).append(bps_key)
                    next_cache[time_key] = timestamp.replace(tzinfo=timezone.utc).isoformat()
                elif delta == 0:
                    next_cache[octet_key] = previous_value
                    next_cache[time_key] = previous.get(time_key) or previous.get("time")
                else:
                    next_cache[time_key] = timestamp.replace(tzinfo=timezone.utc).isoformat()
                interface_metrics["sample_seconds"] = round(elapsed, 2)

    if "in_time" not in next_cache:
        next_cache["in_time"] = timestamp.replace(tzinfo=timezone.utc).isoformat()
    if "out_time" not in next_cache:
        next_cache["out_time"] = timestamp.replace(tzinfo=timezone.utc).isoformat()

    redis_client.setex(
        cache_key,
        86400,
        json.dumps(next_cache),
    )


async def collect_current_interface_metrics(device: Device, interface_index: int, allow_cache: bool = True) -> Optional[Dict[str, Any]]:
    monitor_source = get_effective_monitor_source(device)
    if monitor_source == "asternos_exporter":
        cached = _load_monitor_cache("interface_stats", device.id, suffix=f":{interface_index}")
        if allow_cache and isinstance(cached, dict) and cached.get("interface"):
            return cached["interface"]

        latest = _latest_interface_metrics_from_history(device.id, interface_index) if allow_cache else None
        if latest:
            return latest

        interfaces_cache = _load_monitor_cache("interfaces", device.id)
        interfaces = interfaces_cache.get("interfaces", []) if isinstance(interfaces_cache, dict) else []
        if not interfaces:
            interfaces = await asternos_exporter_client.list_interfaces(device)
        target = next(
            (
                item for item in interfaces
                if item.get("index") == interface_index or str(item.get("name") or "") == f"0/{interface_index}"
            ),
            None,
        )
        if not target:
            return None
        interface_metrics = await asternos_exporter_client.get_interface_stats(device, target["name"])
        interface_metrics["index"] = target.get("index", interface_index)
        apply_octet_rates(device.id, interface_metrics, datetime.now(timezone.utc))
        counter_deltas = await asyncio.to_thread(_get_asternos_counter_deltas, device, target["name"])
        interface_metrics["asternos_counters"] = counter_deltas["counters"]
        interface_metrics.update(counter_deltas["totals"])
        return interface_metrics

    if not device.snmp_version:
        return None
    return await asyncio.to_thread(snmp_collector.get_interface_metrics, device, interface_index)


def _latest_interface_metrics_from_history(
    device_id: int,
    interface_index: int,
    interface_names: Optional[List[str]] = None,
) -> Optional[Dict[str, Any]]:
    interface_filter = _interface_history_filter(interface_index, interface_names)
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: -1h)
      |> filter(fn: (r) => r._measurement == "interface_monitoring")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => {interface_filter})
      |> last()
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"], desc: true)
      |> limit(n: 1)
    '''
    rows = influx_client.query(flux)
    if not rows:
        return None
    row = rows[0]
    resolved_index = row.get("interface_index") or interface_index
    try:
        resolved_index = int(resolved_index)
    except (TypeError, ValueError):
        resolved_index = interface_index
    result = {
        "index": resolved_index,
        "name": row.get("interface_name") or f"if{resolved_index}",
        "description": row.get("interface_name") or f"if{resolved_index}",
        "admin_status": "up" if _safe_float(row.get("admin_status")) == 1 else "down",
        "oper_status": "up" if _safe_float(row.get("oper_status")) == 1 else "down",
    }
    for field in INTERFACE_HISTORY_FIELDS:
        if row.get(field) is not None:
            result[field] = row.get(field)
    return result


def _has_recent_interface_history(device_id: int) -> bool:
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: -10m)
      |> filter(fn: (r) => r._measurement == "interface_monitoring")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => r._field == "in_octets" or r._field == "out_octets" or r._field == "in_bps" or r._field == "out_bps")
      |> limit(n: 1)
    '''
    try:
        return bool(influx_client.query(flux))
    except Exception as exc:
        logger.warning("检查接口历史数据失败", device_id=device_id, error=str(exc))
        return False


def serialize_monitor_device(device: Device) -> dict:
    monitor_source = get_effective_monitor_source(device)
    return {
        "id": device.id,
        "name": device.name,
        "ip_address": device.ip_address,
        "hostname": device.hostname,
        "device_type": device.device_type,
        "device_role": device.device_role,
        "vendor": device.vendor,
        "model": device.model,
        "status": device.normalized_status if hasattr(device, "normalized_status") else device.status,
        "is_monitored": bool(device.is_monitored),
        "monitor_source": monitor_source,
        "prometheus_url": f"http://{device.ip_address}:8101" if monitor_source == "asternos_exporter" else None,
        "prometheus_job": None,
        "prometheus_instance": None,
        "datacenter": {
            "id": device.datacenter_ref.id,
            "name": device.datacenter_ref.name,
            "code": device.datacenter_ref.code,
        } if device.datacenter_ref else None,
        "snmp": {
            "enabled": bool(device.snmp_version),
            "version": device.snmp_version,
            "port": device.snmp_port,
            "community_configured": bool(device.snmp_community),
            "username": device.snmp_username,
            "security_level": device.snmp_security_level,
            "reachable": redis_client.get(_snmp_status_key(device.id)) != SNMP_STATUS_UNREACHABLE,
        },
    }


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _sanitize_impossible_interface_rates(row: Dict[str, Any]) -> None:
    speed_bps = _safe_float(row.get("speed_bps"))
    if not speed_bps or speed_bps <= 0:
        return
    for bps_key, utilization_key in [
        ("in_bps", "in_utilization_percent"),
        ("out_bps", "out_utilization_percent"),
    ]:
        value = _safe_float(row.get(bps_key))
        if value is None:
            continue
        if value < 0:
            row[bps_key] = None
            row[utilization_key] = None
        elif (
            value > speed_bps * INTERFACE_RATE_CAP_MULTIPLIER
            or math.isclose(value, speed_bps, rel_tol=0.0, abs_tol=0.5)
        ):
            # Older collectors clipped impossible deltas to exactly the
            # physical port speed. Treat those legacy line-rate points, and
            # any new over-speed sample, as missing instead of displaying a
            # fabricated 10G/100G peak.
            row[bps_key] = None
            row[utilization_key] = None


def _latest_numeric(device_id: int, measurement: str, fields: List[str]) -> Optional[float]:
    for field in fields:
        value = _safe_float(influx_client.get_last_value(measurement, device_id, field))
        if value is not None:
            return value
    return None


def _latest_grouped_values(
    device_id: int,
    measurement: str,
    field: str,
    group_columns: List[str],
    start: str = "-1h",
) -> List[Dict[str, Any]]:
    columns = ", ".join([f'"{column}"' for column in group_columns])
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {start})
      |> filter(fn: (r) => r._measurement == "{measurement}")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => r._field == "{field}")
      |> group(columns: [{columns}])
      |> last()
    '''
    return influx_client.query(flux)


def _max_latest_grouped_value(device_id: int, measurement: str, field: str, group_columns: List[str]) -> Optional[float]:
    values = [_safe_float(row.get("value")) for row in _latest_grouped_values(device_id, measurement, field, group_columns)]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _latest_snmp_system_info(device_id: int) -> Dict[str, Any]:
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: -24h)
      |> filter(fn: (r) => r._measurement == "snmp_system_info")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => r._field == "uptime_seconds")
      |> last()
    '''
    try:
        rows = influx_client.query(flux)
    except Exception as exc:
        logger.warning("读取SNMP系统信息失败", device_id=device_id, error=str(exc))
        return {"sys_name": None, "sys_descr": None, "software_version": None, "snmp_model": None, "serial_number": None, "uptime_seconds": None}
    if not rows:
        uptime = _latest_numeric(device_id, "snmp_metrics", ["seconds"])
        return {"sys_name": None, "sys_descr": None, "software_version": None, "snmp_model": None, "serial_number": None, "uptime_seconds": uptime}
    row = rows[0]
    sys_descr = row.get("sys_descr")
    snmp_model = row.get("snmp_model") or extract_snmp_model(sys_descr)
    return {
        "sys_name": row.get("sys_name"),
        "sys_descr": sys_descr,
        "software_version": row.get("software_version"),
        "snmp_model": snmp_model,
        "serial_number": row.get("serial_number"),
        "uptime_seconds": _safe_float(row.get("value")),
    }


def _ensure_snmp_system_info_model(item: Dict[str, Any]) -> None:
    """缓存里缺少 snmp_model 时，从 sys_descr 兜底补齐。"""
    system_info = item.get("system_info")
    if not isinstance(system_info, dict):
        return
    if system_info.get("snmp_model"):
        return
    snmp_model = extract_snmp_model(system_info.get("sys_descr"))
    if snmp_model:
        system_info["snmp_model"] = snmp_model


def _hardware_summary(device_id: int) -> Dict[str, Any]:
    up_rows = _latest_grouped_values(device_id, "snmp_hardware", "up", ["component_type", "component"])
    present_rows = _latest_grouped_values(device_id, "snmp_hardware", "present", ["component_type", "component"])
    status_rows = _latest_grouped_values(device_id, "snmp_hardware", "status_known", ["component_type", "component"])
    up_map = {
        (str(row.get("component_type") or ""), str(row.get("component") or "")): _safe_float(row.get("value"))
        for row in up_rows
    }
    present_map = {
        (str(row.get("component_type") or ""), str(row.get("component") or "")): _safe_float(row.get("value"))
        for row in present_rows
    }
    status_map = {
        (str(row.get("component_type") or ""), str(row.get("component") or "")): _safe_float(row.get("value"))
        for row in status_rows
    }
    summary = {
        "fan_total": 0,
        "fan_down": 0,
        "fan_status_known": True,
        "power_total": 0,
        "power_down": 0,
        "power_status_known": True,
    }
    keys = sorted(set(up_map) | set(present_map) | set(status_map))
    for component_type, component in keys:
        value = up_map.get((component_type, component))
        present = present_map.get((component_type, component))
        status_known = status_map.get((component_type, component))
        is_present = present is None or present >= 1
        if component_type == "fan":
            if is_present:
                summary["fan_total"] += 1
            if value == 0:
                summary["fan_down"] += 1
            if status_known == 0:
                summary["fan_status_known"] = False
        elif component_type == "power":
            if is_present:
                summary["power_total"] += 1
            if value == 0:
                summary["power_down"] += 1
            if status_known == 0:
                summary["power_status_known"] = False
    return summary


def _empty_protocol_summary() -> Dict[str, Dict[str, int]]:
    return {
        "bgp": {"total": 0, "up": 0, "down": 0},
        "ospf": {"total": 0, "up": 0, "down": 0},
    }


def _summarize_protocol_rows(rows: List[Dict[str, Any]]) -> Dict[str, Dict[str, int]]:
    summary = _empty_protocol_summary()
    for row in rows:
        protocol = str(row.get("protocol") or "").lower()
        if protocol not in summary:
            continue
        value = _safe_float(row.get("value"))
        is_up = value is not None and value >= 1
        summary[protocol]["total"] += 1
        if is_up:
            summary[protocol]["up"] += 1
        else:
            summary[protocol]["down"] += 1
    return summary


def _get_snmp_protocol_summary(device_id: int) -> Dict[str, Dict[str, int]]:
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: -1h)
      |> filter(fn: (r) => r._measurement == "protocol_status")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => r._field == "state_up")
      |> group(columns: ["protocol", "peer"])
      |> last()
    '''
    return _summarize_protocol_rows(influx_client.query(flux))


def _snmp_overview_has_recent_data(
    device_id: int,
    resources: Dict[str, Any],
    hardware: Dict[str, Any],
    protocols: Dict[str, Dict[str, int]],
    sessions: Dict[str, Any],
) -> bool:
    if any(resources.get(field) is not None for field in ["cpu_percent", "memory_percent", "temperature", "storage_percent"]):
        return True
    if any(sessions.get(field) is not None for field in ["current", "total", "usage_percent"]):
        return True
    if (hardware.get("fan_total") or 0) > 0 or (hardware.get("power_total") or 0) > 0:
        return True
    if (protocols.get("bgp", {}).get("total") or 0) > 0 or (protocols.get("ospf", {}).get("total") or 0) > 0:
        return True
    return _has_recent_interface_history(device_id)


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


def _max_metric_value(rows: List[Dict[str, Any]]) -> Optional[float]:
    values = [_safe_float(row.get("value")) for row in rows]
    values = [value for value in values if value is not None]
    return max(values) if values else None


def _normalize_percent(value: Optional[float]) -> Optional[float]:
    if value is None:
        return None
    return round(value * 100, 1) if 0 <= value <= 1 else round(value, 1)


def _source_value_is_missing(value: Any) -> bool:
    return value is None or value == "" or (isinstance(value, float) and math.isnan(value))


def _overview_field_sources(item: Dict[str, Any]) -> Dict[str, Any]:
    sources = item.get("data_sources")
    if not isinstance(sources, dict):
        sources = {"resources": {}, "protocols": {}, "system_info": {}}
        item["data_sources"] = sources
    sources.setdefault("resources", {})
    sources.setdefault("protocols", {})
    sources.setdefault("system_info", {})
    return sources


def _controller_resource_usage(resource_row: Dict[str, Any], key: str) -> Optional[float]:
    values: List[float] = []
    for threshold in resource_row.get("capacity_thresholds") or []:
        if not isinstance(threshold, dict):
            continue
        usage = _safe_float((threshold.get(key) or {}).get("usage"))
        if usage is not None:
            values.append(usage)
    return _normalize_percent(max(values)) if values else None


async def _load_controller_overview_fallbacks() -> Dict[str, Any]:
    """Fetch controller-side overview data once per request.

    Controller data is only a fallback. Local SNMP/exporter data remains the
    authoritative source when present.
    """
    cache_key = "controller:overview:fallbacks"
    cached = redis_client.get(cache_key)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            pass

    result = {
        "resources_by_ip": {},
        "bgp_by_ip": {},
        "available": False,
    }
    try:
        settings_payload = find_controller_settings(require_enabled=True)
    except Exception:
        return result

    client = ControllerClient(settings_payload)

    try:
        resource_payload = await client.list_device_resources(page_size=200)
        for row in resource_payload.get("items") or []:
            if not isinstance(row, dict):
                continue
            ip = str(row.get("management_ip") or row.get("mngIp") or row.get("deviceIp") or row.get("ip") or "").strip()
            if not ip:
                continue
            result["resources_by_ip"][ip] = {
                "cpu_percent": _controller_resource_usage(row, "cpu"),
                "memory_percent": _controller_resource_usage(row, "memory"),
                "device_status": row.get("device_status"),
                "device_name": row.get("device_name"),
                "score": row.get("score"),
                "raw": row,
            }
        result["available"] = True
    except Exception as exc:
        logger.warning("读取控制器设备资源容量失败", error=str(exc))

    try:
        bgp_payload = await client.list_bgp_instances(page_size=200)
        bgp_summary: Dict[str, Dict[str, int]] = {}
        for instance in bgp_payload.get("items") or []:
            if not isinstance(instance, dict):
                continue
            for peer in instance.get("bgp_peers") or []:
                if not isinstance(peer, dict):
                    continue
                for state in peer.get("bgp_peer_states") or []:
                    if not isinstance(state, dict):
                        continue
                    node_ip = str(state.get("bgp_node_ip") or "").strip()
                    if not node_ip:
                        continue
                    entry = bgp_summary.setdefault(node_ip, {"total": 0, "up": 0, "down": 0})
                    entry["total"] += 1
                    if str(state.get("bgp_peer_state") or "").lower() == "established":
                        entry["up"] += 1
                    else:
                        entry["down"] += 1
        result["bgp_by_ip"] = bgp_summary
        if bgp_summary:
            result["available"] = True
    except Exception as exc:
        logger.warning("读取控制器BGP实例失败", error=str(exc))

    if result.get("available"):
        redis_client.setex(cache_key, 120, json.dumps(result, ensure_ascii=False))
    return result


def _apply_controller_overview_fallback(item: Dict[str, Any], controller_data: Dict[str, Any]) -> None:
    device = item.get("device") or {}
    ip = str(device.get("ip_address") or "").strip()
    if not ip:
        return

    resource = (controller_data.get("resources_by_ip") or {}).get(ip)
    sources = _overview_field_sources(item)
    if resource:
        resources = item.setdefault("resources", {})
        for field in ["cpu_percent", "memory_percent"]:
            if _source_value_is_missing(resources.get(field)) and not _source_value_is_missing(resource.get(field)):
                resources[field] = resource.get(field)
                sources["resources"][field] = "controller_api"

        if item.get("connectivity", {}).get("status") in {"unknown", "not_configured"} and str(resource.get("device_status") or "").lower() == "active":
            item["connectivity"] = {
                "type": item.get("connectivity", {}).get("type") or item.get("monitor_source") or "controller_api",
                "status": "reachable",
                "message": "本地采集暂无有效数据，控制器显示设备 Active",
            }

    bgp = (controller_data.get("bgp_by_ip") or {}).get(ip)
    if bgp and (item.get("protocols", {}).get("bgp", {}).get("total") or 0) <= 0:
        item.setdefault("protocols", {})["bgp"] = bgp
        sources["protocols"]["bgp"] = "controller_api"


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


def _duration_from_time(value: Any) -> Optional[int]:
    if not value:
        return None
    try:
        if isinstance(value, datetime):
            event_time = value
        else:
            event_time = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
        if event_time.tzinfo is None:
            event_time = event_time.replace(tzinfo=timezone.utc)
        return max(int((datetime.now(timezone.utc) - event_time).total_seconds()), 0)
    except Exception:
        return None


def _state_is_up(protocol: str, state_text: str, value: Optional[float]) -> bool:
    text = (state_text or "").lower()
    if protocol == "bgp":
        return "established" in text or (value is not None and value >= 1)
    if protocol == "ospf":
        return "full" in text or (value is not None and value >= 1)
    return value is not None and value >= 1


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
            "neighbor": labels.get("neighbor") or labels.get("Neighbor"),
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
        peer = str(labels.get("Neighbor") or labels.get("peer") or labels.get("Address") or "")
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


def _get_snmp_protocol_neighbors(device_id: int) -> Dict[str, List[Dict[str, Any]]]:
    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: -24h)
      |> filter(fn: (r) => r._measurement == "protocol_status")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => r._field == "state_up")
      |> sort(columns: ["_time"], desc: true)
    '''
    rows = influx_client.query(flux)
    grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
    for row in rows:
        protocol = str(row.get("protocol") or "").lower()
        if protocol not in {"bgp", "ospf"}:
            continue
        peer = str(row.get("peer") or "")
        grouped.setdefault((protocol, peer), []).append(row)

    result: Dict[str, List[Dict[str, Any]]] = {"bgp": [], "ospf": []}
    for (protocol, peer), protocol_rows in grouped.items():
        latest = protocol_rows[0]
        instance = str(latest.get("instance") or "")
        latest_state = str(latest.get("state_text") or "")
        latest_value = _safe_float(latest.get("value"))
        duration_anchor = latest.get("time") or latest.get("_time")
        for row in protocol_rows[1:]:
            state = str(row.get("state_text") or "")
            value = _safe_float(row.get("value"))
            if state != latest_state or value != latest_value:
                break
            duration_anchor = row.get("time") or row.get("_time")
        is_up = _state_is_up(protocol, latest_state, latest_value)
        result[protocol].append({
            "protocol": protocol,
            "peer": peer,
            "neighbor": peer,
            "remote_as": None,
            "interface": None,
            "instance": instance or None,
            "state": latest_state or "-",
            "status": "up" if is_up else "down",
            "duration_seconds": _duration_from_time(duration_anchor),
            "duration_text": None,
            "source": "snmp",
        })
    return result


async def _build_asternos_overview(device: Device) -> Dict[str, Any]:
    metrics = await asternos_exporter_client.scrape(device)

    return {
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
        "hardware": {
            "fan_total": 0,
            "fan_down": 0,
            "fan_status_known": True,
            "power_total": 0,
            "power_down": 0,
            "power_status_known": True,
        },
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
    }


def _build_snmp_overview(
    device: Device,
    include_storage: bool = False,
    include_hardware: bool = False,
    include_sessions: bool = False,
) -> Dict[str, Any]:
    snmp_status = redis_client.get(_snmp_status_key(device.id)) or "unknown"
    resources = {
        "cpu_percent": _latest_numeric(device.id, "snmp_metrics", ["usage"]),
        "memory_percent": _latest_numeric(device.id, "snmp_metrics", ["usage_percent", "used_percent", "percent"]),
        "temperature": _latest_numeric(device.id, "snmp_temperature", ["temperature", "value", "temp"]),
        "storage_percent": _max_latest_grouped_value(device.id, "snmp_storage", "usage_percent", ["storage"]) if include_storage else None,
    }
    sessions = {
        "current": _latest_numeric(device.id, "snmp_sessions", ["current"]) if include_sessions else None,
        "total": _latest_numeric(device.id, "snmp_sessions", ["total"]) if include_sessions else None,
        "usage_percent": _latest_numeric(device.id, "snmp_sessions", ["usage_percent"]) if include_sessions else None,
    }
    hardware = _hardware_summary(device.id) if include_hardware else {
        "fan_total": 0,
        "fan_down": 0,
        "fan_status_known": True,
        "power_total": 0,
        "power_down": 0,
        "power_status_known": True,
    }
    protocols = _get_snmp_protocol_summary(device.id)
    system_info = _latest_snmp_system_info(device.id)
    has_recent_data = _snmp_overview_has_recent_data(device.id, resources, hardware, protocols, sessions)

    if not device.snmp_version:
        connectivity_status = "not_configured"
        message = "未配置SNMP参数"
    elif snmp_status == SNMP_STATUS_UNREACHABLE:
        connectivity_status = "unreachable"
        message = "SNMP最近采集不可达"
        if has_recent_data:
            message = "SNMP最近采集不可达，当前展示最近一次后台采集结果"
    elif snmp_status == SNMP_STATUS_REACHABLE:
        connectivity_status = "reachable"
        message = f"SNMP {device.snmp_version}"
    elif has_recent_data:
        connectivity_status = "reachable"
        message = f"SNMP {device.snmp_version}（展示最近一次后台采集结果）"
    else:
        connectivity_status = "unknown"
        message = "等待SNMP采集结果"

    return {
        "connectivity": {
            "type": "snmp",
            "status": connectivity_status,
            "message": message,
        },
        "resources": resources,
        "sessions": sessions,
        "hardware": hardware,
        "protocols": protocols,
        "system_info": system_info,
    }


def _device_base_overview(device: Device) -> Dict[str, Any]:
    return {
        "device": serialize_monitor_device(device),
        "monitor_source": get_effective_monitor_source(device),
        "connectivity": {
            "type": get_effective_monitor_source(device),
            "status": "unknown",
            "message": "未采集",
        },
        "resources": {
            "cpu_percent": None,
            "memory_percent": None,
            "temperature": None,
            "storage_percent": None,
        },
        "sessions": {"current": None, "total": None, "usage_percent": None},
        "hardware": {
            "fan_total": 0,
            "fan_down": 0,
            "fan_status_known": True,
            "power_total": 0,
            "power_down": 0,
            "power_status_known": True,
        },
        "protocols": _empty_protocol_summary(),
        "system_info": {"sys_name": None, "sys_descr": None, "software_version": None, "snmp_model": None, "serial_number": None, "uptime_seconds": None},
        "data_sources": {"resources": {}, "protocols": {}, "system_info": {}},
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


def _counter_cache_key(device_id: int, metric_base: str, target_key: str) -> str:
    return f"monitor:asternos_counter:{device_id}:{metric_base}:{target_key}"


def _build_counter_target_key(labels: Dict[str, Any], target_labels: List[str]) -> str:
    return "|".join(f"{key}={labels.get(key, '')}" for key in target_labels)


def _get_asternos_counter_deltas(device: Device, interface_name: str) -> Dict[str, Any]:
    metrics = asyncio.run(asternos_exporter_client.scrape(device))
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
            cache_key = _counter_cache_key(device.id, metric_base, target_key)
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
            counters.append(
                {
                    "field": field,
                    "metric_base": metric_base,
                    "label": config["label"],
                    "target": target_key,
                    "labels": labels,
                    "current": current_value,
                    "previous": previous_value,
                    "delta": delta,
                }
            )
        totals[field] = total_delta

    return {"counters": counters, "totals": totals}


@router.get("/monitoring/devices/by-ip")
async def get_monitor_device_by_ip(
    ip_address: str = Query(..., description="设备管理IP"),
    db: Session = Depends(get_db),
):
    """按管理IP获取监控设备信息"""
    device = db.query(Device).filter(Device.ip_address == ip_address.strip()).first()
    if not device:
        raise HTTPException(status_code=404, detail="未找到该管理IP对应的网络设备")
    if device.status not in {"active", "online"} or not device.is_monitored:
        raise HTTPException(status_code=400, detail="该设备未加入监控，或当前不是上线状态")

    monitor_source = get_effective_monitor_source(device)
    if monitor_source == "asternos_exporter":
        cached_overview = _load_monitor_cache("overview", device.id)
        cached_status = (cached_overview or {}).get("connectivity", {}).get("status") if isinstance(cached_overview, dict) else None
        if cached_status == "unreachable":
            raise HTTPException(status_code=400, detail=(cached_overview or {}).get("connectivity", {}).get("message") or "AsterNOS Exporter 无法连通")
        if cached_status is None:
            logger.info("AsterNOS Exporter后台快照尚未生成，先返回设备信息", device_id=device.id)
    else:
        if not device.snmp_version:
            raise HTTPException(status_code=400, detail="该设备未配置SNMP，请检查设备配置")
        await ensure_device_snmp_reachable(device)

    return serialize_monitor_device(device)


@router.get("/monitoring/devices/{device_id}/interfaces")
async def get_monitor_device_interfaces(device_id: int, db: Session = Depends(get_db)):
    """获取设备接口列表"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if device.status not in {"active", "online"} or not device.is_monitored:
        raise HTTPException(status_code=400, detail="该设备未加入监控，或当前不是上线状态")

    monitor_source = get_effective_monitor_source(device)
    if monitor_source == "asternos_exporter":
        cached = _load_monitor_cache("interfaces", device.id)
        interfaces = cached.get("interfaces", []) if isinstance(cached, dict) else []
        if not interfaces:
            try:
                interfaces = await asternos_exporter_client.list_interfaces(device)
            except Exception as exc:
                raise HTTPException(status_code=400, detail=f"AsterNOS Exporter 后台快照暂不可用：{exc}") from exc
    else:
        if not device.snmp_version:
            raise HTTPException(status_code=400, detail="该设备未配置SNMP，无法读取接口信息")
        await ensure_device_snmp_reachable(device)
        interfaces = await asyncio.to_thread(snmp_collector.list_interfaces, device)
    return {
        "device": serialize_monitor_device(device),
        "interfaces": interfaces,
        "total": len(interfaces),
    }


@router.get("/monitoring/devices/{device_id}/interfaces/{interface_index}")
async def get_monitor_device_interface_stats(
    device_id: int,
    interface_index: int,
    fresh: bool = Query(False, description="是否绕过缓存并实时采集当前端口"),
    db: Session = Depends(get_db),
):
    """获取设备单个接口指标"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if device.status not in {"active", "online"} or not device.is_monitored:
        raise HTTPException(status_code=400, detail="该设备未加入监控，或当前不是上线状态")

    monitor_source = get_effective_monitor_source(device)
    if monitor_source == "asternos_exporter":
        lookup = await _resolve_asternos_interface_lookup(device, interface_index)
        interface_names = lookup["interface_names"]
        cached = _load_monitor_cache("interface_stats", device.id, suffix=f":{interface_index}")
        if not fresh and isinstance(cached, dict) and cached.get("interface"):
            interface_metrics = cached["interface"]
            collected_at = cached.get("collected_at") or datetime.now(timezone.utc).isoformat()
        else:
            interface_metrics = None if fresh else _latest_interface_metrics_from_history(device.id, interface_index, interface_names)
            collected_at = datetime.now(timezone.utc).isoformat()
            if not interface_metrics:
                interface_metrics = await collect_current_interface_metrics(device, interface_index, allow_cache=not fresh)
                if not interface_metrics:
                    raise HTTPException(status_code=404, detail="未找到该接口")
    else:
        if not device.snmp_version:
            raise HTTPException(status_code=400, detail="该设备未配置SNMP，无法读取接口信息")
        interface_metrics = None if fresh else _latest_interface_metrics_from_history(device.id, interface_index)
        collected_at = datetime.now(timezone.utc).isoformat()
        if not interface_metrics:
            interface_metrics = await asyncio.to_thread(
                snmp_collector.get_interface_metrics,
                device,
                interface_index,
            )

    if not interface_metrics.get("name"):
        raise HTTPException(status_code=404, detail="未找到该接口")

    return {
        "device": serialize_monitor_device(device),
        "interface": interface_metrics,
        "collected_at": collected_at,
    }


@router.get("/monitoring/devices/{device_id}/interfaces/{interface_index}/history")
async def get_monitor_device_interface_history(
    device_id: int,
    interface_index: int,
    range: str = Query("-10m", description="历史时间范围"),
    interval: str = Query("30s", description="聚合间隔"),
    group: str = Query("traffic", description="监控项分组"),
    start: Optional[str] = Query(None, description="绝对开始时间"),
    end: Optional[str] = Query(None, description="绝对结束时间"),
    start_ts: Optional[float] = Query(None, description="绝对开始时间戳毫秒"),
    end_ts: Optional[float] = Query(None, description="绝对结束时间戳毫秒"),
    db: Session = Depends(get_db),
):
    """获取设备接口历史指标"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    if device.status not in {"active", "online"} or not device.is_monitored:
        raise HTTPException(status_code=400, detail="该设备未加入监控，或当前不是上线状态")

    start_time = datetime.fromtimestamp(start_ts / 1000, timezone.utc) if start_ts else _parse_query_datetime(start)
    end_time = datetime.fromtimestamp(end_ts / 1000, timezone.utc) if end_ts else (_parse_query_datetime(end) or datetime.now(timezone.utc))
    use_absolute_range = bool(start_time and start_time < end_time)
    range_seconds = min(
        int((end_time - start_time).total_seconds()) if use_absolute_range and start_time else _parse_flux_duration_seconds(range, 600),
        MAX_INTERFACE_HISTORY_SECONDS,
    )
    interval_seconds = _history_interval_seconds(interval)
    rate_window_seconds = _history_rate_window_seconds(interval_seconds)
    traffic_start = _flux_duration(range_seconds + rate_window_seconds)
    if use_absolute_range and start_time:
        range_clause = f'start: {_flux_time(start_time)}, stop: {_flux_time(end_time)}'
        traffic_start_time = start_time.timestamp() - rate_window_seconds
        traffic_range_clause = f'start: {_flux_time(datetime.fromtimestamp(traffic_start_time, timezone.utc))}, stop: {_flux_time(end_time)}'
        normalized_group = (group or "traffic").strip() or "traffic"
        cache_suffix = f":v8:{interface_index}:{normalized_group}:abs:{int(start_time.timestamp())}:{int(end_time.timestamp())}:{interval}"
        logger.info(
            "端口历史绝对时间查询",
            device_id=device_id,
            interface_index=interface_index,
            start_ts=start_ts,
            end_ts=end_ts,
            start_time=start_time.isoformat(),
            end_time=end_time.isoformat(),
            interval=interval,
        )
    else:
        normalized_group = (group or "traffic").strip() or "traffic"
        range_clause = f"start: {range}"
        traffic_range_clause = f"start: -{traffic_start}"
        cache_suffix = f":v8:{interface_index}:{normalized_group}:{range}:{interval}"
    is_traffic_only = normalized_group == "traffic"
    fresh_sample_written = False
    if not is_traffic_only:
        fresh_sample_written = await _persist_fresh_interface_sample(device, interface_index, range_seconds)
    cached_response = None if fresh_sample_written else _load_monitor_cache("interface_history", device_id, suffix=cache_suffix)
    if isinstance(cached_response, dict):
        return cached_response

    interface_names: List[str] = []
    if get_effective_monitor_source(device) == "asternos_exporter":
        lookup = await _resolve_asternos_interface_lookup(device, interface_index)
        interface_names = lookup["interface_names"]
    interface_filter = _interface_history_filter(interface_index, interface_names)

    other_field_filter = '''
        r._field == "speed_bps" or
        r._field == "sample_seconds"
    ''' if is_traffic_only else '''
        r._field == "in_utilization_percent" or
        r._field == "out_utilization_percent" or
        r._field == "in_discards" or
        r._field == "out_discards" or
        r._field == "in_discards_delta" or
        r._field == "out_discards_delta" or
        r._field == "in_errors" or
        r._field == "out_errors" or
        r._field == "in_errors_delta" or
        r._field == "out_errors_delta" or
        r._field == "queue_egress_dropped_pkts_delta" or
        r._field == "queue_ingress_dropped_pkts_delta" or
        r._field == "pfc_rx_pkts_delta" or
        r._field == "pfc_tx_pkts_delta" or
        r._field == "ecn_marked_pkts_delta" or
        r._field == "buffer_usage" or
        r._field == "speed_bps" or
        r._field == "sample_seconds"
    '''

    flux = f'''
    rates = from(bucket: "{influx_client.bucket}")
      |> range({range_clause})
      |> filter(fn: (r) => r._measurement == "interface_monitoring")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => {interface_filter})
      |> filter(fn: (r) =>
        r._field == "in_bps" or
        r._field == "out_bps"
      )
      |> aggregateWindow(every: {interval}, fn: mean, createEmpty: false)

    octets = from(bucket: "{influx_client.bucket}")
      |> range({traffic_range_clause})
      |> filter(fn: (r) => r._measurement == "interface_monitoring")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => {interface_filter})
      |> filter(fn: (r) =>
        r._field == "in_octets" or
        r._field == "out_octets"
      )
      |> aggregateWindow(every: {interval}, fn: last, createEmpty: false)

    other = from(bucket: "{influx_client.bucket}")
      |> range({range_clause})
      |> filter(fn: (r) => r._measurement == "interface_monitoring")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => {interface_filter})
      |> filter(fn: (r) =>
        {other_field_filter}
      )
      |> aggregateWindow(every: {interval}, fn: max, createEmpty: false)

    union(tables: [rates, octets, other])
      |> group()
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"])
    '''
    history = influx_client.query(flux)
    if use_absolute_range:
        logger.info(
            "端口历史绝对时间查询结果",
            device_id=device_id,
            interface_index=interface_index,
            rows=len(history),
            interval=interval,
        )
    _apply_windowed_octet_rates(history, rate_window_seconds)
    _apply_windowed_bps_average(history, rate_window_seconds, interval_seconds, range_seconds)
    if get_effective_monitor_source(device) == "asternos_exporter":
        _fill_short_rate_gaps(history)
    _recalculate_utilization_from_display_rates(history)
    _mark_stale_rate_samples(
        history,
        interval_seconds,
        max_sample_seconds=max(75, rate_window_seconds + interval_seconds),
    )
    cutoff = (start_time.timestamp() if use_absolute_range and start_time else datetime.now(timezone.utc).timestamp() - range_seconds)
    stop_ts = end_time.timestamp() if use_absolute_range else None
    history = [
        row for row in history
        if (
            _parse_history_time(row) and
            _parse_history_time(row).timestamp() >= cutoff and
            (stop_ts is None or _parse_history_time(row).timestamp() <= stop_ts)
        )
    ]
    if not history and not is_traffic_only:
        interface_metrics = await collect_current_interface_metrics(device, interface_index)
        if interface_metrics and interface_metrics.get("name"):
            persist_interface_metrics(device, interface_metrics, sync=True)
            if get_effective_monitor_source(device) == "asternos_exporter":
                persist_asternos_queue_detail_metrics(device, interface_metrics, sync=True)
            history = [interface_metrics_to_history_point(interface_metrics)]

    response_data = {
        "device": serialize_monitor_device(device),
        "interface_index": interface_index,
        "range": range,
        "interval": interval,
        "rate_window": _flux_duration(rate_window_seconds),
        "data": history,
        "total": len(history),
    }
    redis_client.setex(
        _monitor_cache_key("interface_history", device_id, suffix=cache_suffix),
        HISTORY_REQUEST_CACHE_SECONDS,
        json.dumps(response_data, default=str),
    )
    return response_data


@router.get("/monitoring/devices/{device_id}/interfaces/{interface_index}/queue-history")
async def get_monitor_device_interface_queue_history(
    device_id: int,
    interface_index: int,
    group: str = Query("queueDropGrowth", description="队列监控分组"),
    range: str = Query("-10m", description="历史时间范围"),
    interval: str = Query("30s", description="聚合间隔"),
    db: Session = Depends(get_db),
):
    """获取端口下具体队列/优先级的历史指标。"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if device.status not in {"active", "online"} or not device.is_monitored:
        raise HTTPException(status_code=400, detail="该设备未加入监控，或当前不是上线状态")
    if get_effective_monitor_source(device) != "asternos_exporter":
        return {
            "device": serialize_monitor_device(device),
            "interface_index": interface_index,
            "range": range,
            "interval": interval,
            "series": [],
            "data": [],
            "total": 0,
            "message": "该设备暂未接入队列级 exporter 指标",
        }

    fields = QUEUE_MONITOR_GROUP_FIELDS.get(group)
    if not fields:
        raise HTTPException(status_code=400, detail="不支持的队列监控分组")

    range_seconds = min(_parse_flux_duration_seconds(range, 600), MAX_INTERFACE_HISTORY_SECONDS)
    range_value = _normalize_query_range(range, "-10m")
    interval_value = _normalize_query_interval(interval, "30s")
    lookup = await _resolve_asternos_interface_lookup(device, interface_index)
    interface_filter = _interface_history_filter(interface_index, lookup["interface_names"])
    field_filter = " or ".join(f'r.field == {_flux_string(field)}' for field in fields)
    cache_suffix = f":queue:v1:{interface_index}:{group}:{range_value}:{interval_value}"
    cached_response = _load_monitor_cache("interface_history", device_id, suffix=cache_suffix)
    if isinstance(cached_response, dict):
        return cached_response

    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {range_value})
      |> filter(fn: (r) => r._measurement == "queue_monitoring")
      |> filter(fn: (r) => r.device_id == "{device_id}")
      |> filter(fn: (r) => {interface_filter})
      |> filter(fn: (r) => r._field == "delta")
      |> filter(fn: (r) => {field_filter})
      |> aggregateWindow(every: {interval_value}, fn: max, createEmpty: false)
      |> group(columns: ["field", "target"])
      |> sort(columns: ["_time"])
    '''
    rows = influx_client.query(flux)
    cutoff = datetime.now(timezone.utc).timestamp() - range_seconds
    series_map: Dict[str, Dict[str, Any]] = {}
    data_map: Dict[str, Dict[str, Any]] = {}

    for row in rows:
        row_time = _parse_history_time(row)
        if not row_time or row_time.timestamp() < cutoff:
            continue
        value = _safe_float(row.get("value"))
        if value is None:
            continue
        field = str(row.get("field") or row.get("metric_field") or "")
        target = str(row.get("target") or "")
        if not field or not target:
            continue
        series_id = f"{field}|{target}"
        if series_id not in series_map:
            queue = row.get("queue")
            prio = row.get("prio")
            detail = f"队列 {queue}" if queue not in (None, "") else (f"优先级 {prio}" if prio not in (None, "") else target)
            field_label = next((item["label"] for item in ASTERNOS_COUNTER_METRICS if item["field"] == field), field)
            series_map[series_id] = {
                "key": f"queue_series_{len(series_map)}",
                "label": f"{field_label} / {detail}",
                "field": field,
                "target": target,
                "queue": queue,
                "prio": prio,
                "color": QUEUE_MONITOR_COLORS[len(series_map) % len(QUEUE_MONITOR_COLORS)],
            }
        timestamp_key = row_time.isoformat()
        point = data_map.setdefault(timestamp_key, {"_time": timestamp_key})
        point[series_map[series_id]["key"]] = value

    series = list(series_map.values())
    data = [data_map[key] for key in sorted(data_map.keys())]
    response_data = {
        "device": serialize_monitor_device(device),
        "interface_index": interface_index,
        "range": range_value,
        "interval": interval_value,
        "series": series,
        "data": data,
        "total": len(data),
    }
    redis_client.setex(
        _monitor_cache_key("interface_history", device_id, suffix=cache_suffix),
        HISTORY_REQUEST_CACHE_SECONDS,
        json.dumps(response_data, default=str),
    )
    return response_data


@router.get("/flow/status")
async def get_flow_status():
    """查看 sFlow/NetFlow 接收与解析状态。"""
    return flow_listener.get_status()


@router.get("/flow/ip-traffic")
async def get_ip_flow_traffic(
    ip: str = Query(..., description="公网 IP 地址"),
    db: Session = Depends(get_db),
    range: str = Query("-1h", description="历史时间范围，例如 -10m/-30m/-1h/-6h"),
    interval: str = Query("30s", description="聚合间隔，例如 10s/30s/1m/5m"),
):
    """按单个公网 IP 查询 sFlow/NetFlow 聚合后的出入向流量。"""
    try:
        ip_value = ipaddress.ip_address(str(ip).strip())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="IP 地址格式不正确") from exc

    cidr = f"{ip_value}/32" if ip_value.version == 4 else f"{ip_value}/128"
    range_value = _normalize_query_range(range)
    interval_value = _normalize_query_interval(interval)
    interval_seconds = _parse_flux_duration_seconds(interval_value, 30)
    customers = _find_customers_by_public_cidr(db, cidr)

    customer_flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {range_value})
      |> filter(fn: (r) => r._measurement == "customer_ip_traffic")
      |> filter(fn: (r) => (exists r.ip and r.ip == {_flux_string(str(ip_value))}) or (not exists r.ip and r.cidr == {_flux_string(cidr)}))
      |> filter(fn: (r) => r._field == "in_bps" or r._field == "out_bps")
      |> aggregateWindow(every: {interval_value}, fn: mean, createEmpty: false)
      |> group(columns: ["_time", "_field"])
      |> sum()
      |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
      |> sort(columns: ["_time"])
    '''
    rows = influx_client.query(customer_flux)
    source = "customer"
    if not rows:
        sflow_flux = f'''
        from(bucket: "{influx_client.bucket}")
          |> range(start: {range_value})
          |> filter(fn: (r) => r._measurement == "sflow_interface_ip_traffic")
          |> filter(fn: (r) => r.ip == {_flux_string(str(ip_value))})
          |> filter(fn: (r) => r._field == "in_bps" or r._field == "out_bps")
          |> aggregateWindow(every: {interval_value}, fn: mean, createEmpty: false)
          |> group(columns: ["_time", "_field"])
          |> sum()
          |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
          |> sort(columns: ["_time"])
        '''
        rows = influx_client.query(sflow_flux)
        source = "sflow_interface"
    return {
        "ip": str(ip_value),
        "cidr": cidr,
        "customers": customers,
        "source": source,
        "range": range_value,
        "interval": interval_value,
        "interval_seconds": interval_seconds,
        "data": rows,
        "total": len(rows),
    }


@router.get("/flow/interface-top-ips")
async def get_interface_top_ips(
    agent_ip: str = Query(..., description="sFlow Agent/设备 IP"),
    interface_index: int = Query(..., ge=1, description="接口 ifIndex"),
    range: str = Query("-10m", description="历史时间范围，例如 -10m/-30m/-1h"),
    interval: str = Query("10s", description="聚合间隔，例如 10s/30s/1m"),
    limit: int = Query(20, ge=1, le=100),
):
    """按 sFlow 采样接口统计 Top IP 带宽排行。"""
    try:
        agent_value = str(ipaddress.ip_address(str(agent_ip).strip()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="设备 IP 地址格式不正确") from exc

    range_value = _normalize_query_range(range, "-10m")
    interval_value = _normalize_query_interval(interval, "10s")
    interval_seconds = _parse_flux_duration_seconds(interval_value, 10)

    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {range_value})
      |> filter(fn: (r) => r._measurement == "sflow_interface_ip_traffic")
      |> filter(fn: (r) => r.agent_ip == {_flux_string(agent_value)})
      |> filter(fn: (r) => r.interface_index == {_flux_string(interface_index)})
      |> filter(fn: (r) => r._field == "in_bps" or r._field == "out_bps" or r._field == "total_bps")
      |> aggregateWindow(every: {interval_value}, fn: mean, createEmpty: false)
      |> group(columns: ["ip", "_field"])
      |> mean()
      |> pivot(rowKey: ["ip"], columnKey: ["_field"], valueColumn: "_value")
      |> map(fn: (r) => ({{
          r with
          in_bps: if exists r.in_bps then r.in_bps else 0.0,
          out_bps: if exists r.out_bps then r.out_bps else 0.0,
          total_bps: if exists r.total_bps then r.total_bps else 0.0
      }}))
      |> sort(columns: ["total_bps"], desc: true)
      |> limit(n: {limit})
    '''
    rows = influx_client.query(flux)
    rows = sorted(rows, key=lambda item: float(item.get("total_bps") or 0), reverse=True)[:limit]
    return {
        "agent_ip": agent_value,
        "interface_index": interface_index,
        "range": range_value,
        "interval": interval_value,
        "interval_seconds": interval_seconds,
        "items": rows,
        "total": len(rows),
    }


@router.get("/flow/interface-ip-series")
async def get_interface_ip_series(
    agent_ip: str = Query(..., description="sFlow Agent/设备 IP"),
    interface_index: int = Query(..., ge=1, description="接口 ifIndex"),
    range: str = Query("-10m", description="历史时间范围，例如 -10m/-30m/-1h"),
    interval: str = Query("10s", description="聚合间隔，例如 10s/30s/1m"),
    limit: int = Query(10, ge=1, le=20),
    ip: Optional[str] = Query(None, description="指定 IP，留空则展示 Top IP"),
):
    """按接口返回 IP 带宽排行和折线图数据。"""
    try:
        agent_value = str(ipaddress.ip_address(str(agent_ip).strip()))
    except ValueError as exc:
        raise HTTPException(status_code=400, detail="设备 IP 地址格式不正确") from exc

    target_ip = None
    if ip:
        try:
            target_ip = str(ipaddress.ip_address(str(ip).strip()))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail="IP 地址格式不正确") from exc

    range_value = _normalize_query_range(range, "-10m")
    interval_value = _normalize_query_interval(interval, "10s")
    interval_seconds = _parse_flux_duration_seconds(interval_value, 10)

    top_flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {range_value})
      |> filter(fn: (r) => r._measurement == "sflow_interface_ip_traffic")
      |> filter(fn: (r) => r.agent_ip == {_flux_string(agent_value)})
      |> filter(fn: (r) => r.interface_index == {_flux_string(interface_index)})
      |> filter(fn: (r) => r._field == "total_bps")
      |> aggregateWindow(every: {interval_value}, fn: mean, createEmpty: false)
      |> group(columns: ["ip"])
      |> mean()
      |> keep(columns: ["ip", "_value"])
    '''
    all_top_rows = []
    for row in influx_client.query(top_flux):
        ip_text = row.get("ip")
        if not ip_text:
            continue
        all_top_rows.append({
            "ip": str(ip_text),
            "in_bps": 0.0,
            "out_bps": 0.0,
            "total_bps": float(row.get("_value") or row.get("value") or 0),
        })
    all_top_rows = sorted(all_top_rows, key=lambda item: float(item.get("total_bps") or 0), reverse=True)
    for index, item in enumerate(all_top_rows, start=1):
        item["rank"] = index
    selected_rank = None
    selected_row = None
    if target_ip:
        for item in all_top_rows:
            if item.get("ip") == target_ip:
                selected_rank = int(item.get("rank") or 0)
                selected_row = item
                break
    top_rows = [selected_row] if selected_row else all_top_rows[:limit]
    selected_ips = [target_ip] if target_ip else [str(item.get("ip")) for item in top_rows if item.get("ip")]
    selected_ips = [item for item in dict.fromkeys(selected_ips) if item]
    if not selected_ips:
        return {
            "agent_ip": agent_value,
            "interface_index": interface_index,
            "range": range_value,
            "interval": interval_value,
            "interval_seconds": interval_seconds,
            "top_ips": top_rows,
            "selected_ip": target_ip,
            "selected_rank": None,
            "series": [],
            "total": 0,
        }

    ip_filter = " or ".join([f'r.ip == {_flux_string(item)}' for item in selected_ips])
    series_flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: {range_value})
      |> filter(fn: (r) => r._measurement == "sflow_interface_ip_traffic")
      |> filter(fn: (r) => r.agent_ip == {_flux_string(agent_value)})
      |> filter(fn: (r) => r.interface_index == {_flux_string(interface_index)})
      |> filter(fn: (r) => {ip_filter})
      |> filter(fn: (r) => r._field == "total_bps")
      |> aggregateWindow(every: {interval_value}, fn: mean, createEmpty: false)
      |> keep(columns: ["_time", "_value", "ip"])
      |> sort(columns: ["_time"])
    '''
    return {
        "agent_ip": agent_value,
        "interface_index": interface_index,
        "range": range_value,
        "interval": interval_value,
        "interval_seconds": interval_seconds,
        "top_ips": top_rows,
        "selected_ip": target_ip,
        "selected_rank": selected_rank,
        "series": influx_client.query(series_flux),
        "total": len(top_rows),
    }


@router.get("/monitoring/devices/{device_id}/asternos/summary")
async def get_asternos_device_summary(device_id: int, db: Session = Depends(get_db)):
    """获取 AsterNOS Exporter 直连模式的设备汇总指标。"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if get_effective_monitor_source(device) != "asternos_exporter":
        raise HTTPException(status_code=400, detail="该设备不是 AsterNOS Exporter 直连模式")
    if device.status not in {"active", "online"} or not device.is_monitored:
        raise HTTPException(status_code=400, detail="该设备未加入监控，或当前不是上线状态")
    try:
        summary = await asternos_exporter_client.get_device_metrics(device)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"AsterNOS Exporter 数据读取失败：{exc}") from exc
    return {
        "device": serialize_monitor_device(device),
        "summary": summary,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/monitoring/devices/overview")
async def get_monitor_devices_overview(
    search: Optional[str] = Query(None, description="按名称/IP/厂商搜索"),
    vendor: Optional[str] = Query(None, description="厂商过滤"),
    model: Optional[str] = Query(None, description="型号过滤"),
    connectivity: Optional[str] = Query(None, description="连通性过滤"),
    monitored_only: bool = Query(True, description="只展示已加入监控的设备"),
    include_storage: bool = Query(False, description="是否查询存储数据"),
    include_hardware: bool = Query(False, description="是否查询风扇/电源数据"),
    include_sessions: bool = Query(False, description="是否查询会话数据"),
    limit: int = Query(500, ge=1, le=1000),
    db: Session = Depends(get_db),
):
    """设备总览：汇总设备资源、监控连通性和路由协议状态。"""
    # 设备总览是高频入口页。这里缓存“全量首屏快照”，再在内存中过滤搜索条件，
    # 避免用户第一次进入、搜索或取消搜索时反复触发重聚合。
    snapshot_cache_key = ":".join([
        DEVICE_OVERVIEW_SNAPSHOT_CACHE_PREFIX,
        str(int(monitored_only)),
        str(int(include_storage)),
        str(int(include_hardware)),
        str(int(include_sessions)),
        str(limit),
    ])
    last_success_cache_key = ":".join([
        "monitor:cache:last_success_overview_snapshot",
        str(int(monitored_only)),
        str(int(include_storage)),
        str(int(include_hardware)),
        str(int(include_sessions)),
        str(limit),
    ])
    cached_snapshot = redis_client.get(snapshot_cache_key)
    stale_snapshot = False
    if not cached_snapshot:
        cached_snapshot = redis_client.get(last_success_cache_key)
        stale_snapshot = bool(cached_snapshot)
    if cached_snapshot:
        try:
            payload = json.loads(cached_snapshot)
            items = payload.get("items", []) if isinstance(payload, dict) else []
            keyword = (search or "").strip().lower()
            vendor_keyword = (vendor or "").strip().lower()
            model_keyword = (model or "").strip().lower()
            filtered_items = []
            for item in items:
                device = item.get("device") or {}
                if connectivity and (item.get("connectivity") or {}).get("status") != connectivity:
                    continue
                if vendor_keyword and vendor_keyword not in str(device.get("vendor") or "").lower():
                    continue
                if model_keyword and model_keyword not in str(device.get("model") or "").lower():
                    continue
                if keyword:
                    system_info = item.get("system_info") or {}
                    datacenter = device.get("datacenter") or {}
                    haystack = " ".join(
                        str(value)
                        for value in [
                            device.get("name"),
                            device.get("hostname"),
                            device.get("ip_address"),
                            device.get("vendor"),
                            device.get("model"),
                            datacenter.get("name") if isinstance(datacenter, dict) else None,
                            system_info.get("sys_name"),
                            system_info.get("snmp_model"),
                            system_info.get("software_version"),
                            system_info.get("serial_number"),
                        ]
                        if value
                    ).lower()
                    if keyword not in haystack:
                        continue
                filtered_items.append(item)
            return {"items": filtered_items, "total": len(filtered_items), "cached": True, "stale": stale_snapshot}
        except Exception:
            redis_client.delete(snapshot_cache_key)

    query = db.query(Device).filter(Device.status.in_(["active", "online"]))
    if monitored_only:
        query = query.filter(Device.is_monitored == True)

    devices = query.order_by(Device.ip_address.asc()).limit(limit).all()
    items: List[Dict[str, Any]] = []
    controller_data = await _load_controller_overview_fallbacks()
    for device in devices:
        item = _device_base_overview(device)
        if not device.is_monitored:
            item["connectivity"] = {
                "type": item["monitor_source"],
                "status": "not_monitored",
                "message": "未加入监控",
            }
            items.append(item)
            continue

        if get_effective_monitor_source(device) == "asternos_exporter":
            cached_overview = _load_monitor_cache("overview", device.id)
            if isinstance(cached_overview, dict):
                item.update(cached_overview)
            else:
                # 缓存缺失时同步补齐完整总览，避免首次进入只显示连通性、资源列短暂为“-”。
                try:
                    overview = await _build_asternos_overview(device)
                    overview["collected_at"] = datetime.now(timezone.utc).isoformat()
                    redis_client.setex(
                        _monitor_cache_key("overview", device.id),
                        MONITOR_CACHE_STALE_SECONDS,
                        json.dumps(overview),
                    )
                    redis_client.set(f"asternos_collect:status:{device.id}", "reachable")
                    item.update(overview)
                except Exception as exc:
                    item["connectivity"] = {
                        "type": "exporter",
                        "status": "unreachable",
                        "message": str(exc),
                    }
                    redis_client.set(f"asternos_collect:status:{device.id}", "unreachable")
        else:
            cached_overview = _load_monitor_cache("overview", device.id)
            if isinstance(cached_overview, dict):
                item.update(cached_overview)
                snmp_status = redis_client.get(_snmp_status_key(device.id)) or "unknown"
                if snmp_status == SNMP_STATUS_UNREACHABLE:
                    item["connectivity"] = {
                        "type": "snmp",
                        "status": "unreachable",
                        "message": "SNMP最近采集不可达，当前展示最近一次后台采集结果",
                    }
            else:
                overview = _build_snmp_overview(
                    device,
                    include_storage=include_storage,
                    include_hardware=include_hardware,
                    include_sessions=include_sessions,
                )
                overview["collected_at"] = datetime.now(timezone.utc).isoformat()
                redis_client.setex(
                    _monitor_cache_key("overview", device.id),
                    MONITOR_CACHE_STALE_SECONDS,
                    json.dumps(overview),
                )
                item.update(overview)
        _ensure_snmp_system_info_model(item)
        _apply_controller_overview_fallback(item, controller_data)
        item["collected_at"] = item.get("collected_at") or datetime.now(timezone.utc).isoformat()
        items.append(item)

    payload = {"items": items, "total": len(items)}
    redis_client.setex(
        snapshot_cache_key,
        DEVICE_OVERVIEW_RESPONSE_CACHE_SECONDS,
        json.dumps(payload, ensure_ascii=False, default=str),
    )
    redis_client.setex(
        last_success_cache_key,
        24 * 60 * 60,
        json.dumps(payload, ensure_ascii=False, default=str),
    )

    # 复用同一份快照过滤本次请求，避免首次带搜索条件访问时漏掉过滤逻辑。
    keyword = (search or "").strip().lower()
    vendor_keyword = (vendor or "").strip().lower()
    model_keyword = (model or "").strip().lower()
    if keyword or vendor_keyword or model_keyword or connectivity:
        filtered_items = []
        for item in items:
            device = item.get("device") or {}
            if connectivity and (item.get("connectivity") or {}).get("status") != connectivity:
                continue
            if vendor_keyword and vendor_keyword not in str(device.get("vendor") or "").lower():
                continue
            if model_keyword and model_keyword not in str(device.get("model") or "").lower():
                continue
            if keyword:
                system_info = item.get("system_info") or {}
                datacenter = device.get("datacenter") or {}
                haystack = " ".join(
                    str(value)
                    for value in [
                        device.get("name"),
                        device.get("hostname"),
                        device.get("ip_address"),
                        device.get("vendor"),
                        device.get("model"),
                        datacenter.get("name") if isinstance(datacenter, dict) else None,
                        system_info.get("sys_name"),
                        system_info.get("snmp_model"),
                        system_info.get("software_version"),
                        system_info.get("serial_number"),
                    ]
                    if value
                ).lower()
                if keyword not in haystack:
                    continue
            filtered_items.append(item)
        return {"items": filtered_items, "total": len(filtered_items), "cached": False}

    return payload


@router.post("/monitoring/devices/refresh")
async def refresh_monitor_devices_overview():
    """手动触发一次监控设备采集，用于新增/修改 SNMP 后立即刷新总览。"""
    from app.tasks.snmp_tasks import collect_all_asternos_exporter, collect_all_snmp

    snmp_task = collect_all_snmp.delay()
    asternos_task = collect_all_asternos_exporter.delay()
    return {
        "message": "已触发后台采集，数据通常会在数十秒内刷新",
        "tasks": {
            "snmp": snmp_task.id,
            "asternos_exporter": asternos_task.id,
        },
    }


@router.post("/monitoring/devices/{device_id}/refresh")
async def refresh_monitor_device(device_id: int, db: Session = Depends(get_db)):
    """手动触发单台设备采集。"""
    from app.tasks.snmp_tasks import collect_asternos_for_device, collect_snmp_for_device

    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if not device.is_monitored:
        raise HTTPException(status_code=400, detail="设备未加入监控")
    if device.status not in {"active", "online"}:
        raise HTTPException(status_code=400, detail="设备不是上线状态，无法触发采集")

    if get_effective_monitor_source(device) == "asternos_exporter":
        task = collect_asternos_for_device.delay(device.id)
        source = "asternos_exporter"
    else:
        if not device.snmp_version:
            raise HTTPException(status_code=400, detail="设备未配置SNMP参数")
        task = collect_snmp_for_device.delay(device.id)
        source = "snmp"

    return {
        "message": f"已触发 {device.ip_address} 后台采集",
        "device_id": device.id,
        "monitor_source": source,
        "task_id": task.id,
    }


@router.get("/monitoring/devices/{device_id}/protocol-neighbors")
async def get_monitor_device_protocol_neighbors(device_id: int, db: Session = Depends(get_db)):
    """查看设备 BGP/OSPF 邻居详情。"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if get_effective_monitor_source(device) == "asternos_exporter":
        cached = _load_monitor_cache("protocol_neighbors", device.id)
        if isinstance(cached, dict) and cached.get("neighbors"):
            return {
                "device": serialize_monitor_device(device),
                "neighbors": cached["neighbors"],
                "collected_at": cached.get("collected_at") or datetime.now(timezone.utc).isoformat(),
            }
        neighbors = {"bgp": [], "ospf": []}
    else:
        neighbors = _get_snmp_protocol_neighbors(device.id)
    return {
        "device": serialize_monitor_device(device),
        "neighbors": neighbors,
        "collected_at": datetime.now(timezone.utc).isoformat(),
    }


@router.get("/query")
async def query_metrics(
    measurement: str = Query(..., description="测量名称"),
    device_id: Optional[int] = None,
    field: Optional[str] = None,
    start: str = Query("-1h", description="开始时间 (Flux duration)"),
    stop: Optional[str] = None,
    aggregation: Optional[str] = None,
    interval: Optional[str] = None,
    limit: int = Query(1000, ge=1, le=10000)
):
    """查询指标数据"""
    try:
        data = influx_client.query_metrics(
            measurement=measurement,
            device_id=device_id,
            start=start,
            stop=stop,
            fields=[field] if field else None,
            aggregation=aggregation,
            interval=interval
        )
        
        return {
            "measurement": measurement,
            "device_id": device_id,
            "field": field,
            "aggregation": aggregation,
            "interval": interval,
            "data": data,
            "total": len(data)
        }
    except Exception as e:
        logger.error("查询指标失败", error=str(e))
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.post("/query")
async def query_metrics_advanced(query: MetricQuery):
    """高级指标查询"""
    try:
        # 构建Flux查询
        flux = f'from(bucket: "{influx_client.bucket}")\\n'
        
        # 时间范围
        if query.start_time and query.end_time:
            start = query.start_time.isoformat()
            stop = query.end_time.isoformat()
            flux += f'  |> range(start: {start}, stop: {stop})\\n'
        else:
            flux += f'  |> range(start: -1h)\\n'
        
        # 测量过滤
        if query.metric_type:
            flux += f'  |> filter(fn: (r) => r._measurement == "{query.metric_type}")\\n'
        
        # 设备过滤
        if query.device_ids:
            if len(query.device_ids) == 1:
                flux += f'  |> filter(fn: (r) => r.device_id == "{query.device_ids[0]}")\\n'
            else:
                ids_filter = ' or '.join([f'r.device_id == "{id}"' for id in query.device_ids])
                flux += f'  |> filter(fn: (r) => {ids_filter})\\n'
        
        # 字段过滤
        if query.field:
            flux += f'  |> filter(fn: (r) => r._field == "{query.field}")\\n'
        
        # 聚合
        if query.aggregation and query.interval:
            flux += f'  |> aggregateWindow(every: {query.interval}, fn: {query.aggregation.value}, createEmpty: false)\\n'
        
        # 限制
        flux += f'  |> limit(n: {query.limit})\\n'
        flux += '  |> yield(name: "result")'
        
        data = influx_client.query(flux)
        
        return {
            "metric_type": query.metric_type,
            "field": query.field,
            "aggregation": query.aggregation.value if query.aggregation else None,
            "interval": query.interval,
            "data": data,
            "total": len(data)
        }
    except Exception as e:
        logger.error("高级查询失败", error=str(e))
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.get("/latest/{device_id}")
async def get_latest_metrics(
    device_id: int,
    measurement: Optional[str] = None
):
    """获取设备最新指标"""
    try:
        measurements = [measurement] if measurement else [
            "snmp_cpu", "snmp_memory", "snmp_traffic", "device_status"
        ]
        
        results = {}
        for m in measurements:
            flux = f'''
            from(bucket: "{influx_client.bucket}")
              |> range(start: -5m)
              |> filter(fn: (r) => r._measurement == "{m}")
              |> filter(fn: (r) => r.device_id == "{device_id}")
              |> last()
            '''
            data = influx_client.query(flux)
            if data:
                results[m] = data
        
        return results
    except Exception as e:
        logger.error("获取最新指标失败", error=str(e))
        raise HTTPException(status_code=500, detail=f"查询失败: {str(e)}")


@router.get("/dashboard/stats")
async def get_dashboard_stats(db: Session = Depends(get_db)):
    """获取Dashboard统计数据"""
    cache_key = "dashboard:stats:v2"
    try:
        cached = redis_client.get(cache_key)
        if cached:
            return json.loads(cached)
    except Exception as e:
        logger.warning("读取Dashboard缓存失败", error=str(e))

    try:
        # 设备统计
        total_devices = db.query(Device).count()
        online_devices = db.query(Device).filter(
            Device.status.in_(["active", "online"])
        ).count()
        offline_devices = db.query(Device).filter(
            Device.status.in_(["inactive", "offline"])
        ).count()

        status_labels = {
            "active": "上线",
            "online": "上线",
            "inactive": "离线",
            "offline": "离线",
            "in_stock": "库存",
            "deployed": "上架",
        }
        status_order = ["上线", "离线", "库存", "上架", "其他"]
        status_counts = {label: 0 for label in status_order}
        for status, count in db.query(Device.status, func.count(Device.id)).group_by(Device.status).all():
            label = status_labels.get(status or "", "其他")
            status_counts[label] = status_counts.get(label, 0) + int(count or 0)
        device_status_distribution = [
            {"name": label, "value": status_counts.get(label, 0)}
            for label in status_order
            if status_counts.get(label, 0) > 0
        ]
        
        # 告警统计
        total_alerts_firing = db.query(AlertHistory).filter(
            AlertHistory.status == "firing"
        ).count()

        # 资源统计
        public_circuits = db.query(Circuit).filter(Circuit.line_type == "internet").count()
        private_circuits = db.query(Circuit).filter(Circuit.line_type == "private_line").count()

        def rows_by_datacenter(query_rows):
            rows = []
            for name, count in query_rows:
                rows.append({"name": name or "未分配机房", "value": int(count or 0)})
            return sorted(rows, key=lambda item: item["value"], reverse=True)

        device_by_datacenter = rows_by_datacenter(
            db.query(Datacenter.name, func.count(Device.id))
            .select_from(Device)
            .outerjoin(Datacenter, Device.datacenter_id == Datacenter.id)
            .group_by(Datacenter.name)
            .all()
        )
        public_circuit_by_datacenter = rows_by_datacenter(
            db.query(Datacenter.name, func.count(Circuit.id))
            .select_from(Circuit)
            .outerjoin(Datacenter, Circuit.datacenter_id == Datacenter.id)
            .filter(Circuit.line_type == "internet")
            .group_by(Datacenter.name)
            .all()
        )
        private_circuit_by_datacenter = rows_by_datacenter(
            db.query(Datacenter.name, func.count(Circuit.id))
            .select_from(Circuit)
            .outerjoin(Datacenter, Circuit.datacenter_id == Datacenter.id)
            .filter(Circuit.line_type == "private_line")
            .group_by(Datacenter.name)
            .all()
        )
        
        # 前端仪表盘已不展示“最近告警”，这里不再查询大表，避免切换仪表盘时被告警历史拖慢。
        recent_alerts_data = []

        # 指标数量统计曾经从 InfluxDB 做 count()，在高基数时会让仪表盘首屏等待十几秒。
        # 当前前端不展示这两个数字，保留字段但不做重查询。
        snmp_count = 0
        gnmi_count = 0

        payload = {
            "total_devices": total_devices,
            "online_devices": online_devices,
            "offline_devices": offline_devices,
            "total_alerts_firing": total_alerts_firing,
            "public_circuits": public_circuits,
            "private_circuits": private_circuits,
            "device_status_distribution": device_status_distribution,
            "asset_by_datacenter": {
                "devices": device_by_datacenter,
                "public_circuits": public_circuit_by_datacenter,
                "private_circuits": private_circuit_by_datacenter,
            },
            "snmp_metrics_count": snmp_count,
            "gnmi_metrics_count": gnmi_count,
            "recent_alerts": recent_alerts_data
        }
        try:
            redis_client.setex(cache_key, DASHBOARD_STATS_CACHE_SECONDS, json.dumps(payload, ensure_ascii=False))
        except Exception as e:
            logger.warning("写入Dashboard缓存失败", error=str(e))
        return payload
    except Exception as e:
        logger.error("获取Dashboard统计失败", error=str(e))
        raise HTTPException(status_code=500, detail=f"获取统计失败: {str(e)}")


@router.get("/server/resources")
async def get_server_resources():
    """获取当前部署服务器的资源使用率"""
    try:
        cpu_percent = psutil.cpu_percent(interval=0.2)
        memory = psutil.virtual_memory()
        disk = psutil.disk_usage("/")
        load_avg = os.getloadavg() if hasattr(os, "getloadavg") else None
        boot_time = datetime.fromtimestamp(psutil.boot_time(), timezone.utc)

        return {
            "hostname": socket.gethostname(),
            "platform": platform.platform(),
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "uptime_seconds": int((datetime.now(timezone.utc) - boot_time).total_seconds()),
            "cpu": {
                "percent": round(cpu_percent, 2),
                "cores": psutil.cpu_count(logical=True) or 0,
                "physical_cores": psutil.cpu_count(logical=False) or 0,
                "load_avg": [round(value, 2) for value in load_avg] if load_avg else None,
            },
            "memory": {
                "total": memory.total,
                "used": memory.used,
                "available": memory.available,
                "percent": round(memory.percent, 2),
            },
            "disk": {
                "path": "/",
                "total": disk.total,
                "used": disk.used,
                "free": disk.free,
                "percent": round(disk.percent, 2),
            },
        }
    except Exception as e:
        logger.error("获取服务器资源失败", error=str(e))
        raise HTTPException(status_code=500, detail=f"获取服务器资源失败: {str(e)}")


@router.get("/measurements")
async def list_measurements():
    """列出所有测量名称"""
    try:
        flux = f'''
        import "influxdata/influxdb/schema"
        schema.measurements(bucket: "{influx_client.bucket}")
        '''
        result = influx_client.query(flux)
        measurements = [r.get('_value') for r in result if '_value' in r]
        return {"measurements": measurements}
    except Exception as e:
        logger.error("获取测量列表失败", error=str(e))
        return {"measurements": []}


@router.get("/fields/{measurement}")
async def list_fields(measurement: str):
    """列出测量的所有字段"""
    try:
        flux = f'''
        import "influxdata/influxdb/schema"
        schema.measurementFieldKeys(bucket: "{influx_client.bucket}", measurement: "{measurement}")
        '''
        result = influx_client.query(flux)
        fields = [r.get('_value') for r in result if '_value' in r]
        return {"measurement": measurement, "fields": fields}
    except Exception as e:
        logger.error("获取字段列表失败", error=str(e))
        return {"measurement": measurement, "fields": []}
