"""
指标查询相关 Pydantic Schemas
"""
from pydantic import BaseModel, Field
from typing import Optional, List, Dict, Any
from datetime import datetime
from enum import Enum


class MetricType(str, Enum):
    """指标类型"""
    SNMP_CPU = "snmp_cpu"
    SNMP_MEMORY = "snmp_memory"
    SNMP_TRAFFIC = "snmp_traffic"
    SNMP_TEMPERATURE = "snmp_temperature"
    GNM_INTERFACE = "gnmi_interface"
    GNM_SYSTEM = "gnmi_system"
    DEVICE_STATUS = "device_status"


class AggregationType(str, Enum):
    """聚合类型"""
    MEAN = "mean"
    SUM = "sum"
    MIN = "min"
    MAX = "max"
    COUNT = "count"
    FIRST = "first"
    LAST = "last"


class MetricQuery(BaseModel):
    """指标查询Schema"""
    device_ids: Optional[List[int]] = None
    metric_type: Optional[str] = None
    field: Optional[str] = None  # 具体字段，如: usage, in_octets
    start_time: Optional[datetime] = None
    end_time: Optional[datetime] = None
    aggregation: AggregationType = AggregationType.MEAN
    interval: Optional[str] = None  # 聚合间隔，如: 1m, 5m, 1h
    limit: int = Field(default=1000, ge=1, le=10000)
    
    # 过滤条件
    filters: Dict[str, Any] = Field(default_factory=dict)


class MetricPoint(BaseModel):
    """指标数据点"""
    timestamp: datetime
    value: float
    device_id: Optional[int] = None
    device_name: Optional[str] = None
    field: Optional[str] = None
    tags: Dict[str, str] = Field(default_factory=dict)


class MetricResponse(BaseModel):
    """指标响应Schema"""
    metric_type: str
    device_id: Optional[int] = None
    device_name: Optional[str] = None
    field: str
    aggregation: Optional[str] = None
    interval: Optional[str] = None
    data: List[MetricPoint]
    total: int


class MetricSeries(BaseModel):
    """指标序列"""
    name: str
    tags: Dict[str, str]
    columns: List[str]
    values: List[List[Any]]


class RealtimeMetric(BaseModel):
    """实时指标"""
    device_id: int
    device_name: str
    metric_type: str
    timestamp: datetime
    values: Dict[str, float]


class DeviceStatusMetric(BaseModel):
    """设备状态指标"""
    device_id: int
    device_name: str
    ip_address: str
    status: str
    last_seen: Optional[datetime] = None
    response_time_ms: Optional[float] = None
    packet_loss: Optional[float] = None


class DashboardStats(BaseModel):
    """Dashboard统计"""
    total_devices: int
    online_devices: int
    offline_devices: int
    total_alerts_firing: int
    snmp_metrics_count: int
    gnmi_metrics_count: int
    recent_alerts: List[Dict[str, Any]]
