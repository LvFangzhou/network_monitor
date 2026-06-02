"""
System self-check tasks.
"""
import asyncio
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from celery import shared_task

from app.config import settings
from app.core import get_logger
from app.utils import influx_client, notification_manager, redis_client

logger = get_logger(__name__)

INFLUX_HEALTH_LOCK_KEY = "system:health:influxdb:lock"
INFLUX_HEALTH_STATE_KEY = "system:health:influxdb:state"
INFLUX_HEALTH_ALERT_KEY = "system:health:influxdb:last_alert"


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
