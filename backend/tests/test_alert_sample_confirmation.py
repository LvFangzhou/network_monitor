import json
from types import SimpleNamespace

from app.tasks import alert_tasks
from app.services.telemetry_receiver import TelemetryInfluxWriter
from app.utils.telemetry_lossless import normalize_lossless_payload


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def set(self, key, value, ex=None, nx=False):
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def delete(self, key):
        self.values.pop(key, None)


def _rule(metric_type="exporter_reachability", duration=60):
    return SimpleNamespace(id=133, metric_type=metric_type, duration=duration, extra_config={})


def _device():
    return SimpleNamespace(id=463)


def _target(sample_time, sample_age_seconds=5):
    return {
        "target_type": "device",
        "target_key": "463",
        "target_name": "Asteros-463",
        "sample_time": sample_time,
        "sample_age_seconds": sample_age_seconds,
    }


def test_same_abnormal_sample_cannot_satisfy_duration(monkeypatch):
    fake_redis = FakeRedis()
    clock = {"now": 1000.0}
    monkeypatch.setattr(alert_tasks, "redis_client", fake_redis)
    monkeypatch.setattr(alert_tasks.time, "time", lambda: clock["now"])

    rule = _rule()
    device = _device()
    target = _target("2026-07-17T06:12:43+00:00")

    assert not alert_tasks._duration_confirmed(rule, device, target, 0.0)
    clock["now"] += 120
    assert not alert_tasks._duration_confirmed(rule, device, target, 0.0)


def test_two_independent_abnormal_samples_can_satisfy_duration(monkeypatch):
    fake_redis = FakeRedis()
    clock = {"now": 1000.0}
    monkeypatch.setattr(alert_tasks, "redis_client", fake_redis)
    monkeypatch.setattr(alert_tasks.time, "time", lambda: clock["now"])

    rule = _rule()
    device = _device()
    assert not alert_tasks._duration_confirmed(rule, device, _target("2026-07-17T06:12:43+00:00"), 0.0)
    clock["now"] += 61
    assert alert_tasks._duration_confirmed(rule, device, _target("2026-07-17T06:13:43+00:00"), 0.0)


def test_stale_sample_is_rejected_even_for_immediate_rule(monkeypatch):
    monkeypatch.setattr(alert_tasks, "redis_client", FakeRedis())
    rule = _rule(duration=0)

    assert not alert_tasks._duration_confirmed(
        rule,
        _device(),
        _target("2026-07-17T05:00:00+00:00", sample_age_seconds=181),
        0.0,
    )


def test_protocol_recovery_requires_a_new_normal_sample(monkeypatch):
    fake_redis = FakeRedis()
    clock = {"now": 2000.0}
    monkeypatch.setattr(alert_tasks, "redis_client", fake_redis)
    monkeypatch.setattr(alert_tasks.time, "time", lambda: clock["now"])

    rule = _rule(metric_type="bgp_peer_state", duration=30)
    device = _device()
    first = _target("2026-07-17T06:12:43+00:00")
    first["target_type"] = "protocol_peer"
    first["target_key"] = "bgp:10.0.0.1"
    assert not alert_tasks._recovery_confirmed(rule, device, first, 1.0)

    clock["now"] += 65
    assert not alert_tasks._recovery_confirmed(rule, device, first, 1.0)

    second = dict(first, sample_time="2026-07-17T06:13:43+00:00")
    assert alert_tasks._recovery_confirmed(rule, device, second, 1.0)


def test_pending_payload_records_first_and_latest_sample(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(alert_tasks, "redis_client", fake_redis)
    monkeypatch.setattr(alert_tasks.time, "time", lambda: 1000.0)
    rule = _rule()
    device = _device()

    alert_tasks._duration_confirmed(rule, device, _target("sample-a"), 0.0)
    alert_tasks._duration_confirmed(rule, device, _target("sample-b"), 0.0)

    payload = json.loads(next(iter(fake_redis.values.values())))
    assert payload["first_sample_time"] == "sample-a"
    assert payload["latest_sample_time"] == "sample-b"


def test_optical_alert_requires_three_distinct_samples(monkeypatch):
    fake_redis = FakeRedis()
    clock = {"now": 1000.0}
    monkeypatch.setattr(alert_tasks, "redis_client", fake_redis)
    monkeypatch.setattr(alert_tasks.time, "time", lambda: clock["now"])
    rule = SimpleNamespace(
        id=201,
        metric_type="optical_lane_power_delta",
        duration=0,
        extra_config={},
    )
    target = _target("sample-a")
    target["target_type"] = "interface"
    target["target_key"] = "10"

    assert not alert_tasks._duration_confirmed(rule, _device(), target, 2.0)
    clock["now"] += 30
    assert not alert_tasks._duration_confirmed(rule, _device(), dict(target, sample_time="sample-b"), 2.0)
    clock["now"] += 30
    assert alert_tasks._duration_confirmed(rule, _device(), dict(target, sample_time="sample-c"), 2.0)


def test_device_ddm_threshold_has_priority_over_profile():
    rule = SimpleNamespace(metric_type="optical_rx_power", condition="<", severity="P1", threshold=-10.0)
    threshold, source = alert_tasks._optical_effective_threshold(
        rule,
        {"rx_low_warning_dbm": -7.25, "transceiver_type": "400G_BASE_DR4"},
        {"speed_bps": 400_000_000_000},
    )
    assert threshold == -7.25
    assert source.startswith("设备DDM")


def test_h3c_ddm_raw_power_threshold_conversion():
    assert TelemetryInfluxWriter._optical_microwatt_to_dbm(50118) == 6.999937
    assert TelemetryInfluxWriter._optical_microwatt_to_dbm(1621) == -7.90217


def test_fec_telemetry_payload_is_normalized():
    rows = normalize_lossless_payload("ifmgr/iffecdata", {
        "Notification": {"Ifmgr": {"IfFecData": {"Interface": [{
            "IfName": "FourHundredGigE1/0/9",
            "Correctable": 2708721789,
            "Uncorrectable": 2,
        }]}}}
    })
    assert rows == [{
        "scope": "port",
        "interface_index": None,
        "interface_name": "FourHundredGigE1/0/9",
        "fec_correctable_packets": 2708721789,
        "fec_uncorrectable_packets": 2,
    }]


def test_optical_sample_age_allows_five_minute_telemetry_interval():
    rule = SimpleNamespace(metric_type="optical_rx_power", extra_config={})
    assert alert_tasks._sample_max_age_seconds(rule, {}) == 420


def test_legacy_syslog_rx_power_change_alert_is_reconciled():
    rule = SimpleNamespace(metric_type="syslog_optical_module_event")
    alert = SimpleNamespace(message="Reason=The transceiver Rx power change exceeded the threshold")
    assert alert_tasks._syslog_optical_power_change_alert(rule, alert)


def test_unrelated_legacy_optical_event_is_not_reconciled():
    rule = SimpleNamespace(metric_type="syslog_optical_module_event")
    alert = SimpleNamespace(message="Reason=Transceiver Tx LOS error")
    assert not alert_tasks._syslog_optical_power_change_alert(rule, alert)


def test_module_session_reset_removes_name_and_index_baselines(monkeypatch):
    fake_redis = FakeRedis()
    fake_redis.set("monitor:cache:optical_modules:310", json.dumps({
        "items": [{
            "interface_name": "FourHundredGigE1/0/21",
            "interface_index": 22,
        }],
    }))
    for key in (
        "alerts:optical_rx_history:310:22",
        "alerts:fec_counter:310:22",
        "alerts:optical_rx_history:310:fourhundredgige1021",
        "alerts:fec_counter:310:fourhundredgige1021",
    ):
        fake_redis.set(key, "old")
    monkeypatch.setattr(alert_tasks, "redis_client", fake_redis)

    alert_tasks.reset_optical_interface_baselines(310, "FourHundredGigE1/0/21")

    assert "alerts:optical_rx_history:310:22" not in fake_redis.values
    assert "alerts:fec_counter:310:22" not in fake_redis.values
    assert "alerts:optical_rx_history:310:fourhundredgige1021" not in fake_redis.values
    assert "alerts:fec_counter:310:fourhundredgige1021" not in fake_redis.values


def test_correctable_fec_growth_alone_is_not_a_p1_correlation():
    # The correctable delta (315526) remains diagnostic context; only the
    # uncorrectable delta is passed as the actionable value.
    assert alert_tasks._optical_fec_correlation_value(5.29, 1.0, 0) == 0
    assert alert_tasks._optical_fec_correlation_value(5.29, 1.0, 2) == 2
    assert alert_tasks._optical_fec_correlation_value(0.5, 1.0, 2) == 0
