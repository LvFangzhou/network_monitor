"""
资源管理相关模型
"""
from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String, Text, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base


class Vendor(Base):
    """供应商模型"""
    __tablename__ = "vendors"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    vendor_type = Column(String(30), default="other")  # dedicated/internet/other
    contact_person = Column(String(100))
    contact_phone = Column(String(20))
    contact_email = Column(String(100))
    service_scope = Column(String(255))
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    circuits = relationship("Circuit", back_populates="vendor_ref")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "vendor_type": self.vendor_type,
            "contact_person": self.contact_person,
            "contact_phone": self.contact_phone,
            "contact_email": self.contact_email,
            "service_scope": self.service_scope,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Customer(Base):
    """客户模型"""
    __tablename__ = "customers"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    legal_name = Column(String(150))
    customer_sites = Column(JSON, default=list)
    private_networks = Column(Text)
    public_addresses = Column(Text)
    bandwidth_description = Column(String(100))
    dedicated_lines = Column(Text)
    service_manager_name = Column(String(100))
    service_manager_contact = Column(String(255))
    sales_name = Column(String(100))
    sales_contact = Column(String(255))
    contact_info = Column(String(255))
    contact_group = Column(String(255))
    description = Column(Text)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    audits = relationship("CustomerAudit", back_populates="customer_ref")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "legal_name": self.legal_name,
            "customer_sites": self.customer_sites or [],
            "private_networks": self.private_networks,
            "public_addresses": self.public_addresses,
            "bandwidth_description": self.bandwidth_description,
            "dedicated_lines": self.dedicated_lines,
            "service_manager_name": self.service_manager_name,
            "service_manager_contact": self.service_manager_contact,
            "sales_name": self.sales_name,
            "sales_contact": self.sales_contact,
            "contact_info": self.contact_info,
            "contact_group": self.contact_group,
            "description": self.description,
            "is_active": self.is_active,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class Circuit(Base):
    """运营商线路模型"""
    __tablename__ = "circuits"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    operator_name = Column(String(50), nullable=False)  # 联通/移动/电信/BGP
    line_type = Column(String(30), default="internet")  # internet/private_line
    access_mode = Column(String(20), default="single")  # single/dual
    ip_address = Column(String(45))
    bandwidth_mbps = Column(Integer, default=0)
    physical_port_rate_gbps = Column(Integer, default=0)
    primary_port_rate = Column(String(20))
    secondary_port_rate = Column(String(20))
    dual_link_mode = Column(String(20))
    is_redundant = Column(Boolean, default=False)
    redundancy_note = Column(String(255))
    status = Column(String(20), default="active")
    datacenter_id = Column(Integer, ForeignKey("datacenters.id"))
    vendor_id = Column(Integer, ForeignKey("vendors.id"), nullable=True)
    customer_id = Column(Integer, ForeignKey("customers.id"), nullable=True)
    primary_device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    primary_port_name = Column(String(100))
    secondary_device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    secondary_port_name = Column(String(100))
    aggregation_monitor_device_id = Column(Integer, ForeignKey("devices.id"), nullable=True)
    aggregation_interface_name = Column(String(100))
    primary_local_interconnect_ip = Column(String(100))
    primary_remote_interconnect_ip = Column(String(100))
    secondary_local_interconnect_ip = Column(String(100))
    secondary_remote_interconnect_ip = Column(String(100))
    primary_interconnect_type = Column(String(20))
    secondary_interconnect_type = Column(String(20))
    primary_routing_mode = Column(String(50))
    primary_bfd_mode = Column(String(20), default="none")
    secondary_routing_mode = Column(String(50))
    secondary_bfd_mode = Column(String(20), default="none")
    primary_interconnect_ip = Column(String(100))
    secondary_interconnect_ip = Column(String(100))
    primary_vlan_id = Column(Integer)
    secondary_vlan_id = Column(Integer)
    interconnect_address = Column(String(100))
    local_interconnect_address = Column(String(100))
    remote_interconnect_address = Column(String(100))
    interconnect_type = Column(String(20))
    routing_mode = Column(String(50))
    bfd_mode = Column(String(20), default="none")
    bfd_enabled = Column(Boolean, default=False)
    routed_cidrs = Column(Text)
    routed_networks = Column(JSON, default=list)
    local_routed_cidrs = Column(Text)
    local_routed_networks = Column(JSON, default=list)
    remote_routed_cidrs = Column(Text)
    remote_routed_networks = Column(JSON, default=list)
    address_segments = Column(JSON, default=list)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    datacenter_ref = relationship("Datacenter")
    vendor_ref = relationship("Vendor", back_populates="circuits")
    customer_ref = relationship("Customer")
    primary_device_ref = relationship("Device", foreign_keys=[primary_device_id])
    secondary_device_ref = relationship("Device", foreign_keys=[secondary_device_id])
    aggregation_monitor_device_ref = relationship("Device", foreign_keys=[aggregation_monitor_device_id])
    ip_addresses = relationship("IPAddressRecord", back_populates="circuit_ref")
    audits = relationship("CircuitAudit", back_populates="circuit_ref")

    def effective_bfd_mode(self):
        if self.bfd_mode and self.bfd_mode != "none":
            return self.bfd_mode
        return "bfd" if self.bfd_enabled else "none"

    def effective_primary_bfd_mode(self):
        return self.primary_bfd_mode or self.effective_bfd_mode()

    def effective_secondary_bfd_mode(self):
        return self.secondary_bfd_mode or self.effective_bfd_mode()

    def to_dict(self):
        segments = self.address_segments or []
        return {
            "id": self.id,
            "name": self.name,
            "operator_name": self.operator_name,
            "line_type": self.line_type,
            "access_mode": self.access_mode,
            "ip_address": self.ip_address,
            "bandwidth_mbps": self.bandwidth_mbps,
            "physical_port_rate_gbps": self.physical_port_rate_gbps,
            "primary_port_rate": self.primary_port_rate,
            "secondary_port_rate": self.secondary_port_rate,
            "dual_link_mode": self.dual_link_mode,
            "is_redundant": self.is_redundant,
            "redundancy_note": self.redundancy_note,
            "status": self.status,
            "datacenter_id": self.datacenter_id,
            "datacenter_name": self.datacenter_ref.name if self.datacenter_ref else None,
            "vendor_id": self.vendor_id,
            "vendor_name": self.vendor_ref.name if self.vendor_ref else None,
            "customer_id": self.customer_id,
            "customer_name": self.customer_ref.name if self.customer_ref else None,
            "primary_device_id": self.primary_device_id,
            "primary_device_name": self.primary_device_ref.name if self.primary_device_ref else None,
            "primary_device_ip": self.primary_device_ref.ip_address if self.primary_device_ref else None,
            "primary_port_name": self.primary_port_name,
            "secondary_device_id": self.secondary_device_id,
            "secondary_device_name": self.secondary_device_ref.name if self.secondary_device_ref else None,
            "secondary_device_ip": self.secondary_device_ref.ip_address if self.secondary_device_ref else None,
            "secondary_port_name": self.secondary_port_name,
            "aggregation_monitor_device_id": self.aggregation_monitor_device_id,
            "aggregation_monitor_device_name": self.aggregation_monitor_device_ref.name if self.aggregation_monitor_device_ref else None,
            "aggregation_monitor_device_ip": self.aggregation_monitor_device_ref.ip_address if self.aggregation_monitor_device_ref else None,
            "aggregation_interface_name": self.aggregation_interface_name,
            "primary_local_interconnect_ip": self.primary_local_interconnect_ip,
            "primary_remote_interconnect_ip": self.primary_remote_interconnect_ip,
            "secondary_local_interconnect_ip": self.secondary_local_interconnect_ip,
            "secondary_remote_interconnect_ip": self.secondary_remote_interconnect_ip,
            "primary_interconnect_type": self.primary_interconnect_type or self.interconnect_type,
            "secondary_interconnect_type": self.secondary_interconnect_type or self.interconnect_type,
            "primary_routing_mode": self.primary_routing_mode or self.routing_mode,
            "primary_bfd_mode": self.effective_primary_bfd_mode(),
            "secondary_routing_mode": self.secondary_routing_mode or self.routing_mode,
            "secondary_bfd_mode": self.effective_secondary_bfd_mode(),
            "primary_interconnect_ip": self.primary_interconnect_ip,
            "secondary_interconnect_ip": self.secondary_interconnect_ip,
            "primary_vlan_id": self.primary_vlan_id,
            "secondary_vlan_id": self.secondary_vlan_id,
            "interconnect_address": self.interconnect_address,
            "local_interconnect_address": self.local_interconnect_address,
            "remote_interconnect_address": self.remote_interconnect_address,
            "interconnect_type": self.interconnect_type,
            "routing_mode": self.routing_mode,
            "bfd_mode": self.effective_bfd_mode(),
            "bfd_enabled": self.bfd_enabled,
            "routed_cidrs": self.routed_cidrs,
            "routed_networks": self.routed_networks or [],
            "local_routed_cidrs": self.local_routed_cidrs,
            "local_routed_networks": self.local_routed_networks or [],
            "remote_routed_cidrs": self.remote_routed_cidrs,
            "remote_routed_networks": self.remote_routed_networks or [],
            "address_segments": segments,
            "segment_count": len(segments),
            "public_segment_count": len([segment for segment in segments if segment.get("is_public")]),
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }


class CircuitAudit(Base):
    """运营商线路审计模型"""
    __tablename__ = "circuit_audits"

    id = Column(Integer, primary_key=True, index=True)
    circuit_id = Column(Integer, ForeignKey("circuits.id", ondelete="SET NULL"), nullable=True, index=True)
    circuit_name = Column(String(100), nullable=False)
    action = Column(String(20), nullable=False)  # create/update/delete
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_username = Column(String(100), nullable=False)
    change_summary = Column(JSON, default=list)
    before_data = Column(JSON, nullable=True)
    after_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    circuit_ref = relationship("Circuit", back_populates="audits")

    def to_dict(self):
        return {
            "id": self.id,
            "circuit_id": self.circuit_id,
            "circuit_name": self.circuit_name,
            "action": self.action,
            "actor_user_id": self.actor_user_id,
            "actor_username": self.actor_username,
            "change_summary": self.change_summary or [],
            "before_data": self.before_data,
            "after_data": self.after_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class CustomerAudit(Base):
    """客户信息更改记录"""
    __tablename__ = "customer_audits"

    id = Column(Integer, primary_key=True, index=True)
    customer_id = Column(Integer, ForeignKey("customers.id", ondelete="SET NULL"), nullable=True, index=True)
    customer_name = Column(String(100), nullable=False)
    action = Column(String(20), nullable=False)  # create/update/delete
    actor_user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    actor_username = Column(String(100), nullable=False)
    change_summary = Column(JSON, default=list)
    before_data = Column(JSON, nullable=True)
    after_data = Column(JSON, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    customer_ref = relationship("Customer", back_populates="audits")

    def to_dict(self):
        return {
            "id": self.id,
            "customer_id": self.customer_id,
            "customer_name": self.customer_name,
            "action": self.action,
            "actor_user_id": self.actor_user_id,
            "actor_username": self.actor_username,
            "change_summary": self.change_summary or [],
            "before_data": self.before_data,
            "after_data": self.after_data,
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class IPAddressRecord(Base):
    """IPDB记录模型"""
    __tablename__ = "ip_address_records"

    id = Column(Integer, primary_key=True, index=True)
    ip_address = Column(String(45), nullable=False, unique=True)
    prefix_length = Column(Integer, default=32)
    status = Column(String(20), default="allocated")  # allocated/available/reserved
    usage_type = Column(String(50), default="business")
    datacenter_id = Column(Integer, ForeignKey("datacenters.id"), nullable=True)
    circuit_id = Column(Integer, ForeignKey("circuits.id"), nullable=True)
    description = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    datacenter_ref = relationship("Datacenter")
    circuit_ref = relationship("Circuit", back_populates="ip_addresses")

    def to_dict(self):
        return {
            "id": self.id,
            "ip_address": self.ip_address,
            "prefix_length": self.prefix_length,
            "status": self.status,
            "usage_type": self.usage_type,
            "datacenter_id": self.datacenter_id,
            "datacenter_name": self.datacenter_ref.name if self.datacenter_ref else None,
            "circuit_id": self.circuit_id,
            "circuit_name": self.circuit_ref.name if self.circuit_ref else None,
            "description": self.description,
            "created_at": self.created_at.isoformat() if self.created_at else None,
            "updated_at": self.updated_at.isoformat() if self.updated_at else None,
        }
