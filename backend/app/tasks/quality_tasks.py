"""
Public quality probe periodic tasks.
"""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from celery import shared_task

from app.core import get_logger
from app.database import SessionLocal
from app.models import QualityProbeTarget
from app.utils import redis_client
from app.utils.quality_probe import apply_quality_loss_window, run_quality_ping, write_quality_probe_result


logger = get_logger(__name__)
QUALITY_PROBE_LOCK_KEY = "quality_probe:collect:lock"
QUALITY_PROBE_LOCK_TTL_SECONDS = 15
QUALITY_PROBE_MAX_WORKERS = 20


def _seconds_since(value: datetime | None) -> float:
    if not value:
        return 10**9
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - value.astimezone(timezone.utc)).total_seconds()


@shared_task(bind=True, name="app.tasks.quality_tasks.collect_quality_probes")
def collect_quality_probes(self) -> Dict[str, Any]:
    """Collect enabled public quality probe targets that are due."""
    try:
        acquired = redis_client.set(QUALITY_PROBE_LOCK_KEY, self.request.id or "1", ex=QUALITY_PROBE_LOCK_TTL_SECONDS, nx=True)
    except Exception:
        acquired = True
    if not acquired:
        return {"status": "locked", "collected": 0}

    db = SessionLocal()
    collected = 0
    failed = 0
    rows: List[Dict[str, Any]] = []
    try:
        targets = (
            db.query(QualityProbeTarget)
            .filter(QualityProbeTarget.is_active == True)  # noqa: E712
            .order_by(QualityProbeTarget.id.asc())
            .all()
        )
        due_targets: List[QualityProbeTarget] = []
        for target in targets:
            interval = max(int(target.interval_seconds or 60), 1)
            if _seconds_since(target.last_probe_at) < interval:
                continue
            due_targets.append(target)

        if not due_targets:
            return {"status": "ok", "collected": 0, "failed": 0, "items": []}

        probe_results: Dict[int, Dict[str, Any]] = {}
        max_workers = max(1, min(QUALITY_PROBE_MAX_WORKERS, len(due_targets)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_quality_ping, target.target, target.packet_count or 1, target.timeout_ms or 1000): target.id
                for target in due_targets
            }
            for future in as_completed(futures):
                target_id = futures[future]
                try:
                    probe_results[target_id] = future.result()
                except Exception as e:
                    probe_results[target_id] = {
                        "success": False,
                        "avg_latency_ms": None,
                        "min_latency_ms": None,
                        "max_latency_ms": None,
                        "jitter_ms": None,
                        "packet_loss_percent": 100.0,
                        "availability_percent": 0.0,
                        "received": 0,
                        "sent": 0,
                        "error": str(e),
                    }

        for target in due_targets:
            result = apply_quality_loss_window(target.id, probe_results.get(target.id) or {})
            target.last_probe_at = datetime.now(timezone.utc)
            target.last_success = bool(result.get("success"))
            target.last_avg_latency_ms = result.get("avg_latency_ms")
            target.last_packet_loss_percent = result.get("packet_loss_percent")
            target.last_jitter_ms = result.get("jitter_ms")
            target.last_error = result.get("error")
            db.commit()
            db.refresh(target)
            write_quality_probe_result(target, result)
            collected += 1
            if not result.get("success"):
                failed += 1
            rows.append({
                "id": target.id,
                "name": target.name,
                "target": target.target,
                "success": bool(result.get("success")),
                "avg_latency_ms": result.get("avg_latency_ms"),
                "packet_loss_percent": result.get("packet_loss_percent"),
            })
        return {"status": "ok", "collected": collected, "failed": failed, "items": rows[:20]}
    except Exception as e:
        db.rollback()
        logger.error("质量探测采集失败", error=str(e))
        return {"status": "error", "error": str(e), "collected": collected, "failed": failed}
    finally:
        db.close()
        try:
            redis_client.delete(QUALITY_PROBE_LOCK_KEY)
        except Exception:
            pass
