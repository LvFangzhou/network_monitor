from app.services.syslog_alert_engine import _resets_optical_baseline, parse_syslog_alert


def test_h3c_rx_power_change_uses_dedicated_rule():
    parsed = parse_syslog_alert(
        "A23FM2L1013U3336-S9867-CSW-SpineB_16 %%10OPTMOD/4/OPTICAL_WARNING_OCCUR: "
        "Transceiver warning alarm occurred. (PhysicalName=FourHundredGigE1/0/128, "
        "Reason=The transceiver Rx power change exceeded the threshold: "
        "Current power change = 5.10 dBm, Change threshold = 5.00 dBm.)"
    )

    assert parsed is not None
    assert parsed.category == "optical_rx_power_change"
    assert parsed.metric_type == "syslog_optical_rx_power_change"
    assert parsed.rule_name == "【H3C】Syslog光模块收光功率突变"
    assert parsed.target_name == "FourHundredGigE1/0/128"
    assert parsed.state == "firing"


def test_h3c_rx_power_change_recovery_matches_dedicated_rule():
    parsed = parse_syslog_alert(
        "%%10OPTMOD/4/OPTICAL_WARNING_RECOVER: Transceiver warning recovered. "
        "(PhysicalName=FourHundredGigE1/0/128, "
        "Reason=The transceiver Rx power change recovered.)"
    )

    assert parsed is not None
    assert parsed.metric_type == "syslog_optical_rx_power_change"
    assert parsed.state == "resolved"


def test_module_remove_resets_optical_baseline():
    message = (
        "%%10OPTMOD/4/MODULE_OUT: Transceiver was removed. "
        "(PhysicalName=FourHundredGigE1/0/21)"
    )
    parsed = parse_syslog_alert(message)
    assert parsed is not None
    assert _resets_optical_baseline(parsed, message)


def test_rx_power_warning_does_not_reset_optical_baseline():
    message = (
        "%%10OPTMOD/4/OPTICAL_WARNING_OCCUR: Transceiver warning alarm occurred. "
        "(PhysicalName=FourHundredGigE1/0/21, Reason=The transceiver Rx power change exceeded the threshold.)"
    )
    parsed = parse_syslog_alert(message)
    assert parsed is not None
    assert not _resets_optical_baseline(parsed, message)
