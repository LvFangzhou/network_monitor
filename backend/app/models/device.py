"""
设备数据模型 - CMDB资产管理
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Table, JSON, Boolean
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# 设备与标签关联表
device_tags = Table(
    'device_tags',
    Base.metadata,
    Column('device_id', Integer, ForeignKey('devices.id'), primary_key=True),
    Column('tag_id', Integer, ForeignKey('tags.id'), primary_key=True)
)


class Datacenter(Base):
    """机房模型"""
    __tablename__ = "datacenters"
    
    id = Column(Integer, primary_key=True, index=True)
    code = Column(String(50), unique=True)  # 机房编号
    name = Column(String(100), nullable=False, unique=True)  # 机房名称
    location = Column(String(255))  # 地理位置
    address = Column(String(500))  # 详细地址
    contact_person = Column(String(100))  # 联系人
    contact_phone = Column(String(20))  # 联系电话
    contact_email = Column(String(100))  # 联系邮箱
    network_owner = Column(String(100))  # 网络负责人
    network_owner_email = Column(String(255))  # 网络负责人邮箱，多个用逗号分隔
    robot_mention = Column(String(255))  # 历史兼容字段：原机器人艾特标识，页面不再使用
    build_date = Column(DateTime(timezone=True))  # 建设时间
    description = Column(Text)  # 描述
    is_active = Column(Boolean, default=True)  # 是否启用
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    devices = relationship("Device", back_populates="datacenter_ref")
    
    def to_dict(self):
        return {
            "id": self.id,
            "code": self.code,
            "name": self.name,
            "location": self.location,
            "address": self.address,
            "contact_person": self.contact_person,
            "contact_phone": self.contact_phone,
            "contact_email": self.contact_email,
            "network_owner": self.network_owner,
            "network_owner_email": self.network_owner_email,
            "robot_mention": self.robot_mention,
            "build_date": self.build_date.isoformat() if self.build_date else None,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeviceType(Base):
    """设备类型模型"""
    __tablename__ = "device_types"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, unique=True)  # 类型名称: Firewall, Switch, Router, Console
    display_name = Column(String(100))  # 显示名称: 防火墙, 交换机, 路由器, 控制台
    icon = Column(String(50))  # 图标
    description = Column(Text)  # 描述
    is_active = Column(Boolean, default=True)  # 是否启用
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    devices = relationship("Device", back_populates="device_type_ref")
    
    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "icon": self.icon,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeviceRole(Base):
    """设备角色字典"""
    __tablename__ = "device_roles"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, unique=True)
    display_name = Column(String(100))
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeviceVendor(Base):
    """设备厂商字典"""
    __tablename__ = "device_vendors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, unique=True)
    display_name = Column(String(100))
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "display_name": self.display_name,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class DeviceGroup(Base):
    """设备分组模型"""
    __tablename__ = "device_groups"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    parent_id = Column(Integer, ForeignKey('device_groups.id'), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    devices = relationship("Device", back_populates="group")
    children = relationship("DeviceGroup", backref="parent", remote_side=[id])


class Tag(Base):
    """标签模型"""
    __tablename__ = "tags"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, unique=True)
    color = Column(String(7), default="#1890ff")  # 标签颜色
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    devices = relationship("Device", secondary=device_tags, back_populates="tags")


class Device(Base):
    """设备模型 - CMDB核心资产"""
    __tablename__ = "devices"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    ip_address = Column(String(45), nullable=False, index=True)  # IPv6支持
    hostname = Column(String(255))
    
    # 设备类型: router, switch, firewall, server, etc.
    device_type = Column(String(50), default="unknown")  # 保留作为兼容
    device_role = Column(String(50))
    vendor = Column(String(50))
    model = Column(String(100))
    serial_number = Column(String(100))
    location = Column(String(255))
    
    # 设备运行状态: active(上线), inactive(离线), in_stock(库存), deployed(上架)
    status = Column(String(20), default="in_stock")
    last_seen = Column(DateTime(timezone=True))
    
    # SNMP配置
    snmp_version = Column(String(10), default="v2c")  # v1, v2c, v3
    snmp_port = Column(Integer, default=161)
    snmp_community = Column(String(100), default="para@2026")  # v1/v2c，导入时使用后台默认值
    snmp_username = Column(String(100))   # v3
    snmp_auth_protocol = Column(String(20))  # MD5, SHA
    snmp_auth_password = Column(String(100))
    snmp_priv_protocol = Column(String(20))  # DES, AES
    snmp_priv_password = Column(String(100))
    snmp_security_level = Column(String(20))  # noAuthNoPriv, authNoPriv, authPriv
    
    # gNMI配置
    gnmi_enabled = Column(Integer, default=0)  # 0=False, 1=True
    gnmi_port = Column(Integer, default=57400)
    gnmi_username = Column(String(100))
    gnmi_password = Column(String(100))
    gnmi_tls_enabled = Column(Integer, default=0)
    gnmi_tls_cert = Column(Text)
    gnmi_skip_verify = Column(Integer, default=1)
    
    # 订阅路径 (JSON格式存储)
    gnmi_subscriptions = Column(JSON, default=list)
    
    # 地理位置
    latitude = Column(String(20))
    longitude = Column(String(20))
    rack = Column(String(50))
    
    # 机房信息 - 外键关联
    datacenter_id = Column(Integer, ForeignKey('datacenters.id'))
    datacenter_ref = relationship("Datacenter", back_populates="devices")
    
    # 设备类型 - 外键关联
    device_type_id = Column(Integer, ForeignKey('device_types.id'))
    device_type_ref = relationship("DeviceType", back_populates="devices")
    
    # 责任人信息
    network_owner = Column(String(100))  # 网络责任人
    ops_owner = Column(String(100))  # 机房运维责任人
    contact_phone = Column(String(20))  # 联系电话
    contact_email = Column(String(100))  # 联系邮箱
    
    # 业务信息
    business_type = Column(String(100))  # 业务类型
    
    # SSH配置
    ssh_port = Column(Integer, default=22)
    ssh_username = Column(String(100))
    ssh_password = Column(String(100))
    ssh_key = Column(Text)
    
    # 描述信息
    description = Column(Text)

    # 监控配置
    is_monitored = Column(Boolean, default=False)
    monitor_source = Column(String(50), default="snmp")  # snmp / asternos_exporter
    prometheus_url = Column(String(255))
    prometheus_job = Column(String(100))
    prometheus_instance = Column(String(255))
    
    # 分组关联
    group_id = Column(Integer, ForeignKey('device_groups.id'))
    group = relationship("DeviceGroup", back_populates="devices")
    
    # 标签关联
    tags = relationship("Tag", secondary=device_tags, back_populates="devices")
    
    # 自定义字段
    custom_fields = Column(JSON, default=dict)
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    alert_histories = relationship("AlertHistory", back_populates="device")
    
    def __repr__(self):
        return f"<Device {self.name} ({self.ip_address})>"

    @property
    def normalized_status(self) -> str:
        """兼容旧状态值，统一返回前端使用的新状态枚举。"""
        status_map = {
            "online": "active",
            "offline": "inactive",
        }
        return status_map.get(self.status, self.status)
    
    def to_dict(self):
        """转换为字典"""
        vendor_value = (self.vendor or "").strip().lower()
        monitor_source = (
            "asternos_exporter"
            if any(marker in vendor_value for marker in ["asternos", "asterfusion", "asteros", "星融元"])
            else "snmp"
        )
        return {
            "id": self.id,
            "name": self.name,
            "ip_address": self.ip_address,
            "hostname": self.hostname,
            "device_type": self.device_type,
            "device_role": self.device_role,
            "device_type_id": self.device_type_id,
            "vendor": self.vendor,
            "model": self.model,
            "serial_number": self.serial_number,
            "location": self.location,
            "latitude": self.latitude,
            "longitude": self.longitude,
            "rack": self.rack,
            "status": self.normalized_status,
            "last_seen": self.last_seen.isoformat() if self.last_seen else None,
            "snmp_version": self.snmp_version,
            "gnmi_enabled": bool(self.gnmi_enabled),
            "datacenter_id": self.datacenter_id,
            "datacenter": self.datacenter_ref.to_dict() if self.datacenter_ref else None,
            "network_owner": self.network_owner,
            "ops_owner": self.ops_owner,
            "contact_phone": self.contact_phone,
            "contact_email": self.contact_email,
            "business_type": self.business_type,
            "snmp": {
                "version": self.snmp_version,
                "port": self.snmp_port,
                "community": self.snmp_community,
                "username": self.snmp_username,
                "auth_protocol": self.snmp_auth_protocol,
                "auth_password": self.snmp_auth_password,
                "priv_protocol": self.snmp_priv_protocol,
                "priv_password": self.snmp_priv_password,
                "security_level": self.snmp_security_level,
            },
            "gnmi": {
                "enabled": bool(self.gnmi_enabled),
                "port": self.gnmi_port,
                "username": self.gnmi_username,
                "password": self.gnmi_password,
                "tls_enabled": bool(self.gnmi_tls_enabled),
                "tls_cert": self.gnmi_tls_cert,
                "skip_verify": bool(self.gnmi_skip_verify),
                "subscriptions": self.gnmi_subscriptions or [],
            },
            "ssh": {
                "port": self.ssh_port,
                "username": self.ssh_username,
                "password": self.ssh_password,
                "key": self.ssh_key,
            },
            "ssh_port": self.ssh_port,
            "ssh_username": self.ssh_username,
            "description": self.description,
            "is_monitored": bool(self.is_monitored),
            "monitor_source": monitor_source,
            "prometheus_url": f"http://{self.ip_address}:8101" if monitor_source == "asternos_exporter" else None,
            "prometheus_job": None,
            "prometheus_instance": None,
            "custom_fields": self.custom_fields or {},
            "group_id": self.group_id,
            "tags": [tag.name for tag in self.tags] if self.tags else [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
