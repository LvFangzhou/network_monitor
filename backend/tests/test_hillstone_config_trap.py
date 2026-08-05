from app.services.snmp_trap_listener import (
    HILLSTONE_TRAP_BASE,
    HILLSTONE_TRAPS,
    _canonical_target_key,
    _config_batch_message,
    _config_batch_details,
    _clean_config_trap_detail,
    _trap_detail_text,
)
from app.utils.notification import NotificationManager


def test_hillstone_config_traps_share_device_aggregation_key():
    trap_oid = f"{HILLSTONE_TRAP_BASE}.19"
    definition = HILLSTONE_TRAPS[trap_oid]

    first = _canonical_target_key(definition, trap_oid, "admin changed policy 1")
    second = _canonical_target_key(definition, trap_oid, "admin changed policy 2")

    assert first == second == "hillstone_trap:config:device"


def test_config_batch_details_are_extracted_for_next_trap():
    message = """设备 fw (10.0.0.1) 收到山石Trap
规则: 【Hillstone】山石配置变更Trap
Trap OID: 1.3.6.1.4.1.28557.3.19
Trap内容:
- 修改安全策略
- 保存配置
"""

    assert _config_batch_details(message) == ["修改安全策略", "保存配置"]


def test_config_detail_removes_hostname_and_keeps_full_operation():
    hostname = "A23FM2L0501U2324-K6680-GLW-FW_Active"
    detail = f'{hostname} / address: "2026安全联动-出向", 0, 0->host: "example.org"'

    assert _clean_config_trap_detail(detail, hostname) == (
        'address: "2026安全联动-出向", 0, 0->host: "example.org"'
    )


def test_config_detail_uses_resolved_device_name_when_sysname_is_missing():
    hostname = "A23FM2L0501U2324-K6680-GLW-FW_Active"
    detail = f"{hostname} / policy: trust-to-untrust / action: permit -> deny"

    assert _clean_config_trap_detail(detail, None, hostname) == (
        "policy: trust-to-untrust / action: permit -> deny"
    )


def test_config_batch_ignores_truncation_marker_when_reaggregating():
    message = """Trap内容:
- 修改安全策略
- 其余 3 条已合并
"""

    assert _config_batch_details(message) == ["修改安全策略"]


def test_config_trap_preserves_payload_beyond_legacy_500_character_limit():
    payload = "配置内容" * 150
    varbinds = [("1.3.6.1.4.1.28557.99.1", payload)]

    assert _trap_detail_text(varbinds, preserve_full_text=True) == payload
    assert _clean_config_trap_detail(payload) == payload


def test_config_batch_keeps_every_change_instead_of_first_twenty_only():
    details = [f"配置变更-{index}" for index in range(1, 26)]
    device = type("DeviceStub", (), {"name": "fw", "ip_address": "10.0.0.1"})()
    trap_oid = f"{HILLSTONE_TRAP_BASE}.19"

    message = _config_batch_message(
        device,
        "10.0.0.1",
        HILLSTONE_TRAPS[trap_oid],
        trap_oid,
        "2026-08-05 16:00:00",
        details,
    )

    assert _config_batch_details(message) == details
    assert "其余" not in message


def test_operation_robot_markdown_does_not_add_ellipsis():
    change_lines = "\n".join(f"• 配置变更-{index}-" + "内容" * 30 for index in range(1, 26))
    manager = NotificationManager()

    markdown = manager._build_card_rows_markdown({
        "notification_kind": "operation",
        "rows": [{"label": "变更内容", "value": change_lines}],
    })
    pages = manager._split_markdown_by_lines(markdown, max_bytes=500)

    assert "..." not in markdown
    assert len(pages) > 1
    assert "\n".join(pages) == markdown
    for index in range(1, 26):
        assert f"配置变更-{index}-" in markdown
