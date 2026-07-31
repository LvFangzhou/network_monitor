from datetime import datetime
from zoneinfo import ZoneInfo

from app.utils.tacacs_time import parse_tacacs_log_time


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")


def test_tacacs_log_time_is_already_shanghai_time():
    parsed = parse_tacacs_log_time(
        "Jul 30 17:09:20",
        now=datetime(2026, 7, 30, 17, 12, 0, tzinfo=SHANGHAI_TZ),
    )

    assert parsed == "2026-07-30 17:09:20"


def test_tacacs_log_time_uses_nearest_year_across_new_year():
    parsed = parse_tacacs_log_time(
        "Dec 31 23:59:59",
        now=datetime(2027, 1, 1, 0, 1, 0, tzinfo=SHANGHAI_TZ),
    )

    assert parsed == "2026-12-31 23:59:59"
