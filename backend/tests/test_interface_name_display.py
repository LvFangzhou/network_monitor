from app.routers.devices import _clean_interface_description, _display_interface_name, _normalize_interface_key


def test_display_interface_name_preserves_h3c_breakout_suffix():
    assert _display_interface_name("TwoHundredGigE1/0/25:1") == "TwoHundredGigE1/0/25:1"
    assert _display_interface_name("200GE1/0/25:2") == "200GE1/0/25:2"


def test_normalize_interface_key_preserves_breakout_suffix():
    assert _normalize_interface_key("TwoHundredGigE1/0/25:1") == "200g1/0/25:1"
    assert _normalize_interface_key("TwoHundredGigE1/0/25:1") != _normalize_interface_key("TwoHundredGigE1/0/25:2")


def test_clean_interface_description_removes_interface_alias_variants():
    assert _clean_interface_description("TwoHundredGigE1/0/25:1", "200GE1/0/25:1") == ""
    assert _clean_interface_description("LoopBack0", "Loop0") == ""
    assert _clean_interface_description("InLoopBack0", "InLoop0") == ""
    assert _clean_interface_description("Register-Tunnel0", "REG0") == ""
    assert _clean_interface_description("FourHundredGigE1/0/1", "to-core01-400GE1/0/1") == "to-core01-400GE1/0/1"


def test_clean_interface_description_removes_generated_vlan_summary():
    assert _clean_interface_description("TwoHundredGigE1/0/25:1", "Trunk / permit vlan 3001") == ""
