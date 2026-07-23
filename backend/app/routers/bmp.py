"""BMP 接入状态查询。"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Optional

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import BmpMessage, BmpSession, Device


router = APIRouter()


def _session_to_dict(session: BmpSession, device: Optional[Device] = None) -> dict:
    return {
        "id": session.id,
        "device_id": session.device_id,
        "device_name": device.name if device else None,
        "device_ip": device.ip_address if device else session.source_ip,
        "source_ip": session.source_ip,
        "source_port": session.source_port,
        "collector_ip": session.collector_ip,
        "collector_port": session.collector_port,
        "status": session.status,
        "connected_at": session.connected_at,
        "disconnected_at": session.disconnected_at,
        "last_seen_at": session.last_seen_at,
        "message_count": session.message_count or 0,
        "peer_up_count": session.peer_up_count or 0,
        "peer_down_count": session.peer_down_count or 0,
        "route_monitoring_count": session.route_monitoring_count or 0,
        "statistics_count": session.statistics_count or 0,
        "last_message_type": session.last_message_type,
        "last_error": session.last_error,
    }


def _message_to_dict(message: BmpMessage, device: Optional[Device] = None) -> dict:
    return {
        "id": message.id,
        "session_id": message.session_id,
        "device_id": message.device_id,
        "device_name": device.name if device else None,
        "device_ip": device.ip_address if device else message.source_ip,
        "source_ip": message.source_ip,
        "message_type": message.message_type,
        "bmp_version": message.bmp_version,
        "length": message.length,
        "peer_ip": message.peer_ip,
        "peer_asn": message.peer_asn,
        "peer_bgp_id": message.peer_bgp_id,
        "peer_type": message.peer_type,
        "peer_flags": message.peer_flags,
        "timestamp_seconds": message.timestamp_seconds,
        "timestamp_microseconds": message.timestamp_microseconds,
        "extra": message.extra or {},
        "created_at": message.created_at,
    }


def _prefix_preview(extra: dict) -> list[str]:
    announced = extra.get("announced_prefixes") or []
    withdrawn = extra.get("withdrawn_prefixes") or []
    if announced:
        return list(announced)[:8]
    if withdrawn:
        return list(withdrawn)[:8]
    return []


@router.get("/sessions")
async def list_bmp_sessions(
    device_id: Optional[int] = Query(None),
    source_ip: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    query = db.query(BmpSession)
    if device_id:
        query = query.filter(BmpSession.device_id == device_id)
    if source_ip:
        query = query.filter(BmpSession.source_ip == source_ip)
    if status:
        query = query.filter(BmpSession.status == status)
    sessions = query.order_by(BmpSession.last_seen_at.desc()).limit(limit).all()
    device_ids = [item.device_id for item in sessions if item.device_id]
    devices = {device.id: device for device in db.query(Device).filter(Device.id.in_(device_ids)).all()} if device_ids else {}
    return {"items": [_session_to_dict(item, devices.get(item.device_id)) for item in sessions]}


@router.get("/messages")
async def list_bmp_messages(
    device_id: Optional[int] = Query(None),
    source_ip: Optional[str] = Query(None),
    message_type: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=24 * 30),
    limit: int = Query(200, ge=1, le=1000),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = db.query(BmpMessage).filter(BmpMessage.created_at >= since)
    if device_id:
        query = query.filter(BmpMessage.device_id == device_id)
    if source_ip:
        query = query.filter(BmpMessage.source_ip == source_ip)
    if message_type:
        query = query.filter(BmpMessage.message_type == message_type)
    total = query.count()
    messages = query.order_by(BmpMessage.created_at.desc()).offset(offset).limit(limit).all()
    device_ids = [item.device_id for item in messages if item.device_id]
    devices = {device.id: device for device in db.query(Device).filter(Device.id.in_(device_ids)).all()} if device_ids else {}
    return {
        "items": [_message_to_dict(item, devices.get(item.device_id)) for item in messages],
        "total": total,
        "limit": limit,
        "offset": offset,
    }


@router.get("/peers")
async def list_bmp_peers(
    device_id: Optional[int] = Query(None),
    source_ip: Optional[str] = Query(None),
    hours: int = Query(24, ge=1, le=24 * 30),
    db: Session = Depends(get_db),
):
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    query = db.query(BmpMessage).filter(BmpMessage.created_at >= since, BmpMessage.peer_ip.isnot(None))
    if device_id:
        query = query.filter(BmpMessage.device_id == device_id)
    if source_ip:
        query = query.filter(BmpMessage.source_ip == source_ip)

    messages = query.order_by(BmpMessage.created_at.desc()).limit(5000).all()
    peer_rows: dict[str, dict] = {}
    for message in messages:
        key = message.peer_ip or "-"
        extra = message.extra or {}
        row = peer_rows.setdefault(
            key,
            {
                "peer_ip": key,
                "peer_asn": message.peer_asn,
                "peer_bgp_id": message.peer_bgp_id,
                "last_seen_at": message.created_at,
                "last_message_type": message.message_type,
                "message_count": 0,
                "route_monitoring_count": 0,
                "peer_up_count": 0,
                "peer_down_count": 0,
                "last_action": extra.get("action"),
                "last_prefixes": _prefix_preview(extra),
                "last_next_hop": extra.get("next_hop"),
                "last_error": extra.get("peer_down_reason_text") or extra.get("bgp_parse_error"),
            },
        )
        row["message_count"] += 1
        if message.message_type == "route_monitoring":
            row["route_monitoring_count"] += 1
        elif message.message_type == "peer_up":
            row["peer_up_count"] += 1
        elif message.message_type == "peer_down":
            row["peer_down_count"] += 1
        if message.created_at and (not row.get("last_seen_at") or message.created_at > row["last_seen_at"]):
            row["peer_asn"] = message.peer_asn
            row["peer_bgp_id"] = message.peer_bgp_id
            row["last_seen_at"] = message.created_at
            row["last_message_type"] = message.message_type
            row["last_action"] = extra.get("action")
            row["last_prefixes"] = _prefix_preview(extra)
            row["last_next_hop"] = extra.get("next_hop")
            row["last_error"] = extra.get("peer_down_reason_text") or extra.get("bgp_parse_error")
    return {"items": list(peer_rows.values())}


@router.get("/summary")
async def get_bmp_summary(db: Session = Depends(get_db)):
    total_sessions = db.query(BmpSession).count()
    connected_sessions = db.query(BmpSession).filter(BmpSession.status == "connected").count()
    recent_since = datetime.now(timezone.utc) - timedelta(hours=1)
    recent_messages = db.query(BmpMessage).filter(BmpMessage.created_at >= recent_since).count()
    recent_peer_down = db.query(BmpMessage).filter(
        BmpMessage.created_at >= recent_since,
        BmpMessage.message_type == "peer_down",
    ).count()
    recent_peer_up = db.query(BmpMessage).filter(
        BmpMessage.created_at >= recent_since,
        BmpMessage.message_type == "peer_up",
    ).count()
    return {
        "total_sessions": total_sessions,
        "connected_sessions": connected_sessions,
        "recent_1h_messages": recent_messages,
        "recent_1h_peer_up": recent_peer_up,
        "recent_1h_peer_down": recent_peer_down,
    }
