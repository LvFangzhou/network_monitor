import json
import importlib
from datetime import datetime, timezone
from types import SimpleNamespace

from app.collectors import snmp_collector
from app.routers import metrics
from app.tasks import snmp_tasks


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

    def setex(self, key, ttl, value):
        self.values[key] = value

    def eval(self, script, key_count, key, token):
        if self.values.get(key) == token:
            del self.values[key]
            return 1
        return 0


class FakeInflux:
    def __init__(self):
        self.points = []

    def write_points(self, points, sync=False):
        self.points.extend(points)
        return True


def test_collector_rejects_rate_above_physical_speed():
    in_bps, out_bps = snmp_collector._sanitize_interface_rates(
        10_400_000_000,
        250_000_000,
        10_000_000_000,
    )
    assert in_bps is None
    assert out_bps == 250_000_000


def test_task_rejects_rate_above_physical_speed():
    stats = {
        "speed_bps": 10_000_000_000,
        "in_bps": 10_400_000_000,
        "out_bps": 250_000_000,
        "in_utilization_percent": 104.0,
        "out_utilization_percent": 2.5,
    }
    snmp_tasks._sanitize_interface_rates(stats)
    assert stats["in_bps"] is None
    assert stats["in_utilization_percent"] is None
    assert stats["out_bps"] == 250_000_000


def test_history_rejects_legacy_exact_line_rate_point():
    row = {
        "speed_bps": 10_000_000_000,
        "in_bps": 10_000_000_000,
        "out_bps": 80_000_000,
        "in_utilization_percent": 100.0,
        "out_utilization_percent": 0.8,
    }
    metrics._sanitize_impossible_interface_rates(row)
    assert row["in_bps"] is None
    assert row["in_utilization_percent"] is None
    assert row["out_bps"] == 80_000_000


def test_older_sample_does_not_replace_newer_counter_baseline(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(snmp_tasks, "redis_client", fake_redis)
    cache_key = snmp_tasks._octet_rate_cache_key(198, 20)
    newer_time = datetime(2026, 6, 19, 14, 0, tzinfo=timezone.utc)
    fake_redis.values[cache_key] = json.dumps({
        "in_octets": 2000,
        "out_octets": 3000,
        "time": newer_time.isoformat(),
        "in_time": newer_time.isoformat(),
        "out_time": newer_time.isoformat(),
    })

    stats = {"index": 20, "in_octets": 1500, "out_octets": 2500}
    snmp_tasks._apply_octet_rates(
        198,
        stats,
        datetime(2026, 6, 19, 13, 59, tzinfo=timezone.utc),
    )

    assert json.loads(fake_redis.values[cache_key])["in_octets"] == 2000
    assert "in_bps" not in stats


def test_valid_counter_delta_is_preserved(monkeypatch):
    fake_redis = FakeRedis()
    monkeypatch.setattr(snmp_tasks, "redis_client", fake_redis)
    cache_key = snmp_tasks._octet_rate_cache_key(198, 20)
    previous_time = datetime(2026, 6, 19, 13, 59, tzinfo=timezone.utc)
    fake_redis.values[cache_key] = json.dumps({
        "in_octets": 1000,
        "out_octets": 2000,
        "time": previous_time.isoformat(),
        "in_time": previous_time.isoformat(),
        "out_time": previous_time.isoformat(),
    })

    stats = {"index": 20, "in_octets": 1500, "out_octets": 3000}
    snmp_tasks._apply_octet_rates(
        198,
        stats,
        datetime(2026, 6, 19, 13, 59, 20, tzinfo=timezone.utc),
    )

    assert stats["in_bps"] == 200.0
    assert stats["out_bps"] == 400.0
    assert stats["sample_seconds"] == 20.0


def test_missing_oper_status_does_not_become_interface_down():
    device = SimpleNamespace(id=198, name="test-switch", monitor_source="snmp")
    point = snmp_tasks._interface_point(
        device,
        {
            "index": 20,
            "name": "HundredGigE1/0/20",
            "in_octets": None,
            "out_octets": None,
            "in_bps": 100.0,
            "out_bps": 200.0,
            "admin_status": "up",
            "oper_status": "unknown",
        },
        datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert point["fields"]["in_bps"] == 100.0
    assert point["fields"]["out_bps"] == 200.0
    assert "oper_status" not in point["fields"]
    assert "admin_up_oper_down" not in point["fields"]


def test_valid_interface_status_still_generates_down_signal():
    device = SimpleNamespace(id=198, name="test-switch", monitor_source="snmp")
    point = snmp_tasks._interface_point(
        device,
        {
            "index": 20,
            "name": "HundredGigE1/0/20",
            "in_octets": None,
            "out_octets": None,
            "admin_status": "up",
            "oper_status": "down",
        },
        datetime(2026, 7, 16, 12, 0, tzinfo=timezone.utc),
    )

    assert point["fields"]["admin_status"] == 1.0
    assert point["fields"]["oper_status"] == 0.0
    assert point["fields"]["admin_up_oper_down"] == 1.0


def test_realtime_collector_keeps_traffic_when_oper_status_walk_times_out(monkeypatch):
    collector_module = importlib.import_module("app.collectors.snmp_collector")
    fake_redis = FakeRedis()
    fake_influx = FakeInflux()
    monkeypatch.setattr(collector_module, "redis_client", fake_redis)
    monkeypatch.setattr(collector_module, "influx_client", fake_influx)

    oid_values = {
        "1.3.6.1.2.1.31.1.1.1.1": {"1": "HundredGigE1/0/1"},
        "1.3.6.1.2.1.2.2.1.2": {"1": "HundredGigE1/0/1"},
        "1.3.6.1.2.1.31.1.1.1.6": {"1": 1000},
        "1.3.6.1.2.1.31.1.1.1.10": {"1": 2000},
        "1.3.6.1.2.1.31.1.1.1.15": {"1": 100000},
        "1.3.6.1.2.1.2.2.1.5": {},
        "1.3.6.1.2.1.2.2.1.7": {"1": 1},
        # Simulate only ifOperStatus timing out while octet counters succeed.
        "1.3.6.1.2.1.2.2.1.8": {},
    }
    monkeypatch.setattr(
        snmp_collector,
        "_walk_indexed_map",
        lambda device, oid, cast: oid_values.get(oid, {}),
    )
    device = SimpleNamespace(id=198, name="test-switch", vendor="H3C")

    result = snmp_collector.collect_interface_monitoring(device, realtime=True)

    assert result["points_written"] == 1
    fields = fake_influx.points[0]["fields"]
    assert fields["in_octets"] == 1000
    assert fields["out_octets"] == 2000
    assert "oper_up" not in fields
    assert "interface_admin_up_oper_down" not in fields
