from app.utils.h3c_vlan_statistics import parse_h3c_vlan_statistics, vlan_id_from_interface_name


def test_vlan_id_from_h3c_interface_name():
    assert vlan_id_from_interface_name("Vlan-interface2014") == 2014
    assert vlan_id_from_interface_name("vlan-interface 2013") == 2013
    assert vlan_id_from_interface_name("Twenty-FiveGigE1/0/19") is None


def test_parse_h3c_vlan_statistics_uses_total_section_and_converts_bps():
    output = """
VLAN 2014 statistics:
 Slot 1:
 Direction        Total packets        Total bytes   Rate(pps)   Rate(Bps)
 Inbound                    10                1000           1         100
 Outbound                   20                2000           2         200
 Total:
 Direction        Total packets        Total bytes   Rate(pps)   Rate(Bps)
 Inbound             1,234,567       9,876,543,210      321.50   493173299
 Outbound              765,432       1,234,567,890       22.25    22186167
"""
    result = parse_h3c_vlan_statistics(output)
    assert result["in_packets"] == 1_234_567
    assert result["out_octets"] == 1_234_567_890
    assert result["in_pps"] == 321.5
    assert result["in_bps"] == 3_945_386_392
    assert result["out_bps"] == 177_489_336


def test_parse_h3c_vlan_statistics_supports_single_section_output():
    result = parse_h3c_vlan_statistics(
        "Inbound 10 1000 1 125\nOutbound 20 2000 2 250\n"
    )
    assert result["in_bps"] == 1000
    assert result["out_bps"] == 2000


def test_parse_h3c_vlan_statistics_supports_h3c_wrapped_rate_rows():
    output = """
Total:
Direction       Total packets        Total bytes
                Rate (pps)           Rate (Bps)
Inbound         513667536            715026036839
                313104               455823312
Outbound        425202746            134257747805
                246040               22144996
"""
    result = parse_h3c_vlan_statistics(output)
    assert result["in_packets"] == 513_667_536
    assert result["in_bps"] == 3_646_586_496
    assert result["out_bps"] == 177_159_968
