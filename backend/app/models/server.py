"""服务器资产、连接证据和受控网络变更模型。"""
from sqlalchemy import Boolean, Column, DateTime, Float, ForeignKey, Integer, String, Text, JSON, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class ServerAsset(Base):
    __tablename__ = "server_assets"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(150), nullable=False, index=True)
    management_ip = Column(String(45), index=True)
    serial_number = Column(String(100), index=True)
    vendor = Column(String(80), index=True)
    model = Column(String(120), index=True)
    asset_tag = Column(String(100), index=True)
    status = Column(String(30), default="in_stock", nullable=False, index=True)
    datacenter_id = Column(Integer, ForeignKey("datacenters.id"), index=True)
    rack = Column(String(50))
    rack_unit = Column(String(30))
    operating_system = Column(String(120))
    cpu_summary = Column(String(255))
    memory_gb = Column(Float)
    storage_summary = Column(Text)
    gpu_summary = Column(Text)
    bmc_type = Column(String(50))
    bmc_address = Column(String(45))
    redfish_endpoint = Column(String(255))
    agent_status = Column(String(30), default="unknown")
    agent_last_seen_at = Column(DateTime(timezone=True))
    owner = Column(String(100))
    business_system = Column(String(150))
    description = Column(Text)
    extra_data = Column(JSON, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    datacenter_ref = relationship("Datacenter")
    components = relationship("ServerComponent", back_populates="server", cascade="all, delete-orphan")
    nics = relationship("ServerNIC", back_populates="server", cascade="all, delete-orphan")


class ServerComponent(Base):
    __tablename__ = "server_components"

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("server_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    component_type = Column(String(30), nullable=False, index=True)  # cpu/memory/disk/gpu/fan/power/bmc
    name = Column(String(150), nullable=False)
    vendor = Column(String(80))
    model = Column(String(120))
    serial_number = Column(String(100))
    health = Column(String(30), default="unknown", index=True)
    properties = Column(JSON, default=dict)
    source = Column(String(30), default="manual")
    last_discovered_at = Column(DateTime(timezone=True))
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    server = relationship("ServerAsset", back_populates="components")


class ServerNIC(Base):
    __tablename__ = "server_nics"
    __table_args__ = (UniqueConstraint("server_id", "mac_address", name="uq_server_nic_mac"),)

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("server_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    name = Column(String(100), nullable=False)
    mac_address = Column(String(32), nullable=False, index=True)
    pci_address = Column(String(40))
    vendor = Column(String(80))
    model = Column(String(120))
    speed_mbps = Column(Integer)
    bond_name = Column(String(100))
    network_type = Column(String(30), default="business", index=True)  # business/management/parameter/storage/roce
    mtu = Column(Integer)
    status = Column(String(30), default="unknown")
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    server = relationship("ServerAsset", back_populates="nics")
    ip_addresses = relationship("ServerIPAddress", back_populates="nic", cascade="all, delete-orphan")
    connections = relationship("ServerNetworkConnection", back_populates="nic", cascade="all, delete-orphan")


class ServerIPAddress(Base):
    __tablename__ = "server_ip_addresses"
    __table_args__ = (UniqueConstraint("nic_id", "address", name="uq_server_nic_ip"),)

    id = Column(Integer, primary_key=True)
    nic_id = Column(Integer, ForeignKey("server_nics.id", ondelete="CASCADE"), nullable=False, index=True)
    address = Column(String(45), nullable=False, index=True)
    prefix_length = Column(Integer, default=32)
    vlan_id = Column(Integer)
    network_type = Column(String(30), default="business", index=True)
    is_primary = Column(Boolean, default=False)
    source = Column(String(30), default="manual")
    last_discovered_at = Column(DateTime(timezone=True))

    nic = relationship("ServerNIC", back_populates="ip_addresses")


class ServerNetworkConnection(Base):
    __tablename__ = "server_network_connections"
    __table_args__ = (
        UniqueConstraint("nic_id", "switch_device_id", "switch_port", name="uq_server_nic_switch_port"),
    )

    id = Column(Integer, primary_key=True)
    server_id = Column(Integer, ForeignKey("server_assets.id", ondelete="CASCADE"), nullable=False, index=True)
    nic_id = Column(Integer, ForeignKey("server_nics.id", ondelete="CASCADE"), nullable=False, index=True)
    switch_device_id = Column(Integer, ForeignKey("devices.id"), index=True)
    switch_port = Column(String(128), nullable=False, index=True)
    state = Column(String(30), default="candidate", nullable=False, index=True)  # candidate/confirmed/rejected/stale
    confidence = Column(Float, default=0, nullable=False, index=True)
    confidence_level = Column(String(20), default="low")
    evidence = Column(JSON, default=list)
    conflict_reasons = Column(JSON, default=list)
    first_discovered_at = Column(DateTime(timezone=True), server_default=func.now())
    last_discovered_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)
    confirmed_by = Column(String(100))
    confirmed_at = Column(DateTime(timezone=True))
    confirmation_note = Column(Text)

    server = relationship("ServerAsset")
    nic = relationship("ServerNIC", back_populates="connections")
    switch_device = relationship("Device")
    changes = relationship("ServerPortChange", back_populates="connection")


class ServerPortChange(Base):
    __tablename__ = "server_port_changes"

    id = Column(Integer, primary_key=True)
    connection_id = Column(Integer, ForeignKey("server_network_connections.id"), nullable=False, index=True)
    status = Column(String(30), default="draft", nullable=False, index=True)
    requested_config = Column(JSON, default=dict)
    existing_config = Column(JSON, default=dict)
    config_diff = Column(JSON, default=list)
    precheck_result = Column(JSON, default=dict)
    validation_result = Column(JSON, default=dict)
    rollback_config = Column(JSON, default=dict)
    generated_commands = Column(JSON, default=list)
    rollback_commands = Column(JSON, default=list)
    requested_by = Column(String(100), nullable=False)
    requested_at = Column(DateTime(timezone=True), server_default=func.now())
    approved_by = Column(String(100))
    approved_at = Column(DateTime(timezone=True))
    approval_note = Column(Text)
    executed_by = Column(String(100))
    executed_at = Column(DateTime(timezone=True))
    completed_at = Column(DateTime(timezone=True))
    error_message = Column(Text)
    audit_events = Column(JSON, default=list)

    connection = relationship("ServerNetworkConnection", back_populates="changes")
