from types import SimpleNamespace

from app.tasks.alert_tasks import _device_matches_monitoring_scope
from app.utils.monitor_profile import (
    get_device_monitor_features,
    get_device_monitor_profile,
    normalize_monitoring_profile,
)


def device(vendor, model="", role="", custom_fields=None):
    return SimpleNamespace(vendor=vendor, model=model, device_role=role, custom_fields=custom_fields or {})


def test_h3c_s9867_is_roce_fabric():
    target = device("H3C", "S9867-128DH", "Spine")
    assert get_device_monitor_profile(target) == "roce_fabric"
    assert get_device_monitor_features(target)["roce"] is True
    assert get_device_monitor_features(target)["evpn_vxlan"] is False
    assert _device_matches_monitoring_scope(target, {"required_features": ["roce"]}) is True


def test_asternos_never_enables_roce():
    fields = {"monitoring": {"monitor_profile": "roce_fabric", "features": {"roce": True}}}
    normalized = normalize_monitoring_profile(fields, "Asteros", "CX308P48Y", "Leaf")
    target = device("Asteros", "CX308P48Y", "Leaf", normalized)
    assert normalized["monitoring"]["features"]["roce"] is False
    assert _device_matches_monitoring_scope(target, {"required_features": ["roce"]}) is False


def test_oob_and_firewall_profiles():
    assert get_device_monitor_profile(device("H3C", "S5130", "OOB")) == "oob_switch"
    assert get_device_monitor_profile(device("Hillstone", "SG-6000", "Firewall")) == "firewall"


def test_management_fabric_vendor_and_models():
    assert get_device_monitor_profile(device("Asteros", "CX564P-NT-AC", "Leaf")) == "dc_fabric"
    for model in ("S6805", "S9850-32H", "S6850-56HF"):
        target = device("H3C", model, "Core")
        assert get_device_monitor_profile(target) == "dc_fabric"
        assert get_device_monitor_features(target)["evpn_vxlan"] is True
        assert get_device_monitor_features(target)["roce"] is False


def test_normalization_preserves_existing_monitoring_fields():
    source = {"monitoring": {"interface_scope": {"mode": "include", "include": "1/0/1"}}}
    result = normalize_monitoring_profile(source, "H3C", "S6805", "Border")
    assert result["monitoring"]["interface_scope"] == source["monitoring"]["interface_scope"]
    assert result["monitoring"]["monitor_profile"] == "dc_fabric"
