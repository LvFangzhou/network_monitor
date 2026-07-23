"""
资源管理相关 Schema
"""
from datetime import datetime
from typing import Optional, List

from pydantic import BaseModel, Field


class VendorBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    vendor_type: str = Field(default="other", max_length=30)
    contact_person: Optional[str] = Field(None, max_length=100)
    contact_phone: Optional[str] = Field(None, max_length=20)
    contact_email: Optional[str] = Field(None, max_length=100)
    service_scope: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    is_active: bool = True


class VendorCreate(VendorBase):
    pass


class VendorUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    vendor_type: Optional[str] = None
    contact_person: Optional[str] = None
    contact_phone: Optional[str] = None
    contact_email: Optional[str] = None
    service_scope: Optional[str] = None
    description: Optional[str] = None
    is_active: Optional[bool] = None


class VendorResponse(VendorBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CustomerBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    legal_name: Optional[str] = Field(None, max_length=150)
    customer_sites: List[dict] = Field(default_factory=list)
    private_networks: Optional[str] = None
    public_addresses: Optional[str] = None
    bandwidth_description: Optional[str] = Field(None, max_length=100)
    dedicated_lines: Optional[str] = None
    service_manager_name: Optional[str] = Field(None, max_length=100)
    service_manager_contact: Optional[str] = Field(None, max_length=255)
    sales_name: Optional[str] = Field(None, max_length=100)
    sales_contact: Optional[str] = Field(None, max_length=255)
    contact_info: Optional[str] = Field(None, max_length=255)
    contact_group: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    is_active: bool = True


class CustomerCreate(CustomerBase):
    pass


class CustomerUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    legal_name: Optional[str] = Field(None, max_length=150)
    customer_sites: Optional[List[dict]] = None
    private_networks: Optional[str] = None
    public_addresses: Optional[str] = None
    bandwidth_description: Optional[str] = Field(None, max_length=100)
    dedicated_lines: Optional[str] = None
    service_manager_name: Optional[str] = Field(None, max_length=100)
    service_manager_contact: Optional[str] = Field(None, max_length=255)
    sales_name: Optional[str] = Field(None, max_length=100)
    sales_contact: Optional[str] = Field(None, max_length=255)
    contact_info: Optional[str] = Field(None, max_length=255)
    contact_group: Optional[str] = Field(None, max_length=255)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class CustomerResponse(CustomerBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class CircuitBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    operator_name: Optional[str] = Field(None, max_length=50)
    line_type: str = Field(default="internet", max_length=30)
    access_mode: str = Field(default="single", max_length=20)
    ip_address: Optional[str] = Field(None, max_length=45)
    bandwidth_mbps: int = Field(default=0, ge=0)
    physical_port_rate_gbps: int = Field(default=0, ge=0)
    primary_port_rate: Optional[str] = Field(None, max_length=20)
    secondary_port_rate: Optional[str] = Field(None, max_length=20)
    dual_link_mode: Optional[str] = Field(None, max_length=20)
    is_redundant: bool = False
    redundancy_note: Optional[str] = Field(None, max_length=255)
    status: str = Field(default="active", max_length=20)
    datacenter_id: Optional[int] = None
    vendor_id: Optional[int] = None
    customer_id: Optional[int] = None
    primary_device_id: Optional[int] = None
    primary_port_name: Optional[str] = Field(None, max_length=100)
    secondary_device_id: Optional[int] = None
    secondary_port_name: Optional[str] = Field(None, max_length=100)
    aggregation_monitor_device_id: Optional[int] = None
    aggregation_interface_name: Optional[str] = Field(None, max_length=100)
    primary_local_interconnect_ip: Optional[str] = Field(None, max_length=100)
    primary_remote_interconnect_ip: Optional[str] = Field(None, max_length=100)
    secondary_local_interconnect_ip: Optional[str] = Field(None, max_length=100)
    secondary_remote_interconnect_ip: Optional[str] = Field(None, max_length=100)
    primary_interconnect_type: Optional[str] = Field(None, max_length=20)
    secondary_interconnect_type: Optional[str] = Field(None, max_length=20)
    primary_routing_mode: Optional[str] = Field(None, max_length=50)
    primary_bfd_mode: str = Field(default="none", max_length=20)
    secondary_routing_mode: Optional[str] = Field(None, max_length=50)
    secondary_bfd_mode: str = Field(default="none", max_length=20)
    primary_interconnect_ip: Optional[str] = Field(None, max_length=100)
    secondary_interconnect_ip: Optional[str] = Field(None, max_length=100)
    primary_vlan_id: Optional[int] = Field(None, ge=1, le=4094)
    secondary_vlan_id: Optional[int] = Field(None, ge=1, le=4094)
    interconnect_address: Optional[str] = Field(None, max_length=100)
    local_interconnect_address: Optional[str] = Field(None, max_length=100)
    remote_interconnect_address: Optional[str] = Field(None, max_length=100)
    interconnect_type: Optional[str] = Field(None, max_length=20)
    routing_mode: Optional[str] = Field(None, max_length=50)
    bfd_mode: str = Field(default="none", max_length=20)
    bfd_enabled: bool = False
    routed_cidrs: Optional[str] = None
    routed_networks: List[dict] = Field(default_factory=list)
    local_routed_cidrs: Optional[str] = None
    local_routed_networks: List[dict] = Field(default_factory=list)
    remote_routed_cidrs: Optional[str] = None
    remote_routed_networks: List[dict] = Field(default_factory=list)
    address_segments: List[dict] = Field(default_factory=list)
    description: Optional[str] = None


class CircuitCreate(CircuitBase):
    pass


class CircuitUpdate(BaseModel):
    name: Optional[str] = None
    operator_name: Optional[str] = None
    line_type: Optional[str] = None
    access_mode: Optional[str] = None
    ip_address: Optional[str] = None
    bandwidth_mbps: Optional[int] = Field(None, ge=0)
    physical_port_rate_gbps: Optional[int] = Field(None, ge=0)
    primary_port_rate: Optional[str] = None
    secondary_port_rate: Optional[str] = None
    dual_link_mode: Optional[str] = None
    is_redundant: Optional[bool] = None
    redundancy_note: Optional[str] = None
    status: Optional[str] = None
    datacenter_id: Optional[int] = None
    vendor_id: Optional[int] = None
    customer_id: Optional[int] = None
    primary_device_id: Optional[int] = None
    primary_port_name: Optional[str] = None
    secondary_device_id: Optional[int] = None
    secondary_port_name: Optional[str] = None
    aggregation_monitor_device_id: Optional[int] = None
    aggregation_interface_name: Optional[str] = None
    primary_local_interconnect_ip: Optional[str] = None
    primary_remote_interconnect_ip: Optional[str] = None
    secondary_local_interconnect_ip: Optional[str] = None
    secondary_remote_interconnect_ip: Optional[str] = None
    primary_interconnect_type: Optional[str] = None
    secondary_interconnect_type: Optional[str] = None
    primary_routing_mode: Optional[str] = None
    primary_bfd_mode: Optional[str] = None
    secondary_routing_mode: Optional[str] = None
    secondary_bfd_mode: Optional[str] = None
    primary_interconnect_ip: Optional[str] = None
    secondary_interconnect_ip: Optional[str] = None
    primary_vlan_id: Optional[int] = Field(None, ge=1, le=4094)
    secondary_vlan_id: Optional[int] = Field(None, ge=1, le=4094)
    interconnect_address: Optional[str] = None
    local_interconnect_address: Optional[str] = None
    remote_interconnect_address: Optional[str] = None
    interconnect_type: Optional[str] = None
    routing_mode: Optional[str] = None
    bfd_mode: Optional[str] = None
    bfd_enabled: Optional[bool] = None
    routed_cidrs: Optional[str] = None
    routed_networks: Optional[List[dict]] = None
    local_routed_cidrs: Optional[str] = None
    local_routed_networks: Optional[List[dict]] = None
    remote_routed_cidrs: Optional[str] = None
    remote_routed_networks: Optional[List[dict]] = None
    address_segments: Optional[List[dict]] = None
    description: Optional[str] = None


class CircuitResponse(CircuitBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class IPAddressRecordBase(BaseModel):
    ip_address: str = Field(..., min_length=1, max_length=45)
    prefix_length: int = Field(default=32, ge=0, le=128)
    status: str = Field(default="allocated", max_length=20)
    usage_type: str = Field(default="business", max_length=50)
    datacenter_id: Optional[int] = None
    circuit_id: Optional[int] = None
    description: Optional[str] = None


class IPAddressRecordCreate(IPAddressRecordBase):
    pass


class IPAddressRecordUpdate(BaseModel):
    ip_address: Optional[str] = None
    prefix_length: Optional[int] = Field(None, ge=0, le=128)
    status: Optional[str] = None
    usage_type: Optional[str] = None
    datacenter_id: Optional[int] = None
    circuit_id: Optional[int] = None
    description: Optional[str] = None


class IPAddressRecordResponse(IPAddressRecordBase):
    id: int
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class QualityProbeTargetBase(BaseModel):
    name: str = Field(..., min_length=1, max_length=100)
    target: str = Field(..., min_length=1, max_length=255)
    datacenter_id: Optional[int] = None
    circuit_id: Optional[int] = None
    device_id: Optional[int] = None
    probe_source: str = Field(default="server_icmp", pattern="^(server_icmp|device_nqa_snmp)$")
    nqa_admin_name: Optional[str] = Field(None, max_length=32)
    nqa_operation_tag: Optional[str] = Field(None, max_length=32)
    operator_name: Optional[str] = Field(None, max_length=50)
    interval_seconds: int = Field(default=60, ge=1, le=3600)
    packet_count: int = Field(default=5, ge=1, le=20)
    timeout_ms: int = Field(default=1000, ge=200, le=10000)
    latency_threshold_ms: int = Field(default=100, ge=1, le=10000)
    loss_threshold_percent: int = Field(default=1, ge=0, le=100)
    jitter_threshold_ms: int = Field(default=30, ge=0, le=10000)
    description: Optional[str] = None
    is_active: bool = True


class QualityProbeTargetCreate(QualityProbeTargetBase):
    pass


class QualityProbeTargetUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=100)
    target: Optional[str] = Field(None, min_length=1, max_length=255)
    datacenter_id: Optional[int] = None
    circuit_id: Optional[int] = None
    device_id: Optional[int] = None
    probe_source: Optional[str] = Field(None, pattern="^(server_icmp|device_nqa_snmp)$")
    nqa_admin_name: Optional[str] = Field(None, max_length=32)
    nqa_operation_tag: Optional[str] = Field(None, max_length=32)
    operator_name: Optional[str] = Field(None, max_length=50)
    interval_seconds: Optional[int] = Field(None, ge=1, le=3600)
    packet_count: Optional[int] = Field(None, ge=1, le=20)
    timeout_ms: Optional[int] = Field(None, ge=200, le=10000)
    latency_threshold_ms: Optional[int] = Field(None, ge=1, le=10000)
    loss_threshold_percent: Optional[int] = Field(None, ge=0, le=100)
    jitter_threshold_ms: Optional[int] = Field(None, ge=0, le=10000)
    description: Optional[str] = None
    is_active: Optional[bool] = None


class QualityProbeTargetResponse(QualityProbeTargetBase):
    id: int
    datacenter_name: Optional[str] = None
    circuit_name: Optional[str] = None
    circuit_bandwidth_mbps: Optional[int] = None
    circuit_device_name: Optional[str] = None
    circuit_device_ip: Optional[str] = None
    circuit_port_name: Optional[str] = None
    device_name: Optional[str] = None
    device_ip: Optional[str] = None
    last_probe_at: Optional[datetime] = None
    last_success: Optional[bool] = None
    last_avg_latency_ms: Optional[float] = None
    last_packet_loss_percent: Optional[float] = None
    last_availability_percent: Optional[float] = None
    last_jitter_ms: Optional[float] = None
    last_error: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True
