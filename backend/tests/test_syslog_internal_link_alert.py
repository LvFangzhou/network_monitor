from app.services.syslog_alert_engine import parse_syslog_alert


def test_h3c_internal_link_clear_is_interface_recovery():
    message = (
        "A23FM2L0302U3737-S6850-GLW-Leaf05 %%10DEV/2/INTERNALLINK_ALARM_CLEAR: "
        "-DevIP=10.239.3.105; Internal link alarm cleared. "
        "(PhysicalIndex=14, PhysicalName=Board, RelativeResource=WGE1/0/22, "
        "ErrorCode=482001, Reason= Recovered from remote fault. on port WGE1/0/22)"
    )

    parsed = parse_syslog_alert(message, severity=2)

    assert parsed is not None
    assert parsed.state == "resolved"
    assert parsed.category == "interface_phy"
    assert parsed.target_name == "WGE1/0/22"
    assert parsed.metric_type == "syslog_interface_phy_down"
    assert parsed.rule_name == "【H3C】Syslog接口物理Down/瞬断"


def test_h3c_internal_link_alarm_and_clear_use_same_target():
    alarm = parse_syslog_alert(
        "%%10DEV/2/INTERNALLINK_ALARM: Internal link alarm occurred. "
        "(RelativeResource=WGE1/0/22, ErrorCode=482001, "
        "Reason=Remote fault detected on port WGE1/0/22)",
        severity=2,
    )
    clear = parse_syslog_alert(
        "%%10DEV/2/INTERNALLINK_ALARM_CLEAR: Internal link alarm cleared. "
        "(RelativeResource=WGE1/0/22, ErrorCode=482001, "
        "Reason=Recovered from remote fault. on port WGE1/0/22)",
        severity=2,
    )

    assert alarm is not None and alarm.state == "firing"
    assert clear is not None and clear.state == "resolved"
    assert alarm.target_key == clear.target_key


def test_unknown_explicit_recovery_does_not_create_critical_alarm():
    parsed = parse_syslog_alert(
        "%%10UNKNOWN/2/BOARD_ALARM_CLEAR: Board alarm cleared after recovery.",
        severity=2,
    )

    assert parsed is None


def test_non_recovery_critical_syslog_still_uses_generic_fallback():
    parsed = parse_syslog_alert(
        "%%10DEV/2/UNKNOWN_FATAL_EVENT: An unrecoverable device failure occurred.",
        severity=2,
    )

    assert parsed is not None
    assert parsed.state == "firing"
    assert parsed.rule_name == "【H3C】Syslog设备主动严重异常"
