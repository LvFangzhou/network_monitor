from app.tasks import quality_tasks
from app.tasks import alert_tasks
from types import SimpleNamespace


class FakeRedis:
    def __init__(self):
        self.values = {}

    def incr(self, key):
        self.values[key] = int(self.values.get(key, 0)) + 1
        return self.values[key]

    def expire(self, key, seconds):
        return key in self.values

    def delete(self, key):
        self.values.pop(key, None)


def test_consecutive_loss_counter_requires_each_probe_cycle_to_have_loss(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(quality_tasks, "redis_client", fake_redis)

    for expected in range(1, 6):
        count = quality_tasks._update_consecutive_loss_count(9, {"sent": 5, "received": 4})
        assert count == expected

    assert quality_tasks._update_consecutive_loss_count(9, {"sent": 5, "received": 5}) == 0
    assert quality_tasks._update_consecutive_loss_count(9, {"sent": 5, "received": 4}) == 1


def test_probe_execution_error_without_sent_packets_does_not_count_as_packet_loss(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(quality_tasks, "redis_client", fake_redis)

    assert quality_tasks._update_consecutive_loss_count(10, {"sent": 0, "received": 0}) == 0
    assert not fake_redis.values


def test_quality_notification_uses_target_specific_robot_and_mentions():
    rule = SimpleNamespace(
        metric_type="quality_packet_loss",
        notification_channels=[],
        extra_config={
            "target_notifications": {
                "9": {
                    "enabled": True,
                    "channel_type": "wechat",
                    "webhook_url": "https://example.invalid/robot/target-9",
                    "mention_users": ["13800138000"],
                },
                "10": {
                    "enabled": True,
                    "channel_type": "wechat",
                    "webhook_url": "https://example.invalid/robot/target-10",
                    "mention_users": ["13900139000"],
                },
            }
        },
    )
    alert = SimpleNamespace(alert_target_type="quality_probe", alert_target_key="9")

    channels = alert_tasks._notification_channels_for_alert(rule, alert)

    assert channels[0]["config"]["webhook"].endswith("target-9")
    assert alert_tasks._notification_mentions_for_alert(rule, alert) == ["13800138000"]


def test_multi_target_quality_notification_uses_parent_card_robot():
    rule = SimpleNamespace(
        metric_type="quality_packet_loss",
        notification_channels=[],
        extra_config={
            "target_notifications": {
                "9": {
                    "enabled": True,
                    "channel_type": "wechat",
                    "webhook_url": "https://example.invalid/robot/target-9",
                    "mention_users": ["13800138000"],
                },
            }
        },
    )
    alert = SimpleNamespace(
        alert_target_type="quality_probe",
        alert_target_key="9:0123456789ab",
    )

    channels = alert_tasks._notification_channels_for_alert(rule, alert)

    assert channels[0]["config"]["webhook"].endswith("target-9")
    assert alert_tasks._notification_mentions_for_alert(rule, alert) == ["13800138000"]


def test_quality_notification_impact_text_is_actionable():
    assert "完全不可达" in alert_tasks._quality_impact_text(100)
    assert "严重丢包" in alert_tasks._quality_impact_text(10)
    assert "链路质量下降" in alert_tasks._quality_impact_text(3)
    assert "持续丢包" in alert_tasks._quality_impact_text(0.5)


def test_quality_notification_title_distinguishes_fault_and_recovery():
    rule = SimpleNamespace(
        metric_type="quality_packet_loss",
        severity="P1",
        extra_config={},
    )

    assert "公网链路严重丢包" in alert_tasks._build_notification_title(rule, "firing", None)
    assert "公网链路丢包恢复" in alert_tasks._build_notification_title(rule, "auto_resolved", None)


def test_quality_latency_title_is_distinct_from_packet_loss():
    rule = SimpleNamespace(metric_type="quality_latency", severity="P1", extra_config={})

    assert alert_tasks._build_notification_title(rule, "firing", None) == "P1-公网链路延迟升高"
    assert alert_tasks._build_notification_title(rule, "auto_resolved", None) == "P1-公网链路延迟恢复"


def test_critical_packet_loss_title_is_p0_and_distinct_from_latency():
    rule = SimpleNamespace(metric_type="quality_packet_loss_critical", severity="P0", extra_config={})

    assert alert_tasks._build_notification_title(rule, "firing", None) == "P0-公网链路严重丢包"
    assert alert_tasks._build_notification_title(rule, "auto_resolved", None) == "P0-公网链路丢包恢复"


def test_firing_quality_alert_repeats_after_rule_interval(monkeypatch):
    alert = SimpleNamespace(
        status="firing",
        started_at=None,
        notifications_sent=[
            {
                "event_type": "firing",
                "success": True,
                "sent_at": "2026-08-04T10:40:00+00:00",
            }
        ],
    )
    rule = SimpleNamespace(suppress_duration=300)

    monkeypatch.setattr(
        alert_tasks,
        "_utc_now",
        lambda: alert_tasks.datetime.fromisoformat("2026-08-04T10:45:01+00:00"),
    )

    assert alert_tasks._should_repeat_notify(alert, rule) is True


def test_firing_quality_alert_does_not_repeat_before_rule_interval(monkeypatch):
    alert = SimpleNamespace(
        status="firing",
        started_at=None,
        notifications_sent=[
            {
                "event_type": "firing",
                "success": True,
                "sent_at": "2026-08-04T10:40:00+00:00",
            }
        ],
    )
    rule = SimpleNamespace(suppress_duration=300)

    monkeypatch.setattr(
        alert_tasks,
        "_utc_now",
        lambda: alert_tasks.datetime.fromisoformat("2026-08-04T10:44:59+00:00"),
    )

    assert alert_tasks._should_repeat_notify(alert, rule) is False


def test_reachability_recovery_title_keeps_original_probe_type():
    snmp_rule = SimpleNamespace(
        name="【Hillstone】SNMP不可达",
        metric_type="snmp_reachability",
        severity="P0",
        extra_config={},
    )
    ping_rule = SimpleNamespace(
        name="【H3C】Ping不可达",
        metric_type="device_reachability",
        severity="P0",
        extra_config={},
    )

    assert alert_tasks._build_notification_title(snmp_rule, "auto_resolved", None) == "P0-【Hillstone】SNMP不可达，已恢复"
    assert alert_tasks._build_notification_title(ping_rule, "auto_resolved", None) == "P0-【H3C】Ping不可达，已恢复"


def test_quality_target_thresholds_override_global_defaults():
    rule = SimpleNamespace(threshold=10, extra_config={"consecutive_samples": 5})

    assert quality_tasks._quality_target_thresholds(rule, {}) == (5, 10.0)
    assert quality_tasks._quality_target_thresholds(
        rule,
        {"consecutive_samples": 8, "loss_threshold_percent": 3.5},
    ) == (8, 3.5)
