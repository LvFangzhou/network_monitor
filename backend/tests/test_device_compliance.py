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


def test_asternos_requires_exporter_syslog_and_tacacs_even_for_legacy_profile(monkeypatch):
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
    assert checks["tacacs"]["status"] == "pending"
    assert checks["tacacs"]["required"] is True
    assert result["overall_status"] == "pending"


def test_asternos_hardware_can_pass_with_known_fans_and_unreported_power(monkeypatch):
    device = Device(
        id=31,
        name="aster-hardware",
        ip_address="192.0.2.31",
        vendor="Asteros",
        model="CX308P-48Y-NF-AC",
        is_monitored=True,
        monitor_source="asternos_exporter",
        custom_fields={},
    )
    profile = DeviceModelProfile(
        id=31,
        name="CX308P-48Y-NF-AC",
        vendor="Asteros",
        model_pattern="CX308P-48Y-NF-AC",
        network_type="management",
        capabilities={"snmp": False, "exporter": True, "syslog": False, "tacacs": False},
        required_checks=["model_profile", "exporter", "hardware"],
        priority=10,
        is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "collected_at": "2026-08-03T06:10:59+00:00",
        "system_info": {"sys_name": "aster-hardware", "software_version": "3.1", "snmp_model": "CX308P-48Y-NF-AC"},
        "hardware": {
            "fan_total": 4,
            "fan_down": 0,
            "fan_status_known": True,
            "power_total": 0,
            "power_down": 0,
            "power_status_known": False,
        },
    })

    result = device_compliance.evaluate_device(device, [profile], [], None, set())
    hardware = _check_map(result)["hardware"]

    assert hardware["status"] == "passed"
    assert hardware["message"] == "风扇 4 个运行正常；电源指标未上报"


def test_asternos_identity_matches_recorded_asset_and_requires_all_services(monkeypatch):
    device = Device(
        id=32,
        name="QDD10N24J07U41-CX308P48Y-LEAF3-2",
        ip_address="192.0.2.32",
        vendor="Asteros",
        model="CX308P-48Y-NF-AC",
        serial_number="F02225AB813",
        is_monitored=True,
        monitor_source="asternos_exporter",
        custom_fields={},
    )
    profile = DeviceModelProfile(
        id=32, name="CX308P-48Y-NF-AC", vendor="Asteros", model_pattern="CX308P-48Y-NF-AC",
        network_type="management", capabilities={"exporter": True, "syslog": True, "tacacs": True},
        # Legacy profiles may still contain the retired Part Number check.
        required_checks=["model_profile", "part_number", "version", "patch", "hardware", "exporter", "syslog", "tacacs"],
        priority=10, is_active=True,
    )
    baseline = VersionBaseline(
        id=32, name="CX308P生产基线", model_profile_id=32, vendor="Asteros",
        allowed_versions=["V3.1R0407P04"], required_patches=["V3.1R0407P04-00008"],
        forbidden_versions=[], priority=10, is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "collected_at": "2026-08-03T06:10:59+00:00",
        "system_info": {
            "sys_name": device.name,
            "software_version": "Software V3.1R0407P04",
            "software_patches": ["V3.1R0407P04-00008"],
            "snmp_model": device.model,
            "serial_number": device.serial_number,
        },
        "hardware": {"fan_total": 4, "fan_down": 0, "fan_absent": 0, "fan_status_known": True},
    })

    result = device_compliance.evaluate_device(
        device, [profile], [baseline], datetime.now(timezone.utc), {device.ip_address},
    )
    checks = _check_map(result)

    assert "part_number" not in checks
    for key in ("device_name", "device_model", "serial_number", "version", "patch", "hardware", "exporter", "syslog", "tacacs"):
        assert checks[key]["status"] == "passed"
        assert checks[key]["required"] is True
    assert result["overall_status"] == "compliant"


def test_asternos_identity_mismatch_and_absent_fan_block_onboarding(monkeypatch):
    device = Device(
        id=33, name="recorded-name", ip_address="192.0.2.33", vendor="Asteros",
        model="CX308P-48Y-NF-AC", serial_number="RECORDED-SN", is_monitored=True,
        monitor_source="asternos_exporter", custom_fields={},
    )
    profile = DeviceModelProfile(
        id=33, name="CX308P-48Y-NF-AC", vendor="Asteros", model_pattern="CX308P-48Y-NF-AC",
        network_type="management", capabilities={}, required_checks=["model_profile"],
        priority=10, is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "collected_at": "2026-08-03T06:10:59+00:00",
        "system_info": {
            "sys_name": "actual-name", "snmp_model": device.model,
            "serial_number": "ACTUAL-SN",
        },
        "hardware": {
            "fan_total": 3, "fan_expected_total": 4, "fan_absent": 1,
            "fan_down": 0, "fan_status_known": True,
        },
    })

    result = device_compliance.evaluate_device(device, [profile], [], None, set())
    checks = _check_map(result)

    assert checks["device_name"]["status"] == "failed"
    assert checks["serial_number"]["status"] == "failed"
    assert "part_number" not in checks
    assert checks["hardware"]["status"] == "failed"
    assert "风扇缺位 1 个" in checks["hardware"]["message"]
    assert result["overall_status"] == "non_compliant"


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


def test_ruijie_version_compares_rgos_platform_and_device_version(monkeypatch):
    device = Device(
        id=9, name="s6980", ip_address="192.0.2.18", vendor="锐捷",
        model="S6980", is_monitored=True, monitor_source="snmp", custom_fields={},
    )
    profile = DeviceModelProfile(
        id=9, name="S6980", vendor="锐捷", model_pattern="S6980*",
        network_type="general", capabilities={"snmp": False, "syslog": False, "tacacs": False},
        required_checks=["model_profile", "version"], priority=100, is_active=True,
    )
    baseline = VersionBaseline(
        id=6, name="S6980生产版本", model_profile_id=9, vendor="锐捷",
        platform_version="12.*", allowed_releases=["12.5(2)B0605"],
        allowed_versions=[], required_patches=[], forbidden_versions=[],
        priority=100, is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "system_info": {"software_version": "Software Version S6980_RGOS 12.5(2)B0605"},
    })

    result = device_compliance.evaluate_device(device, [profile], [baseline], None, set())
    version_check = _check_map(result)["version"]

    assert version_check["status"] == "passed"
    assert version_check["evidence"]["current_platform_version"] == "12.5"
    assert version_check["evidence"]["current_device_version"] == "12.5(2)B0605"


def test_ruijie_device_version_mismatch_fails_even_when_platform_matches(monkeypatch):
    device = Device(
        id=10, name="s6500", ip_address="192.0.2.19", vendor="Ruijie",
        model="S6500", is_monitored=True, monitor_source="snmp", custom_fields={},
    )
    profile = DeviceModelProfile(
        id=10, name="S6500", vendor="Ruijie", model_pattern="S6500*",
        network_type="general", capabilities={"snmp": False, "syslog": False, "tacacs": False},
        required_checks=["model_profile", "version"], priority=100, is_active=True,
    )
    baseline = VersionBaseline(
        id=7, name="S6500生产版本", model_profile_id=10, vendor="Ruijie",
        platform_version="11.*", allowed_releases=["11.0(5)B9P61"],
        allowed_versions=[], required_patches=[], forbidden_versions=[],
        priority=100, is_active=True,
    )
    monkeypatch.setattr(device_compliance, "load_overview", lambda _device_id: {
        "system_info": {"software_version": "Software Version S6500-X86_RGOS 11.0(5)B9P62"},
    })

    result = device_compliance.evaluate_device(device, [profile], [baseline], None, set())

    assert _check_map(result)["version"]["status"] == "failed"
