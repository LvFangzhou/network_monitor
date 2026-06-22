from types import SimpleNamespace

from app.tasks import alert_tasks


def _silence(conditions):
    return SimpleNamespace(
        starts_at=None,
        expires_at=None,
        rule_id=None,
        device_id=None,
        include_device_ip=None,
        include_interface=None,
        include_message=None,
        exclude_device_ip=None,
        exclude_interface=None,
        exclude_message=None,
        target_pattern=None,
        conditions=conditions,
    )


def _rule_and_device():
    rule = SimpleNamespace(id=1)
    device = SimpleNamespace(id=1, ip_address="10.242.2.13", name="AGG01")
    return rule, device


def test_interface_equals_matches_display_name(monkeypatch):
    monkeypatch.setattr(alert_tasks, "_build_alert_message", lambda *args, **kwargs: "message")
    rule, device = _rule_and_device()
    target = {"target_name": "0/60", "target_key": "15", "value": 1}

    assert alert_tasks._silence_matches(
        _silence([{"field": "interface", "operator": "equals", "value": "0/60"}]),
        rule,
        device,
        target,
    )


def test_ip_range_and_interface_conditions_match_together(monkeypatch):
    monkeypatch.setattr(alert_tasks, "_build_alert_message", lambda *args, **kwargs: "message")
    rule, device = _rule_and_device()
    target = {"target_name": "0/48", "target_key": "12", "value": 1}
    conditions = [
        {"field": "ip", "operator": "contains", "value": "10.242.2.13-10.242.2.16"},
        {"field": "interface", "operator": "contains", "value": "0/48\n0/52\n0/56\n0/60"},
    ]

    assert alert_tasks._silence_matches(_silence(conditions), rule, device, target)
