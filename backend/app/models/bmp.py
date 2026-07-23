"""BGP Monitoring Protocol (BMP) runtime state and event records."""
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB

from app.database import Base


class BmpSession(Base):
    """Latest BMP TCP session state per collector/source tuple."""

    __tablename__ = "bmp_sessions"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="SET NULL"), index=True)
    source_ip = Column(String(45), nullable=False, index=True)
    source_port = Column(Integer)
    collector_ip = Column(String(45))
    collector_port = Column(Integer, default=1790)
    status = Column(String(20), nullable=False, default="connected", index=True)
    connected_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    disconnected_at = Column(DateTime(timezone=True))
    last_seen_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    message_count = Column(Integer, default=0)
    peer_up_count = Column(Integer, default=0)
    peer_down_count = Column(Integer, default=0)
    route_monitoring_count = Column(Integer, default=0)
    statistics_count = Column(Integer, default=0)
    last_message_type = Column(String(50))
    last_error = Column(Text)


class BmpMessage(Base):
    """Compact BMP message/event record.

    The collector intentionally stores a concise summary first. Full BGP UPDATE
    route decoding can be layered on later without losing session observability.
    """

    __tablename__ = "bmp_messages"

    id = Column(Integer, primary_key=True, index=True)
    session_id = Column(Integer, ForeignKey("bmp_sessions.id", ondelete="SET NULL"), index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="SET NULL"), index=True)
    source_ip = Column(String(45), nullable=False, index=True)
    message_type = Column(String(50), nullable=False, index=True)
    bmp_version = Column(Integer)
    length = Column(Integer)
    peer_ip = Column(String(45), index=True)
    peer_asn = Column(Integer)
    peer_bgp_id = Column(String(45))
    peer_type = Column(Integer)
    peer_flags = Column(Integer)
    timestamp_seconds = Column(Integer)
    timestamp_microseconds = Column(Integer)
    payload_preview = Column(Text)
    extra = Column(JSONB, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
