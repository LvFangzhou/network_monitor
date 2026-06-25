"""
控制器集成配置。

当前先使用文件持久化，和配置备份机器人通知保持同一类轻量配置方式。
GET 接口会隐藏密码，只有保存/测试时才接收明文密码。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


CONTROLLER_DATA_DIR = Path("/app/data/tacacs")
CONTROLLER_SETTINGS_FILE = CONTROLLER_DATA_DIR / "controller_settings.json"
MASKED_SECRET = "******"


def _default_settings() -> Dict[str, Any]:
    return {
        "enabled": False,
        "base_url": "http://10.239.16.1:30000",
        "username": "",
        "password": "",
        "user_id": "1",
        "region_id": "",
        "effective_time": 7200,
        "timeout": 5,
        "area_type": 1,
        "insecure": False,
    }


def load_controller_settings(mask_secret: bool = False) -> Dict[str, Any]:
    settings = _default_settings()
    try:
        if CONTROLLER_SETTINGS_FILE.exists():
            loaded = json.loads(CONTROLLER_SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings.update(loaded)
    except Exception:
        pass
    if mask_secret and settings.get("password"):
        settings["password"] = MASKED_SECRET
    return settings


def save_controller_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    current = load_controller_settings(mask_secret=False)
    next_settings = {**current}
    for key in _default_settings().keys():
        if key not in payload:
            continue
        value = payload[key]
        if key == "password" and value == MASKED_SECRET:
            continue
        next_settings[key] = value

    next_settings["base_url"] = str(next_settings.get("base_url") or "").strip().rstrip("/")
    next_settings["username"] = str(next_settings.get("username") or "").strip()
    next_settings["user_id"] = str(next_settings.get("user_id") or "1").strip() or "1"
    next_settings["region_id"] = str(next_settings.get("region_id") or "").strip()
    next_settings["effective_time"] = int(next_settings.get("effective_time") or 7200)
    next_settings["timeout"] = float(next_settings.get("timeout") or 5)
    next_settings["area_type"] = int(next_settings.get("area_type") if next_settings.get("area_type") is not None else 1)
    next_settings["enabled"] = bool(next_settings.get("enabled"))
    next_settings["insecure"] = bool(next_settings.get("insecure"))

    CONTROLLER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONTROLLER_SETTINGS_FILE.write_text(
        json.dumps(next_settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {**next_settings, "password": MASKED_SECRET if next_settings.get("password") else ""}


def merge_runtime_settings(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    settings = load_controller_settings(mask_secret=False)
    if payload:
        for key, value in payload.items():
            if key == "password" and value == MASKED_SECRET:
                continue
            if value is not None:
                settings[key] = value
    settings["base_url"] = str(settings.get("base_url") or "").strip().rstrip("/")
    return settings
