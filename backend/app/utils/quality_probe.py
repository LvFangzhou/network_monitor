"""
Public quality probe helpers.
"""
from __future__ import annotations

import time
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core import get_logger
from app.utils import influx_client, redis_client

try:
    from ping3 import ping
    PING3_AVAILABLE = True
except ImportError:
    ping = None
    PING3_AVAILABLE = False


logger = get_logger(__name__)
QUALITY_LOSS_WINDOW_SECONDS = 5 * 60
QUALITY_LOSS_WINDOW_MAX_SAMPLES = 1200
QUALITY_LOSS_WINDOW_TTL_SECONDS = 24 * 60 * 60
DISMAN_PING_RESULTS_ENTRY_OID = "1.3.6.1.2.1.80.1.3.1"
DISMAN_PING_CONTROL_ENTRY_OID = "1.3.6.1.2.1.80.1.2.1"


def _decode_disman_ping_index(parts: List[int]) -> tuple[str, str] | None:
    """Decode pingCtlOwnerIndex/pingCtlTestName from an OID suffix."""
    try:
        owner_length = parts[0]
        owner_end = 1 + owner_length
        owner = bytes(parts[1:owner_end]).decode("utf-8", errors="replace")
        tag_length = parts[owner_end]
        tag_start = owner_end + 1
        tag = bytes(parts[tag_start:tag_start + tag_length]).decode("utf-8", errors="replace")
        return owner, tag
    except (IndexError, TypeError, ValueError):
        return None


def _encode_disman_ping_index(owner: str, operation_tag: str) -> str:
    owner_bytes = str(owner).encode("utf-8")
    tag_bytes = str(operation_tag).encode("utf-8")
    parts = [len(owner_bytes), *owner_bytes, len(tag_bytes), *tag_bytes]
    return ".".join(str(part) for part in parts)


def _read_h3c_native_jitter(
    collector: Any,
    device: Any,
    owner: str,
    operation_tag: str,
) -> Dict[str, Any]:
    """Read native H3C ICMP-jitter statistics (legacy and new enterprise IDs)."""
    index_suffix = _encode_disman_ping_index(owner, operation_tag)
    type_oid = collector.snmp_get(
        device,
        f"{DISMAN_PING_CONTROL_ENTRY_OID}.16.{index_suffix}",
    )
    type_text = str(type_oid or "").strip().lstrip(".")
    root_match = re.match(r"^(1\.3\.6\.1\.4\.1\.\d+\.8\.3)\.2\.3$", type_text)
    if not root_match:
        return {}

    table_oid = f"{root_match.group(1)}.1.4.1"
    rows = collector.snmp_walk(device, table_oid) or []
    values: Dict[int, Any] = {}
    prefix = table_oid + "."
    for oid, value in rows:
        oid_text = str(oid or "").lstrip(".")
        if not oid_text.startswith(prefix) or "No Such" in str(value):
            continue
        try:
            suffix = [int(part) for part in oid_text[len(prefix):].split(".")]
            column = suffix[0]
        except (TypeError, ValueError, IndexError):
            continue
        if _decode_disman_ping_index(suffix[1:]) == (owner, operation_tag):
            values[column] = value

    def _number(column: int) -> float | None:
        try:
            return float(values[column])
        except (KeyError, TypeError, ValueError):
            return None

    if not values:
        return {}
    jitter_sd = _number(31)
    jitter_ds = _number(32)
    directional = [value for value in (jitter_sd, jitter_ds) if value is not None]
    return {
        "jitter_ms": round(max(directional), 2) if directional else None,
        "jitter_sd_ms": jitter_sd,
        "jitter_ds_ms": jitter_ds,
        "jitter_source": "h3c_nqa_native",
        "jitter_rtt_samples": int(_number(1) or 0),
        "packet_loss_sd": int(_number(22) or 0),
        "packet_loss_ds": int(_number(23) or 0),
    }


def discover_quality_nqa_snmp_instances(device: Any) -> List[Dict[str, Any]]:
    """Discover configured DISMAN-PING-MIB NQA jobs and their latest results."""
    from app.collectors.snmp_collector import SNMPCollector

    collector = SNMPCollector()
    control_rows = collector.snmp_walk(device, DISMAN_PING_CONTROL_ENTRY_OID) or []
    result_rows = collector.snmp_walk(device, DISMAN_PING_RESULTS_ENTRY_OID) or []
    instances: Dict[tuple[str, str], Dict[str, Any]] = {}

    def _consume(rows: List[Any], base_oid: str, bucket: str) -> None:
        prefix = base_oid + "."
        for oid, value in rows:
            oid_text = str(oid or "")
            if not oid_text.startswith(prefix) or "No Such" in str(value):
                continue
            try:
                suffix = [int(part) for part in oid_text[len(prefix):].split(".")]
                column = suffix[0]
            except (TypeError, ValueError, IndexError):
                continue
            index = _decode_disman_ping_index(suffix[1:])
            if not index:
                continue
            item = instances.setdefault(index, {"admin_name": index[0], "operation_tag": index[1]})
            item.setdefault(bucket, {})[column] = value

    _consume(control_rows, DISMAN_PING_CONTROL_ENTRY_OID, "control")
    _consume(result_rows, DISMAN_PING_RESULTS_ENTRY_OID, "result")

    output: List[Dict[str, Any]] = []
    for (admin_name, operation_tag), item in sorted(instances.items()):
        control = item.get("control", {})
        result = item.get("result", {})

        def _number(values: Dict[int, Any], column: int) -> float | None:
            try:
                return float(values[column])
            except (KeyError, TypeError, ValueError):
                return None

        sent = int(_number(result, 8) or 0)
        received = max(0, min(int(_number(result, 7) or 0), sent)) if sent else 0
        loss = round((sent - received) * 100.0 / sent, 2) if sent else None
        frequency = int(_number(control, 10) or 0)
        timeout_seconds = _number(control, 6)
        output.append({
            "key": f"{admin_name}\u0000{operation_tag}",
            "admin_name": admin_name,
            "operation_tag": operation_tag,
            "target": str(control.get(4) or "").strip(),
            "source": str(control.get(19) or "").strip() or None,
            "frequency_seconds": frequency or None,
            "packet_count": int(_number(control, 7) or 0) or None,
            "timeout_ms": int(timeout_seconds * 1000) if timeout_seconds is not None else None,
            "is_enabled": int(_number(control, 8) or 0) == 1,
            "has_result": bool(result),
            "avg_latency_ms": _number(result, 6),
            "min_latency_ms": _number(result, 4),
            "max_latency_ms": _number(result, 5),
            "packet_loss_percent": loss,
            "sent": sent,
            "received": received,
        })
    return output


def run_quality_nqa_snmp(target: Any, device: Any) -> Dict[str, Any]:
    """Read the latest device-generated NQA result through DISMAN-PING-MIB.

    H3C ICMP-jitter uses the device-native SD/DS value when available. ICMP
    echo falls back to the absolute change between consecutive average RTTs.
    """
    from app.collectors.snmp_collector import SNMPCollector

    owner = str(getattr(target, "nqa_admin_name", None) or "test")
    operation_tag = str(getattr(target, "nqa_operation_tag", None) or "1")
    collector = SNMPCollector()
    try:
        rows = collector.snmp_walk(device, DISMAN_PING_RESULTS_ENTRY_OID) or []
    except Exception as exc:
        return {
            "success": False,
            "avg_latency_ms": None,
            "min_latency_ms": None,
            "max_latency_ms": None,
            "jitter_ms": None,
            "packet_loss_percent": 100.0,
            "availability_percent": 0.0,
            "received": 0,
            "sent": 0,
            "error": f"SNMP读取NQA失败：{exc}",
        }

    values: Dict[int, Any] = {}
    prefix = DISMAN_PING_RESULTS_ENTRY_OID + "."
    for oid, value in rows:
        oid_text = str(oid or "")
        if not oid_text.startswith(prefix) or "No Such" in str(value):
            continue
        try:
            suffix = [int(part) for part in oid_text[len(prefix):].split(".")]
            column = suffix[0]
        except (TypeError, ValueError, IndexError):
            continue
        index = _decode_disman_ping_index(suffix[1:])
        if index == (owner, operation_tag):
            values[column] = value

    def _number(column: int) -> float | None:
        try:
            return float(values[column])
        except (KeyError, TypeError, ValueError):
            return None

    min_latency = _number(4)
    max_latency = _number(5)
    avg_latency = _number(6)
    received = int(_number(7) or 0)
    sent = int(_number(8) or 0)
    if not values or sent <= 0:
        return {
            "success": False,
            "avg_latency_ms": avg_latency,
            "min_latency_ms": min_latency,
            "max_latency_ms": max_latency,
            "jitter_ms": None,
            "packet_loss_percent": 100.0 if sent > 0 else None,
            "availability_percent": 0.0 if sent > 0 else None,
            "received": received,
            "sent": sent,
            "error": f"未读取到NQA任务 {owner}/{operation_tag} 的有效结果",
        }

    received = max(0, min(received, sent))
    loss = round((sent - received) * 100.0 / sent, 2)
    availability = round(received * 100.0 / sent, 2)
    native_jitter: Dict[str, Any] = {}
    try:
        native_jitter = _read_h3c_native_jitter(collector, device, owner, operation_tag)
    except Exception as exc:
        logger.debug("读取H3C原生NQA抖动失败，使用RTT变化估算", device=getattr(device, "ip_address", None), error=str(exc))

    jitter = native_jitter.get("jitter_ms")
    jitter_source = native_jitter.get("jitter_source")
    if jitter is None and avg_latency is not None:
        previous_key = f"quality_probe:nqa_prev_latency:{target.id}"
        try:
            previous = redis_client.get(previous_key)
            if previous is not None:
                jitter = round(abs(avg_latency - float(previous)), 2)
                jitter_source = "rtt_delta_estimate"
            redis_client.set(previous_key, str(avg_latency), ex=24 * 60 * 60)
        except Exception:
            pass

    return {
        "success": received > 0,
        "avg_latency_ms": avg_latency,
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "jitter_ms": jitter,
        "packet_loss_percent": loss,
        "availability_percent": availability,
        "received": received,
        "sent": sent,
        "error": None if received > 0 else "NQA探测未收到响应",
        "probe_source": "device_nqa_snmp",
        "nqa_admin_name": owner,
        "nqa_operation_tag": operation_tag,
        "jitter_sd_ms": native_jitter.get("jitter_sd_ms"),
        "jitter_ds_ms": native_jitter.get("jitter_ds_ms"),
        "jitter_source": jitter_source,
        "jitter_rtt_samples": native_jitter.get("jitter_rtt_samples"),
    }


def run_quality_ping(target_host: str, packet_count: int = 5, timeout_ms: int = 1000) -> Dict[str, Any]:
    """Run one ICMP quality probe and return latency/loss/jitter."""
    host = str(target_host or "").strip()
    sent = max(1, min(int(packet_count or 5), 20))
    if not host:
        return {
            "success": False,
            "avg_latency_ms": None,
            "min_latency_ms": None,
            "max_latency_ms": None,
            "jitter_ms": None,
            "packet_loss_percent": 100.0,
            "availability_percent": 0.0,
            "received": 0,
            "sent": 0,
            "error": "目标地址为空",
        }
    if not PING3_AVAILABLE or ping is None:
        return {
            "success": False,
            "avg_latency_ms": None,
            "min_latency_ms": None,
            "max_latency_ms": None,
            "jitter_ms": None,
            "packet_loss_percent": 100.0,
            "availability_percent": 0.0,
            "received": 0,
            "sent": sent,
            "error": "服务器缺少 ping3 依赖，无法执行 ICMP 探测",
        }

    timeout_seconds = max(0.2, min(float(timeout_ms or 1000) / 1000.0, 10.0))
    latencies: List[float] = []
    errors: List[str] = []
    for _ in range(sent):
        try:
            result = ping(host, timeout=timeout_seconds, unit="ms")
            if result is not None and result is not False:
                latencies.append(float(result))
        except Exception as e:
            errors.append(str(e))
        time.sleep(0.05)

    received = len(latencies)
    packet_loss = round((sent - received) * 100.0 / sent, 2)
    availability = round(received * 100.0 / sent, 2)
    avg_latency = round(sum(latencies) / received, 2) if received else None
    min_latency = round(min(latencies), 2) if received else None
    max_latency = round(max(latencies), 2) if received else None
    if len(latencies) >= 2:
        diffs = [abs(latencies[index] - latencies[index - 1]) for index in range(1, len(latencies))]
        jitter = round(sum(diffs) / len(diffs), 2)
    elif len(latencies) == 1:
        jitter = 0.0
    else:
        jitter = None

    return {
        "success": received > 0,
        "avg_latency_ms": avg_latency,
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "jitter_ms": jitter,
        "packet_loss_percent": packet_loss,
        "availability_percent": availability,
        "received": received,
        "sent": sent,
        "error": None if received > 0 else (errors[-1] if errors else "目标无响应"),
    }


def apply_quality_loss_window(
    target_id: Any,
    result: Dict[str, Any],
    window_seconds: int = QUALITY_LOSS_WINDOW_SECONDS,
) -> Dict[str, Any]:
    """Convert single-probe loss into a rolling time-window loss rate.

    A 1-packet probe naturally produces either 0% or 100% loss. For the UI and
    alert snapshot we show packet loss over a recent time window instead:
    (sum(sent) - sum(received)) / sum(sent).
    """
    smoothed = dict(result or {})
    try:
        sent = int(float(smoothed.get("sent") or 0))
        received = int(float(smoothed.get("received") or 0))
    except (TypeError, ValueError):
        return smoothed
    if not target_id or sent <= 0:
        return smoothed

    now_ts = time.time()
    sample = {
        "ts": now_ts,
        "sent": sent,
        "received": max(0, min(received, sent)),
    }
    key = f"quality_probe:loss_window:{target_id}"
    raw_sample = json.dumps(sample, ensure_ascii=False)

    try:
        redis_client.lpush(key, raw_sample)
        redis_client.ltrim(key, 0, QUALITY_LOSS_WINDOW_MAX_SAMPLES - 1)
        redis_client.expire(key, QUALITY_LOSS_WINDOW_TTL_SECONDS)
        raw_items = redis_client.lrange(key, 0, QUALITY_LOSS_WINDOW_MAX_SAMPLES - 1) or []
    except Exception:
        raw_items = [raw_sample]

    cutoff = now_ts - max(30, int(window_seconds or QUALITY_LOSS_WINDOW_SECONDS))
    total_sent = 0
    total_received = 0
    for raw in raw_items:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            item = json.loads(raw)
            item_ts = float(item.get("ts") or 0)
            item_sent = int(float(item.get("sent") or 0))
            item_received = int(float(item.get("received") or 0))
        except Exception:
            continue
        if item_ts < cutoff or item_sent <= 0:
            continue
        total_sent += item_sent
        total_received += max(0, min(item_received, item_sent))

    if total_sent > 0:
        total_received = max(0, min(total_received, total_sent))
        smoothed["packet_loss_percent"] = round((total_sent - total_received) * 100.0 / total_sent, 2)
        smoothed["availability_percent"] = round(total_received * 100.0 / total_sent, 2)
        smoothed["loss_window_seconds"] = max(30, int(window_seconds or QUALITY_LOSS_WINDOW_SECONDS))
        smoothed["loss_window_sent"] = total_sent
        smoothed["loss_window_received"] = total_received

    return smoothed


def write_quality_probe_result(target: Any, result: Dict[str, Any]) -> None:
    """Write quality probe result to InfluxDB."""
    try:
        influx_client.write_point(
            "quality_probe",
            tags={
                "target_id": str(target.id),
                "target": target.target,
                "name": target.name,
                "datacenter": target.datacenter_ref.name if getattr(target, "datacenter_ref", None) else "",
                "operator": target.operator_name or "",
                "probe_source": getattr(target, "probe_source", None) or "server_icmp",
                "device_id": str(getattr(target, "device_id", None) or ""),
            },
            fields={
                "success": bool(result.get("success")),
                "avg_latency_ms": result.get("avg_latency_ms"),
                "min_latency_ms": result.get("min_latency_ms"),
                "max_latency_ms": result.get("max_latency_ms"),
                "jitter_ms": result.get("jitter_ms"),
                "jitter_sd_ms": result.get("jitter_sd_ms"),
                "jitter_ds_ms": result.get("jitter_ds_ms"),
                "packet_loss_percent": result.get("packet_loss_percent"),
                "availability_percent": result.get("availability_percent"),
                "received": result.get("received"),
                "sent": result.get("sent"),
            },
            timestamp=datetime.now(timezone.utc),
            sync=False,
        )
    except Exception as e:
        logger.warning("写入质量探测结果失败", target_id=getattr(target, "id", None), error=str(e))
