"""RabbitMQ and receiver queue health aggregation."""
from __future__ import annotations

import time
from typing import Any
from urllib.parse import quote

import httpx

from app.config import settings
from app.utils import redis_client


MONITORED_QUEUES = {
    "events_syslog": "Syslog 实时事件",
    "events_trap": "SNMP Trap 实时事件",
    "quality_fast": "质量快速故障探测",
    "quality_regular": "质量常规探测",
    "quality_mtr": "MTR 路径观察",
    "notification": "机器人通知",
    "alerts_fast": "接口快速告警",
    "alerts_health": "设备健康告警",
    "snmp_circuit_realtime": "线路接口实时采集",
}


def _number(value: Any) -> float:
    try:
        return float(value or 0)
    except (TypeError, ValueError):
        return 0.0


def _queue_status(messages: int, consumers: int) -> str:
    if messages > 0 and consumers <= 0:
        return "critical"
    if consumers <= 0:
        return "warning"
    if messages >= 1000:
        return "critical"
    if messages >= 100:
        return "warning"
    return "healthy"


def _task_metrics(name: str) -> dict[str, Any]:
    fields = ("submitted", "processed", "failed", "duplicate", "last_submitted_at", "last_processed_at", "last_latency_ms", "last_error")
    try:
        values = redis_client.mget([f"queue:{name}:{field}" for field in fields])
    except Exception:
        return {}
    result = dict(zip(fields, values))
    now = int(time.time())
    last_submitted = int(_number(result.get("last_submitted_at")))
    last_processed = int(_number(result.get("last_processed_at")))
    return {
        "submitted": int(_number(result.get("submitted"))),
        "processed": int(_number(result.get("processed"))),
        "failed": int(_number(result.get("failed"))),
        "duplicate": int(_number(result.get("duplicate"))),
        "last_latency_ms": int(_number(result.get("last_latency_ms"))),
        "processing_lag_seconds": max(0, last_submitted - last_processed) if last_submitted else 0,
        "last_activity_age_seconds": max(0, now - max(last_submitted, last_processed)) if max(last_submitted, last_processed) else None,
        "last_error": result.get("last_error") or None,
    }


def load_queue_health() -> dict[str, Any]:
    vhost = quote(settings.RABBITMQ_VHOST or "/", safe="")
    url = f"http://{settings.RABBITMQ_HOST}:{settings.RABBITMQ_MANAGEMENT_PORT}/api/queues/{vhost}"
    try:
        response = httpx.get(
            url,
            auth=(settings.RABBITMQ_USER, settings.RABBITMQ_PASSWORD),
            timeout=httpx.Timeout(3.0, connect=1.5),
        )
        response.raise_for_status()
        raw_queues = response.json()
    except Exception as exc:
        return {
            "status": "unavailable",
            "checked_at": int(time.time()),
            "message": f"RabbitMQ 队列监控暂不可用：{str(exc)[:180]}",
            "broker": {"reachable": False, "total_messages": 0, "total_ready": 0, "total_unacked": 0, "consumers": 0},
            "queues": [],
        }

    by_name = {str(row.get("name")): row for row in raw_queues if row.get("name") in MONITORED_QUEUES}
    queues = []
    for name, display_name in MONITORED_QUEUES.items():
        row = by_name.get(name, {})
        messages = int(_number(row.get("messages")))
        ready = int(_number(row.get("messages_ready")))
        unacked = int(_number(row.get("messages_unacknowledged")))
        consumers = int(_number(row.get("consumers")))
        rates = row.get("message_stats") or {}
        status = _queue_status(messages, consumers)
        queues.append({
            "name": name,
            "display_name": display_name,
            "messages": messages,
            "ready": ready,
            "unacked": unacked,
            "consumers": consumers,
            "publish_rate": round(_number((rates.get("publish_details") or {}).get("rate")), 2),
            "deliver_rate": round(_number((rates.get("deliver_get_details") or {}).get("rate")), 2),
            "state": row.get("state") or ("not_created" if not row else "unknown"),
            "status": status,
            **_task_metrics(name),
        })

    statuses = {item["status"] for item in queues}
    overall = "critical" if "critical" in statuses else "warning" if "warning" in statuses else "healthy"
    return {
        "status": overall,
        "checked_at": int(time.time()),
        "message": None,
        "broker": {
            "reachable": True,
            "total_messages": sum(item["messages"] for item in queues),
            "total_ready": sum(item["ready"] for item in queues),
            "total_unacked": sum(item["unacked"] for item in queues),
            "consumers": sum(item["consumers"] for item in queues),
        },
        "queues": queues,
    }
