"""
Menu first-screen cache prewarm tasks.

These tasks keep the data used by common menu landing pages warm in Redis, so
interactive users normally read cached data instead of paying the aggregation
cost when they log in or switch menus.
"""
from __future__ import annotations

import os
import time
from typing import Iterable, List

import httpx
from celery import shared_task

from app.core import get_logger
from app.database import SessionLocal
from app.models.resource import Circuit
from app.utils import redis_client

logger = get_logger(__name__)

API_BASE_URL = os.getenv("MENU_CACHE_PREWARM_API_BASE_URL", "http://api:8000/api/v1").rstrip("/")
PREWARM_LOCK_TTL_SECONDS = 55


def _prewarm_endpoint(path: str, *, timeout: float = 30.0) -> dict:
    url = f"{API_BASE_URL}{path}"
    started_at = time.time()
    response = httpx.get(url, timeout=timeout)
    elapsed = round(time.time() - started_at, 3)
    response.raise_for_status()
    return {
        "path": path,
        "status_code": response.status_code,
        "elapsed_seconds": elapsed,
        "bytes": len(response.content or b""),
    }


def _prewarm(paths: Iterable[str], delete_keys: Iterable[str] = ()) -> dict:
    for key in delete_keys:
        redis_client.delete(key)

    results: List[dict] = []
    errors: List[dict] = []
    for path in paths:
        try:
            results.append(_prewarm_endpoint(path))
        except Exception as exc:
            logger.warning("菜单缓存预热失败", path=path, error=str(exc))
            errors.append({"path": path, "error": str(exc)})
    return {
        "results": results,
        "errors": errors,
    }


def _run_with_lock(lock_key: str, func):
    lock_value = str(time.time())
    if not redis_client.set(lock_key, lock_value, ex=PREWARM_LOCK_TTL_SECONDS, nx=True):
        return {"skipped": True, "reason": "previous prewarm still running"}
    try:
        return func()
    finally:
        try:
            current_value = redis_client.get(lock_key)
            if isinstance(current_value, bytes):
                current_value = current_value.decode()
            if current_value == lock_value:
                redis_client.delete(lock_key)
        except Exception:
            pass


@shared_task
def prewarm_fast_menu_caches():
    """Prewarm lightweight menu caches that users touch frequently."""
    return _run_with_lock(
        "menu_cache:prewarm:fast:lock",
        lambda: _prewarm(
            [
                "/metrics/dashboard/stats?refresh=true",
                "/alerts/history/summary?refresh=true",
                "/config-backups/filters",
            ],
            delete_keys=[
                "dashboard:stats:v2",
                "config_backups:filters:v1",
            ],
        ),
    )


@shared_task
def prewarm_device_overview_cache():
    """Prewarm the heavier device overview landing page cache."""
    return _run_with_lock(
        "menu_cache:prewarm:device_overview:lock",
        lambda: _prewarm(
            [
                "/metrics/monitoring/devices/overview?limit=1000&refresh=true",
            ],
        ),
    )


def _traffic_query_prewarm_paths() -> List[str]:
    """Build the shared traffic-query paths that should stay warm for all users."""
    paths = [
        "/metrics/traffic/summary?line_type=internet&range=-24h&interval=5m&fresh=true",
        "/metrics/traffic/summary?line_type=private_line&range=-24h&interval=5m&fresh=true",
    ]

    db = SessionLocal()
    try:
        datacenter_ids = [
            row[0]
            for row in db.query(Circuit.datacenter_id)
            .filter(
                Circuit.status == "active",
                Circuit.line_type == "internet",
                Circuit.datacenter_id.isnot(None),
            )
            .distinct()
            .all()
            if row[0]
        ]
    finally:
        db.close()

    for datacenter_id in sorted(set(datacenter_ids)):
        paths.append(
            f"/metrics/traffic/summary?line_type=internet&datacenter_id={datacenter_id}&range=-24h&interval=5m&fresh=true"
        )
    return paths


@shared_task
def prewarm_traffic_query_cache():
    """Prewarm shared traffic-query summary charts used by the landing page and presets."""
    return _run_with_lock(
        "menu_cache:prewarm:traffic_query:lock",
        lambda: _prewarm(_traffic_query_prewarm_paths()),
    )
