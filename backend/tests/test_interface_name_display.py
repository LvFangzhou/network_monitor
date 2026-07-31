from app.routers.devices import _display_interface_name, _normalize_interface_key


def test_display_interface_name_preserves_h3c_breakout_suffix():
    assert _display_interface_name("TwoHundredGigE1/0/25:1") == "TwoHundredGigE1/0/25:1"
    assert _display_interface_name("200GE1/0/25:2") == "200GE1/0/25:2"


def test_normalize_interface_key_preserves_breakout_suffix():
    assert _normalize_interface_key("TwoHundredGigE1/0/25:1") == "200g1/0/25:1"
    assert _normalize_interface_key("TwoHundredGigE1/0/25:1") != _normalize_interface_key("TwoHundredGigE1/0/25:2")
