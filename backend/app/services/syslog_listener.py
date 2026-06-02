"""
Syslog UDP 监听器
"""
from __future__ import annotations

import asyncio
import re
from typing import Optional, Tuple

from app.config import settings
from app.core import get_logger
from app.database import SessionLocal
from app.models import Device, SyslogEvent

logger = get_logger(__name__)

RFC3164_RE = re.compile(
    r"^(?:<(?P<pri>\d+)>)?(?P<timestamp>[A-Z][a-z]{2}\s+\d+\s+\d+:\d+:\d+)\s+(?P<host>\S+)\s*(?P<body>.*)$"
)
RFC5424_RE = re.compile(
    r"^(?:<(?P<pri>\d+)>)?\d+\s+(?P<timestamp>\S+)\s+(?P<host>\S+)\s+(?P<app>\S+)\s+\S+\s+\S+\s+\S+\s*(?P<body>.*)$"
)


class _SyslogProtocol(asyncio.DatagramProtocol):
    def datagram_received(self, data: bytes, addr: Tuple[str, int]) -> None:
        source_ip, _ = addr
        try:
            raw = data.decode("utf-8", errors="replace").strip()
            if not raw:
                return
            _persist_syslog_event(source_ip, raw)
        except Exception as exc:
            logger.error("处理Syslog消息失败", source_ip=source_ip, error=str(exc))


def _parse_syslog_message(raw: str) -> tuple[Optional[int], Optional[int], Optional[str], Optional[str], str]:
    pri: Optional[int] = None
    source_host: Optional[str] = None
    app_name: Optional[str] = None
    message = raw

    match = RFC5424_RE.match(raw) or RFC3164_RE.match(raw)
    if match:
        pri_text = match.groupdict().get("pri")
        if pri_text and pri_text.isdigit():
            pri = int(pri_text)
        source_host = match.groupdict().get("host")
        app_name = match.groupdict().get("app")
        message = (match.groupdict().get("body") or raw).strip()

    facility = pri // 8 if pri is not None else None
    severity = pri % 8 if pri is not None else None
    return facility, severity, source_host, app_name, message


def _persist_syslog_event(source_ip: str, raw_message: str) -> None:
    facility, severity, source_host, app_name, message = _parse_syslog_message(raw_message)
    db = SessionLocal()
    try:
        device = db.query(Device).filter(Device.ip_address == source_ip).first()
        if not device and source_host:
            device = db.query(Device).filter(
                (Device.ip_address == source_host) |
                (Device.hostname == source_host) |
                (Device.name == source_host)
            ).first()
        event = SyslogEvent(
            device_id=device.id if device else None,
            source_ip=source_ip,
            source_host=source_host,
            facility=facility,
            severity=severity,
            app_name=app_name,
            message=message,
            raw_message=raw_message,
        )
        db.add(event)
        db.commit()
    except Exception as exc:
        db.rollback()
        logger.error("写入Syslog事件失败", source_ip=source_ip, error=str(exc))
    finally:
        db.close()


class SyslogListener:
    def __init__(self) -> None:
        self.transport = None

    async def start(self) -> None:
        if not settings.SYSLOG_ENABLED:
            logger.info("Syslog监听未启用")
            return
        if self.transport is not None:
            return

        loop = asyncio.get_running_loop()
        self.transport, _ = await loop.create_datagram_endpoint(
            lambda: _SyslogProtocol(),
            local_addr=(settings.SYSLOG_LISTEN_HOST, settings.SYSLOG_LISTEN_PORT),
        )
        logger.info(
            "Syslog监听已启动",
            host=settings.SYSLOG_LISTEN_HOST,
            port=settings.SYSLOG_LISTEN_PORT,
        )

    async def stop(self) -> None:
        if self.transport is not None:
            self.transport.close()
            self.transport = None
            logger.info("Syslog监听已停止")


syslog_listener = SyslogListener()
