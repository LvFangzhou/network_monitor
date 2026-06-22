from app.utils.ip_match import ip_value_matches


def test_matches_inclusive_ipv4_range():
    candidate = "10.239.0.1-10.239.0.254"
    assert ip_value_matches("10.239.0.1", candidate)
    assert ip_value_matches("10.239.0.128", candidate)
    assert ip_value_matches("10.239.0.254", candidate)
    assert not ip_value_matches("10.239.0.0", candidate)
    assert not ip_value_matches("10.239.0.255", candidate)


def test_range_allows_spaces_around_separator():
    assert ip_value_matches("10.239.0.20", "10.239.0.1 - 10.239.0.254")


def test_invalid_or_reversed_range_does_not_match():
    assert not ip_value_matches("10.239.0.20", "10.239.0.254-10.239.0.1")
    assert not ip_value_matches("10.239.0.20", "invalid-10.239.0.254")
    assert not ip_value_matches("10.239.0.20", "10.239.0.1-10.239.0.254-extra")


def test_exact_ip_and_cidr_are_still_supported():
    assert ip_value_matches("10.239.0.20", "10.239.0.20")
    assert ip_value_matches("10.239.0.20", "10.239.0.0/24")
    assert not ip_value_matches("10.239.1.20", "10.239.0.0/24")
