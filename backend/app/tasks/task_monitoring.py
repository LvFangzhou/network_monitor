"""Low-overhead Celery task runtime metrics for operational queues."""
from __future__ import annotations

import threading
import time
from typing import Any

from celery.signals import task_failure, task_postrun, task_prerun

from app.utils import redis_client


MONITORED_TASK_QUEUES = {
    "quality_fast",
    "quality_regular",
    "quality_mtr",
    "notification",
    "alerts_fast",
    "alerts_health",
    "snmp_circuit_realtime",
}
METRIC_TTL_SECONDS = 7 * 24 * 60 * 60
_starts: dict[str, float] = {}
_starts_lock = threading.Lock()


def _queue_for_task(task: Any) -> str | None:
    delivery = getattr(getattr(task, "request", None), "delivery_info", None) or {}
    queue = str(delivery.get("routing_key") or "").strip()
    return queue if queue in MONITORED_TASK_QUEUES else None


def _increment(queue: str, field: str) -> None:
    try:
        key = f"queue:{queue}:{field}"
        redis_client.incr(key)
        redis_client.expire(key, METRIC_TTL_SECONDS)
    except Exception:
        pass


def _set(queue: str, field: str, value: Any) -> None:
    try:
        redis_client.set(f"queue:{queue}:{field}", value, ex=METRIC_TTL_SECONDS)
    except Exception:
        pass


@task_prerun.connect
def record_task_start(task_id: str | None = None, task: Any = None, **_: Any) -> None:
    queue = _queue_for_task(task)
    if not queue or not task_id:
        return
    with _starts_lock:
        _starts[task_id] = time.monotonic()
    _set(queue, "last_started_at", int(time.time()))


@task_postrun.connect
def record_task_finish(task_id: str | None = None, task: Any = None, state: str | None = None, **_: Any) -> None:
    queue = _queue_for_task(task)
    if not queue:
        return
    started = None
    if task_id:
        with _starts_lock:
            started = _starts.pop(task_id, None)
    if started is not None:
        _set(queue, "last_latency_ms", int((time.monotonic() - started) * 1000))
    _set(queue, "last_processed_at", int(time.time()))
    if state == "SUCCESS":
        _increment(queue, "processed")


@task_failure.connect
def record_task_failure(task_id: str | None = None, sender: Any = None, exception: BaseException | None = None, **_: Any) -> None:
    queue = _queue_for_task(sender)
    if not queue:
        return
    _increment(queue, "failed")
    _set(queue, "last_error", str(exception or "unknown task failure")[:500])
