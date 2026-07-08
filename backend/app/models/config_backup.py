from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import relationship

from app.database import Base


class ConfigBackupJob(Base):
    """配置备份任务。"""

    __tablename__ = "config_backup_jobs"

    id = Column(Integer, primary_key=True, index=True)
    status = Column(String(20), default="pending", index=True)
    trigger_type = Column(String(20), default="manual", index=True)
    total_devices = Column(Integer, default=0)
    success_count = Column(Integer, default=0)
    failed_count = Column(Integer, default=0)
    config_changed_count = Column(Integer, default=0)
    config_saved_count = Column(Integer, default=0)
    config_save_failed_count = Column(Integer, default=0)
    summary = Column(Text)
    error_message = Column(Text)
    started_by = Column(String(100))
    started_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    finished_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    results = relationship("ConfigBackupResult", back_populates="job", cascade="all, delete-orphan")


class ConfigBackupResult(Base):
    """单台设备配置备份结果。"""

    __tablename__ = "config_backup_results"

    id = Column(Integer, primary_key=True, index=True)
    job_id = Column(Integer, ForeignKey("config_backup_jobs.id"), index=True)
    device_id = Column(Integer, ForeignKey("devices.id"), index=True)
    device_name = Column(String(255), index=True)
    device_ip = Column(String(45), index=True)
    datacenter_name = Column(String(100), index=True)
    vendor = Column(String(100))
    model = Column(String(100))
    status = Column(String(20), default="pending", index=True)
    command = Column(String(255))
    config_content = Column(Text)
    config_hash = Column(String(64), index=True)
    line_count = Column(Integer, default=0)
    startup_command = Column(String(255))
    startup_config_content = Column(Text)
    startup_config_hash = Column(String(64), index=True)
    startup_line_count = Column(Integer, default=0)
    config_sync_status = Column(String(30), index=True)
    config_sync_diff = Column(Text)
    config_save_command = Column(String(255))
    config_save_status = Column(String(30), index=True)
    config_save_message = Column(Text)
    error_message = Column(Text)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), index=True)

    job = relationship("ConfigBackupJob", back_populates="results")
    device = relationship("Device")
