"""
告警数据模型
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Float, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


def _short_alarm_id(alert_id: int, started_at):
    if started_at:
        return f"A{started_at.strftime('%m%d')}-{alert_id % 100000:05d}"
    return f"A-{alert_id % 100000:05d}"


class AlertRule(Base):
    """告警规则模型"""
    __tablename__ = "alert_rules"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    description = Column(Text)
    
    # 告警类型: threshold, change_rate, duration
    rule_type = Column(String(20), default="threshold")
    
    # 指标类型: snmp_cpu, snmp_memory, snmp_traffic, gnmi_*
    metric_type = Column(String(50), nullable=False)
    
    # 条件: >, <, >=, <=, ==, !=
    condition = Column(String(10), nullable=False)
    
    # 阈值
    threshold = Column(Float, nullable=False)
    
    # 持续时间(秒): 超过阈值持续多长时间才触发告警
    duration = Column(Integer, default=0)
    
    # 变化率告警配置
    change_rate_threshold = Column(Float)  # 变化率阈值
    change_rate_window = Column(Integer)   # 变化率计算窗口(秒)
    
    # 告警级别: P0, P1, P2
    severity = Column(String(20), default="P1")
    
    # 通知渠道配置 (JSON格式)
    notification_channels = Column(JSON, default=list)
    # 示例: [{"type": "wechat", "webhook": "..."}, {"type": "email", "recipients": ["..."]}]
    
    # 告警抑制配置
    suppress_duration = Column(Integer, default=300)  # 抑制时间(秒)
    
    # 启用状态
    enabled = Column(Integer, default=1)
    
    # 应用到设备组或指定设备
    device_group_id = Column(Integer, ForeignKey('device_groups.id'), nullable=True)
    device_ids = Column(JSON, default=list)  # 指定设备ID列表
    extra_config = Column(JSON, default=dict)  # 附加匹配配置，如接口名、邻居、关键字等
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    alert_histories = relationship("AlertHistory", back_populates="rule")
    
    def __repr__(self):
        return f"<AlertRule {self.name}>"


class AlertHistory(Base):
    """告警历史记录模型"""
    __tablename__ = "alert_histories"
    
    id = Column(Integer, primary_key=True, index=True)
    alarm_id = Column(String(64), index=True)
    
    # 关联规则
    rule_id = Column(Integer, ForeignKey('alert_rules.id'))
    rule = relationship("AlertRule", back_populates="alert_histories")
    
    # 关联设备
    device_id = Column(Integer, ForeignKey('devices.id'))
    device = relationship("Device", back_populates="alert_histories")
    
    # 告警详情
    alert_value = Column(Float)
    threshold = Column(Float)
    message = Column(Text)
    alert_target_type = Column(String(50))
    alert_target_key = Column(String(255), index=True)
    alert_target_name = Column(String(255))
    
    # 状态: firing, resolved, acknowledged, ignored, snoozed
    status = Column(String(20), default="firing")
    
    # 处理信息
    acknowledged_by = Column(String(100))
    acknowledged_at = Column(DateTime(timezone=True))
    ignored_by = Column(String(100))
    ignored_at = Column(DateTime(timezone=True))
    resolved_by = Column(String(100))
    resolved_at = Column(DateTime(timezone=True))
    resolution_note = Column(Text)
    
    # 通知记录
    notifications_sent = Column(JSON, default=list)
    
    # 时间戳
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    def __repr__(self):
        return f"<AlertHistory {self.id}: {self.status}>"
    
    def to_dict(self):
        mention_users = []
        if self.rule and isinstance(self.rule.extra_config, dict):
            raw_mentions = self.rule.extra_config.get("mention_users") or []
            if isinstance(raw_mentions, str):
                mention_users = [item.strip() for item in raw_mentions.split(",") if item.strip()]
            elif isinstance(raw_mentions, list):
                mention_users = [str(item).strip() for item in raw_mentions if str(item).strip()]
        resolved_by = None if self.resolved_by == "rule_disabled" else self.resolved_by
        current_handler = None
        if self.status == "acknowledged":
            current_handler = self.acknowledged_by
        elif self.status == "ignored":
            current_handler = self.ignored_by
        elif self.status == "snoozed":
            current_handler = resolved_by or self.acknowledged_by
        elif self.status == "resolved":
            current_handler = resolved_by or self.acknowledged_by
        elif mention_users:
            current_handler = "、".join(mention_users)
        return {
            "id": self.id,
            "alarm_id": _short_alarm_id(self.id, self.started_at),
            "raw_alarm_id": self.alarm_id,
            "rule_id": self.rule_id,
            "device_id": self.device_id,
            "device_name": self.device.name if self.device else None,
            "device_ip": self.device.ip_address if self.device else None,
            "alert_value": self.alert_value,
            "threshold": self.threshold,
            "message": self.message,
            "alert_target_type": self.alert_target_type,
            "alert_target_key": self.alert_target_key,
            "alert_target_name": self.alert_target_name,
            "status": self.status,
            "severity": self.rule.severity if self.rule else None,
            "acknowledged_by": self.acknowledged_by,
            "acknowledged_at": self.acknowledged_at.isoformat() if self.acknowledged_at else None,
            "ignored_by": self.ignored_by,
            "ignored_at": self.ignored_at.isoformat() if self.ignored_at else None,
            "resolved_by": self.resolved_by,
            "started_at": self.started_at.isoformat() if self.started_at else None,
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
            "resolution_note": self.resolution_note,
            "current_handler": current_handler,
        }


class AlertSilence(Base):
    """告警屏蔽规则"""
    __tablename__ = "alert_silences"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    rule_id = Column(Integer, ForeignKey('alert_rules.id'), nullable=True)
    device_id = Column(Integer, ForeignKey('devices.id'), nullable=True)
    target_pattern = Column(String(255), nullable=True)
    include_device_ip = Column(String(255), nullable=True)
    include_interface = Column(String(255), nullable=True)
    include_message = Column(String(255), nullable=True)
    exclude_device_ip = Column(String(255), nullable=True)
    exclude_interface = Column(String(255), nullable=True)
    exclude_message = Column(String(255), nullable=True)
    starts_at = Column(DateTime(timezone=True), nullable=True)
    conditions = Column(JSON, default=list)
    reason = Column(Text, nullable=True)
    created_by = Column(String(100), nullable=True)
    enabled = Column(Integer, default=1)
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    rule = relationship("AlertRule")
    device = relationship("Device")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "rule_id": self.rule_id,
            "device_id": self.device_id,
            "device_name": self.device.name if self.device else None,
            "target_pattern": self.target_pattern,
            "include_device_ip": self.include_device_ip,
            "include_interface": self.include_interface,
            "include_message": self.include_message,
            "exclude_device_ip": self.exclude_device_ip,
            "exclude_interface": self.exclude_interface,
            "exclude_message": self.exclude_message,
            "starts_at": self.starts_at.isoformat() if self.starts_at else None,
            "conditions": self.conditions or [],
            "reason": self.reason,
            "created_by": self.created_by,
            "enabled": bool(self.enabled),
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class SyslogEvent(Base):
    """设备 Syslog 事件"""
    __tablename__ = "syslog_events"

    id = Column(Integer, primary_key=True, index=True)
    device_id = Column(Integer, ForeignKey('devices.id'), nullable=True)
    source_ip = Column(String(45), index=True)
    source_host = Column(String(255), nullable=True)
    facility = Column(Integer, nullable=True)
    severity = Column(Integer, nullable=True)
    app_name = Column(String(100), nullable=True)
    message = Column(Text, nullable=False)
    raw_message = Column(Text, nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    device = relationship("Device")

    def __repr__(self):
        return f"<SyslogEvent {self.source_ip}: {self.message[:32]}>"
