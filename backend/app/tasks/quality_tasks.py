"""
Public quality probe periodic tasks.
"""
from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List

from celery import shared_task

from app.core import get_logger
from app.database import SessionLocal
from app.models import AlertHistory, AlertRule, Device, QualityProbeTarget, QualityMtrSnapshot, QualityMtrEvent
from app.utils import redis_client
from app.utils.quality_probe import (
    apply_quality_loss_window,
    normalize_server_icmp_probe_config,
    run_quality_nqa_snmp,
    run_quality_ping,
    run_quality_ping_batch,
    run_quality_fast_ping_batch,
    run_mtr_or_trace,
    normalize_quality_ping_result,
    normalize_quality_target_addresses,
    quality_probe_member_key,
    write_quality_probe_result,
)


logger = get_logger(__name__)
QUALITY_PROBE_LOCK_KEY = "quality_probe:collect:lock"
# A full 10-packet sweep currently takes about 28 seconds in production.
# Keep the lock beyond one sweep so the 1-second scheduler cannot start a
# duplicate collection while the previous one is still running.
QUALITY_PROBE_LOCK_TTL_SECONDS = 60
QUALITY_PROBE_MAX_WORKERS = 20
QUALITY_LOSS_ALERT_METRIC_TYPE = "quality_packet_loss"
QUALITY_CRITICAL_LOSS_ALERT_METRIC_TYPE = "quality_packet_loss_critical"
QUALITY_LATENCY_ALERT_METRIC_TYPE = "quality_latency"
QUALITY_JITTER_ALERT_METRIC_TYPE = "quality_jitter"
QUALITY_ALERT_ACTIVE_STATUSES = ("firing", "acknowledged", "ignored", "snoozed")
QUALITY_MTR_LOCK_KEY = "quality_probe:mtr:collect:lock"
QUALITY_MTR_LOCK_TTL_SECONDS = 120
QUALITY_MTR_MAX_WORKERS = 4
QUALITY_MTR_LATENCY_EVENT_THRESHOLD_MS = 50.0
QUALITY_FAST_PROBE_LOCK_KEY = "quality_probe:fast_collect:lock"
QUALITY_FAST_PROBE_LOCK_TTL_SECONDS = 10
QUALITY_FAST_CRITICAL_LOSS_PERCENT = 50.0


def _quality_loss_counter_key(target_id: Any) -> str:
    return f"quality_probe:consecutive_loss:{target_id}"


def _quality_latency_counter_key(target_id: Any) -> str:
    return f"quality_probe:consecutive_latency:{target_id}"


def _quality_latency_recovery_counter_key(target_id: Any) -> str:
    return f"quality_probe:latency_recovery:{target_id}"


def _quality_jitter_counter_key(target_id: Any) -> str:
    return f"quality_probe:consecutive_jitter:{target_id}"


def _quality_jitter_recovery_counter_key(target_id: Any) -> str:
    return f"quality_probe:jitter_recovery:{target_id}"


def _update_consecutive_threshold_count(target_id: Any, metric_name: str, is_abnormal: bool) -> int:
    if metric_name == "latency":
        key = _quality_latency_counter_key(target_id)
    elif metric_name == "jitter":
        key = _quality_jitter_counter_key(target_id)
    else:
        key = _quality_loss_counter_key(target_id)
    if not is_abnormal:
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
        # Redis异常时宁可保守，只按本周期异常计 1 次。
        return 1


def _update_consecutive_loss_count(target_id: Any, raw_result: Dict[str, Any]) -> int:
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


def _quality_latency_thresholds(rule: AlertRule, target_notification: Dict[str, Any], target: QualityProbeTarget) -> tuple[int, float]:
    extra_config = rule.extra_config or {}
    try:
        required_count = int(
            target_notification.get("latency_consecutive_samples")
            if target_notification.get("latency_consecutive_samples") is not None
            else target_notification.get("consecutive_samples")
            if target_notification.get("consecutive_samples") is not None
            else extra_config.get("consecutive_samples") or 5
        )
    except (TypeError, ValueError):
        required_count = 5
    try:
        threshold = float(
            target_notification.get("latency_threshold_ms")
            if target_notification.get("latency_threshold_ms") is not None
            else getattr(target, "latency_threshold_ms", None)
            if getattr(target, "latency_threshold_ms", None) is not None
            else rule.threshold or 100.0
        )
    except (TypeError, ValueError):
        threshold = 100.0
    return max(1, min(required_count, 60)), max(1.0, min(threshold, 10000.0))


def _quality_latency_recovery_required(rule: AlertRule) -> int:
    extra_config = rule.extra_config or {}
    try:
        return max(1, min(int(extra_config.get("recovery_required_samples") or 2), 10))
    except (TypeError, ValueError):
        return 2


def _quality_jitter_thresholds(rule: AlertRule, target_notification: Dict[str, Any], target: QualityProbeTarget) -> tuple[int, float]:
    extra_config = rule.extra_config or {}
    try:
        required_count = int(
            target_notification.get("jitter_consecutive_samples")
            if target_notification.get("jitter_consecutive_samples") is not None
            else target_notification.get("consecutive_samples")
            if target_notification.get("consecutive_samples") is not None
            else extra_config.get("consecutive_samples") or 5
        )
    except (TypeError, ValueError):
        required_count = 5
    try:
        threshold = float(
            target_notification.get("jitter_threshold_ms")
            if target_notification.get("jitter_threshold_ms") is not None
            else getattr(target, "jitter_threshold_ms", None)
            if getattr(target, "jitter_threshold_ms", None) is not None
            else rule.threshold or 30.0
        )
    except (TypeError, ValueError):
        threshold = 30.0
    return max(1, min(required_count, 60)), max(0.1, min(threshold, 10000.0))


def _quality_jitter_recovery_required(rule: AlertRule) -> int:
    extra_config = rule.extra_config or {}
    try:
        return max(1, min(int(extra_config.get("recovery_required_samples") or 2), 10))
    except (TypeError, ValueError):
        return 2


def _quality_target_notification(rule: AlertRule | None, target: QualityProbeTarget, fallback_rule: AlertRule | None = None) -> Dict[str, Any]:
    if not rule:
        return {}
    extra_config = rule.extra_config or {}
    target_notification = (extra_config.get("target_notifications") or {}).get(str(target.id)) or {}
    if target_notification:
        return target_notification
    # 延迟/抖动告警复用质量探测目标里已经配置的机器人，避免同一个探测目标重复配置机器人。
    if rule.metric_type in {
        QUALITY_CRITICAL_LOSS_ALERT_METRIC_TYPE,
        QUALITY_LATENCY_ALERT_METRIC_TYPE,
        QUALITY_JITTER_ALERT_METRIC_TYPE,
    } and fallback_rule:
        fallback_extra = fallback_rule.extra_config or {}
        return (fallback_extra.get("target_notifications") or {}).get(str(target.id)) or {}
    return {}


def _quality_alert_message(
    target: QualityProbeTarget,
    metric_label: str,
    current_value: float,
    threshold: float,
    consecutive_count: int,
    required_count: int,
    raw_result: Dict[str, Any],
    smoothed_result: Dict[str, Any],
    member_target: str | None = None,
    fast_confirmed: bool = False,
) -> str:
    datacenter = target.datacenter_ref.name if target.datacenter_ref else "-"
    latency = smoothed_result.get("avg_latency_ms")
    latency_text = "-" if latency is None else f"{float(latency):.2f} ms"
    loss = smoothed_result.get("packet_loss_percent")
    loss_text = "-" if loss is None else f"{float(loss):.2f}%"
    sent = int(float(raw_result.get("sent") or 0))
    received = int(float(raw_result.get("received") or 0))
    unit = "ms" if metric_label in {"延迟", "抖动"} else "%"
    value_text = f"{current_value:.2f}{unit}"
    threshold_text = f"{threshold:.2f}{unit}"
    loss_window_label = "快速复核丢包率" if fast_confirmed else "最近5分钟丢包率"
    probe_cycle_label = "次快速复核" if fast_confirmed else "个探测周期"
    return (
        f"探测目标 {target.name} ({member_target or target.target}) {metric_label} {value_text}，"
        f"已连续 {consecutive_count} {probe_cycle_label}{metric_label}异常，告警要求连续 {required_count} 次，"
        f"{metric_label}阈值为 {threshold_text}；"
        f"当前延迟：{latency_text}，{loss_window_label}：{loss_text}，本轮收发：{received}/{sent}；"
        f"机房：{datacenter}，运营商：{target.operator_name or '-'}"
    )


def _get_or_create_critical_loss_rule(db, config_rule: AlertRule | None) -> AlertRule | None:
    """Keep P0 severe loss separate from ordinary quality thresholds."""
    rule = (
        db.query(AlertRule)
        .filter(AlertRule.metric_type == QUALITY_CRITICAL_LOSS_ALERT_METRIC_TYPE)
        .order_by(AlertRule.id.asc())
        .first()
    )
    if rule:
        changed = False
        if rule.severity != "P0":
            rule.severity = "P0"
            changed = True
        if float(rule.threshold or 0) != QUALITY_FAST_CRITICAL_LOSS_PERCENT:
            rule.threshold = QUALITY_FAST_CRITICAL_LOSS_PERCENT
            changed = True
        if config_rule is not None and int(rule.enabled or 0) != int(config_rule.enabled or 0):
            rule.enabled = int(config_rule.enabled or 0)
            changed = True
        if changed:
            db.commit()
        return rule
    if not config_rule:
        return None
    rule = AlertRule(
        name="公网质量探测严重丢包",
        description="快速探测连续两轮复核后丢包率仍达到50%，立即触发P0；正常质量采样负责确认恢复",
        rule_type="threshold",
        metric_type=QUALITY_CRITICAL_LOSS_ALERT_METRIC_TYPE,
        condition=">=",
        threshold=QUALITY_FAST_CRITICAL_LOSS_PERCENT,
        duration=0,
        suppress_duration=300,
        severity="P0",
        enabled=1 if config_rule.enabled else 0,
        device_ids=[],
        extra_config={"quality_probe_global": True, "consecutive_samples": 1},
        notification_channels=[],
    )
    db.add(rule)
    db.commit()
    db.refresh(rule)
    return rule


def _evaluate_quality_loss_alert(
    db,
    rule: AlertRule | None,
    target: QualityProbeTarget,
    raw_result: Dict[str, Any],
    smoothed_result: Dict[str, Any],
    member_target: str | None = None,
    immediate_critical: bool = False,
    fallback_channel_rule: AlertRule | None = None,
) -> None:
    if not rule:
        return
    target_notification = _quality_target_notification(rule, target, fallback_channel_rule)
    if not target_notification.get("enabled") or not str(target_notification.get("webhook_url") or "").strip():
        return
    required_count, threshold = _quality_target_thresholds(rule, target_notification)
    if immediate_critical or rule.metric_type == QUALITY_CRITICAL_LOSS_ALERT_METRIC_TYPE:
        required_count = 1
        threshold = max(float(threshold), QUALITY_FAST_CRITICAL_LOSS_PERCENT)
    member_target = member_target or target.target
    member_key = quality_probe_member_key(target.id, member_target)
    consecutive_count = _update_consecutive_loss_count(member_key, raw_result)
    try:
        loss_percent = float(smoothed_result.get("packet_loss_percent") or 0.0)
    except (TypeError, ValueError):
        return
    should_alert = consecutive_count >= required_count and loss_percent >= threshold
    target_key = member_key
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
    message = _quality_alert_message(
        target,
        "丢包率",
        loss_percent,
        threshold,
        consecutive_count,
        required_count,
        raw_result,
        smoothed_result,
        member_target,
        fast_confirmed=immediate_critical,
    )
    now = datetime.now(timezone.utc)
    if should_alert:
        try:
            redis_client.delete(_quality_latency_recovery_counter_key(member_key))
        except Exception:
            pass
        if active_alert:
            active_alert.alert_value = loss_percent
            active_alert.threshold = threshold
            active_alert.message = message
            active_alert.alert_target_name = f"{target.name} / {member_target}"
            active_alert.updated_at = now
            db.commit()
            # 质量探测使用独立评估流程，不会经过 alert_tasks 的通用规则扫描。
            # 持续丢包时仍复用通用重复通知判断，按规则 suppress_duration
            # （当前公网质量规则为 300 秒）再次播报，恢复后自然停止。
            from app.tasks.alert_tasks import enqueue_alert_notification, _should_repeat_notify
            if _should_repeat_notify(active_alert, rule):
                enqueue_alert_notification(active_alert.id)
                logger.info(
                    "公网质量持续丢包重复通知",
                    target_id=target.id,
                    target=member_target,
                    alert_id=active_alert.id,
                    loss_percent=loss_percent,
                    interval_seconds=rule.suppress_duration,
                )
            return
        alert = AlertHistory(
            rule_id=rule.id,
            device_id=None,
            alert_value=loss_percent,
            threshold=threshold,
            message=message,
            alert_target_type="quality_probe",
            alert_target_key=target_key,
            alert_target_name=f"{target.name} / {member_target}",
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
            target=member_target,
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


@shared_task(bind=True, name="app.tasks.quality_tasks.collect_quality_fast_outages")
def collect_quality_fast_outages(self) -> Dict[str, Any]:
    """Every five seconds, detect only confirmed severe loss/outage."""
    try:
        acquired = redis_client.set(
            QUALITY_FAST_PROBE_LOCK_KEY,
            self.request.id or "1",
            ex=QUALITY_FAST_PROBE_LOCK_TTL_SECONDS,
            nx=True,
        )
    except Exception:
        acquired = True
    if not acquired:
        return {"status": "locked", "checked": 0}

    db = SessionLocal()
    try:
        targets = (
            db.query(QualityProbeTarget)
            .filter(
                QualityProbeTarget.is_active == True,  # noqa: E712
                QualityProbeTarget.probe_source == "server_icmp",
            )
            .order_by(QualityProbeTarget.id.asc())
            .all()
        )
        config_rule = (
            db.query(AlertRule)
            .filter(AlertRule.metric_type == QUALITY_LOSS_ALERT_METRIC_TYPE, AlertRule.enabled == 1)
            .order_by(AlertRule.id.asc())
            .first()
        )
        if not config_rule:
            return {"status": "disabled", "checked": 0}
        rule = _get_or_create_critical_loss_rule(db, config_rule)
        if not rule or not rule.enabled:
            return {"status": "disabled", "checked": 0}

        probes: List[Dict[str, Any]] = []
        targets_by_member: Dict[str, tuple[QualityProbeTarget, str]] = {}
        for target in targets:
            for address in normalize_quality_target_addresses(target.target, target.target_addresses):
                member_key = quality_probe_member_key(target.id, address)
                probes.append({"id": member_key, "target": address, "timeout_ms": min(int(target.timeout_ms or 1000), 1000)})
                targets_by_member[member_key] = (target, address)

        results = run_quality_fast_ping_batch(probes)
        critical = 0
        for member_key, result in results.items():
            try:
                loss_percent = float(result.get("packet_loss_percent") or 0.0)
            except (TypeError, ValueError):
                continue
            if loss_percent < QUALITY_FAST_CRITICAL_LOSS_PERCENT or int(result.get("confirmed_rounds") or 0) < 2:
                continue
            target, address = targets_by_member[str(member_key)]
            _evaluate_quality_loss_alert(
                db,
                rule,
                target,
                result,
                result,
                address,
                immediate_critical=True,
                fallback_channel_rule=config_rule,
            )
            critical += 1
        return {"status": "ok", "checked": len(results), "critical": critical}
    except Exception as exc:
        db.rollback()
        logger.error("快速公网质量探测失败", error=str(exc))
        return {"status": "error", "error": str(exc), "checked": 0}
    finally:
        db.close()
        try:
            redis_client.delete(QUALITY_FAST_PROBE_LOCK_KEY)
        except Exception:
            pass


def _evaluate_quality_latency_alert(
    db,
    rule: AlertRule | None,
    fallback_channel_rule: AlertRule | None,
    target: QualityProbeTarget,
    raw_result: Dict[str, Any],
    smoothed_result: Dict[str, Any],
    member_target: str | None = None,
) -> None:
    if not rule:
        return
    target_notification = _quality_target_notification(rule, target, fallback_channel_rule)
    if not target_notification.get("enabled") or not str(target_notification.get("webhook_url") or "").strip():
        return
    required_count, threshold = _quality_latency_thresholds(rule, target_notification, target)
    latency_value = smoothed_result.get("avg_latency_ms")
    try:
        latency_ms = float(latency_value)
    except (TypeError, ValueError):
        latency_ms = 0.0
        is_abnormal = False
    else:
        is_abnormal = latency_ms >= threshold
    member_target = member_target or target.target
    member_key = quality_probe_member_key(target.id, member_target)
    consecutive_count = _update_consecutive_threshold_count(member_key, "latency", is_abnormal)
    should_alert = consecutive_count >= required_count and is_abnormal
    target_key = member_key
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
    message = _quality_alert_message(target, "延迟", latency_ms, threshold, consecutive_count, required_count, raw_result, smoothed_result, member_target)
    now = datetime.now(timezone.utc)
    if should_alert:
        if active_alert:
            active_alert.alert_value = latency_ms
            active_alert.threshold = threshold
            active_alert.message = message
            active_alert.alert_target_name = f"{target.name} / {member_target}"
            active_alert.updated_at = now
            db.commit()
            from app.tasks.alert_tasks import enqueue_alert_notification, _should_repeat_notify
            if _should_repeat_notify(active_alert, rule):
                enqueue_alert_notification(active_alert.id)
            return
        alert = AlertHistory(
            rule_id=rule.id,
            device_id=None,
            alert_value=latency_ms,
            threshold=threshold,
            message=message,
            alert_target_type="quality_probe",
            alert_target_key=target_key,
            alert_target_name=f"{target.name} / {member_target}",
            status="firing",
            started_at=now,
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        from app.tasks.alert_tasks import enqueue_alert_notification
        enqueue_alert_notification(alert.id)
        logger.warning(
            "质量探测连续延迟超阈值告警触发",
            target_id=target.id,
            target=member_target,
            consecutive_count=consecutive_count,
            latency_ms=latency_ms,
            threshold=threshold,
            alert_id=alert.id,
        )
        return
    if active_alert:
        recovery_key = _quality_latency_recovery_counter_key(member_key)
        recovery_required = _quality_latency_recovery_required(rule)
        if is_abnormal:
            try:
                redis_client.delete(recovery_key)
            except Exception:
                pass
            return
        try:
            recovery_count = int(redis_client.incr(recovery_key))
            redis_client.expire(recovery_key, 24 * 60 * 60)
        except Exception:
            recovery_count = 1
        if recovery_count < recovery_required:
            active_alert.alert_value = latency_ms
            active_alert.message = message
            active_alert.updated_at = now
            db.commit()
            return
        recovered_reasons = []
        if consecutive_count < required_count:
            recovered_reasons.append(f"连续异常周期已降至 {consecutive_count}/{required_count}")
        if not is_abnormal:
            recovered_reasons.append(f"平均延迟已降至 {latency_ms:.2f}ms（阈值 {threshold:.2f}ms）")
            recovered_reasons.append(f"已连续 {recovery_count}/{recovery_required} 个周期恢复正常")
        active_alert.status = "resolved"
        active_alert.resolved_at = now
        active_alert.resolved_by = "system"
        active_alert.resolution_note = (
            f"质量探测延迟已恢复：{'；'.join(recovered_reasons) or '触发条件已不再满足'}；"
            f"当前连续异常周期 {consecutive_count}/{required_count}，"
            f"当前平均延迟 {latency_ms:.2f}ms（阈值 {threshold:.2f}ms）"
        )
        active_alert.alert_value = latency_ms
        active_alert.updated_at = now
        db.commit()
        try:
            redis_client.delete(recovery_key)
        except Exception:
            pass
        from app.tasks.alert_tasks import enqueue_alert_notification
        enqueue_alert_notification(active_alert.id, "auto_resolved", "system")
        logger.info("质量探测连续延迟超阈值告警恢复", target_id=target.id, alert_id=active_alert.id)


def _evaluate_quality_jitter_alert(
    db,
    rule: AlertRule | None,
    fallback_channel_rule: AlertRule | None,
    target: QualityProbeTarget,
    raw_result: Dict[str, Any],
    smoothed_result: Dict[str, Any],
    member_target: str | None = None,
) -> None:
    if not rule:
        return
    target_notification = _quality_target_notification(rule, target, fallback_channel_rule)
    if not target_notification.get("enabled") or not str(target_notification.get("webhook_url") or "").strip():
        return
    required_count, threshold = _quality_jitter_thresholds(rule, target_notification, target)
    jitter_value = smoothed_result.get("jitter_ms")
    try:
        jitter_ms = float(jitter_value)
    except (TypeError, ValueError):
        jitter_ms = 0.0
        is_abnormal = False
    else:
        is_abnormal = jitter_ms >= threshold
    member_target = member_target or target.target
    member_key = quality_probe_member_key(target.id, member_target)
    consecutive_count = _update_consecutive_threshold_count(member_key, "jitter", is_abnormal)
    should_alert = consecutive_count >= required_count and is_abnormal
    target_key = member_key
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
    message = _quality_alert_message(target, "抖动", jitter_ms, threshold, consecutive_count, required_count, raw_result, smoothed_result, member_target)
    now = datetime.now(timezone.utc)
    if should_alert:
        if active_alert:
            active_alert.alert_value = jitter_ms
            active_alert.threshold = threshold
            active_alert.message = message
            active_alert.alert_target_name = f"{target.name} / {member_target}"
            active_alert.updated_at = now
            db.commit()
            from app.tasks.alert_tasks import enqueue_alert_notification, _should_repeat_notify
            if _should_repeat_notify(active_alert, rule):
                enqueue_alert_notification(active_alert.id)
            return
        alert = AlertHistory(
            rule_id=rule.id,
            device_id=None,
            alert_value=jitter_ms,
            threshold=threshold,
            message=message,
            alert_target_type="quality_probe",
            alert_target_key=target_key,
            alert_target_name=f"{target.name} / {member_target}",
            status="firing",
            started_at=now,
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        from app.tasks.alert_tasks import enqueue_alert_notification
        enqueue_alert_notification(alert.id)
        logger.warning(
            "质量探测连续抖动超阈值告警触发",
            target_id=target.id,
            target=member_target,
            consecutive_count=consecutive_count,
            jitter_ms=jitter_ms,
            threshold=threshold,
            alert_id=alert.id,
        )
        return
    if active_alert:
        recovery_key = _quality_jitter_recovery_counter_key(member_key)
        recovery_required = _quality_jitter_recovery_required(rule)
        if is_abnormal:
            try:
                redis_client.delete(recovery_key)
            except Exception:
                pass
            return
        try:
            recovery_count = int(redis_client.incr(recovery_key))
            redis_client.expire(recovery_key, 24 * 60 * 60)
        except Exception:
            recovery_count = 1
        if recovery_count < recovery_required:
            active_alert.alert_value = jitter_ms
            active_alert.message = message
            active_alert.updated_at = now
            db.commit()
            return
        recovered_reasons = []
        if consecutive_count < required_count:
            recovered_reasons.append(f"连续异常周期已降至 {consecutive_count}/{required_count}")
        if not is_abnormal:
            recovered_reasons.append(f"抖动已降至 {jitter_ms:.2f}ms（阈值 {threshold:.2f}ms）")
            recovered_reasons.append(f"已连续 {recovery_count}/{recovery_required} 个周期恢复正常")
        active_alert.status = "resolved"
        active_alert.resolved_at = now
        active_alert.resolved_by = "system"
        active_alert.resolution_note = (
            f"质量探测抖动已恢复：{'；'.join(recovered_reasons) or '触发条件已不再满足'}；"
            f"当前连续异常周期 {consecutive_count}/{required_count}，"
            f"当前抖动 {jitter_ms:.2f}ms（阈值 {threshold:.2f}ms）"
        )
        active_alert.alert_value = jitter_ms
        active_alert.updated_at = now
        db.commit()
        try:
            redis_client.delete(recovery_key)
        except Exception:
            pass
        from app.tasks.alert_tasks import enqueue_alert_notification
        enqueue_alert_notification(active_alert.id, "auto_resolved", "system")
        logger.info("质量探测连续抖动超阈值告警恢复", target_id=target.id, alert_id=active_alert.id)


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

        probe_results: Dict[str, Dict[str, Any]] = {}
        device_ids = {
            int(target.device_id)
            for target in due_targets
            if (target.probe_source or "server_icmp") == "device_nqa_snmp" and target.device_id
        }
        devices_by_id = {
            device.id: device
            for device in db.query(Device).filter(Device.id.in_(device_ids)).all()
        } if device_ids else {}
        server_ping_targets: List[Dict[str, Any]] = []
        for target in due_targets:
            if (target.probe_source or "server_icmp") != "server_icmp":
                continue
            safe_config = normalize_server_icmp_probe_config({
                "probe_source": "server_icmp",
                "packet_count": target.packet_count,
                "timeout_ms": target.timeout_ms,
            })
            try:
                addresses = normalize_quality_target_addresses(target.target, target.target_addresses)
            except ValueError:
                addresses = [target.target]
            for address in addresses:
                server_ping_targets.append({
                    "id": quality_probe_member_key(target.id, address),
                    "target": address,
                    "packet_count": safe_config["packet_count"],
                    "timeout_ms": safe_config["timeout_ms"],
                })
        if server_ping_targets:
            probe_results.update(run_quality_ping_batch(server_ping_targets))

        nqa_targets = [
            target for target in due_targets
            if (target.probe_source or "server_icmp") == "device_nqa_snmp"
        ]
        max_workers = max(1, min(QUALITY_PROBE_MAX_WORKERS, len(nqa_targets) or 1))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {}
            for target in nqa_targets:
                device = devices_by_id.get(target.device_id)
                if not device:
                    probe_results[str(target.id)] = {
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
                futures[future] = str(target.id)
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

        quality_loss_alert_rule = (
            db.query(AlertRule)
            # 丢包率不再独立触发告警；这里仅保留为每个探测对象的机器人/负责人配置来源。
            .filter(AlertRule.metric_type == QUALITY_LOSS_ALERT_METRIC_TYPE)
            .order_by(AlertRule.id.asc())
            .first()
        )
        quality_critical_loss_alert_rule = _get_or_create_critical_loss_rule(db, quality_loss_alert_rule)
        if not quality_loss_alert_rule or not quality_loss_alert_rule.enabled:
            quality_critical_loss_alert_rule = None
        quality_latency_alert_rule = (
            db.query(AlertRule)
            .filter(AlertRule.metric_type == QUALITY_LATENCY_ALERT_METRIC_TYPE, AlertRule.enabled == 1)
            .order_by(AlertRule.id.asc())
            .first()
        )
        quality_jitter_alert_rule = (
            db.query(AlertRule)
            .filter(AlertRule.metric_type == QUALITY_JITTER_ALERT_METRIC_TYPE, AlertRule.enabled == 1)
            .order_by(AlertRule.id.asc())
            .first()
        )
        for target in due_targets:
            is_server_icmp = (target.probe_source or "server_icmp") == "server_icmp"
            addresses = normalize_quality_target_addresses(target.target, target.target_addresses) if is_server_icmp else [target.target]
            member_statuses: Dict[str, Dict[str, Any]] = dict(target.target_statuses or {})
            member_results: List[tuple[str, Dict[str, Any], Dict[str, Any]]] = []
            now = datetime.now(timezone.utc)
            for address in addresses:
                member_key = quality_probe_member_key(target.id, address) if is_server_icmp else str(target.id)
                raw_result = normalize_quality_ping_result(probe_results.get(member_key) or {})
                result = apply_quality_loss_window(member_key, raw_result)
                member_statuses[address] = {
                    "success": bool(result.get("success")),
                    "avg_latency_ms": result.get("avg_latency_ms"),
                    "packet_loss_percent": result.get("current_packet_loss_percent", raw_result.get("packet_loss_percent")),
                    "rolling_packet_loss_percent": result.get("packet_loss_percent"),
                    "jitter_ms": result.get("jitter_ms"),
                    "received": result.get("received"),
                    "sent": result.get("sent"),
                    "error": result.get("error"),
                    "last_probe_at": now.isoformat(),
                    **({"last_mtr_at": member_statuses.get(address, {}).get("last_mtr_at")} if member_statuses.get(address, {}).get("last_mtr_at") else {}),
                }
                write_quality_probe_result(target, result, address)
                if is_server_icmp:
                    # 快速层负责触发；正常采样使用同一P0规则确认持续状态及恢复，
                    # 不再另外生成一条普通丢包告警。
                    _evaluate_quality_loss_alert(
                        db,
                        quality_critical_loss_alert_rule,
                        target,
                        raw_result,
                        result,
                        address,
                        fallback_channel_rule=quality_loss_alert_rule,
                    )
                _evaluate_quality_latency_alert(db, quality_latency_alert_rule, quality_loss_alert_rule, target, raw_result, result, address)
                _evaluate_quality_jitter_alert(db, quality_jitter_alert_rule, quality_loss_alert_rule, target, raw_result, result, address)
                member_results.append((address, raw_result, result))
                collected += 1
                if not result.get("success"):
                    failed += 1
                rows.append({
                    "id": target.id,
                    "name": target.name,
                    "target": address,
                    "success": bool(result.get("success")),
                    "avg_latency_ms": result.get("avg_latency_ms"),
                    "packet_loss_percent": result.get("packet_loss_percent"),
                })

            worst_address, worst_raw, worst_result = max(
                member_results,
                key=lambda item: (
                    0 if item[2].get("success") else 1,
                    float(item[2].get("packet_loss_percent") or 0),
                    float(item[2].get("avg_latency_ms") or 0),
                ),
            )
            target.target_addresses = addresses
            target.target_statuses = member_statuses
            target.last_probe_at = now
            target.last_success = all(bool(item[2].get("success")) for item in member_results)
            target.last_avg_latency_ms = worst_result.get("avg_latency_ms")
            target.last_packet_loss_percent = worst_result.get("current_packet_loss_percent", worst_raw.get("packet_loss_percent"))
            target.last_jitter_ms = max(
                (float(item[2].get("jitter_ms")) for item in member_results if item[2].get("jitter_ms") is not None),
                default=None,
            )
            failed_addresses = [address for address, _, result in member_results if not result.get("success")]
            target.last_error = f"异常目标：{', '.join(failed_addresses)}" if failed_addresses else worst_result.get("error")
            db.commit()
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


def _create_mtr_event_if_needed(db, target: QualityProbeTarget, previous: QualityMtrSnapshot | None, current: QualityMtrSnapshot) -> QualityMtrEvent | None:
    if not previous or not current.success:
        return None
    path_changed = bool(previous.path_hash and current.path_hash and previous.path_hash != current.path_hash)
    previous_latency = previous.final_avg_latency_ms
    current_latency = current.final_avg_latency_ms
    latency_delta = None
    latency_increased = False
    if previous_latency is not None and current_latency is not None:
        latency_delta = float(current_latency) - float(previous_latency)
        latency_increased = latency_delta >= QUALITY_MTR_LATENCY_EVENT_THRESHOLD_MS
    if not path_changed and not latency_increased:
        return None

    event_type = "path_changed" if path_changed else "latency_increased"
    title = "公网路径发生变化" if path_changed else "公网路径延迟升高"
    event = QualityMtrEvent(
        target_id=target.id,
        event_type=event_type,
        title=title,
        previous_snapshot_id=previous.id,
        current_snapshot_id=current.id,
        previous_path_hash=previous.path_hash,
        current_path_hash=current.path_hash,
        previous_final_latency_ms=previous_latency,
        current_final_latency_ms=current_latency,
        latency_delta_ms=latency_delta,
        detail={
            "target_name": target.name,
            "target": current.target,
            "previous_path": [hop.get("ip") for hop in (previous.hops or []) if hop.get("ip")],
            "current_path": [hop.get("ip") for hop in (current.hops or []) if hop.get("ip")],
            "path_changed": path_changed,
            "latency_increased": latency_increased,
        },
    )
    db.add(event)
    return event


@shared_task(bind=True, name="app.tasks.quality_tasks.collect_quality_mtr_paths")
def collect_quality_mtr_paths(self) -> Dict[str, Any]:
    """Periodically observe MTR paths for enabled public quality targets."""
    try:
        acquired = redis_client.set(QUALITY_MTR_LOCK_KEY, self.request.id or "1", ex=QUALITY_MTR_LOCK_TTL_SECONDS, nx=True)
    except Exception:
        acquired = True
    if not acquired:
        return {"status": "locked", "collected": 0}

    db = SessionLocal()
    collected = 0
    failed = 0
    events = 0
    rows: List[Dict[str, Any]] = []
    try:
        targets = (
            db.query(QualityProbeTarget)
            .filter(QualityProbeTarget.is_active == True)  # noqa: E712
            .filter(QualityProbeTarget.probe_source == "server_icmp")
            .filter(QualityProbeTarget.mtr_enabled == True)  # noqa: E712
            .order_by(QualityProbeTarget.id.asc())
            .all()
        )
        due_members: List[tuple[QualityProbeTarget, str]] = []
        now = datetime.now(timezone.utc)
        for target in targets:
            interval = max(int(target.mtr_interval_seconds or 3600), 3600)
            statuses = target.target_statuses or {}
            for address in normalize_quality_target_addresses(target.target, target.target_addresses):
                last_mtr_raw = (statuses.get(address) or {}).get("last_mtr_at")
                try:
                    last_mtr_at = datetime.fromisoformat(str(last_mtr_raw).replace("Z", "+00:00")) if last_mtr_raw else None
                except (TypeError, ValueError):
                    last_mtr_at = None
                if _seconds_since(last_mtr_at) >= interval:
                    due_members.append((target, address))
        if not due_members:
            return {"status": "ok", "collected": 0, "failed": 0, "events": 0, "items": []}

        results: Dict[str, Dict[str, Any]] = {}
        max_workers = max(1, min(QUALITY_MTR_MAX_WORKERS, len(due_members)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            futures = {
                executor.submit(run_mtr_or_trace, address, 5, 35, False): quality_probe_member_key(target.id, address)
                for target, address in due_members
            }
            for future in as_completed(futures):
                target_id = futures[future]
                try:
                    results[target_id] = future.result()
                except Exception as exc:
                    results[target_id] = {"success": False, "error": str(exc), "hops": [], "output": str(exc), "tool": "none"}

        for target, address in due_members:
            result = results.get(quality_probe_member_key(target.id, address)) or {}
            previous = (
                db.query(QualityMtrSnapshot)
                .filter(QualityMtrSnapshot.target_id == target.id)
                .filter(QualityMtrSnapshot.target == address)
                .order_by(QualityMtrSnapshot.created_at.desc(), QualityMtrSnapshot.id.desc())
                .first()
            )
            snapshot = QualityMtrSnapshot(
                target_id=target.id,
                target=address,
                path_hash=result.get("path_hash") or "",
                hop_count=int(result.get("hop_count") or 0),
                final_hop_ip=result.get("final_hop_ip"),
                final_avg_latency_ms=result.get("final_avg_latency_ms"),
                final_loss_percent=result.get("final_loss_percent"),
                max_avg_latency_ms=result.get("max_avg_latency_ms"),
                command=result.get("command"),
                tool=result.get("tool"),
                raw_output=result.get("output"),
                hops=result.get("hops") or [],
                success=bool(result.get("success")) and bool(result.get("hops")),
                error=result.get("error"),
                created_at=now,
            )
            db.add(snapshot)
            db.flush()
            event = _create_mtr_event_if_needed(db, target, previous, snapshot)
            if event:
                events += 1
            statuses = dict(target.target_statuses or {})
            member_status = dict(statuses.get(address) or {})
            member_status.update({
                "last_mtr_at": now.isoformat(),
                "last_mtr_path_hash": snapshot.path_hash,
                "last_mtr_final_latency_ms": snapshot.final_avg_latency_ms,
            })
            statuses[address] = member_status
            target.target_statuses = statuses
            if address == target.target or not target.last_mtr_at:
                target.last_mtr_at = now
                target.last_mtr_path_hash = snapshot.path_hash
                target.last_mtr_final_latency_ms = snapshot.final_avg_latency_ms
            if not snapshot.success:
                failed += 1
            collected += 1
            rows.append({
                "id": target.id,
                "name": target.name,
                "target": address,
                "success": snapshot.success,
                "hop_count": snapshot.hop_count,
                "final_avg_latency_ms": snapshot.final_avg_latency_ms,
                "path_hash": snapshot.path_hash,
            })
        db.commit()
        return {"status": "ok", "collected": collected, "failed": failed, "events": events, "items": rows[:20]}
    except Exception as exc:
        db.rollback()
        logger.error("公网MTR路径观察失败", error=str(exc))
        return {"status": "error", "error": str(exc), "collected": collected, "failed": failed, "events": events}
    finally:
        db.close()
        try:
            redis_client.delete(QUALITY_MTR_LOCK_KEY)
        except Exception:
            pass
