"""TACACS accounting日志时间解析。"""
from __future__ import annotations

from datetime import datetime
from typing import Optional
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def parse_tacacs_log_time(raw_time: str, now: Optional[datetime] = None) -> Optional[str]:
    """日志前缀已经是北京时间，不再做UTC到CST的二次转换。"""
    try:
        month_text, day, time_text = raw_time.split()
        hour, minute, second = [int(item) for item in time_text.split(":")]
        current = now or datetime.now(SHANGHAI_TZ)
        if current.tzinfo is None:
            current = current.replace(tzinfo=SHANGHAI_TZ)
        else:
            current = current.astimezone(SHANGHAI_TZ)
        candidates = [
            datetime(year, MONTH_MAP[month_text], int(day), hour, minute, second, tzinfo=SHANGHAI_TZ)
            for year in (current.year - 1, current.year, current.year + 1)
        ]
        parsed = min(candidates, key=lambda value: abs((value - current).total_seconds()))
        return parsed.strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None
