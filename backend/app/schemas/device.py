"""
设备相关 Pydantic Schemas
"""
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List, Dict, Any
from datetime import datetime


class DatacenterBase(BaseModel):
    """机房基础Schema"""
    code: Optional[str] = Field(None, max_length=50, description="机房编号")
    name: str = Field(..., min_length=1, max_length=100, description="机房名称")
    location: Optional[str] = Field(None, max_length=255, description="地理位置")
    address: Optional[str] = Field(None, max_length=500, description="详细地址")
    contact_person: Optional[str] = Field(None, max_length=100, description="联系人")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系电话")
    contact_email: Optional[str] = Field(None, max_length=100, description="联系邮箱")
    network_owner: Optional[str] = Field(None, max_length=100, description="网络负责人")
    network_owner_email: Optional[str] = Field(None, max_length=255, description="网络负责人邮箱，多个用逗号分隔")
    robot_mention: Optional[str] = Field(None, max_length=255, description="历史兼容字段：原机器人艾特标识，页面不再使用")
    build_date: Optional[datetime] = Field(None, description="建设时间")
    description: Optional[str] = None
    is_active: bool = True


class DatacenterCreate(DatacenterBase):
    pass


class DatacenterUpdate(BaseModel):
    code: Optional[str] = Field(None, max_length=50)
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    location: Optional[str] = None
    address: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    network_owner: Optional[str] = None
    network_owner_email: Optional[str] = None
    robot_mention: Optional[str] = None
    build_date: Optional[datetime] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DatacenterResponse(DatacenterBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DeviceTypeBase(BaseModel):
    """设备类型基础Schema"""
    name: str = Field(..., min_length=1, max_length=50, description="类型名称")
    display_name: Optional[str] = Field(None, max_length=100, description="显示名称")
    icon: Optional[str] = Field(None, max_length=50, description="图标")
    description: Optional[str] = None
    is_active: bool = True


class DeviceTypeCreate(DeviceTypeBase):
    pass


class DeviceTypeUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    display_name: Optional[str] = None
    icon: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DeviceTypeResponse(DeviceTypeBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DeviceRoleBase(BaseModel):
    """设备角色基础Schema"""
    name: str = Field(..., min_length=1, max_length=50, description="角色名称")
    display_name: Optional[str] = Field(None, max_length=100, description="显示名称")
    description: Optional[str] = None
    is_active: bool = True


class DeviceRoleCreate(DeviceRoleBase):
    pass


class DeviceRoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    display_name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DeviceRoleResponse(DeviceRoleBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DeviceVendorBase(BaseModel):
    """设备厂商基础Schema"""
    name: str = Field(..., min_length=1, max_length=50, description="厂商名称")
    display_name: Optional[str] = Field(None, max_length=100, description="显示名称")
    description: Optional[str] = None
    is_active: bool = True


class DeviceVendorCreate(DeviceVendorBase):
    pass


class DeviceVendorUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    display_name: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class DeviceVendorResponse(DeviceVendorBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class SNMPConfig(BaseModel):
    """SNMP配置"""
    version: str = Field(default="v2c", pattern="^(v1|v2c|v3)$")
    port: int = Field(default=161, ge=1, le=65535)
    community: Optional[str] = Field(default="para@2026")
    username: Optional[str] = None
    auth_protocol: Optional[str] = None
    auth_password: Optional[str] = None
    priv_protocol: Optional[str] = None
    priv_password: Optional[str] = None
    security_level: Optional[str] = None


class GNMIConfig(BaseModel):
    """gNMI配置"""
    enabled: bool = False
    port: int = Field(default=57400, ge=1, le=65535)
    username: Optional[str] = None
    password: Optional[str] = None
    tls_enabled: bool = False
    tls_cert: Optional[str] = None
    skip_verify: bool = True
    subscriptions: List[Dict[str, Any]] = Field(default_factory=list)


class SSHConfig(BaseModel):
    """SSH配置"""
    port: int = Field(default=22, ge=1, le=65535)
    username: Optional[str] = None
    password: Optional[str] = None
    key: Optional[str] = None


class DeviceBase(BaseModel):
    """设备基础Schema"""
    name: str = Field(..., min_length=1, max_length=100)
    ip_address: str = Field(..., min_length=1, max_length=45)
    hostname: Optional[str] = Field(None, max_length=255)
    device_type: str = Field(default="unknown", max_length=50)
    device_role: Optional[str] = Field(None, max_length=50)
    vendor: Optional[str] = Field(None, max_length=50)
    model: Optional[str] = Field(None, max_length=100)
    serial_number: Optional[str] = Field(None, max_length=100)
    status: str = Field(default="in_stock", max_length=20)
    location: Optional[str] = Field(None, max_length=255)
    latitude: Optional[str] = Field(None, max_length=20)
    longitude: Optional[str] = Field(None, max_length=20)
    
    # 机房信息
    datacenter: Optional[str] = Field(None, max_length=100, description="机房名称(兼容旧字段)")
    datacenter_id: Optional[int] = Field(None, description="机房ID")
    rack: Optional[str] = Field(None, max_length=50, description="机柜位置")
    device_type_id: Optional[int] = Field(None, description="设备类型ID")
    
    # 责任人信息
    network_owner: Optional[str] = Field(None, max_length=100, description="网络责任人")
    ops_owner: Optional[str] = Field(None, max_length=100, description="机房运维责任人")
    contact_phone: Optional[str] = Field(None, max_length=20, description="联系电话")
    contact_email: Optional[str] = Field(None, max_length=100, description="联系邮箱")
    business_type: Optional[str] = Field(None, max_length=100, description="业务类型")
    is_monitored: bool = Field(default=False, description="是否加入监控")
    monitor_source: str = Field(default="snmp", pattern="^(snmp|asternos_exporter)$", description="监控方式")
    prometheus_url: Optional[str] = Field(None, max_length=255, description="AsterNOS Exporter地址")
    prometheus_job: Optional[str] = Field(None, max_length=100, description="旧版采集Job，已不在前端使用")
    prometheus_instance: Optional[str] = Field(None, max_length=255, description="旧版采集实例标识，已不在前端使用")
    
    description: Optional[str] = None
    group_id: Optional[int] = None
    tags: List[str] = Field(default_factory=list)
    custom_fields: Dict[str, Any] = Field(default_factory=dict)


class DeviceCreate(DeviceBase):
    """创建设备Schema"""
    snmp: SNMPConfig = Field(default_factory=SNMPConfig)
    gnmi: GNMIConfig = Field(default_factory=GNMIConfig)
    ssh: SSHConfig = Field(default_factory=SSHConfig)


class DeviceUpdate(BaseModel):
    """更新设备Schema"""
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    ip_address: Optional[str] = Field(None, min_length=1, max_length=45)
    hostname: Optional[str] = Field(None, max_length=255)
    device_type: Optional[str] = Field(None, max_length=50)
    device_role: Optional[str] = Field(None, max_length=50)
    vendor: Optional[str] = Field(None, max_length=50)
    model: Optional[str] = Field(None, max_length=100)
    serial_number: Optional[str] = Field(None, max_length=100)
    status: Optional[str] = Field(None, max_length=20)
    location: Optional[str] = Field(None, max_length=255)
    latitude: Optional[str] = None
    longitude: Optional[str] = None
    datacenter_id: Optional[int] = None
    rack: Optional[str] = None
    device_type_id: Optional[int] = None
    network_owner: Optional[str] = None
    ops_owner: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    business_type: Optional[str] = None
    is_monitored: Optional[bool] = None
    monitor_source: Optional[str] = Field(None, pattern="^(snmp|asternos_exporter)$")
    prometheus_url: Optional[str] = Field(None, max_length=255, description="AsterNOS Exporter地址")
    prometheus_job: Optional[str] = None
    prometheus_instance: Optional[str] = None
    description: Optional[str] = None
    group_id: Optional[int] = None
    tags: Optional[List[str]] = None
    snmp: Optional[SNMPConfig] = None
    gnmi: Optional[GNMIConfig] = None
    ssh: Optional[SSHConfig] = None
    custom_fields: Optional[Dict[str, Any]] = None


class DeviceResponse(DeviceBase):
    """设备响应Schema"""
    id: int
    status: str
    last_seen: Optional[datetime] = None
    snmp_version: Optional[str] = None
    gnmi_enabled: bool
    ssh_port: int = 22
    ssh_username: Optional[str] = None
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class DeviceListResponse(BaseModel):
    """设备列表响应"""
    total: int
    items: List[DeviceResponse]


class DeviceGroupBase(BaseModel):
    """设备分组基础Schema"""
    name: str = Field(..., min_length=1, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[int] = None


class DeviceGroupCreate(DeviceGroupBase):
    pass


class DeviceGroupUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    description: Optional[str] = None
    parent_id: Optional[int] = None


class DeviceGroupResponse(DeviceGroupBase):
    """设备分组响应Schema"""
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    device_count: int = 0

    class Config:
        from_attributes = True


class DeviceStatusUpdate(BaseModel):
    """设备状态更新"""
    status: str = Field(..., pattern="^(active|inactive|in_stock|deployed)$")
    last_seen: Optional[datetime] = None
