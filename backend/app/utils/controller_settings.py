"""
控制器集成配置。

当前先使用文件持久化，和配置备份机器人通知保持同一类轻量配置方式。
GET 接口会隐藏密码，只有保存/测试时才接收明文密码。
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict, List, Optional


CONTROLLER_DATA_DIR = Path("/app/data/tacacs")
CONTROLLER_SETTINGS_FILE = CONTROLLER_DATA_DIR / "controller_settings.json"
MASKED_SECRET = "******"


def _default_controller(index: int = 1) -> Dict[str, Any]:
    return {
        "id": f"controller-{index}",
        "name": f"控制器{index}",
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


def _default_settings() -> Dict[str, Any]:
    return {"controllers": [_default_controller(1)]}


def _looks_like_legacy_single_controller(payload: Dict[str, Any]) -> bool:
    return "controllers" not in payload and any(key in payload for key in ["base_url", "username", "password"])


def _normalize_controller(raw: Dict[str, Any], index: int = 1, current: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    base = {**_default_controller(index), **(current or {})}
    for key in _default_controller(index).keys():
        if key not in raw:
            continue
        value = raw[key]
        if key == "password" and value == MASKED_SECRET:
            continue
        base[key] = value

    base["id"] = str(base.get("id") or f"controller-{index}").strip() or f"controller-{index}"
    base["name"] = str(base.get("name") or f"控制器{index}").strip() or f"控制器{index}"
    base["base_url"] = str(base.get("base_url") or "").strip().rstrip("/")
    base["username"] = str(base.get("username") or "").strip()
    base["password"] = str(base.get("password") or "")
    base["user_id"] = str(base.get("user_id") or "1").strip() or "1"
    base["region_id"] = str(base.get("region_id") or "").strip()
    base["effective_time"] = int(base.get("effective_time") or 7200)
    base["timeout"] = float(base.get("timeout") or 5)
    base["area_type"] = int(base.get("area_type") if base.get("area_type") is not None else 1)
    base["enabled"] = bool(base.get("enabled"))
    base["insecure"] = bool(base.get("insecure"))
    return base


def _mask_controllers(controllers: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    masked = []
    for controller in controllers:
        item = {**controller}
        if item.get("password"):
            item["password"] = MASKED_SECRET
        masked.append(item)
    return masked


def _coerce_settings(raw: Dict[str, Any] | None = None) -> Dict[str, Any]:
    raw = raw if isinstance(raw, dict) else {}
    if _looks_like_legacy_single_controller(raw):
        return {"controllers": [_normalize_controller(raw, 1)]}

    raw_controllers = raw.get("controllers")
    if not isinstance(raw_controllers, list) or not raw_controllers:
        return _default_settings()

    controllers = []
    for index, item in enumerate(raw_controllers, start=1):
        if isinstance(item, dict):
            controllers.append(_normalize_controller(item, index))
    return {"controllers": controllers or [_default_controller(1)]}


def load_controller_settings(mask_secret: bool = False) -> Dict[str, Any]:
    settings = _default_settings()
    try:
        if CONTROLLER_SETTINGS_FILE.exists():
            loaded = json.loads(CONTROLLER_SETTINGS_FILE.read_text(encoding="utf-8"))
            if isinstance(loaded, dict):
                settings = _coerce_settings(loaded)
    except Exception:
        pass
    if mask_secret:
        settings = {**settings, "controllers": _mask_controllers(settings.get("controllers") or [])}
    return settings


def save_controller_settings(payload: Dict[str, Any]) -> Dict[str, Any]:
    current = load_controller_settings(mask_secret=False)
    if _looks_like_legacy_single_controller(payload):
        payload = {"controllers": [payload]}

    current_by_id = {str(item.get("id")): item for item in current.get("controllers") or []}
    next_controllers: List[Dict[str, Any]] = []
    raw_controllers = payload.get("controllers") if isinstance(payload, dict) else None
    if isinstance(raw_controllers, list):
        for index, item in enumerate(raw_controllers, start=1):
            if not isinstance(item, dict):
                continue
            controller_id = str(item.get("id") or f"controller-{index}")
            next_controllers.append(_normalize_controller(item, index, current_by_id.get(controller_id)))
    next_settings = {"controllers": next_controllers or current.get("controllers") or [_default_controller(1)]}

    CONTROLLER_DATA_DIR.mkdir(parents=True, exist_ok=True)
    CONTROLLER_SETTINGS_FILE.write_text(
        json.dumps(next_settings, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return {**next_settings, "controllers": _mask_controllers(next_settings.get("controllers") or [])}


def find_controller_settings(controller_id: Optional[str] = None, require_enabled: bool = False) -> Dict[str, Any]:
    settings = load_controller_settings(mask_secret=False)
    controllers = settings.get("controllers") or []
    selected = None
    if controller_id:
        selected = next((item for item in controllers if str(item.get("id")) == str(controller_id)), None)
    if selected is None:
        selected = next((item for item in controllers if item.get("enabled")), None)
    if selected is None and controllers:
        selected = controllers[0]
    if not selected:
        raise ValueError("未配置控制器")
    if require_enabled and not selected.get("enabled"):
        raise ValueError("控制器集成尚未启用")
    return selected


def merge_runtime_settings(payload: Dict[str, Any] | None = None) -> Dict[str, Any]:
    if payload and "controllers" in payload:
        raise ValueError("测试接口一次只能测试一个控制器")
    settings = load_controller_settings(mask_secret=False)
    if settings.get("controllers"):
        current = settings["controllers"][0]
    else:
        current = _default_controller(1)
    settings = {**current}
    if payload:
        for key, value in payload.items():
            if key == "password" and value == MASKED_SECRET:
                continue
            if value is not None:
                settings[key] = value
    settings["base_url"] = str(settings.get("base_url") or "").strip().rstrip("/")
    return settings
