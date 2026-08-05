"""设备上线合规接口 Schema。"""
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


CAPABILITY_KEYS = (
    "snmp", "exporter", "syslog", "tacacs", "telemetry", "bmp", "nqa",
    "evpn_vxlan", "roce", "pfc", "ecn", "buffer", "config_backup",
)
CHECK_KEYS = (
    "model_profile", "device_name", "device_model", "serial_number",
    "version", "patch", "hardware", "snmp", "exporter", "syslog", "tacacs",
)


class ModelProfilePayload(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: str = Field(..., min_length=1, max_length=150)
    vendor: str = Field(..., min_length=1, max_length=50)
    model_pattern: str = Field(..., min_length=1, max_length=120)
    network_type: str = Field(default="general", max_length=50)
    device_type: Optional[str] = Field(default=None, max_length=50)
    default_role: Optional[str] = Field(default=None, max_length=50)
    capabilities: Dict[str, bool] = Field(default_factory=dict)
    required_checks: List[str] = Field(default_factory=lambda: ["model_profile", "version", "hardware", "snmp", "syslog", "tacacs"])
    description: Optional[str] = None
    priority: int = Field(default=100, ge=0, le=10000)
    is_active: bool = True

    @field_validator("capabilities")
    @classmethod
    def validate_capabilities(cls, value: Dict[str, Any]):
        unknown = set(value) - set(CAPABILITY_KEYS)
        if unknown:
            raise ValueError(f"不支持的能力字段: {', '.join(sorted(unknown))}")
        return {key: bool(item) for key, item in value.items()}

    @field_validator("required_checks")
    @classmethod
    def validate_checks(cls, value: List[str]):
        # Part Number is no longer collected or checked. Ignore the retired
        # value when an older model profile is edited through the API.
        normalized = list(dict.fromkeys(
            str(item).strip() for item in value
            if str(item).strip() and str(item).strip() != "part_number"
        ))
        unknown = set(normalized) - set(CHECK_KEYS)
        if unknown:
            raise ValueError(f"不支持的检查项: {', '.join(sorted(unknown))}")
        return normalized


class ModelProfileUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    vendor: Optional[str] = Field(default=None, min_length=1, max_length=50)
    model_pattern: Optional[str] = Field(default=None, min_length=1, max_length=120)
    network_type: Optional[str] = Field(default=None, max_length=50)
    device_type: Optional[str] = Field(default=None, max_length=50)
    default_role: Optional[str] = Field(default=None, max_length=50)
    capabilities: Optional[Dict[str, bool]] = None
    required_checks: Optional[List[str]] = None
    description: Optional[str] = None
    priority: Optional[int] = Field(default=None, ge=0, le=10000)
    is_active: Optional[bool] = None


class VersionBaselinePayload(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: str = Field(..., min_length=1, max_length=150)
    model_profile_id: Optional[int] = None
    vendor: Optional[str] = Field(default=None, max_length=50)
    model_pattern: Optional[str] = Field(default=None, max_length=120)
    device_role: Optional[str] = Field(default=None, max_length=50)
    platform_version: Optional[str] = Field(default=None, max_length=100)
    allowed_releases: List[str] = Field(default_factory=list)
    allowed_versions: List[str] = Field(default_factory=list)
    minimum_version: Optional[str] = Field(default=None, max_length=100)
    required_patches: List[str] = Field(default_factory=list)
    forbidden_versions: List[str] = Field(default_factory=list)
    recommendation: Optional[str] = None
    priority: int = Field(default=100, ge=0, le=10000)
    is_active: bool = True

    @field_validator("allowed_releases", "allowed_versions", "required_patches", "forbidden_versions")
    @classmethod
    def normalize_list(cls, value: List[str]):
        return list(dict.fromkeys(str(item).strip() for item in value if str(item).strip()))


class VersionBaselineUpdate(BaseModel):
    model_config = ConfigDict(protected_namespaces=())
    name: Optional[str] = Field(default=None, min_length=1, max_length=150)
    model_profile_id: Optional[int] = None
    vendor: Optional[str] = Field(default=None, max_length=50)
    model_pattern: Optional[str] = Field(default=None, max_length=120)
    device_role: Optional[str] = Field(default=None, max_length=50)
    platform_version: Optional[str] = Field(default=None, max_length=100)
    allowed_releases: Optional[List[str]] = None
    allowed_versions: Optional[List[str]] = None
    minimum_version: Optional[str] = Field(default=None, max_length=100)
    required_patches: Optional[List[str]] = None
    forbidden_versions: Optional[List[str]] = None
    recommendation: Optional[str] = None
    priority: Optional[int] = Field(default=None, ge=0, le=10000)
    is_active: Optional[bool] = None
