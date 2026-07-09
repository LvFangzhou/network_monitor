"""
Public quality probe helpers.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core import get_logger
from app.utils import influx_client

try:
    from ping3 import ping
    PING3_AVAILABLE = True
except ImportError:
    ping = None
    PING3_AVAILABLE = False


logger = get_logger(__name__)


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
