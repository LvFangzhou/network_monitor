"""
配置备份页面级设置。

目前用于保存配置备份完成后的机器人通知地址。文件放在 /app/data/tacacs 下，
复用现有持久化挂载，API 和 Celery Worker 都能读取。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List

from app.config import settings

CONFIG_BACKUP_DATA_DIR = Path("/app/data/tacacs")
CONFIG_BACKUP_SETTINGS_FILE = CONFIG_BACKUP_DATA_DIR / "config_backup_settings.json"


def detect_webhook_type(webhook_url: str) -> str:
    url = (webhook_url or "").strip().lower()
    if "work.weixin.qq.com" in url or "qyapi.weixin.qq.com" in url:
        return "wechat"
    if "oapi.dingtalk.com" in url or "api.dingtalk.com" in url:
        return "dingtalk"
    if "open.feishu.cn" in url or "open.larksuite.com" in url:
        return "feishu"
    return "webhook"


def _default_settings() -> Dict[str, Any]:
    channels: List[Dict[str, str]] = []
    for webhook_url in [
        settings.SYSTEM_ALERT_WEBHOOK_URL,
        settings.WECHAT_WEBHOOK_URL,
        settings.DINGTALK_WEBHOOK_URL,
        settings.TACACS_WEBHOOK_URL,
    ]:
        value = (webhook_url or "").strip()
        if not value:
            continue
        if any((item.get("webhook") or item.get("url")) == value for item in channels):
            continue
        channels.append({"type": detect_webhook_type(value), "webhook": value})
    return {"notification_channels": channels}


def load_config_backup_settings() -> Dict[str, Any]:
    default_settings = _default_settings()
    try:
        if CONFIG_BACKUP_SETTINGS_FILE.exists():
            loaded = json.loads(CONFIG_BACKUP_SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                return {**default_settings, **loaded}
    except Exception:
        return default_settings
    return default_settings


def save_config_backup_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    CONFIG_BACKUP_DATA_DIR.mkdir(parents=True, exist_ok=True)
    settings_payload = {**_default_settings(), **payload}
    CONFIG_BACKUP_SETTINGS_FILE.write_text(
        json.dumps(settings_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return settings_payload


def normalize_notification_channels(raw_channels: Any) -> List[Dict[str, Dict[str, str]]]:
    if not isinstance(raw_channels, list):
        return []

    normalized: List[Dict[str, Dict[str, str]]] = []
    for channel in raw_channels:
        if not isinstance(channel, dict):
            continue
        webhook = str(channel.get("webhook") or channel.get("url") or "").strip()
        if not webhook:
            continue
        channel_type = detect_webhook_type(webhook)
        config_key = "url" if channel_type == "webhook" else "webhook"
        if any(item["config"].get(config_key) == webhook for item in normalized):
            continue
        normalized.append({"type": channel_type, "config": {config_key: webhook}})
    return normalized


def config_backup_notification_channels() -> List[Dict[str, Dict[str, str]]]:
    return normalize_notification_channels(load_config_backup_settings().get("notification_channels") or [])
