from types import SimpleNamespace
import importlib

from app.collectors.snmp_collector import SNMPCollector
from app.tasks.snmp_tasks import _build_roce_interface_rule_payload


class FakeRedis:
    def __init__(self):
        self.values = {}

    def get(self, key):
        return self.values.get(key)

    def setex(self, key, _ttl, value):
        self.values[key] = value


class FakeInflux:
    def __init__(self):
        self.points = []

    def write_points(self, points, sync=False):
        self.points.extend(points)
        return True


def test_roce_health_collector_uses_separate_delta_baseline(monkeypatch):
    module = importlib.import_module("app.collectors.snmp_collector")

    fake_redis = FakeRedis()
    fake_influx = FakeInflux()
    monkeypatch.setattr(module, "redis_client", fake_redis)
    monkeypatch.setattr(module, "influx_client", fake_influx)
    values = {
        "1.3.6.1.2.1.31.1.1.1.1": {1: "FourHundredGigE1/0/1"},
        "1.3.6.1.2.1.2.2.1.13": {1: 100},
        "1.3.6.1.2.1.2.2.1.19": {1: 200},
        "1.3.6.1.2.1.2.2.1.14": {1: 3},
        "1.3.6.1.2.1.2.2.1.20": {1: 4},
    }
    collector = SNMPCollector()
    monkeypatch.setattr(collector, "_walk_indexed_map", lambda _device, oid, _cast: dict(values[oid]))
    device = SimpleNamespace(id=10, name="S9867-01")

    first = collector.collect_interface_health(device)
    assert first["points_written"] == 0

    values["1.3.6.1.2.1.2.2.1.13"][1] = 105
    values["1.3.6.1.2.1.2.2.1.14"][1] = 4
    second = collector.collect_interface_health(device)
    assert second["points_written"] == 1
    fields = fake_influx.points[0]["fields"]
    assert fields["in_discards_delta"] == 5.0
    assert fields["in_errors_delta"] == 1.0


def test_roce_rule_is_limited_to_h3c_s9867_profile():
    payload = _build_roce_interface_rule_payload(
        "test", "interface_in_errors_delta", 0.0, "test", []
    )
    config = payload["extra_config"]
    assert config["applicable_vendors"] == ["H3C"]
    assert config["model_regex"] == "^S9867-128DH$"
    assert config["monitor_profiles"] == ["roce_fabric"]
    assert config["required_features"] == ["roce"]
