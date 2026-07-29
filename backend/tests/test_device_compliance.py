from datetime import datetime, timezone

from app.models import Device, DeviceModelProfile, VersionBaseline
from app.utils import device_compliance


def _check_map(result):
    return {item["key"]: item for item in result["checks"]}


def test_compliant_device_requires_matching_profile_version_and_services(monkeypatch):
    device = Device(
        id=1,
        name="leaf01",
        ip_address="192.0.2.10",
        vendor="H3C",
        model="S9867-128DH",
        device_role="Leaf",
        is_monitored=True,
        monitor_source="snmp",
        custom_fields={"software_patches": ["PATCH-001"]},
    )
    profile = DeviceModelProfile(
        id=10,
        name="H3C S9867 RoCE",
        vendor="H3C",
        model_pattern="S9867*",
        network_type="roce",
        capabilities={"snmp": True, "syslog": True, "tacacs": True, "roce": True},
        required_checks=["model_profile", "version", "patch", "snmp", "syslog", "tacacs"],
        priority=10,
        is_active=True,
    )
    baseline = VersionBaseline(
        id=20,
        name="S9867生产基线",
        model_profile_id=10,
        allowed_versions=["7.1.076*"],
        required_patches=["PATCH-001"],
        forbidden_versions=[],
        priority=10,
        is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "collected_at": "2026-07-29T10:00:00+00:00",
        "system_info": {
            "sys_name": "leaf01",
            "software_version": "7.1.076 Release 6635",
            "snmp_model": "S9867-128DH",
        },
        "data_sources": {"system_info": {"sys_name": "snmp", "software_version": "snmp"}},
    })

    result = device_compliance.evaluate_device(
        device, [profile], [baseline], datetime.now(timezone.utc), {"192.0.2.10"},
    )

    assert result["overall_status"] == "compliant"
    assert result["score"] == 100
    assert all(item["status"] == "passed" for item in result["checks"] if item["required"])


def test_missing_required_patch_is_non_compliant(monkeypatch):
    device = Device(
        id=2,
        name="leaf02",
        ip_address="192.0.2.11",
        vendor="H3C",
        model="S9867-128DH",
        is_monitored=True,
        monitor_source="snmp",
        custom_fields={"software_patches": []},
    )
    profile = DeviceModelProfile(
        id=11,
        name="H3C S9867",
        vendor="H3C",
        model_pattern="S9867*",
        network_type="roce",
        capabilities={"snmp": True, "syslog": True, "tacacs": True},
        required_checks=["model_profile", "version", "patch"],
        priority=10,
        is_active=True,
    )
    baseline = VersionBaseline(
        id=21,
        name="S9867补丁基线",
        model_profile_id=11,
        allowed_versions=["7.1.076*"],
        required_patches=["PATCH-002"],
        forbidden_versions=[],
        priority=10,
        is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "system_info": {"software_version": "7.1.076", "snmp_model": "S9867-128DH"},
    })

    result = device_compliance.evaluate_device(device, [profile], [baseline], None, set())

    assert result["overall_status"] == "non_compliant"
    assert _check_map(result)["patch"]["status"] == "failed"
    assert "PATCH-002" in _check_map(result)["patch"]["message"]


def test_asternos_can_skip_unsupported_snmp_and_tacacs(monkeypatch):
    device = Device(
        id=3,
        name="aster-leaf",
        ip_address="192.0.2.12",
        vendor="Asteros",
        model="CX532P",
        is_monitored=True,
        monitor_source="asternos_exporter",
        custom_fields={},
    )
    profile = DeviceModelProfile(
        id=12,
        name="Asteros CX532P",
        vendor="Asteros",
        model_pattern="CX532P",
        network_type="management",
        capabilities={"snmp": False, "syslog": True, "tacacs": False},
        required_checks=["model_profile", "snmp", "syslog", "tacacs"],
        priority=10,
        is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "system_info": {"software_version": "1.0", "snmp_model": "CX532P"},
    })

    result = device_compliance.evaluate_device(
        device, [profile], [], datetime.now(timezone.utc), set(),
    )
    checks = _check_map(result)

    assert checks["snmp"]["status"] == "skipped"
    assert checks["tacacs"]["status"] == "skipped"
    assert result["overall_status"] == "compliant"


def test_vendor_prefixed_duplicate_model_can_use_explicit_baseline_scope(monkeypatch):
    device = Device(
        id=4,
        name="s6805",
        ip_address="192.0.2.13",
        vendor="H3C",
        model="S6805-54HF",
        is_monitored=True,
        monitor_source="snmp",
        custom_fields={},
    )
    matched_profile = DeviceModelProfile(
        id=4, name="H3C S6805-54HF", vendor="H3C", model_pattern="S6805-54HF",
        network_type="general", capabilities={"snmp": False, "syslog": False, "tacacs": False},
        required_checks=["model_profile", "version"], priority=100, is_active=True,
    )
    duplicate_profile = DeviceModelProfile(
        id=3, name="H3C H3C S6805-54HF", vendor="H3C", model_pattern="H3C S6805-54HF",
        network_type="general", capabilities={}, required_checks=[], priority=100, is_active=True,
    )
    baseline = VersionBaseline(
        id=1, name="S6805版本", model_profile_id=3, vendor="H3C", model_pattern="6805",
        allowed_versions=["Software Version 7.1.070, Release 6715P01"],
        required_patches=[], forbidden_versions=[], priority=100, is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "system_info": {"software_version": "Software Version 7.1.070, Release 6715P01"},
    })

    result = device_compliance.evaluate_device(
        device, [duplicate_profile, matched_profile], [baseline], None, set(),
    )

    assert result["model_profile_id"] == 4
    assert result["version_baseline_id"] == 1
    assert _check_map(result)["version"]["status"] == "passed"
