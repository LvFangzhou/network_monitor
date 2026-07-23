"""Lightweight BGP Monitoring Protocol (BMP) collector.

This collector accepts TCP BMP sessions, parses the BMP common/per-peer header,
and extracts operator-friendly summaries from BGP UPDATE messages carried in
Route Monitoring events.
"""
from __future__ import annotations

import argparse
import asyncio
import binascii
import logging
import os
import socket
import struct
from datetime import datetime, timezone
from typing import Any, Optional

from sqlalchemy.orm import Session

from app.database import SessionLocal, init_db
from app.models import BmpMessage, BmpSession, Device


LOGGER = logging.getLogger("bmp_receiver")

BMP_VERSION = 3
BMP_HEADER_LENGTH = 6
BMP_MAX_MESSAGE_LENGTH = int(os.getenv("BMP_MAX_MESSAGE_LENGTH", str(16 * 1024 * 1024)))
BMP_SESSION_HEARTBEAT_SECONDS = int(os.getenv("BMP_SESSION_HEARTBEAT_SECONDS", "30"))

BMP_MESSAGE_TYPES = {
    0: "route_monitoring",
    1: "statistics_report",
    2: "peer_down",
    3: "peer_up",
    4: "initiation",
    5: "termination",
    6: "route_mirroring",
}


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _ip_from_16(raw: bytes, ipv6: bool = False) -> str:
    if len(raw) != 16:
        return ""
    try:
        if ipv6:
            return socket.inet_ntop(socket.AF_INET6, raw)
        if raw[:12] == b"\x00" * 12 or raw[:12] == b"\x00" * 10 + b"\xff\xff":
            return socket.inet_ntop(socket.AF_INET, raw[-4:])
        # Some vendors set IPv4 peer addresses in the last four bytes without
        # the v4-mapped marker. Prefer a readable IPv4 form when the high bytes
        # are empty, otherwise fall back to IPv6 notation for diagnostics.
        if raw[:12].rstrip(b"\x00") == b"":
            return socket.inet_ntop(socket.AF_INET, raw[-4:])
        return socket.inet_ntop(socket.AF_INET6, raw)
    except OSError:
        return binascii.hexlify(raw).decode("ascii")


def _safe_preview(payload: bytes, limit: int = 192) -> str:
    if not payload:
        return ""
    return binascii.hexlify(payload[:limit]).decode("ascii") + ("..." if len(payload) > limit else "")


def _resolve_device_id(db: Session, source_ip: str) -> Optional[int]:
    if not source_ip:
        return None
    device = db.query(Device.id).filter(Device.ip_address == source_ip).first()
    return int(device.id) if device else None


def _parse_peer_header(payload: bytes, offset: int = 0) -> tuple[dict[str, Any], int]:
    """Parse BMP per-peer header if present.

    Layout is 42 bytes:
    peer type(1), flags(1), distinguisher(8), peer address(16), peer AS(4),
    peer BGP ID(4), timestamp seconds(4), timestamp microseconds(4).
    """
    if len(payload) < offset + 42:
        return {}, offset
    header = payload[offset : offset + 42]
    peer_type = header[0]
    peer_flags = header[1]
    peer_address = header[10:26]
    peer_asn = struct.unpack("!I", header[26:30])[0]
    bgp_id = socket.inet_ntoa(header[30:34])
    ts_sec = struct.unpack("!I", header[34:38])[0]
    ts_usec = struct.unpack("!I", header[38:42])[0]
    # RFC7854 peer flag V indicates IPv6 address when set.
    ipv6 = bool(peer_flags & 0x80)
    return {
        "peer_type": peer_type,
        "peer_flags": peer_flags,
        "peer_ip": _ip_from_16(peer_address, ipv6=ipv6),
        "peer_asn": peer_asn,
        "peer_bgp_id": bgp_id,
        "timestamp_seconds": ts_sec,
        "timestamp_microseconds": ts_usec,
    }, offset + 42


def _parse_ipv4_prefixes(data: bytes, offset: int = 0, limit: int = 64) -> tuple[list[str], int]:
    prefixes: list[str] = []
    pos = offset
    while pos < len(data) and len(prefixes) < limit:
        prefix_len = data[pos]
        pos += 1
        byte_len = (prefix_len + 7) // 8
        if prefix_len > 32 or pos + byte_len > len(data):
            break
        raw = data[pos : pos + byte_len] + b"\x00" * (4 - byte_len)
        pos += byte_len
        try:
            prefixes.append(f"{socket.inet_ntoa(raw)}/{prefix_len}")
        except OSError:
            continue
    return prefixes, pos


def _parse_as_path(value: bytes) -> str:
    parts: list[str] = []
    pos = 0
    while pos + 2 <= len(value):
        segment_type = value[pos]
        segment_len = value[pos + 1]
        pos += 2
        asns: list[str] = []
        # H3C/S9867 normally exports AS4 paths. Fall back to AS2 if the value
        # length indicates an older format.
        remaining = len(value) - pos
        asn_width = 4 if remaining >= segment_len * 4 else 2
        for _ in range(segment_len):
            if pos + asn_width > len(value):
                break
            asn = struct.unpack("!I" if asn_width == 4 else "!H", value[pos : pos + asn_width])[0]
            pos += asn_width
            asns.append(str(asn))
        if asns:
            if segment_type == 2:
                parts.extend(asns)
            else:
                parts.append("{" + ",".join(asns) + "}")
    return " ".join(parts)


def _parse_bgp_update(payload: bytes) -> dict[str, Any]:
    """Parse the useful IPv4 unicast fields from a BGP UPDATE message."""
    result: dict[str, Any] = {}
    if len(payload) < 19 or payload[:16] != b"\xff" * 16:
        result["bgp_parse_error"] = "payload is not a complete BGP message"
        return result
    bgp_len = struct.unpack("!H", payload[16:18])[0]
    bgp_type = payload[18]
    result.update({"bgp_message_type": bgp_type, "bgp_message_length": bgp_len})
    if bgp_type != 2:
        return result
    end = min(len(payload), bgp_len)
    pos = 19
    if pos + 2 > end:
        result["bgp_parse_error"] = "missing withdrawn-routes length"
        return result
    withdrawn_len = struct.unpack("!H", payload[pos : pos + 2])[0]
    pos += 2
    withdrawn_blob = payload[pos : min(pos + withdrawn_len, end)]
    withdrawn, _ = _parse_ipv4_prefixes(withdrawn_blob)
    pos += withdrawn_len
    if pos + 2 > end:
        result.update({"withdrawn_prefixes": withdrawn, "action": "withdraw" if withdrawn else "unknown"})
        return result

    attr_len = struct.unpack("!H", payload[pos : pos + 2])[0]
    pos += 2
    attr_end = min(pos + attr_len, end)
    attrs: dict[str, Any] = {}
    while pos + 3 <= attr_end:
        flags = payload[pos]
        attr_type = payload[pos + 1]
        pos += 2
        if flags & 0x10:
            if pos + 2 > attr_end:
                break
            length = struct.unpack("!H", payload[pos : pos + 2])[0]
            pos += 2
        else:
            length = payload[pos]
            pos += 1
        value = payload[pos : min(pos + length, attr_end)]
        pos += length
        if attr_type == 1 and value:
            attrs["origin"] = {0: "IGP", 1: "EGP", 2: "INCOMPLETE"}.get(value[0], str(value[0]))
        elif attr_type == 2:
            as_path = _parse_as_path(value)
            if as_path:
                attrs["as_path"] = as_path
        elif attr_type == 3 and len(value) >= 4:
            attrs["next_hop"] = socket.inet_ntoa(value[:4])
        elif attr_type == 4 and len(value) >= 4:
            attrs["med"] = struct.unpack("!I", value[:4])[0]
        elif attr_type == 5 and len(value) >= 4:
            attrs["local_pref"] = struct.unpack("!I", value[:4])[0]
        elif attr_type == 8:
            attrs["community_count"] = len(value) // 4
        elif attr_type == 14:
            attrs["mp_reach_nlri"] = True
        elif attr_type == 15:
            attrs["mp_unreach_nlri"] = True

    announced, _ = _parse_ipv4_prefixes(payload[attr_end:end])
    result.update(attrs)
    result["withdrawn_prefixes"] = withdrawn
    result["announced_prefixes"] = announced
    result["withdrawn_count"] = len(withdrawn)
    result["announced_count"] = len(announced)
    if announced and withdrawn:
        result["action"] = "announce_and_withdraw"
    elif announced:
        result["action"] = "announce"
    elif withdrawn:
        result["action"] = "withdraw"
    elif attrs.get("mp_reach_nlri") or attrs.get("mp_unreach_nlri"):
        result["action"] = "mp_bgp_update"
    else:
        result["action"] = "attribute_only"
    return result


def _parse_message(version: int, message_type_id: int, payload: bytes) -> dict[str, Any]:
    message_type = BMP_MESSAGE_TYPES.get(message_type_id, f"unknown_{message_type_id}")
    parsed: dict[str, Any] = {
        "bmp_version": version,
        "message_type": message_type,
        "message_type_id": message_type_id,
        "payload_preview": _safe_preview(payload),
        "extra": {},
    }
    if message_type_id in {0, 1, 2, 3, 6}:
        peer, offset = _parse_peer_header(payload)
        parsed.update(peer)
        parsed["extra"]["payload_after_peer_header_bytes"] = max(len(payload) - offset, 0)
        if message_type_id == 0 and len(payload) > offset:
            parsed["extra"].update(_parse_bgp_update(payload[offset:]))
        if message_type_id == 2 and len(payload) > offset:
            reason = payload[offset]
            parsed["extra"]["peer_down_reason"] = reason
            parsed["extra"]["peer_down_reason_text"] = {
                1: "本地系统关闭BGP会话",
                2: "本地系统收到Notification",
                3: "远端系统关闭BGP会话",
                4: "远端系统收到Notification",
            }.get(reason, f"未知原因({reason})")
        if message_type_id == 3 and len(payload) >= offset + 20:
            parsed["extra"]["local_address"] = _ip_from_16(payload[offset : offset + 16])
            parsed["extra"]["local_port"] = struct.unpack("!H", payload[offset + 16 : offset + 18])[0]
            parsed["extra"]["remote_port"] = struct.unpack("!H", payload[offset + 18 : offset + 20])[0]
    elif message_type_id in {4, 5}:
        parsed["extra"]["tlv_payload_bytes"] = len(payload)
    return parsed


def _create_session(source_ip: str, source_port: int, collector_ip: str, collector_port: int) -> int:
    db = SessionLocal()
    try:
        device_id = _resolve_device_id(db, source_ip)
        session = BmpSession(
            device_id=device_id,
            source_ip=source_ip,
            source_port=source_port,
            collector_ip=collector_ip,
            collector_port=collector_port,
            status="connected",
            connected_at=_now(),
            last_seen_at=_now(),
        )
        db.add(session)
        db.commit()
        db.refresh(session)
        return int(session.id)
    except Exception:
        db.rollback()
        LOGGER.exception("failed to create BMP session", extra={"source_ip": source_ip})
        raise
    finally:
        db.close()


def _record_message(session_id: int, source_ip: str, parsed: dict[str, Any], length: int) -> None:
    db = SessionLocal()
    try:
        session = db.query(BmpSession).filter(BmpSession.id == session_id).first()
        device_id = session.device_id if session else _resolve_device_id(db, source_ip)
        message_type = str(parsed.get("message_type") or "unknown")
        message = BmpMessage(
            session_id=session_id,
            device_id=device_id,
            source_ip=source_ip,
            message_type=message_type,
            bmp_version=parsed.get("bmp_version"),
            length=length,
            peer_ip=parsed.get("peer_ip"),
            peer_asn=parsed.get("peer_asn"),
            peer_bgp_id=parsed.get("peer_bgp_id"),
            peer_type=parsed.get("peer_type"),
            peer_flags=parsed.get("peer_flags"),
            timestamp_seconds=parsed.get("timestamp_seconds"),
            timestamp_microseconds=parsed.get("timestamp_microseconds"),
            payload_preview=parsed.get("payload_preview"),
            extra=parsed.get("extra") or {},
        )
        db.add(message)
        if session:
            session.device_id = device_id
            session.status = "connected"
            session.last_seen_at = _now()
            session.message_count = (session.message_count or 0) + 1
            session.last_message_type = message_type
            if message_type == "peer_up":
                session.peer_up_count = (session.peer_up_count or 0) + 1
            elif message_type == "peer_down":
                session.peer_down_count = (session.peer_down_count or 0) + 1
            elif message_type == "route_monitoring":
                session.route_monitoring_count = (session.route_monitoring_count or 0) + 1
            elif message_type == "statistics_report":
                session.statistics_count = (session.statistics_count or 0) + 1
        db.commit()
    except Exception:
        db.rollback()
        LOGGER.exception("failed to record BMP message", extra={"source_ip": source_ip, "session_id": session_id})
    finally:
        db.close()


def _touch_session(session_id: int) -> None:
    """Refresh liveness for an open BMP TCP connection.

    BMP route-monitoring messages are event-like: after the initial burst there
    may be no new BGP UPDATE for a while, but the TCP session is still healthy.
    Keeping last_seen_at fresh prevents operators from mistaking a quiet BMP
    feed for a stale one.
    """
    db = SessionLocal()
    try:
        session = db.query(BmpSession).filter(BmpSession.id == session_id).first()
        if session:
            session.status = "connected"
            session.last_seen_at = _now()
            db.commit()
    except Exception:
        db.rollback()
        LOGGER.exception("failed to touch BMP session", extra={"session_id": session_id})
    finally:
        db.close()


def _mark_previous_sessions_disconnected() -> None:
    """Mark sessions left behind by a collector restart as disconnected."""
    db = SessionLocal()
    try:
        now = _now()
        stale_sessions = db.query(BmpSession).filter(BmpSession.status == "connected").all()
        for session in stale_sessions:
            session.status = "disconnected"
            session.disconnected_at = now
            session.last_seen_at = now
            session.last_error = "collector restarted"
        if stale_sessions:
            db.commit()
            LOGGER.info("Marked %s previous BMP sessions as disconnected", len(stale_sessions))
    except Exception:
        db.rollback()
        LOGGER.exception("failed to mark previous BMP sessions disconnected")
    finally:
        db.close()


async def _session_heartbeat(session_id: int) -> None:
    try:
        while True:
            await asyncio.sleep(BMP_SESSION_HEARTBEAT_SECONDS)
            _touch_session(session_id)
    except asyncio.CancelledError:
        raise


def _close_session(session_id: int, error: str | None = None) -> None:
    db = SessionLocal()
    try:
        session = db.query(BmpSession).filter(BmpSession.id == session_id).first()
        if session:
            session.status = "disconnected"
            session.disconnected_at = _now()
            session.last_seen_at = _now()
            session.last_error = error
            db.commit()
    except Exception:
        db.rollback()
        LOGGER.exception("failed to close BMP session", extra={"session_id": session_id})
    finally:
        db.close()


async def _handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    peer = writer.get_extra_info("peername") or ("", 0)
    sock = writer.get_extra_info("sockname") or ("", 0)
    source_ip, source_port = str(peer[0]), int(peer[1])
    collector_ip, collector_port = str(sock[0]), int(sock[1])
    session_id = _create_session(source_ip, source_port, collector_ip, collector_port)
    LOGGER.info("BMP client connected source=%s:%s session_id=%s", source_ip, source_port, session_id)
    error: str | None = None
    heartbeat_task = asyncio.create_task(_session_heartbeat(session_id))
    try:
        while True:
            header = await reader.readexactly(BMP_HEADER_LENGTH)
            version = header[0]
            length = struct.unpack("!I", header[1:5])[0]
            message_type_id = header[5]
            if version != BMP_VERSION:
                error = f"unsupported BMP version {version}"
                LOGGER.warning("%s from %s", error, source_ip)
                break
            if length < BMP_HEADER_LENGTH or length > BMP_MAX_MESSAGE_LENGTH:
                error = f"invalid BMP length {length}"
                LOGGER.warning("%s from %s", error, source_ip)
                break
            payload = await reader.readexactly(length - BMP_HEADER_LENGTH)
            parsed = _parse_message(version, message_type_id, payload)
            _record_message(session_id, source_ip, parsed, length)
    except asyncio.IncompleteReadError:
        error = "client disconnected"
    except Exception as exc:
        error = str(exc)
        LOGGER.exception("BMP client handler error source=%s session_id=%s", source_ip, session_id)
    finally:
        heartbeat_task.cancel()
        try:
            await heartbeat_task
        except asyncio.CancelledError:
            pass
        _close_session(session_id, error)
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass
        LOGGER.info("BMP client disconnected source=%s:%s session_id=%s error=%s", source_ip, source_port, session_id, error)


async def run_server(host: str, port: int) -> None:
    init_db()
    _mark_previous_sessions_disconnected()
    server = await asyncio.start_server(_handle_client, host, port)
    addresses = ", ".join(str(sock.getsockname()) for sock in server.sockets or [])
    LOGGER.info("BMP receiver listening on %s", addresses)
    async with server:
        await server.serve_forever()


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="BMP TCP receiver")
    parser.add_argument("--host", default=os.getenv("BMP_RECEIVER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("BMP_RECEIVER_PORT", "1790")))
    args = parser.parse_args()
    asyncio.run(run_server(args.host, args.port))


if __name__ == "__main__":
    main()
