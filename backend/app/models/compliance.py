"""设备型号、版本基线与上线合规模型。"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, JSON, String, Text, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base


class DeviceModelProfile(Base):
    """厂商型号能力模板。"""

    __tablename__ = "device_model_profiles"
    __table_args__ = (
        UniqueConstraint("vendor", "model_pattern", "network_type", name="uq_device_model_profile_scope"),
    )

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    vendor = Column(String(50), nullable=False, index=True)
    model_pattern = Column(String(120), nullable=False, index=True)
    network_type = Column(String(50), nullable=False, default="general", server_default="general", index=True)
    device_type = Column(String(50))
    default_role = Column(String(50))
    capabilities = Column(JSON, nullable=False, default=dict)
    required_checks = Column(JSON, nullable=False, default=list)
    description = Column(Text)
    priority = Column(Integer, nullable=False, default=100, server_default="100")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "vendor": self.vendor,
            "model_pattern": self.model_pattern,
            "network_type": self.network_type,
            "device_type": self.device_type,
            "default_role": self.default_role,
            "capabilities": self.capabilities or {},
            "required_checks": self.required_checks or [],
            "description": self.description,
            "priority": self.priority,
            "is_active": bool(self.is_active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class VersionBaseline(Base):
    """设备软件版本和补丁基线。"""

    __tablename__ = "version_baselines"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False)
    model_profile_id = Column(Integer, ForeignKey("device_model_profiles.id", ondelete="SET NULL"), index=True)
    vendor = Column(String(50), index=True)
    model_pattern = Column(String(120), index=True)
    device_role = Column(String(50), index=True)
    allowed_versions = Column(JSON, nullable=False, default=list)
    minimum_version = Column(String(100))
    required_patches = Column(JSON, nullable=False, default=list)
    forbidden_versions = Column(JSON, nullable=False, default=list)
    recommendation = Column(Text)
    priority = Column(Integer, nullable=False, default=100, server_default="100")
    is_active = Column(Boolean, nullable=False, default=True, server_default="true")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "model_profile_id": self.model_profile_id,
            "vendor": self.vendor,
            "model_pattern": self.model_pattern,
            "device_role": self.device_role,
            "allowed_versions": self.allowed_versions or [],
            "minimum_version": self.minimum_version,
            "required_patches": self.required_patches or [],
            "forbidden_versions": self.forbidden_versions or [],
            "recommendation": self.recommendation,
            "priority": self.priority,
            "is_active": bool(self.is_active),
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeviceComplianceSnapshot(Base):
    """最近一次设备上线合规评估结果。"""

    __tablename__ = "device_compliance_snapshots"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, unique=True, index=True)
    model_profile_id = Column(Integer, ForeignKey("device_model_profiles.id", ondelete="SET NULL"), index=True)
    version_baseline_id = Column(Integer, ForeignKey("version_baselines.id", ondelete="SET NULL"), index=True)
    overall_status = Column(String(30), nullable=False, default="pending", server_default="pending", index=True)
    score = Column(Integer, nullable=False, default=0, server_default="0")
    observed_vendor = Column(String(50))
    observed_model = Column(String(120))
    observed_version = Column(String(255))
    observed_patches = Column(JSON, nullable=False, default=list)
    checks = Column(JSON, nullable=False, default=list)
    blockers = Column(JSON, nullable=False, default=list)
    evaluated_at = Column(DateTime(timezone=True), nullable=False, server_default=func.now(), index=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "device_id": self.device_id,
            "model_profile_id": self.model_profile_id,
            "version_baseline_id": self.version_baseline_id,
            "overall_status": self.overall_status,
            "score": self.score,
            "observed_vendor": self.observed_vendor,
            "observed_model": self.observed_model,
            "observed_version": self.observed_version,
            "observed_patches": self.observed_patches or [],
            "checks": self.checks or [],
            "blockers": self.blockers or [],
            "evaluated_at": self.evaluated_at.isoformat() if self.evaluated_at else None,
        }
