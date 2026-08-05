import asyncio

from app.tasks import tacacs_tasks
from app.utils.tacacs_commands import extract_tacacs_command, is_tacacs_user_command


def test_extract_command_rebuilds_cmd_args_without_accounting_fields():
    raw = "show cmd-arg=ip route vrf all err_msg= start_time=1754209166"
    assert extract_tacacs_command(raw) == "show ip route vrf all"


def test_real_network_cli_commands_are_retained():
    for command in ("configure", "show running-config", "display ip routing-table"):
        assert is_tacacs_user_command(command)


def test_asteros_login_helpers_are_filtered():
    for command in (
        "/usr/sbin/sshd exit=0",
        "/usr/sbin/usermod -G sudo,docker lvfz",
        "/usr/bin/id -u",
        "/usr/bin/sonic-cli",
        "/usr/sbin/cli/clish_start",
        "sudo /usr/bin/cat /etc/passwd",
        "startup",
        "xml",
    ):
        assert not is_tacacs_user_command(command)


def test_parse_line_filters_system_helpers():
    prefix = "Aug  3 16:19:18 10.242.2.11 lvfz pts/0 192.168.1.10 "
    assert tacacs_tasks._parse_line(prefix + "cmd=show cmd-arg=ip route") is not None
    assert tacacs_tasks._parse_line(prefix + "cmd=/usr/bin/id cmd-arg=-u") is None
    assert tacacs_tasks._parse_line(prefix + "cmd=xml") is None


def test_generic_webhook_channel_is_sent(monkeypatch):
    calls = []

    async def fake_send(channel_type, config, title, content, card_data):
        calls.append((channel_type, config))
        return True

    monkeypatch.setattr(
        tacacs_tasks,
        "_load_notification_channels",
        lambda: [{"type": "webhook", "webhook": "https://example.test/hook"}],
    )
    monkeypatch.setattr(tacacs_tasks.notification_manager, "send_notification", fake_send)

    sent = asyncio.run(tacacs_tasks._send_robot_notification("title", "content", {}))

    assert sent == 1
    assert calls == [("webhook", {"url": "https://example.test/hook"})]
