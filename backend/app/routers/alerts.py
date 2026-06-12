"""
告警管理路由
"""
import hashlib
import json
import re

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import or_
from sqlalchemy.orm import Session
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from app.database import get_db
from app.models import AlertRule, AlertHistory, AlertSilence, Device, SyslogEvent, User
from app.routers.auth import get_current_active_user
from app.tasks.alert_tasks import (
    CIRCUIT_METRIC_TYPES,
    COMMON_DEVICE_METRIC_TYPES,
    DEVICE_REACHABILITY_METRIC_TYPES,
    EXPORTER_METRIC_TYPES,
    FAST_ALERT_METRIC_TYPES,
    HILLSTONE_ONLY_METRIC_TYPES,
    INTERFACE_METRIC_TYPES,
    PROTOCOL_METRIC_TYPES,
    SNMP_DEVICE_METRIC_TYPES,
    SNMP_TARGET_METRIC_TYPES,
    _evaluate_rule_condition,
    _get_effective_monitor_source,
    _get_metric_targets,
    _is_hillstone_vendor,
    _is_targeted_metric,
    _silence_matches,
)
from app.utils import notification_manager, redis_client
from app.schemas import (
    AlertRuleCreate, AlertRuleUpdate, AlertRuleResponse,
    AlertHistoryResponse, AlertAcknowledge, AlertResolve, AlertIgnore, SyslogEventResponse,
    AlertSilenceCreate, AlertSilenceUpdate
)
from app.core import get_logger

logger = get_logger(__name__)
router = APIRouter()
LOCAL_TIMEZONE = ZoneInfo("Asia/Shanghai")
RULE_STATUS_CACHE_PREFIX = "alerts:rule_status"
RULE_STATUS_CACHE_TTL_SECONDS = 900
SILENCE_MATCH_CACHE_PREFIX = "alerts:silence_matches"
SILENCE_MATCH_CACHE_TTL_SECONDS = 60

SEVERITY_NORMALIZATION = {
    "critical": "P0",
    "warning": "P1",
    "info": "P2",
    "P0": "P0",
    "P1": "P1",
    "P2": "P2",
    "P3": "P3",
}


def _normalize_severity(value: Optional[str]) -> str:
    return SEVERITY_NORMALIZATION.get((value or "").strip(), "P1")


def _severity_filter_values(value: Optional[str]) -> List[str]:
    normalized = _normalize_severity(value)
    reverse_map = {
        "P0": ["P0", "critical"],
        "P1": ["P1", "warning"],
        "P2": ["P2", "info"],
        "P3": ["P3"],
    }
    return reverse_map.get(normalized, [normalized])


SILENCE_ACTIVE_STATUSES = ["firing", "acknowledged", "ignored", "snoozed"]


def _get_silence_matched_alerts(
    db: Session,
    silence: AlertSilence,
    *,
    active_only: bool = True,
) -> List[AlertHistory]:
    alerts_query = _build_silence_candidate_query(db, silence, active_only=active_only)
    alerts = alerts_query.order_by(AlertHistory.started_at.desc()).all()
    matched = []
    for alert in alerts:
        if _alert_matches_silence(silence, alert):
            matched.append(alert)
    return matched


def _alert_matches_silence(silence: AlertSilence, alert: AlertHistory) -> bool:
    if not alert.rule or not alert.device:
        return False
    target = {
        "target_type": alert.alert_target_type,
        "target_key": alert.alert_target_key,
        "target_name": alert.alert_target_name,
        "value": alert.alert_value,
        "alarm_id": alert.alarm_id,
    }
    return _silence_matches(silence, alert.rule, alert.device, target)


def _split_silence_values(value: Optional[str]) -> List[str]:
    return [item.strip() for item in re.split(r"[,，;；\n\r]+", value or "") if item.strip()]


def _build_silence_candidate_query(
    db: Session,
    silence: AlertSilence,
    *,
    active_only: bool,
):
    alerts_query = (
        db.query(AlertHistory)
        .join(AlertRule, AlertRule.id == AlertHistory.rule_id)
        .join(Device, Device.id == AlertHistory.device_id)
    )
    if active_only:
        alerts_query = alerts_query.filter(AlertHistory.status.in_(SILENCE_ACTIVE_STATUSES))
    if silence.rule_id:
        alerts_query = alerts_query.filter(AlertHistory.rule_id == silence.rule_id)
    if silence.device_id:
        alerts_query = alerts_query.filter(AlertHistory.device_id == silence.device_id)

    for condition in silence.conditions or []:
        field_name = str((condition or {}).get("field") or "").strip()
        operator = str((condition or {}).get("operator") or "contains").strip()
        values = _split_silence_values(str((condition or {}).get("value") or ""))
        if not values or operator in {"not_contains", "not_equals", "not_regex", "regex"}:
            continue
        if field_name in {"ip", "device_ip"}:
            alerts_query = alerts_query.filter(or_(*[Device.ip_address == value for value in values]))
        elif field_name in {"interface"}:
            alerts_query = alerts_query.filter(
                or_(
                    *[
                        or_(
                            AlertHistory.alert_target_name.ilike(f"%{value}%"),
                            AlertHistory.alert_target_key.ilike(f"%{value}%"),
                        )
                        for value in values
                    ]
                )
            )
        elif field_name == "alarm_id":
            alerts_query = alerts_query.filter(or_(*[AlertHistory.alarm_id == value for value in values]))
    return alerts_query


def _count_silence_matches(db: Session, silence: AlertSilence) -> int:
    return len(_get_silence_matched_alerts(db, silence))


def _silence_match_count_cache_key(silence: AlertSilence, active_only: bool) -> str:
    updated_marker = silence.updated_at or silence.created_at
    marker_text = updated_marker.isoformat() if updated_marker else "none"
    raw = f"{silence.id}:{int(bool(active_only))}:{int(silence.enabled or 0)}:{marker_text}"
    digest = hashlib.sha1(raw.encode("utf-8")).hexdigest()
    return f"{SILENCE_MATCH_CACHE_PREFIX}:{digest}"


def _count_silence_matches_cached(db: Session, silence: AlertSilence, *, active_only: bool = True) -> int:
    cache_key = _silence_match_count_cache_key(silence, active_only)
    cached = redis_client.get(cache_key)
    if cached is not None:
        try:
            return int(cached)
        except (TypeError, ValueError):
            pass
    count = len(_get_silence_matched_alerts(db, silence, active_only=active_only))
    redis_client.setex(cache_key, SILENCE_MATCH_CACHE_TTL_SECONDS, str(count))
    return count


def _clear_silence_match_cache() -> None:
    try:
        for key in redis_client.scan_iter(f"{SILENCE_MATCH_CACHE_PREFIX}:*"):
            redis_client.delete(key)
    except Exception as exc:
        logger.warning("清理告警屏蔽命中缓存失败", error=str(exc))


def _resolve_active_alerts_for_disabled_rule(db: Session, rule_id: int) -> int:
    active_alerts = (
        db.query(AlertHistory)
        .filter(
            AlertHistory.rule_id == rule_id,
            AlertHistory.status.in_(SILENCE_ACTIVE_STATUSES),
        )
        .all()
    )
    if not active_alerts:
        return 0
    now = _utc_now()
    for alert in active_alerts:
        alert.status = "resolved"
        alert.resolved_at = now
        alert.resolved_by = None
        alert.resolution_note = "告警规则已停用，系统静默恢复活动告警"
        alert.updated_at = now
    return len(active_alerts)


def _serialize_notification_channels(channels):
    """兼容 Pydantic 模型和普通 dict 两种通知渠道输入。"""
    if not channels:
        return []

    serialized = []
    for channel in channels:
        if hasattr(channel, "model_dump"):
            serialized.append(channel.model_dump())
        elif isinstance(channel, dict):
            serialized.append(channel)
        else:
            serialized.append(
                {
                    "type": getattr(channel, "type", ""),
                    "config": getattr(channel, "config", {}) or {},
                }
            )
    return serialized


def _detect_notification_type(webhook_url: str) -> str:
    url = (webhook_url or "").strip().lower()
    if "work.weixin.qq.com" in url or "qyapi.weixin.qq.com" in url:
        return "wechat"
    if "oapi.dingtalk.com" in url or "api.dingtalk.com" in url:
        return "dingtalk"
    if "open.feishu.cn" in url or "open.larksuite.com" in url:
        return "feishu"
    return "webhook"


def _format_local_time(value: Optional[datetime] = None) -> str:
    current = value or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current.astimezone(LOCAL_TIMEZONE).strftime("%Y/%m/%d %H:%M:%S")


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _format_snooze_until(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_TIMEZONE).strftime("%Y-%m-%d %H:%M:%S")


def _validate_rule_logic(metric_type: Optional[str], condition: Optional[str], threshold: Optional[float]) -> None:
    if metric_type != "device_reachability":
        return
    if condition is None or threshold is None:
        return
    # device_reachability: 1 = reachable, 0 = unreachable
    # Unreachable alarms should use < 1 / <= 0 / == 0 to avoid impossible matches.
    if condition in {">", ">="} and float(threshold) >= 1:
        raise HTTPException(
            status_code=400,
            detail="设备可达状态规则配置不正确：该指标 1=可达、0=不可达。不可达告警请使用 '< 1' 或 '== 0'。",
        )


def _serialize_rule(rule: AlertRule) -> dict:
    return {
        "id": rule.id,
        "name": rule.name,
        "description": rule.description,
        "rule_type": rule.rule_type,
        "metric_type": rule.metric_type,
        "condition": rule.condition,
        "threshold": rule.threshold,
        "duration": rule.duration,
        "change_rate_threshold": rule.change_rate_threshold,
        "change_rate_window": rule.change_rate_window,
        "severity": _normalize_severity(rule.severity),
        "suppress_duration": rule.suppress_duration,
        "enabled": bool(rule.enabled),
        "device_group_id": rule.device_group_id,
        "device_ids": rule.device_ids,
        "extra_config": rule.extra_config or {},
        "notification_channels": rule.notification_channels,
        "created_at": rule.created_at,
        "updated_at": rule.updated_at,
    }


def _json_safe(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    return value


def _rule_status_cache_key(
    rule: AlertRule,
    search: Optional[str],
    status_filter: Optional[str],
    limit: int,
) -> str:
    payload = {
        "rule_id": rule.id,
        "updated_at": rule.updated_at.isoformat() if rule.updated_at else None,
        "search": (search or "").strip().lower(),
        "status": status_filter or "",
        "limit": limit,
    }
    digest = hashlib.sha1(json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
    return f"{RULE_STATUS_CACHE_PREFIX}:{rule.id}:{digest}"


def _get_rule_applicable_devices(db: Session, rule: AlertRule) -> List[Device]:
    if rule.device_group_id:
        devices = db.query(Device).filter(Device.group_id == rule.device_group_id).all()
    elif rule.device_ids:
        devices = db.query(Device).filter(Device.id.in_(rule.device_ids)).all()
    else:
        devices = db.query(Device).all()

    if rule.metric_type in DEVICE_REACHABILITY_METRIC_TYPES:
        return [
            device for device in devices
            if device.is_monitored and device.status in {"active", "online"} and device.ip_address
        ]
    if rule.metric_type in INTERFACE_METRIC_TYPES or rule.metric_type in CIRCUIT_METRIC_TYPES:
        return [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and (_get_effective_monitor_source(device) == "asternos_exporter" or device.snmp_version)
        ]
    if rule.metric_type in EXPORTER_METRIC_TYPES:
        return [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and _get_effective_monitor_source(device) == "asternos_exporter"
        ]
    if rule.metric_type in PROTOCOL_METRIC_TYPES:
        return [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and (_get_effective_monitor_source(device) == "asternos_exporter" or device.snmp_version)
        ]
    if rule.metric_type in COMMON_DEVICE_METRIC_TYPES:
        return [
            device for device in devices
            if device.is_monitored
            and device.status in {"active", "online"}
            and (
                _get_effective_monitor_source(device) == "asternos_exporter"
                or device.snmp_version
            )
        ]
    if rule.metric_type in SNMP_TARGET_METRIC_TYPES or rule.metric_type in SNMP_DEVICE_METRIC_TYPES:
        return [
            device for device in devices
            if device.is_monitored
            and device.snmp_version
            and _get_effective_monitor_source(device) == "snmp"
            and (rule.metric_type not in HILLSTONE_ONLY_METRIC_TYPES or _is_hillstone_vendor(device.vendor))
            and device.status in {"active", "online"}
        ]
    if _is_targeted_metric(rule.metric_type):
        return [
            device for device in devices
            if device.is_monitored
            and device.snmp_version
            and _get_effective_monitor_source(device) == "snmp"
            and device.status in {"active", "online"}
        ]
    return [
        device for device in devices
        if device.is_monitored and device.status in {"active", "online"}
    ]


@router.get("/rules", response_model=dict)
async def list_alert_rules(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    enabled: Optional[bool] = None,
    severity: Optional[str] = None,
    search: Optional[str] = None
):
    """获取告警规则列表"""
    query = db.query(AlertRule)
    
    if enabled is not None:
        query = query.filter(AlertRule.enabled == (1 if enabled else 0))
    if severity:
        query = query.filter(AlertRule.severity.in_(_severity_filter_values(severity)))
    if search:
        query = query.filter(AlertRule.name.ilike(f"%{search}%"))
    
    total = query.count()
    rules = query.offset(skip).limit(limit).all()
    
    return {
        "total": total,
        "items": [{
            "id": r.id,
            "name": r.name,
            "description": r.description,
            "rule_type": r.rule_type,
            "metric_type": r.metric_type,
            "condition": r.condition,
            "threshold": r.threshold,
            "duration": r.duration,
            "severity": _normalize_severity(r.severity),
            "enabled": bool(r.enabled),
            "device_group_id": r.device_group_id,
            "device_ids": r.device_ids,
            "extra_config": r.extra_config or {},
            "suppress_duration": r.suppress_duration,
            "notification_channels": r.notification_channels,
            "created_at": r.created_at,
            "updated_at": r.updated_at
        } for r in rules]
    }


@router.get("/rules/{rule_id}", response_model=dict)
async def get_alert_rule(rule_id: int, db: Session = Depends(get_db)):
    """获取告警规则详情"""
    rule = db.query(AlertRule).filter(AlertRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="告警规则不存在")
    
    return _serialize_rule(rule)


@router.get("/rules/{rule_id}/status", response_model=dict)
def get_alert_rule_status(
    rule_id: int,
    db: Session = Depends(get_db),
    search: Optional[str] = None,
    status: Optional[str] = Query(None, description="normal / alert / no_data"),
    limit: int = Query(500, ge=1, le=2000),
    refresh: bool = Query(False, description="是否绕过缓存重新评估"),
):
    """实时评估某条告警规则，展示正常、异常和无数据对象。"""
    rule = db.query(AlertRule).filter(AlertRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="告警规则不存在")

    keyword = (search or "").strip().lower()
    allowed_statuses = {"normal", "alert", "no_data"}
    normalized_status = status if status in allowed_statuses else None
    cache_key = _rule_status_cache_key(rule, search, normalized_status, limit)
    if not refresh:
        cached_raw = redis_client.get(cache_key)
        if cached_raw:
            try:
                cached_payload = json.loads(cached_raw)
                cached_payload["cached"] = True
                cached_payload["cache_ttl_seconds"] = redis_client.ttl(cache_key)
                return cached_payload
            except Exception as exc:
                logger.warning("读取规则状态缓存失败", rule_id=rule.id, error=str(exc))

    devices = _get_rule_applicable_devices(db, rule)
    if keyword:
        devices = [
            device for device in devices
            if keyword in (device.name or "").lower()
            or keyword in (device.ip_address or "").lower()
            or keyword in (device.model or "").lower()
            or keyword in (device.vendor or "").lower()
        ]

    items = []
    summary = {"total": 0, "normal": 0, "alert": 0, "no_data": 0}
    for device in devices:
        try:
            targets = _get_metric_targets(db, device, rule.metric_type, rule.extra_config or {})
        except Exception as exc:
            logger.error("规则状态评估失败", rule_id=rule.id, device_id=device.id, error=str(exc))
            targets = []

        if not targets:
            row = {
                "rule_id": rule.id,
                "device_id": device.id,
                "device_name": device.name,
                "device_ip": device.ip_address,
                "monitor_source": _get_effective_monitor_source(device),
                "target_type": "device",
                "target_key": str(device.id),
                "target_name": None,
                "value": None,
                "condition": f"{rule.condition} {rule.threshold}",
                "status": "no_data",
                "state_text": None,
                "message": "当前规则没有采集到可评估对象",
            }
            summary["total"] += 1
            summary["no_data"] += 1
            if normalized_status in (None, "no_data") and len(items) < limit:
                items.append(row)
            continue

        for target in targets:
            value = target.get("value")
            current_status = "no_data"
            is_alert = False
            if value is not None:
                is_alert = _evaluate_rule_condition(rule, float(value))
                current_status = "alert" if is_alert else "normal"

            summary["total"] += 1
            summary[current_status] += 1
            if normalized_status and normalized_status != current_status:
                continue
            if len(items) >= limit:
                continue

            row = {
                "rule_id": rule.id,
                "device_id": device.id,
                "device_name": device.name,
                "device_ip": device.ip_address,
                "monitor_source": _get_effective_monitor_source(device),
                "target_type": target.get("target_type"),
                "target_key": target.get("target_key"),
                "target_name": target.get("target_name"),
                "value": float(value) if value is not None else None,
                "condition": f"{rule.condition} {rule.threshold}",
                "status": current_status,
                "state_text": target.get("state_text"),
                "message": "触发告警" if is_alert else "正常",
            }
            items.append(row)

    payload = {
        "rule": _serialize_rule(rule),
        "summary": summary,
        "items": items,
        "limit": limit,
        "truncated": len(items) >= limit and summary["total"] > limit,
        "evaluated_at": _utc_now(),
        "cached": False,
        "cache_ttl_seconds": RULE_STATUS_CACHE_TTL_SECONDS,
    }
    try:
        redis_client.setex(
            cache_key,
            RULE_STATUS_CACHE_TTL_SECONDS,
            json.dumps(_json_safe(payload), ensure_ascii=False),
        )
    except Exception as exc:
        logger.warning("写入规则状态缓存失败", rule_id=rule.id, error=str(exc))
    return payload


@router.post("/rules", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_alert_rule(
    rule: AlertRuleCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """创建告警规则"""
    _validate_rule_logic(rule.metric_type, rule.condition, rule.threshold)
    db_rule = AlertRule(
        name=rule.name,
        description=rule.description,
        rule_type=rule.rule_type,
        metric_type=rule.metric_type,
        condition=rule.condition,
        threshold=rule.threshold,
        duration=rule.duration,
        change_rate_threshold=rule.change_rate_threshold,
        change_rate_window=rule.change_rate_window,
        severity=_normalize_severity(rule.severity),
        suppress_duration=rule.suppress_duration,
        enabled=1 if rule.enabled else 0,
        device_group_id=rule.device_group_id,
        device_ids=rule.device_ids,
        extra_config=rule.extra_config or {},
        notification_channels=_serialize_notification_channels(rule.notification_channels)
    )
    
    db.add(db_rule)
    db.commit()
    db.refresh(db_rule)
    
    logger.info("告警规则创建成功", rule_id=db_rule.id, name=db_rule.name)
    return _serialize_rule(db_rule)


@router.put("/rules/{rule_id}", response_model=dict)
async def update_alert_rule(
    rule_id: int,
    rule: AlertRuleUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """更新告警规则"""
    db_rule = db.query(AlertRule).filter(AlertRule.id == rule_id).first()
    if not db_rule:
        raise HTTPException(status_code=404, detail="告警规则不存在")
    
    update_data = rule.model_dump(exclude_unset=True)
    _validate_rule_logic(
        update_data.get("metric_type", db_rule.metric_type),
        update_data.get("condition", db_rule.condition),
        update_data.get("threshold", db_rule.threshold),
    )
    
    # 处理通知渠道
    if "notification_channels" in update_data:
        update_data["notification_channels"] = _serialize_notification_channels(
            update_data["notification_channels"]
        )
    
    # 处理enabled字段
    if "enabled" in update_data:
        update_data["enabled"] = 1 if update_data["enabled"] else 0
    if "severity" in update_data and update_data["severity"] is not None:
        update_data["severity"] = _normalize_severity(update_data["severity"])
    
    for key, value in update_data.items():
        if value is not None and hasattr(db_rule, key):
            setattr(db_rule, key, value)
    
    db_rule.updated_at = _utc_now()
    resolved_count = 0
    if "enabled" in update_data and update_data["enabled"] == 0:
        resolved_count = _resolve_active_alerts_for_disabled_rule(db, db_rule.id)
    db.commit()
    db.refresh(db_rule)
    
    logger.info("告警规则更新成功", rule_id=rule_id, resolved_active_alerts=resolved_count)
    return _serialize_rule(db_rule)


@router.delete("/rules/{rule_id}")
async def delete_alert_rule(
    rule_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """删除告警规则"""
    rule = db.query(AlertRule).filter(AlertRule.id == rule_id).first()
    if not rule:
        raise HTTPException(status_code=404, detail="告警规则不存在")
    
    db.delete(rule)
    db.commit()
    
    logger.info("告警规则删除成功", rule_id=rule_id)
    return {"message": "告警规则已删除"}


@router.post("/test-notification", response_model=dict)
async def test_notification(
    payload: dict,
    current_user: User = Depends(get_current_active_user),
):
    """测试机器人/Webhook 通知是否可用。"""
    webhook_url = (payload.get("url") or "").strip()
    if not webhook_url:
        raise HTTPException(status_code=400, detail="Webhook 地址不能为空")

    channel_type = _detect_notification_type(webhook_url)
    config = {"url": webhook_url} if channel_type == "webhook" else {"webhook": webhook_url}

    success = await notification_manager.send_notification(
        channel_type,
        config,
        "网络监控测试消息",
        "网络监控测试消息\n这是一条测试告警消息，用于验证机器人 webhook 是否配置正确。",
        {
            "severity": "P1",
            "title": "P1-故障通知@测试对象",
            "headline": "P1",
            "summary": "接口异常-interface-down",
            "subtitle": _format_local_time(),
            "rows": [
                {"label": "故障标题", "value": "【接口异常-interface-down】"},
                {"label": "交换机", "value": "TEST-SWITCH"},
                {"label": "管理地址", "value": "10.0.0.1"},
                {"label": "接口", "value": "Ten-GigabitEthernet1/0/1"},
                {"label": "过去1小时down次数", "value": "1次"},
                {"label": "Alarm ID", "value": "ALMTEST202604240001"},
                {"label": "发生时间", "value": _format_local_time()},
                {"label": "当前处理人", "value": "测试人员"},
            ],
            "detail_url": "https://localhost:8443/alerts/history",
        },
    )

    if not success:
        detail = notification_manager.last_error_message or f"{channel_type} 测试消息发送失败，请检查 webhook 地址或机器人配置"
        raise HTTPException(status_code=400, detail=detail)

    return {
        "success": True,
        "channel_type": channel_type,
        "message": f"{channel_type} 测试消息发送成功",
    }


# ========== 告警历史 ==========

@router.get("/history", response_model=dict)
async def list_alert_history(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=1000),
    status: Optional[str] = None,
    device_id: Optional[int] = None,
    rule_id: Optional[int] = None,
    alert_id: Optional[int] = None,
    alarm_id: Optional[str] = None,
    severity: Optional[str] = None,
    search: Optional[str] = None,
):
    """获取告警历史列表"""
    query = db.query(AlertHistory).join(Device).outerjoin(AlertRule, AlertHistory.rule_id == AlertRule.id)
    
    if status:
        query = query.filter(AlertHistory.status == status)
    if device_id:
        query = query.filter(AlertHistory.device_id == device_id)
    if rule_id:
        query = query.filter(AlertHistory.rule_id == rule_id)
    if alert_id:
        query = query.filter(AlertHistory.id == alert_id)
    if alarm_id:
        query = query.filter(AlertHistory.alarm_id.ilike(f"%{alarm_id}%"))
    if severity:
        query = query.filter(AlertRule.severity.in_(_severity_filter_values(severity)))
    if search:
        keyword = f"%{search}%"
        query = query.filter(
            or_(
                AlertHistory.message.ilike(keyword),
                AlertHistory.alarm_id.ilike(keyword),
                AlertHistory.alert_target_name.ilike(keyword),
                AlertHistory.alert_target_key.ilike(keyword),
                Device.name.ilike(keyword),
                Device.ip_address.ilike(keyword),
                AlertRule.name.ilike(keyword),
            )
        )
    
    total = query.count()
    histories = query.order_by(AlertHistory.started_at.desc()).offset(skip).limit(limit).all()
    
    return {
        "total": total,
        "items": [h.to_dict() for h in histories]
    }


@router.get("/history/{alert_id}", response_model=dict)
async def get_alert_history(alert_id: int, db: Session = Depends(get_db)):
    """获取告警历史详情"""
    alert = db.query(AlertHistory).filter(AlertHistory.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="告警记录不存在")
    return alert.to_dict()


@router.get("/syslog", response_model=dict)
async def list_syslog_events(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    device_id: Optional[int] = None,
    search: Optional[str] = None,
):
    """获取最近的 Syslog 事件"""
    query = db.query(SyslogEvent)
    if device_id:
        query = query.filter(SyslogEvent.device_id == device_id)
    if search:
        query = query.filter(SyslogEvent.message.ilike(f"%{search}%"))

    total = query.count()
    items = query.order_by(SyslogEvent.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": item.id,
                "device_id": item.device_id,
                "source_ip": item.source_ip,
                "source_host": item.source_host,
                "facility": item.facility,
                "severity": item.severity,
                "app_name": item.app_name,
                "message": item.message,
                "raw_message": item.raw_message,
                "created_at": item.created_at,
            }
            for item in items
        ],
    }


@router.post("/history/{alert_id}/acknowledge")
async def acknowledge_alert(
    alert_id: int,
    data: AlertAcknowledge,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """确认告警"""
    alert = db.query(AlertHistory).filter(AlertHistory.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="告警记录不存在")
    
    if alert.status != "firing":
        raise HTTPException(status_code=400, detail="只能确认正在触发的告警")
    
    alert.status = "acknowledged"
    username = (data.actor_username or current_user.username or "admin").strip() or "admin"
    alert.acknowledged_by = username
    alert.acknowledged_at = _utc_now()
    
    db.commit()
    db.refresh(alert)
    
    logger.info("告警已确认", alert_id=alert_id, user=username)
    return alert.to_dict()


@router.post("/history/{alert_id}/resolve")
async def resolve_alert(
    alert_id: int,
    data: AlertResolve,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """解决告警"""
    alert = db.query(AlertHistory).filter(AlertHistory.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="告警记录不存在")
    
    if alert.status == "resolved":
        raise HTTPException(status_code=400, detail="告警已恢复，无需暂停复查")
    
    username = (data.actor_username or current_user.username or "admin").strip() or "admin"
    now = _utc_now()
    snooze_until = now + timedelta(hours=1)
    alert.status = "snoozed"
    alert.resolved_at = now
    alert.resolved_by = username
    note = (data.note or "人工点击已解决").strip()
    alert.resolution_note = f"{note}；暂停复查至 {_format_snooze_until(snooze_until)}，到期后如故障仍存在会重新触发。"
    
    db.commit()
    db.refresh(alert)
    
    logger.info("告警已解决", alert_id=alert_id, user=username)
    return alert.to_dict()


@router.post("/history/{alert_id}/ignore")
async def ignore_alert(
    alert_id: int,
    data: AlertIgnore,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """忽略告警"""
    alert = db.query(AlertHistory).filter(AlertHistory.id == alert_id).first()
    if not alert:
        raise HTTPException(status_code=404, detail="告警记录不存在")

    if alert.status == "resolved":
        raise HTTPException(status_code=400, detail="已解决告警无需忽略")

    username = (data.actor_username or current_user.username or "admin").strip() or "admin"
    alert.status = "ignored"
    alert.ignored_by = username
    alert.ignored_at = _utc_now()
    alert.resolution_note = data.note or alert.resolution_note

    db.commit()
    db.refresh(alert)
    from app.tasks.alert_tasks import _send_alert_event_notification
    _send_alert_event_notification.delay(alert.id, "ignored", username)

    logger.info("告警已忽略", alert_id=alert_id, user=username)
    return alert.to_dict()


@router.get("/silences", response_model=dict)
async def list_alert_silences(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    enabled: Optional[bool] = None,
):
    query = db.query(AlertSilence)
    if enabled is not None:
        query = query.filter(AlertSilence.enabled == (1 if enabled else 0))
    total = query.count()
    items = query.order_by(AlertSilence.created_at.desc()).offset(skip).limit(limit).all()
    response_items = []
    for item in items:
        data = item.to_dict()
        data["matched_active_alerts"] = _count_silence_matches_cached(db, item, active_only=True)
        data["matched_total_alerts"] = _count_silence_matches_cached(db, item, active_only=False)
        response_items.append(data)
    return {"total": total, "items": response_items}


@router.get("/silences/{silence_id}/matches", response_model=dict)
async def list_alert_silence_matches(
    silence_id: int,
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    active_only: bool = Query(False, description="只查看当前触发中的命中告警"),
):
    silence = db.query(AlertSilence).filter(AlertSilence.id == silence_id).first()
    if not silence:
        raise HTTPException(status_code=404, detail="告警屏蔽不存在")
    matched_alerts = _get_silence_matched_alerts(db, silence, active_only=active_only)
    return {
        "total": len(matched_alerts),
        "items": [alert.to_dict() for alert in matched_alerts[skip: skip + limit]],
    }


@router.post("/silences", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_alert_silence(
    payload: AlertSilenceCreate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    silence = AlertSilence(
        name=payload.name,
        rule_id=payload.rule_id,
        device_id=payload.device_id,
        target_pattern=payload.target_pattern,
        include_device_ip=payload.include_device_ip,
        include_interface=payload.include_interface,
        include_message=payload.include_message,
        exclude_device_ip=payload.exclude_device_ip,
        exclude_interface=payload.exclude_interface,
        exclude_message=payload.exclude_message,
        starts_at=payload.starts_at,
        conditions=payload.conditions or [],
        reason=payload.reason,
        created_by=(payload.actor_username or current_user.username or "admin").strip() or "admin",
        enabled=1 if payload.enabled else 0,
        expires_at=payload.expires_at,
    )
    db.add(silence)
    db.commit()
    db.refresh(silence)
    return silence.to_dict()


@router.put("/silences/{silence_id}", response_model=dict)
async def update_alert_silence(
    silence_id: int,
    payload: AlertSilenceUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    silence = db.query(AlertSilence).filter(AlertSilence.id == silence_id).first()
    if not silence:
        raise HTTPException(status_code=404, detail="告警屏蔽不存在")
    update_data = payload.model_dump(exclude_unset=True)
    if "enabled" in update_data:
        update_data["enabled"] = 1 if update_data["enabled"] else 0
    for key, value in update_data.items():
        setattr(silence, key, value)
    silence.updated_at = _utc_now()
    db.commit()
    db.refresh(silence)
    return silence.to_dict()


@router.delete("/silences/{silence_id}")
async def delete_alert_silence(
    silence_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    silence = db.query(AlertSilence).filter(AlertSilence.id == silence_id).first()
    if not silence:
        raise HTTPException(status_code=404, detail="告警屏蔽不存在")
    db.delete(silence)
    db.commit()
    return {"message": "告警屏蔽已删除"}


@router.get("/stats")
async def get_alert_stats(db: Session = Depends(get_db)):
    """获取告警统计"""
    total_firing = db.query(AlertHistory).filter(AlertHistory.status == "firing").count()
    total_resolved = db.query(AlertHistory).filter(AlertHistory.status == "resolved").count()
    
    # 按严重级别统计
    by_severity = {}
    severity_counts = db.query(AlertRule.severity, AlertHistory.id).join(
        AlertHistory, AlertRule.id == AlertHistory.rule_id
    ).filter(AlertHistory.status == "firing").all()
    
    for severity, _ in severity_counts:
        normalized = _normalize_severity(severity)
        by_severity[normalized] = by_severity.get(normalized, 0) + 1
    
    # 按设备统计
    by_device = {}
    device_counts = db.query(Device.name, AlertHistory.id).join(
        AlertHistory, Device.id == AlertHistory.device_id
    ).filter(AlertHistory.status == "firing").all()
    
    for device_name, _ in device_counts:
        by_device[device_name] = by_device.get(device_name, 0) + 1
    
    return {
        "total_firing": total_firing,
        "total_resolved": total_resolved,
        "by_severity": by_severity,
        "by_device": by_device
    }
