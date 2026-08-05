from app.tasks.config_backup_tasks import (
    _build_sync_payload,
    _config_diff_summary,
    _normalize_config_for_compare,
)


def test_cisco_nxos_running_and_startup_dates_do_not_create_config_diff():
    running = """!Running configuration last done at: Fri Jul 24 08:34:58 2026
!Time: Tue Aug  4 07:53:21 2026
!Command: show running-config
version 10.6(1) Bios:version 01.18
hostname A23FM3L0616U3536-9364D-VASTBE-Leaf1A
feature lacp
"""
    startup = """!Time: Tue Aug  4 07:53:23 2026
!Startup config saved at: Fri Jul 24 08:44:03 2026
!Command: show startup-config
version 10.6(1) Bios:version 01.18
hostname A23FM3L0616U3536-9364D-VASTBE-Leaf1A
feature lacp
"""

    assert _normalize_config_for_compare(running) == _normalize_config_for_compare(startup)
    assert _config_diff_summary(running, startup) == ""


def test_cisco_nxos_real_command_difference_is_still_detected():
    running = """!Running configuration last done at: Tue Aug 4 08:00:00 2026
hostname leaf-01
feature lacp
"""
    startup = """!Startup config saved at: Mon Aug 3 08:00:00 2026
hostname leaf-01
"""

    payload = _build_sync_payload(
        device=None,
        running_command="show running-config",
        running_config=running,
        startup_command="show startup-config",
        startup_config=startup,
        save_command=None,
        save_status=None,
        save_message=None,
    )

    assert payload["config_sync_status"] == "changed_not_saved"
    assert "+feature lacp" in payload["config_sync_diff"]
