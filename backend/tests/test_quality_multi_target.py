from app.utils.quality_probe import normalize_quality_target_addresses, quality_probe_member_key


def test_normalize_quality_target_addresses_splits_deduplicates_and_preserves_order():
    addresses = normalize_quality_target_addresses(
        "1.1.1.1",
        ["1.1.1.1, 8.8.8.8", "223.5.5.5；8.8.8.8\nwww.example.com"],
    )

    assert addresses == ["1.1.1.1", "8.8.8.8", "223.5.5.5", "www.example.com"]


def test_normalize_quality_target_addresses_keeps_legacy_single_target():
    assert normalize_quality_target_addresses("114.114.114.114", None) == ["114.114.114.114"]


def test_quality_probe_member_keys_are_isolated_between_addresses():
    first = quality_probe_member_key(9, "1.1.1.1")
    second = quality_probe_member_key(9, "8.8.8.8")

    assert first.startswith("9:")
    assert second.startswith("9:")
    assert first != second
