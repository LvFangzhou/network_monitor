"""
System self-check tasks.
"""
import asyncio
import json
import os
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional

from celery import shared_task

from app.config import settings
from app.core import get_logger
from app.database import SessionLocal
from app.models import AlertHistory, AuditLog, BmpMessage, QualityMtrEvent, SyslogEvent
from app.utils import influx_client, notification_manager, redis_client
from app.utils.server_resources import collect_host_resource_sample, store_host_resource_sample

logger = get_logger(__name__)

INFLUX_HEALTH_LOCK_KEY = "system:health:influxdb:lock"
INFLUX_HEALTH_STATE_KEY = "system:health:influxdb:state"
INFLUX_HEALTH_ALERT_KEY = "system:health:influxdb:last_alert"


@shared_task
def collect_server_resources() -> Dict[str, Any]:
    """Persist one host resource sample for the shared seven-day dashboard history."""
    sample = collect_host_resource_sample()
    store_host_resource_sample(sample)
    return {
        "timestamp": sample.get("timestamp"),
        "cpu_percent": sample.get("cpu", {}).get("percent"),
        "memory_percent": sample.get("memory", {}).get("percent"),
        "network_interfaces": len(sample.get("network") or []),
    }


def _detect_webhook_provider(webhook_url: str) -> str:
    url = (webhook_url or "").lower()
    if "work.weixin.qq.com" in url or "qyapi.weixin.qq.com" in url:
        return "wechat"
    if "oapi.dingtalk.com" in url or "api.dingtalk.com" in url:
        return "dingtalk"
    if "open.feishu.cn" in url or "open.larksuite.com" in url:
        return "feishu"
    return "webhook"


def _notification_channels() -> List[Dict[str, Any]]:
    channels: List[Dict[str, Any]] = []
    for webhook_url in [
        settings.SYSTEM_ALERT_WEBHOOK_URL,
        settings.WECHAT_WEBHOOK_URL,
        settings.DINGTALK_WEBHOOK_URL,
        settings.TACACS_WEBHOOK_URL,
    ]:
        value = (webhook_url or "").strip()
        if not value:
            continue
        channel_type = _detect_webhook_provider(value)
        config_key = "url" if channel_type == "webhook" else "webhook"
        if any(item["config"].get(config_key) == value for item in channels):
            continue
        channels.append({"type": channel_type, "config": {config_key: value}})
    return channels


async def _send_system_alert(title: str, content: str, rows: List[Dict[str, str]]) -> None:
    channels = _notification_channels()
    if not channels:
        logger.warning("系统自检异常但没有配置机器人通知", title=title, content=content)
        return

    card_data = {
        "severity": "P0",
        "rows": rows,
    }
    for channel in channels:
        await notification_manager.send_notification(
            channel["type"],
            channel["config"],
            title,
            content,
            card_data,
        )


def _write_state(status: str, failures: List[str]) -> None:
    payload = {
        "status": status,
        "failures": failures,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
    redis_client.set(INFLUX_HEALTH_STATE_KEY, json.dumps(payload, ensure_ascii=False))


def _check_influx_data_path() -> List[str]:
    failures: List[str] = []
    data_path = (settings.INFLUXDB_DATA_PATH or "").strip()
    if not data_path:
        return failures
    if not os.path.isdir(data_path):
        failures.append(f"InfluxDB 数据目录不存在: {data_path}")
        return failures
    for relative_path in ["engine", "engine/data", "engine/wal"]:
        full_path = os.path.join(data_path, relative_path)
        if not os.path.isdir(full_path):
            failures.append(f"InfluxDB 数据子目录不存在: {full_path}")
    return failures


def _check_influx_write_query() -> List[str]:
    failures: List[str] = []
    now = datetime.now(timezone.utc)
    ok = influx_client.write_point(
        measurement="system_health",
        tags={"component": "influxdb", "probe": "write_query"},
        fields={"ok": 1.0},
        timestamp=now,
        sync=True,
    )
    if not ok:
        return ["InfluxDB 写入探针失败"]

    flux = f'''
    from(bucket: "{influx_client.bucket}")
      |> range(start: -5m)
      |> filter(fn: (r) => r._measurement == "system_health")
      |> filter(fn: (r) => r.component == "influxdb")
      |> filter(fn: (r) => r.probe == "write_query")
      |> filter(fn: (r) => r._field == "ok")
      |> last()
    '''
    try:
        rows = influx_client.query(flux)
        if not rows:
            failures.append("InfluxDB 查询不到刚写入的探针数据")
    except Exception as exc:
        failures.append(f"InfluxDB 查询探针失败: {exc}")
    return failures


@shared_task
def check_influxdb_storage_health() -> Dict[str, Any]:
    """Check the InfluxDB storage mount and write path, then notify on failure."""
    lock_acquired = redis_client.set(INFLUX_HEALTH_LOCK_KEY, "1", ex=55, nx=True)
    if not lock_acquired:
        return {"status": "skipped", "reason": "locked"}

    failures = []
    try:
        failures.extend(_check_influx_data_path())
        failures.extend(_check_influx_write_query())
        if failures:
            _write_state("unhealthy", failures)
            logger.error("InfluxDB 自检失败", failures=failures)
            if redis_client.set(INFLUX_HEALTH_ALERT_KEY, "1", ex=900, nx=True):
                content = "\n".join(f"- {item}" for item in failures)
                rows = [
                    {"label": "组件", "value": "InfluxDB 时序库"},
                    {"label": "状态", "value": "异常"},
                    {"label": "检查时间", "value": datetime.now().strftime("%Y-%m-%d %H:%M:%S")},
                    {"label": "异常原因", "value": "；".join(failures)},
                ]
                asyncio.run(_send_system_alert("系统自检告警：InfluxDB 写入异常", content, rows))
            return {"status": "unhealthy", "failures": failures}

        _write_state("healthy", [])
        return {"status": "healthy"}
    finally:
        redis_client.delete(INFLUX_HEALTH_LOCK_KEY)


@shared_task
def cleanup_monitoring_history() -> Dict[str, int]:
    """每日清理低价值历史；活动告警永不删除。"""
    now = datetime.now(timezone.utc)
    db = SessionLocal()
    deleted: Dict[str, int] = {}
    try:
        # 已明确由光模块HIGH/LOW缓存串用造成的误报，可直接清理。
        deleted["confirmed_false_optical_alerts"] = (
            db.query(AlertHistory)
            .filter(
                AlertHistory.status == "resolved",
                AlertHistory.resolution_note == "修复光模块高低阈值目标缓存串用导致的误报",
            )
            .delete(synchronize_session=False)
        )
        deleted["resolved_alerts_180d"] = (
            db.query(AlertHistory)
            .filter(
                AlertHistory.status.in_(["resolved", "ignored"]),
                AlertHistory.started_at < now - timedelta(days=180),
            )
            .delete(synchronize_session=False)
        )
        deleted["read_audits_7d"] = (
            db.query(AuditLog)
            .filter(AuditLog.action == "view", AuditLog.created_at < now - timedelta(days=7))
            .delete(synchronize_session=False)
        )
        deleted["write_audits_180d"] = (
            db.query(AuditLog)
            .filter(AuditLog.action != "view", AuditLog.created_at < now - timedelta(days=180))
            .delete(synchronize_session=False)
        )
        deleted["syslog_90d"] = (
            db.query(SyslogEvent)
            .filter(SyslogEvent.created_at < now - timedelta(days=90))
            .delete(synchronize_session=False)
        )
        deleted["bmp_30d"] = (
            db.query(BmpMessage)
            .filter(BmpMessage.created_at < now - timedelta(days=30))
            .delete(synchronize_session=False)
        )
        deleted["mtr_events_90d"] = (
            db.query(QualityMtrEvent)
            .filter(QualityMtrEvent.created_at < now - timedelta(days=90))
            .delete(synchronize_session=False)
        )
        # MTR snapshot 可能仍被较新的路径变化事件引用；快照随目标级联删除，
        # 此处只清事件，避免破坏外键或历史路径对比。
        db.commit()
        logger.info("监控历史保留策略执行完成", **deleted)
        return deleted
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()
