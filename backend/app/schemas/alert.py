"""
告警相关 Pydantic Schemas
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime


class NotificationChannel(BaseModel):
    """通知渠道配置"""
    type: str = Field(..., pattern="^(wechat|dingtalk|feishu|email|webhook)$")
    config: Dict[str, Any] = Field(default_factory=dict)


class AlertRuleBase(BaseModel):
    """告警规则基础Schema"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    rule_type: str = Field(default="threshold", pattern="^(threshold|change_rate|duration)$")
    metric_type: str = Field(..., min_length=1, max_length=50)
    condition: str = Field(..., pattern="^(>|>=|<|<=|==|!=)$")
    threshold: float
    duration: int = Field(default=0, ge=0)
    change_rate_threshold: Optional[float] = None
    change_rate_window: Optional[int] = None
    severity: str = Field(default="P1", pattern="^(critical|warning|info|P0|P1|P2|P3)$")
    suppress_duration: int = Field(default=300, ge=0)
    enabled: bool = True
    device_group_id: Optional[int] = None
    device_ids: List[int] = Field(default_factory=list)
    extra_config: Dict[str, Any] = Field(default_factory=dict)


class AlertRuleCreate(AlertRuleBase):
    """创建告警规则Schema"""
    notification_channels: List[NotificationChannel] = Field(default_factory=list)


class AlertRuleUpdate(BaseModel):
    """更新告警规则Schema"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    rule_type: Optional[str] = Field(None, pattern="^(threshold|change_rate|duration)$")
    metric_type: Optional[str] = Field(None, min_length=1, max_length=50)
    condition: Optional[str] = Field(None, pattern="^(>|>=|<|<=|==|!=)$")
    threshold: Optional[float] = None
    duration: Optional[int] = Field(None, ge=0)
    change_rate_threshold: Optional[float] = None
    change_rate_window: Optional[int] = None
    severity: Optional[str] = Field(None, pattern="^(critical|warning|info|P0|P1|P2|P3)$")
    suppress_duration: Optional[int] = Field(None, ge=0)
    enabled: Optional[bool] = None
    notification_channels: Optional[List[NotificationChannel]] = None
    device_group_id: Optional[int] = None
    device_ids: Optional[List[int]] = None
    extra_config: Optional[Dict[str, Any]] = None


class AlertRuleResponse(AlertRuleBase):
    """告警规则响应Schema"""
    id: int
    notification_channels: List[NotificationChannel]
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AlertHistoryResponse(BaseModel):
    """告警历史响应Schema"""
    id: int
    alarm_id: Optional[str] = None
    rule_id: int
    device_id: int
    device_name: Optional[str] = None
    device_ip: Optional[str] = None
    alert_value: Optional[float] = None
    threshold: Optional[float] = None
    message: Optional[str] = None
    alert_target_type: Optional[str] = None
    alert_target_key: Optional[str] = None
    alert_target_name: Optional[str] = None
    status: str
    severity: Optional[str] = None
    acknowledged_by: Optional[str] = None
    acknowledged_at: Optional[datetime] = None
    ignored_by: Optional[str] = None
    ignored_at: Optional[datetime] = None
    resolved_by: Optional[str] = None
    resolved_at: Optional[datetime] = None
    resolution_note: Optional[str] = None
    current_handler: Optional[str] = None
    started_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class AlertAcknowledge(BaseModel):
    """告警确认Schema"""
    note: Optional[str] = None
    actor_username: Optional[str] = None


class AlertResolve(BaseModel):
    """告警解决Schema"""
    note: Optional[str] = None
    actor_username: Optional[str] = None


class AlertIgnore(BaseModel):
    """告警忽略Schema"""
    note: Optional[str] = None
    actor_username: Optional[str] = None


class AlertHistoryClear(BaseModel):
    """批量清除告警历史"""
    status: Optional[str] = None
    severity: Optional[str] = None
    datacenter: Optional[str] = None
    search: Optional[str] = None
    alert_id: Optional[int] = None
    device_id: Optional[int] = None
    rule_id: Optional[int] = None
    alarm_id: Optional[str] = None
    older_than_days: Optional[int] = None
    include_active: bool = False
    confirm_text: str
    actor_username: Optional[str] = None


class AlertStats(BaseModel):
    """告警统计"""
    total_firing: int
    total_resolved: int
    by_severity: Dict[str, int]
    by_device: Dict[str, int]


class SyslogEventResponse(BaseModel):
    id: int
    device_id: Optional[int] = None
    source_ip: Optional[str] = None
    source_host: Optional[str] = None
    facility: Optional[int] = None
    severity: Optional[int] = None
    app_name: Optional[str] = None
    message: str
    raw_message: str
    created_at: datetime

    class Config:
        from_attributes = True


class AlertSilenceBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    rule_id: Optional[int] = None
    device_id: Optional[int] = None
    target_pattern: Optional[str] = None
    include_device_ip: Optional[str] = None
    include_interface: Optional[str] = None
    include_message: Optional[str] = None
    exclude_device_ip: Optional[str] = None
    exclude_interface: Optional[str] = None
    exclude_message: Optional[str] = None
    starts_at: Optional[datetime] = None
    conditions: List[Dict[str, Any]] = Field(default_factory=list)
    reason: Optional[str] = None
    enabled: bool = True
    expires_at: Optional[datetime] = None
    actor_username: Optional[str] = None


class AlertSilenceCreate(AlertSilenceBase):
    pass


class AlertSilenceUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    rule_id: Optional[int] = None
    device_id: Optional[int] = None
    target_pattern: Optional[str] = None
    include_device_ip: Optional[str] = None
    include_interface: Optional[str] = None
    include_message: Optional[str] = None
    exclude_device_ip: Optional[str] = None
    exclude_interface: Optional[str] = None
    exclude_message: Optional[str] = None
    starts_at: Optional[datetime] = None
    conditions: Optional[List[Dict[str, Any]]] = None
    reason: Optional[str] = None
    enabled: Optional[bool] = None
    expires_at: Optional[datetime] = None


class AlertSilenceResponse(AlertSilenceBase):
    id: int
    device_name: Optional[str] = None
    created_by: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
