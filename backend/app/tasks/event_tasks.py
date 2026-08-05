"""Realtime event ingestion tasks.

UDP listeners should acknowledge the kernel quickly.  Database writes, alert
matching and notifications therefore run in dedicated Celery queues.
"""
from __future__ import annotations

import base64
import time
from typing import Any, Callable

from celery import shared_task

from app.core import get_logger
from app.utils import redis_client


logger = get_logger(__name__)
EVENT_METRIC_TTL_SECONDS = 7 * 24 * 60 * 60
EVENT_DEDUP_TTL_SECONDS = 24 * 60 * 60


def _metric_key(stream: str, field: str) -> str:
    return f"queue:{stream}:{field}"


def record_event_queue_metric(stream: str, field: str, value: Any | None = None) -> None:
    """Best-effort self-monitoring; event processing must not depend on Redis metrics."""
    try:
        key = _metric_key(stream, field)
        if value is None:
            redis_client.incr(key)
        else:
            redis_client.set(key, value, ex=EVENT_METRIC_TTL_SECONDS)
        redis_client.expire(key, EVENT_METRIC_TTL_SECONDS)
    except Exception:
        pass


def _process_once(stream: str, event_id: str, callback: Callable[[], None]) -> dict[str, Any]:
    done_key = f"queue:{stream}:processed_event:{event_id}"
    lock_key = f"queue:{stream}:processing_event:{event_id}"
    try:
        if redis_client.exists(done_key):
            record_event_queue_metric(stream, "duplicate")
            return {"status": "duplicate", "event_id": event_id}
        acquired = redis_client.set(lock_key, "1", nx=True, ex=600)
        if not acquired:
            record_event_queue_metric(stream, "duplicate")
            return {"status": "processing", "event_id": event_id}
    except Exception:
        acquired = False

    started = time.monotonic()
    try:
        callback()
        try:
            redis_client.set(done_key, "1", ex=EVENT_DEDUP_TTL_SECONDS)
        except Exception:
            pass
        record_event_queue_metric(stream, "processed")
        record_event_queue_metric(stream, "last_processed_at", int(time.time()))
        record_event_queue_metric(stream, "last_latency_ms", int((time.monotonic() - started) * 1000))
        return {"status": "processed", "event_id": event_id}
    except Exception as exc:
        record_event_queue_metric(stream, "failed")
        record_event_queue_metric(stream, "last_error", str(exc)[:500])
        logger.exception("实时事件异步处理失败", stream=stream, event_id=event_id)
        raise
    finally:
        if acquired:
            try:
                redis_client.delete(lock_key)
            except Exception:
                pass


@shared_task(
    bind=True,
    name="app.tasks.event_tasks.process_syslog_event",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def process_syslog_event(
    self,
    source_ip: str,
    raw_message: str,
    event_id: str,
    received_at: float | None = None,
) -> dict[str, Any]:
    from app.services.syslog_listener import _persist_syslog_event

    return _process_once("events_syslog", event_id, lambda: _persist_syslog_event(source_ip, raw_message))


@shared_task(
    bind=True,
    name="app.tasks.event_tasks.process_snmp_trap_event",
    acks_late=True,
    reject_on_worker_lost=True,
    ignore_result=True,
    autoretry_for=(ConnectionError, TimeoutError),
    retry_backoff=True,
    retry_kwargs={"max_retries": 3},
)
def process_snmp_trap_event(
    self,
    source_ip: str,
    payload_b64: str,
    event_id: str,
    received_at: float | None = None,
) -> dict[str, Any]:
    from app.services.snmp_trap_listener import _handle_trap_datagram

    payload = base64.b64decode(payload_b64.encode("ascii"), validate=True)
    return _process_once("events_trap", event_id, lambda: _handle_trap_datagram(source_ip, payload))
