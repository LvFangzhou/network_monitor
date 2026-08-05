from types import SimpleNamespace

from app.collectors.snmp_collector import SNMPCollector


def test_h3c_package_table_extracts_active_patch_version(monkeypatch):
    collector = SNMPCollector()
    base = "1.3.6.1.4.1.25506.2.3.1.7.2.1"
    rows = [
        (f"{base}.2.917505", "s9850_6850-cmw710-boot-r6715.bin"),
        (f"{base}.2.917507", "s9850_6850-cmw710-r6715hs09.bin"),
        (f"{base}.5.917505", 1),
        (f"{base}.5.917507", 4),
        (f"{base}.8.917507", "system-patch package"),
        (f"{base}.10.917505", "Release 6715"),
        (f"{base}.10.917507", "R6715HS09"),
    ]
    monkeypatch.setattr(collector, "snmp_walk", lambda _device, _oid: rows)

    result = collector._collect_software_patches(
        SimpleNamespace(ip_address="192.0.2.1"),
        {"software_package_table_oid": base},
    )

    assert result["software_patches"] == ["R6715HS09"]
    assert result["software_patch_packages"] == [{
        "index": "917507",
        "name": "s9850_6850-cmw710-r6715hs09.bin",
        "type": 4,
        "description": "system-patch package",
        "version": "R6715HS09",
    }]
