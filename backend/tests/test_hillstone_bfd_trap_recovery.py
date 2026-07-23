from types import SimpleNamespace

from app.tasks.alert_tasks import (
    _hillstone_bfd_alert_endpoints,
    _parse_hillstone_bfd_sessions,
)


def test_parse_hillstone_bfd_sessions():
    output = """
Total session number: 2
OurAddr                    Neighbor                   State       LD    RD
27.21.255.14               27.21.255.13               Up          9     35417
27.21.255.18               27.21.255.17               Down        10    35411
"""
    assert _parse_hillstone_bfd_sessions(output) == {
        ("27.21.255.14", "27.21.255.13"): "up",
        ("27.21.255.18", "27.21.255.17"): "down",
    }


def test_extract_endpoints_from_legacy_trap_alert():
    alert = SimpleNamespace(
        alert_target_key=(
            "hillstone_trap:bfd:FW / BFD session single-hop "
            "local:27.21.255.18 neighbor:27.21.255.17 UP -> DOWN"
        ),
        alert_target_name="",
        message="",
    )
    assert _hillstone_bfd_alert_endpoints(alert) == ("27.21.255.18", "27.21.255.17")


def test_extract_endpoints_from_canonical_trap_alert():
    alert = SimpleNamespace(
        alert_target_key="hillstone_trap:bfd:local=27.21.255.18|neighbor=27.21.255.17",
        alert_target_name="",
        message="",
    )
    assert _hillstone_bfd_alert_endpoints(alert) == ("27.21.255.18", "27.21.255.17")
