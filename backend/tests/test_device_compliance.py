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
        capabilities={"snmp": False, "exporter": True, "syslog": True, "tacacs": False},
        required_checks=["model_profile", "exporter", "syslog", "tacacs"],
        priority=10,
        is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "collected_at": "2026-07-30T06:10:59+00:00",
        "system_info": {"sys_name": "aster-leaf", "software_version": "1.0", "snmp_model": "CX532P"},
    })

    result = device_compliance.evaluate_device(
        device, [profile], [], datetime.now(timezone.utc), set(),
    )
    checks = _check_map(result)

    assert "snmp" not in checks
    assert checks["exporter"]["status"] == "passed"
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


def test_h3c_version_compares_comware_platform_and_release_separately(monkeypatch):
    device = Device(
        id=5, name="s6805", ip_address="192.0.2.14", vendor="H3C",
        model="S6805-54HF", is_monitored=True, monitor_source="snmp", custom_fields={},
    )
    profile = DeviceModelProfile(
        id=5, name="S6805-54HF", vendor="H3C", model_pattern="S6805-54HF",
        network_type="general", capabilities={"snmp": False, "syslog": False, "tacacs": False},
        required_checks=["model_profile", "version"], priority=100, is_active=True,
    )
    baseline = VersionBaseline(
        id=2, name="S6805生产版本", model_profile_id=5, vendor="H3C",
        platform_version="7.1.070", allowed_releases=["6715P01"],
        allowed_versions=[], required_patches=[], forbidden_versions=[],
        priority=100, is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "system_info": {"software_version": "Software Version 7.1.070, Release 6715P01"},
    })

    result = device_compliance.evaluate_device(device, [profile], [baseline], None, set())
    version_check = _check_map(result)["version"]

    assert version_check["status"] == "passed"
    assert version_check["evidence"]["current_comware_platform"] == "7.1.070"
    assert version_check["evidence"]["current_software_release"] == "6715P01"


def test_h3c_release_mismatch_fails_even_when_comware_platform_matches(monkeypatch):
    device = Device(
        id=6, name="s6805", ip_address="192.0.2.15", vendor="H3C",
        model="S6805-54HF", is_monitored=True, monitor_source="snmp", custom_fields={},
    )
    profile = DeviceModelProfile(
        id=6, name="S6805-54HF", vendor="H3C", model_pattern="S6805-54HF",
        network_type="general", capabilities={"snmp": False, "syslog": False, "tacacs": False},
        required_checks=["model_profile", "version"], priority=100, is_active=True,
    )
    baseline = VersionBaseline(
        id=3, name="S6805生产版本", model_profile_id=6, vendor="H3C",
        platform_version="7.1.070", allowed_releases=["6715P01"],
        allowed_versions=[], required_patches=[], forbidden_versions=[],
        priority=100, is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "system_info": {"software_version": "Software Version 7.1.070, Release 6715"},
    })

    result = device_compliance.evaluate_device(device, [profile], [baseline], None, set())

    assert _check_map(result)["version"]["status"] == "failed"


def test_h3c_comware_platform_supports_wildcard(monkeypatch):
    device = Device(
        id=7, name="s9867", ip_address="192.0.2.16", vendor="H3C",
        model="S9867-128DH", is_monitored=True, monitor_source="snmp", custom_fields={},
    )
    profile = DeviceModelProfile(
        id=7, name="S9867-128DH", vendor="H3C", model_pattern="S9867*",
        network_type="roce", capabilities={"snmp": False, "syslog": False, "tacacs": False},
        required_checks=["model_profile", "version"], priority=100, is_active=True,
    )
    baseline = VersionBaseline(
        id=4, name="S9867生产版本", model_profile_id=7, vendor="H3C",
        platform_version="7.1.*", allowed_releases=["6635P01"],
        allowed_versions=[], required_patches=[], forbidden_versions=[],
        priority=100, is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "system_info": {"software_version": "Software Version 7.1.076, Release 6635P01"},
    })

    result = device_compliance.evaluate_device(device, [profile], [baseline], None, set())

    assert _check_map(result)["version"]["status"] == "passed"


def test_compliance_uses_snmp_patch_evidence_from_system_info(monkeypatch):
    device = Device(
        id=8, name="s6850", ip_address="192.0.2.17", vendor="H3C",
        model="S6850-56HF", is_monitored=True, monitor_source="snmp", custom_fields={},
    )
    profile = DeviceModelProfile(
        id=8, name="S6850-56HF", vendor="H3C", model_pattern="S6850*",
        network_type="general", capabilities={"snmp": False, "syslog": False, "tacacs": False},
        required_checks=["model_profile", "version", "patch"], priority=100, is_active=True,
    )
    baseline = VersionBaseline(
        id=5, name="S6850生产版本", model_profile_id=8, vendor="H3C",
        platform_version="7.1.*", allowed_releases=["6715"],
        allowed_versions=[], required_patches=["R6715HS09"], forbidden_versions=[],
        priority=100, is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "system_info": {
            "software_version": "Software Version 7.1.070, Release 6715",
            "software_patches": ["R6715HS09"],
            "software_patch_packages": [{"name": "s9850_6850-cmw710-r6715hs09.bin", "version": "R6715HS09"}],
        },
    })

    result = device_compliance.evaluate_device(device, [profile], [baseline], None, set())

    assert _check_map(result)["patch"]["status"] == "passed"
    assert result["observed_patches"] == ["R6715HS09"]
