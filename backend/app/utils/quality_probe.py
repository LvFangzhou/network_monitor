"""
Public quality probe helpers.
"""
from __future__ import annotations

import time
import json
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
            },
            fields={
                "success": bool(result.get("success")),
                "avg_latency_ms": result.get("avg_latency_ms"),
                "min_latency_ms": result.get("min_latency_ms"),
                "max_latency_ms": result.get("max_latency_ms"),
                "jitter_ms": result.get("jitter_ms"),
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
