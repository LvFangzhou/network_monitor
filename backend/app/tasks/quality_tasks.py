"""
Public quality probe periodic tasks.
"""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from celery import shared_task

from app.core import get_logger
from app.database import SessionLocal
from app.models import AlertHistory, AlertRule, Device, QualityProbeTarget
from app.utils import redis_client
from app.utils.quality_probe import (
    apply_quality_loss_window,
    run_quality_nqa_snmp,
    run_quality_ping,
    write_quality_probe_result,
)


logger = get_logger(__name__)
QUALITY_PROBE_LOCK_KEY = "quality_probe:collect:lock"
QUALITY_PROBE_LOCK_TTL_SECONDS = 15
QUALITY_PROBE_MAX_WORKERS = 20
QUALITY_ALERT_METRIC_TYPE = "quality_packet_loss"
QUALITY_ALERT_ACTIVE_STATUSES = ("firing", "acknowledged", "ignored", "snoozed")


def _quality_loss_counter_key(target_id: int) -> str:
    return f"quality_probe:consecutive_loss:{target_id}"


def _update_consecutive_loss_count(target_id: int, raw_result: Dict[str, Any]) -> int:
    """Count consecutive probe cycles with at least one lost packet."""
    try:
        sent = int(float(raw_result.get("sent") or 0))
        received = int(float(raw_result.get("received") or 0))
    except (TypeError, ValueError):
        sent, received = 0, 0
    key = _quality_loss_counter_key(target_id)
    if sent <= 0 or received >= sent:
        try:
            redis_client.delete(key)
        except Exception:
            pass
        return 0
    try:
        count = int(redis_client.incr(key))
        redis_client.expire(key, 24 * 60 * 60)
        return count
    except Exception:
        # Redis异常时宁可不触发，也不要把一次丢包误认为连续多次。
        return 1


def _quality_target_thresholds(rule: AlertRule, target_notification: Dict[str, Any]) -> tuple[int, float]:
    """Return per-target thresholds, falling back to the global defaults."""
    extra_config = rule.extra_config or {}
    try:
        required_count = int(
            target_notification.get("consecutive_samples")
            if target_notification.get("consecutive_samples") is not None
            else extra_config.get("consecutive_samples") or 5
        )
    except (TypeError, ValueError):
        required_count = 5
    try:
        threshold = float(
            target_notification.get("loss_threshold_percent")
            if target_notification.get("loss_threshold_percent") is not None
            else rule.threshold or 10.0
        )
    except (TypeError, ValueError):
        threshold = 10.0
    return max(1, min(required_count, 60)), max(0.01, min(threshold, 100.0))


def _evaluate_quality_loss_alert(
    db,
    rule: AlertRule | None,
    target: QualityProbeTarget,
    raw_result: Dict[str, Any],
    smoothed_result: Dict[str, Any],
) -> None:
    if not rule:
        return
    extra_config = rule.extra_config or {}
    target_notification = (extra_config.get("target_notifications") or {}).get(str(target.id)) or {}
    if not target_notification.get("enabled") or not str(target_notification.get("webhook_url") or "").strip():
        return
    required_count, threshold = _quality_target_thresholds(rule, target_notification)
    consecutive_count = _update_consecutive_loss_count(target.id, raw_result)
    try:
        loss_percent = float(smoothed_result.get("packet_loss_percent") or 0.0)
    except (TypeError, ValueError):
        return
    should_alert = consecutive_count >= required_count and loss_percent >= threshold
    target_key = str(target.id)
    active_alert = (
        db.query(AlertHistory)
        .filter(
            AlertHistory.rule_id == rule.id,
            AlertHistory.alert_target_type == "quality_probe",
            AlertHistory.alert_target_key == target_key,
            AlertHistory.status.in_(QUALITY_ALERT_ACTIVE_STATUSES),
        )
        .order_by(AlertHistory.id.desc())
        .first()
    )
    datacenter = target.datacenter_ref.name if target.datacenter_ref else "-"
    latency = smoothed_result.get("avg_latency_ms")
    latency_text = "-" if latency is None else f"{float(latency):.2f} ms"
    sent = int(float(raw_result.get("sent") or 0))
    received = int(float(raw_result.get("received") or 0))
    message = (
        f"探测目标 {target.name} ({target.target}) 最近5分钟丢包率 {loss_percent:.2f}%，"
        f"已连续 {consecutive_count} 个探测周期发生丢包，告警要求连续 {required_count} 个周期，"
        f"丢包率阈值为 {threshold:.2f}%；"
        f"当前延迟：{latency_text}，本轮收发：{received}/{sent}；"
        f"机房：{datacenter}，运营商：{target.operator_name or '-'}"
    )
    now = datetime.now(timezone.utc)
    if should_alert:
        if active_alert:
            active_alert.alert_value = loss_percent
            active_alert.threshold = threshold
            active_alert.message = message
            active_alert.alert_target_name = f"{target.name} / {target.target}"
            active_alert.updated_at = now
            db.commit()
            return
        alert = AlertHistory(
            rule_id=rule.id,
            device_id=None,
            alert_value=loss_percent,
            threshold=threshold,
            message=message,
            alert_target_type="quality_probe",
            alert_target_key=target_key,
            alert_target_name=f"{target.name} / {target.target}",
            status="firing",
            started_at=now,
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        from app.tasks.alert_tasks import enqueue_alert_notification
        enqueue_alert_notification(alert.id)
        logger.warning(
            "公网质量连续丢包告警触发",
            target_id=target.id,
            target=target.target,
            consecutive_count=consecutive_count,
            loss_percent=loss_percent,
            threshold=threshold,
            alert_id=alert.id,
        )
        return
    if active_alert:
        recovered_reasons = []
        if consecutive_count < required_count:
            recovered_reasons.append(f"连续异常周期已降至 {consecutive_count}/{required_count}")
        if loss_percent < threshold:
            recovered_reasons.append(f"5分钟丢包率已降至 {loss_percent:.2f}%（阈值 {threshold:.2f}%）")
        active_alert.status = "resolved"
        active_alert.resolved_at = now
        active_alert.resolved_by = "system"
        active_alert.resolution_note = (
            f"公网质量探测已恢复：{'；'.join(recovered_reasons) or '触发条件已不再满足'}；"
            f"当前连续异常周期 {consecutive_count}/{required_count}，"
            f"最近5分钟丢包率 {loss_percent:.2f}%（阈值 {threshold:.2f}%）"
        )
        active_alert.alert_value = loss_percent
        active_alert.updated_at = now
        db.commit()
        from app.tasks.alert_tasks import enqueue_alert_notification
        enqueue_alert_notification(active_alert.id, "auto_resolved", "system")
        logger.info("公网质量连续丢包告警恢复", target_id=target.id, alert_id=active_alert.id)


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
        device_ids = {
            int(target.device_id)
            for target in due_targets
            if (target.probe_source or "server_icmp") == "device_nqa_snmp" and target.device_id
        }
        devices_by_id = {
            device.id: device
            for device in db.query(Device).filter(Device.id.in_(device_ids)).all()
        } if device_ids else {}
        max_workers = max(1, min(QUALITY_PROBE_MAX_WORKERS, len(due_targets)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for target in due_targets:
                if (target.probe_source or "server_icmp") == "device_nqa_snmp":
                    device = devices_by_id.get(target.device_id)
                    if not device:
                        probe_results[target.id] = {
                            "success": False,
                            "avg_latency_ms": None,
                            "min_latency_ms": None,
                            "max_latency_ms": None,
                            "jitter_ms": None,
                            "packet_loss_percent": None,
                            "availability_percent": None,
                            "received": 0,
                            "sent": 0,
                            "error": "NQA采集设备不存在",
                        }
                        continue
                    future = executor.submit(run_quality_nqa_snmp, target, device)
                else:
                    future = executor.submit(
                        run_quality_ping,
                        target.target,
                        target.packet_count or 1,
                        target.timeout_ms or 1000,
                    )
                futures[future] = target.id
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

        quality_alert_rule = (
            db.query(AlertRule)
            .filter(AlertRule.metric_type == QUALITY_ALERT_METRIC_TYPE)
            .order_by(AlertRule.id.asc())
            .first()
        )
        for target in due_targets:
            raw_result = probe_results.get(target.id) or {}
            result = apply_quality_loss_window(target.id, raw_result)
            target.last_probe_at = datetime.now(timezone.utc)
            target.last_success = bool(result.get("success"))
            target.last_avg_latency_ms = result.get("avg_latency_ms")
            target.last_packet_loss_percent = result.get("packet_loss_percent")
            target.last_jitter_ms = result.get("jitter_ms")
            target.last_error = result.get("error")
            db.commit()
            db.refresh(target)
            write_quality_probe_result(target, result)
            _evaluate_quality_loss_alert(db, quality_alert_rule, target, raw_result, result)
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
