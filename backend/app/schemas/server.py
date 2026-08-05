"""服务器管理 API Schema。"""
import ipaddress
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, field_validator


class ServerAssetCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=150)
    management_ip: Optional[str] = None
    serial_number: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    asset_tag: Optional[str] = None
    status: str = "in_stock"
    datacenter_id: Optional[int] = None
    rack: Optional[str] = None
    rack_unit: Optional[str] = None
    operating_system: Optional[str] = None
    cpu_summary: Optional[str] = None
    memory_gb: Optional[float] = Field(None, ge=0)
    storage_summary: Optional[str] = None
    gpu_summary: Optional[str] = None
    bmc_type: Optional[str] = None
    bmc_address: Optional[str] = None
    redfish_endpoint: Optional[str] = None
    owner: Optional[str] = None
    business_system: Optional[str] = None
    description: Optional[str] = None
    extra_data: Dict[str, Any] = Field(default_factory=dict)

    @field_validator("management_ip", "bmc_address")
    @classmethod
    def validate_optional_ip(cls, value: Optional[str]):
        if value in (None, ""):
            return None
        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError as exc:
            raise ValueError("IP 地址格式不正确") from exc


class ServerAssetUpdate(BaseModel):
    name: Optional[str] = None
    management_ip: Optional[str] = None
    serial_number: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    asset_tag: Optional[str] = None
    status: Optional[str] = None
    datacenter_id: Optional[int] = None
    rack: Optional[str] = None
    rack_unit: Optional[str] = None
    operating_system: Optional[str] = None
    cpu_summary: Optional[str] = None
    memory_gb: Optional[float] = Field(None, ge=0)
    storage_summary: Optional[str] = None
    gpu_summary: Optional[str] = None
    bmc_type: Optional[str] = None
    bmc_address: Optional[str] = None
    redfish_endpoint: Optional[str] = None
    owner: Optional[str] = None
    business_system: Optional[str] = None
    description: Optional[str] = None
    extra_data: Optional[Dict[str, Any]] = None

    @field_validator("management_ip", "bmc_address")
    @classmethod
    def validate_optional_ip(cls, value: Optional[str]):
        return ServerAssetCreate.validate_optional_ip(value)


class ServerNICCreate(BaseModel):
    name: str
    mac_address: str
    pci_address: Optional[str] = None
    vendor: Optional[str] = None
    model: Optional[str] = None
    speed_mbps: Optional[int] = Field(None, ge=0)
    bond_name: Optional[str] = None
    network_type: str = "business"
    mtu: Optional[int] = Field(None, ge=576, le=65535)
    status: str = "unknown"


class ServerIPCreate(BaseModel):
    address: str
    prefix_length: int = Field(32, ge=0, le=128)
    vlan_id: Optional[int] = Field(None, ge=1, le=4094)
    network_type: str = "business"
    is_primary: bool = False

    @field_validator("address")
    @classmethod
    def validate_address(cls, value: str):
        try:
            return str(ipaddress.ip_address(value.strip()))
        except ValueError as exc:
            raise ValueError("IP 地址格式不正确") from exc


class ServerNICWithIPsCreate(ServerNICCreate):
    ip_addresses: List[ServerIPCreate] = Field(default_factory=list)


class ServerAssetWithNetworkCreate(ServerAssetCreate):
    nics: List[ServerNICWithIPsCreate] = Field(default_factory=list)


class ConnectionEvidence(BaseModel):
    source: str = Field(..., pattern="^(lldp|mac_table|arp|redfish|agent|manual)$")
    observed_at: Optional[str] = None
    details: Dict[str, Any] = Field(default_factory=dict)


class ConnectionDiscovery(BaseModel):
    server_id: int
    nic_id: int
    switch_device_id: int
    switch_port: str
    evidence: List[ConnectionEvidence] = Field(..., min_length=1)


class ConnectionDecision(BaseModel):
    note: Optional[str] = None


class PortChangeCreate(BaseModel):
    connection_id: int
    requested_config: Dict[str, Any]
    reason: str = Field(..., min_length=2, max_length=500)


class PortChangeApproval(BaseModel):
    approved: bool
    note: Optional[str] = None


class PortChangeExecute(BaseModel):
    confirm: bool = False
