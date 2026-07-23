"""设备监控模板与功能开关。

模板用于表达设备在网络中的用途，功能开关用于告警的最终匹配。厂商只决定
采集能力，不能单独推断所有业务特性，例如 AsterNOS Fabric 不承载 RoCE。
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Dict, Mapping, Optional


MONITOR_PROFILES = {
    "roce_fabric",
    "dc_fabric",
    "general_switch",
    "oob_switch",
    "firewall",
    "border",
}

MONITOR_FEATURES = {"roce", "evpn_vxlan", "flow_export"}


def _text(value: Any) -> str:
    return str(value or "").strip().lower()


def is_asternos_vendor(vendor: Any) -> bool:
    value = _text(vendor)
    return any(marker in value for marker in ("asternos", "asterfusion", "asteros", "aster", "星融元"))


def is_hillstone_vendor(vendor: Any) -> bool:
    value = _text(vendor)
    return "hillstone" in value or "山石" in value


def is_h3c_vendor(vendor: Any) -> bool:
    value = _text(vendor)
    return any(marker in value for marker in ("h3c", "comware", "华三", "新华三"))


def infer_monitor_profile(vendor: Any, model: Any, device_role: Any) -> str:
    role = _text(device_role)
    model_value = _text(model)
    if is_hillstone_vendor(vendor):
        return "firewall"
    if role in {"oob", "out-of-band", "带外", "带外管理"} or "oob" in role:
        return "oob_switch"
    if is_h3c_vendor(vendor) and "s9867-128dh" in model_value:
        return "roce_fabric"
    # 现场管理网为 EVPN/VXLAN Fabric，主要由 H3C 6805/9850/6850 与
    # AsterNOS 设备构成；AsterNOS 仅属于管理 Fabric，不启用 RoCE。
    if is_asternos_vendor(vendor):
        return "dc_fabric"
    if is_h3c_vendor(vendor) and any(family in model_value for family in ("6805", "9850", "6850")):
        return "dc_fabric"
    if role in {"border", "edge", "出口", "边界"} or "border" in role:
        return "border"
    if role in {"spine", "leaf", "aggregation", "agg"} or any(
        marker in role for marker in ("spine", "leaf", "汇聚")
    ):
        return "dc_fabric"
    return "general_switch"


def default_monitor_features(profile: str, vendor: Any) -> Dict[str, bool]:
    features = {
        "roce": profile == "roce_fabric",
        "evpn_vxlan": profile == "dc_fabric",
        "flow_export": False,
    }
    # 当前 AsterNOS 网络明确不承载 RoCE，避免人工误选后产生无意义采集和告警。
    if is_asternos_vendor(vendor):
        features["roce"] = False
    # 当前两张 S9867 RoCE 参数网不运行 EVPN/VXLAN，避免手工误选后加载无效规则。
    if profile == "roce_fabric":
        features["evpn_vxlan"] = False
    return features


def normalize_monitoring_profile(
    custom_fields: Optional[Mapping[str, Any]],
    vendor: Any,
    model: Any,
    device_role: Any,
) -> Dict[str, Any]:
    result: Dict[str, Any] = deepcopy(dict(custom_fields or {}))
    monitoring = result.get("monitoring")
    if not isinstance(monitoring, dict):
        monitoring = {}

    configured_profile = _text(monitoring.get("monitor_profile"))
    profile = configured_profile if configured_profile in MONITOR_PROFILES else infer_monitor_profile(vendor, model, device_role)
    supplied_features = monitoring.get("features")
    supplied_features = supplied_features if isinstance(supplied_features, dict) else {}
    features = default_monitor_features(profile, vendor)
    for key in MONITOR_FEATURES:
        if key in supplied_features:
            features[key] = bool(supplied_features[key])
    if is_asternos_vendor(vendor):
        features["roce"] = False
    if profile == "roce_fabric":
        features["evpn_vxlan"] = False

    monitoring["monitor_profile"] = profile
    monitoring["features"] = features
    result["monitoring"] = monitoring
    return result


def get_device_monitor_profile(device: Any) -> str:
    custom_fields = getattr(device, "custom_fields", None) or {}
    monitoring = custom_fields.get("monitoring") if isinstance(custom_fields, dict) else None
    configured = _text(monitoring.get("monitor_profile")) if isinstance(monitoring, dict) else ""
    if configured in MONITOR_PROFILES:
        return configured
    return infer_monitor_profile(
        getattr(device, "vendor", None),
        getattr(device, "model", None),
        getattr(device, "device_role", None),
    )


def get_device_monitor_features(device: Any) -> Dict[str, bool]:
    profile = get_device_monitor_profile(device)
    features = default_monitor_features(profile, getattr(device, "vendor", None))
    custom_fields = getattr(device, "custom_fields", None) or {}
    monitoring = custom_fields.get("monitoring") if isinstance(custom_fields, dict) else None
    configured = monitoring.get("features") if isinstance(monitoring, dict) else None
    if isinstance(configured, dict):
        for key in MONITOR_FEATURES:
            if key in configured:
                features[key] = bool(configured[key])
    if is_asternos_vendor(getattr(device, "vendor", None)):
        features["roce"] = False
    if profile == "roce_fabric":
        features["evpn_vxlan"] = False
    return features


def device_feature_enabled(device: Any, feature: str) -> bool:
    return bool(get_device_monitor_features(device).get(str(feature or "").strip()))
