"""
Tacacs+ 命令日志通知任务。
"""
import asyncio
import json
import re
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional

from celery import shared_task

from app.config import settings
from app.core import get_logger
from app.utils import notification_manager, redis_client

logger = get_logger(__name__)

TACACS_LOG_FILE = Path("/app/data/tacacs/logs/tacacs.log")
TACACS_SETTINGS_FILE = Path("/app/data/tacacs/settings.json")
OFFSET_KEY = "tacacs:log:offset"
LOCK_KEY = "tacacs:log:process:lock"
LOCK_TTL_SECONDS = 55
BUFFER_KEY = "tacacs:log:buffer"
BUFFER_FIRST_SEEN_KEY = "tacacs:log:buffer:first_seen"
BATCH_DELAY_SECONDS = 20
LOG_PATTERN = re.compile(r"(\w+\s+\d+\s+\d+:\d+:\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\w+)\s+(\S+)\s+(\S+).*?cmd=(.*)")
MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_log_time(raw_time: str) -> str:
    try:
        month_text, day, time_text = raw_time.split()
        hour, minute, second = [int(item) for item in time_text.split(":")]
        parsed = datetime(datetime.now().year, MONTH_MAP[month_text], int(day), hour, minute, second)
        return (parsed + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return raw_time


def _extract_command(raw_command: str) -> str:
    """
    Tacacs accounting 行里 cmd 后面可能带 cmd-arg/err_msg/start_time 等字段。
    机器人和前端只展示真实命令，附加字段留在 raw 日志里。
    """
    text = (raw_command or "").strip()
    if not text:
        return ""

    marker_match = re.search(r"\s+(cmd-arg|err_msg|start_time)=", text)
    command = text[:marker_match.start()].strip() if marker_match else text
    rest = text[marker_match.start():] if marker_match else ""

    arg_match = re.search(
        r"\bcmd-arg=(.*?)(?=\s+(?:err_msg|start_time)=|$)",
        rest,
    )
    cmd_arg = arg_match.group(1).strip() if arg_match else ""
    if cmd_arg:
        return f"{command} {cmd_arg}".strip()
    return command


def _parse_line(line: str) -> Optional[Dict[str, str]]:
    match = LOG_PATTERN.search(line)
    if not match:
        return None
    command = _extract_command(match.group(6))
    if not command or command in {"startup"}:
        return None
    return {
        "time": _parse_log_time(match.group(1)),
        "device": match.group(2),
        "user": match.group(3),
        "tty": match.group(4),
        "client": match.group(5),
        "cmd": command,
    }


def _buffer_entries(entries: List[Dict[str, str]]) -> int:
    buffered = 0
    for entry in entries:
        redis_client.rpush(BUFFER_KEY, json.dumps(entry, ensure_ascii=False))
        buffered += 1
    if buffered:
        redis_client.set(BUFFER_FIRST_SEEN_KEY, str(time.time()), ex=LOCK_TTL_SECONDS, nx=True)
    return buffered


def _pop_buffered_entries() -> List[Dict[str, str]]:
    raw_entries = redis_client.lrange(BUFFER_KEY, 0, -1) or []
    redis_client.delete(BUFFER_KEY)
    redis_client.delete(BUFFER_FIRST_SEEN_KEY)
    entries: List[Dict[str, str]] = []
    for raw in raw_entries:
        try:
            item = json.loads(raw)
        except (TypeError, json.JSONDecodeError):
            continue
        if isinstance(item, dict):
            entries.append(item)
    return entries


def _load_notification_channels() -> List[Dict[str, str]]:
    channels: List[Dict[str, str]] = []
    if TACACS_SETTINGS_FILE.exists():
        try:
            payload = json.loads(TACACS_SETTINGS_FILE.read_text(encoding="utf-8"))
            raw_channels = payload.get("notification_channels") or []
            if isinstance(raw_channels, list):
                channels.extend([item for item in raw_channels if isinstance(item, dict)])
        except Exception as exc:
            logger.error("读取Tacacs通知配置失败", error=str(exc))
    if not channels and settings.TACACS_WEBHOOK_URL.strip():
        channels.append({"type": "dingtalk", "webhook": settings.TACACS_WEBHOOK_URL.strip()})
    return channels


async def _send_robot_notification(title: str, content: str, card_data: Dict[str, object]) -> int:
    sent = 0
    for channel in _load_notification_channels():
        channel_type = str(channel.get("type") or "").strip()
        webhook = str(channel.get("webhook") or channel.get("url") or "").strip()
        if channel_type not in {"wechat", "dingtalk", "feishu"} or not webhook:
            continue
        if await notification_manager.send_notification(
            channel_type,
            {"webhook": webhook, "url": webhook},
            title,
            content,
            card_data,
        ):
            sent += 1
    return sent


@shared_task
def process_tacacs_command_logs():
    if not redis_client.set(LOCK_KEY, "1", ex=LOCK_TTL_SECONDS, nx=True):
        logger.info("上一轮Tacacs命令日志处理仍在执行，本轮跳过")
        return {"processed": 0, "skipped": True}

    if not TACACS_LOG_FILE.exists():
        redis_client.delete(LOCK_KEY)
        return {"processed": 0, "reason": "log file not found"}

    try:
        file_size = TACACS_LOG_FILE.stat().st_size
        last_offset = int(redis_client.get(OFFSET_KEY) or 0)
        if file_size < last_offset:
            last_offset = 0
        entries: List[Dict[str, str]] = []
        if file_size > last_offset:
            with TACACS_LOG_FILE.open("r", encoding="utf-8", errors="ignore") as fp:
                fp.seek(last_offset)
                content = fp.read()

            entries = [entry for line in content.splitlines() if "cmd=" in line for entry in [_parse_line(line)] if entry]
            redis_client.set(OFFSET_KEY, file_size)
            if not entries:
                logger.info("Tacacs命令日志无新增命令记录", offset=file_size, bytes_read=len(content))

        buffered = _buffer_entries(entries)
        first_seen_raw = redis_client.get(BUFFER_FIRST_SEEN_KEY)
        if not first_seen_raw:
            return {"processed": 0, "buffered": buffered}
        try:
            first_seen = float(first_seen_raw)
        except (TypeError, ValueError):
            first_seen = time.time()
        if time.time() - first_seen < BATCH_DELAY_SECONDS:
            return {"processed": 0, "buffered": buffered, "waiting": True}

        entries = _pop_buffered_entries()
        if not entries:
            return {"processed": 0, "buffered": buffered}

        grouped: Dict[tuple[str, str], List[Dict[str, str]]] = {}
        for entry in entries:
            grouped.setdefault((entry["user"], entry["device"]), []).append(entry)

        sent = 0
        for (user, device), group_entries in grouped.items():
            first = group_entries[0]
            command_lines = [f"· {item['cmd']}" for item in group_entries[:30]]
            commands = "\n".join(command_lines)
            text = (
                f"操作设备: {device}\n\n"
                f"操作人员: {user}\n\n"
                f"操作时间: {first['time']}\n\n"
                f"操作命令:\n{commands}"
            )
            card_data = {
                "severity": "TACACS",
                "notification_type": "tacacs",
                "title": "Tacacs命令操作通知",
                "headline": "Tacacs",
                "summary": f"{user} @ {device}",
                "subtitle": first["time"],
                "rows": [
                    {"label": "操作设备", "value": device},
                    {"label": "操作人员", "value": user},
                    {"label": "操作时间", "value": first["time"]},
                    {"label": "操作命令", "value": "\n" + "\n".join(command_lines[:10])},
                ],
            }
            try:
                sent += asyncio.run(_send_robot_notification("Tacacs命令操作通知", text, card_data))
            except Exception as exc:
                logger.error("Tacacs命令日志通知失败", error=str(exc))

        logger.info("Tacacs命令日志处理完成", processed=len(entries), groups=len(grouped), sent=sent)
        return {"processed": len(entries), "sent": sent}
    finally:
        redis_client.delete(LOCK_KEY)
