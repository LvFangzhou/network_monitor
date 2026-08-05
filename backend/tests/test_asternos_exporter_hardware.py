from app.utils.asternos_exporter_client import AsterNOSExporterClient


def _row(value, **labels):
    return {"metric": labels, "value": value}


def test_hardware_summary_counts_present_operational_fans():
    metrics = {
        "AsterNOS_device_fan_available_status": [
            _row(1, name="FAN1", slot="0"),
            _row(1, name="FAN2", slot="0"),
            _row(0, name="FAN5", slot="0"),
        ],
        "AsterNOS_device_fan_operational_status": [
            _row(1, name="FAN1", slot="0"),
            _row(0, name="FAN2", slot="0"),
            _row(0, name="FAN5", slot="0"),
        ],
        "AsterNOS_device_fan_rpm": [
            _row(9120, name="FAN1", slot="0"),
            _row(0, name="FAN2", slot="0"),
            _row(0, name="FAN5", slot="0"),
        ],
    }

    summary = AsterNOSExporterClient.hardware_summary(metrics)

    assert summary["fan_total"] == 2
    assert summary["fan_expected_total"] == 3
    assert summary["fan_absent"] == 1
    assert summary["fan_down"] == 1
    assert summary["fan_status_known"] is True
    assert [item["name"] for item in summary["fans"]] == ["FAN1", "FAN2"]
    assert summary["power_total"] == 0
    assert summary["power_status_known"] is False


def test_hardware_summary_keeps_missing_metrics_unknown():
    summary = AsterNOSExporterClient.hardware_summary({})

    assert summary["fan_total"] == 0
    assert summary["fan_absent"] == 0
    assert summary["fan_status_known"] is False
    assert summary["power_total"] == 0
    assert summary["power_status_known"] is False
