"""
SNMP 采集器
支持 SNMP v1/v2c/v3
"""
from pysnmp.hlapi import *
from typing import Dict, Any, List, Optional, Tuple
from datetime import datetime
import asyncio
import time
import re
import subprocess
import json
import ipaddress
from concurrent.futures import ThreadPoolExecutor
from app.config import settings
from app.utils import influx_client, redis_client
from app.utils.snmp_system_info import extract_snmp_model
from app.core import LoggerMixin


class SNMPCollector(LoggerMixin):
    """SNMP采集器"""
    INTERFACE_RATE_CAP_MULTIPLIER = 1.03
    
    # 预定义OID模板
    OID_TEMPLATES = {
        "cpu": {
            "name": "CPU使用率",
            "oids": {
                "1.3.6.1.4.1.9.9.109.1.1.1.1.7.1": "cisco",  # Cisco
                "1.3.6.1.4.1.2011.6.3.4.1.2.0": "huawei",     # Huawei
                "1.3.6.1.4.1.25506.2.6.1.1.1.1.6.0": "h3c",   # H3C
                "1.3.6.1.2.1.25.3.3.1.2.1": "standard",       # 标准Linux/Unix
            }
        },
        "memory": {
            "name": "内存使用率",
            "oids": {
                "1.3.6.1.4.1.9.9.48.1.1.1.5.1": "cisco_used",  # Cisco used
                "1.3.6.1.4.1.9.9.48.1.1.1.6.1": "cisco_free",  # Cisco free
                "1.3.6.1.4.1.2011.6.3.5.1.1.2.0": "huawei",    # Huawei
                "1.3.6.1.4.1.25506.2.6.1.1.1.2.4.0": "h3c",    # H3C
            }
        },
        "interface_traffic": {
            "name": "接口流量",
            "oids": {
                "1.3.6.1.2.1.2.2.1.10": "in_octets",   # ifInOctets
                "1.3.6.1.2.1.2.2.1.16": "out_octets",  # ifOutOctets
                "1.3.6.1.2.1.2.2.1.2": "if_descr",     # ifDescr
            }
        },
        "interface_status": {
            "name": "接口状态",
            "oids": {
                "1.3.6.1.2.1.2.2.1.8": "if_oper_status",  # ifOperStatus
                "1.3.6.1.2.1.2.2.1.2": "if_descr",
            }
        },
        "temperature": {
            "name": "设备温度",
            "oids": {
                "1.3.6.1.4.1.9.9.13.1.3.1.3.1": "cisco",      # Cisco
                "1.3.6.1.4.1.2011.5.25.31.1.1.1.1.11.67108873": "huawei",  # Huawei
            }
        },
        "uptime": {
            "name": "运行时间",
            "oids": {
                "1.3.6.1.2.1.1.3.0": "sysUpTime",  # 标准
            }
        },
        "system_info": {
            "name": "系统信息",
            "oids": {
                "1.3.6.1.2.1.1.1.0": "sysDescr",
                "1.3.6.1.2.1.1.5.0": "sysName",
            }
        }
    }

    HILLSTONE_PRIVATE_OIDS = {
        "cpu_usage_table_oids": [
            "1.3.6.1.4.1.28557.2.25.1.2.1.15",
            "1.3.6.1.4.1.28557.2.25.1.2.1.8",
            "1.3.6.1.4.1.28557.2.25.1.2.1.7",
            "1.3.6.1.4.1.28557.2.25.1.2.1.13",
        ],
        "cpu_usage_aggregate": "max",
        "cpu_usage_oid": "1.3.6.1.4.1.28557.2.2.1.3.0",
        "memory_usage_oid": "1.3.6.1.4.1.28557.2.2.1.17.0",
        "temperature_oid": "1.3.6.1.4.1.28557.2.28.1.2.1.3",
        "session_total_oid": "1.3.6.1.4.1.28557.2.2.1.6.0",
        "session_current_oid": "1.3.6.1.4.1.28557.2.2.1.7.0",
        "ha_status_oid": "1.3.6.1.4.1.28557.2.2.1.8.0",
        "pending_session_queue_full_drop_oid": "1.3.6.1.4.1.28557.2.2.1.35.0",
        "fan_speed_oid": "1.3.6.1.4.1.28557.2.26.1.2.1.4",
        "fan_state_oid": "1.3.6.1.4.1.28557.2.26.1.2.1.5",
        "power_state_oid": "1.3.6.1.4.1.28557.2.27.1.2.1.3",
        "storage_total_oid": "1.3.6.1.4.1.28557.2.35.1.1.1.3",
        "storage_free_oid": "1.3.6.1.4.1.28557.2.35.1.1.1.4",
        "pak_buffer_total_oid": "1.3.6.1.4.1.28557.2.36.1.1.1.3",
        "pak_buffer_used_oid": "1.3.6.1.4.1.28557.2.36.1.1.1.4",
        "ipsec_tunnel_name_oid": "1.3.6.1.4.1.28557.2.1.1.1.1.2",
        "ipsec_tunnel_peer_oid": "1.3.6.1.4.1.28557.2.1.1.1.1.5",
        "ipsec_tunnel_status_oid": "1.3.6.1.4.1.28557.2.1.1.1.1.12",
        "snat_tcp_total_oid": "1.3.6.1.4.1.28557.2.33.1.1.1.1.2",
        "snat_tcp_used_oid": "1.3.6.1.4.1.28557.2.33.1.1.1.1.3",
        "snat_udp_total_oid": "1.3.6.1.4.1.28557.2.33.1.1.1.1.4",
        "snat_udp_used_oid": "1.3.6.1.4.1.28557.2.33.1.1.1.1.5",
        "snat_icmp_total_oid": "1.3.6.1.4.1.28557.2.33.1.1.1.1.6",
        "snat_icmp_used_oid": "1.3.6.1.4.1.28557.2.33.1.1.1.1.7",
        "dnat_server_address_oid": "1.3.6.1.4.1.28557.2.33.1.2.1.1.2",
        "dnat_server_connections_oid": "1.3.6.1.4.1.28557.2.33.1.2.1.1.4",
        "dnat_server_status_oid": "1.3.6.1.4.1.28557.2.33.1.2.1.1.5",
        "slb_vs_name_oid": "1.3.6.1.4.1.28557.2.31.1.2.1.2",
        "slb_vs_status_oid": "1.3.6.1.4.1.28557.2.31.1.2.1.3",
        "slb_vs_connections_oid": "1.3.6.1.4.1.28557.2.31.1.2.1.4",
    }

    def _sanitize_interface_rates(
        self,
        in_bps: Optional[float],
        out_bps: Optional[float],
        speed_bps: Optional[float],
    ) -> Tuple[Optional[float], Optional[float]]:
        if not speed_bps or speed_bps <= 0:
            return in_bps, out_bps
        speed_value = float(speed_bps)
        if in_bps is not None:
            if in_bps < 0:
                in_bps = None
            elif in_bps > speed_value * self.INTERFACE_RATE_CAP_MULTIPLIER:
                # A rate above the physical interface speed is a bad sample
                # (usually a stale/crossed counter baseline), not real traffic.
                in_bps = None
        if out_bps is not None:
            if out_bps < 0:
                out_bps = None
            elif out_bps > speed_value * self.INTERFACE_RATE_CAP_MULTIPLIER:
                out_bps = None
        return in_bps, out_bps

    H3C_PRIVATE_OIDS = {
        "cpu_usage_table_oids": [
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.33",
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.6",
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.20",
        ],
        "memory_usage_table_oids": [
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.8",
            "1.3.6.1.4.1.25506.2.6.1.1.1.1.27",
        ],
        "temperature_oid": "1.3.6.1.4.1.25506.2.6.1.1.1.1.12",
        "temperature_ignore_values": [65535],
        "entity_class_oid": "1.3.6.1.2.1.47.1.1.1.1.5",
        "entity_name_oid": "1.3.6.1.2.1.47.1.1.1.1.7",
        "entity_oper_status_oid": "1.3.6.1.4.1.25506.2.6.1.1.1.1.3",
        "entity_error_status_oid": "1.3.6.1.4.1.25506.2.6.1.1.1.1.19",
        "hardware_ok_values": [2, 3],
        "bgp_state_oids": [
            "1.3.6.1.2.1.15.3.1.2",
            "1.3.6.1.4.1.25506.2.202.1.1.2.1.4",
        ],
        # HH3C-TRANSCEIVER-INFO-MIB. One subtree walk returns identity and
        # dynamic DOM values, which is much lighter than walking every column.
        "optical_info_table_oid": "1.3.6.1.4.1.25506.2.70.1.1.1",
        "optical_vendor": "h3c",
    }

    CISCO_PRIVATE_OIDS = {
        # Cisco Nexus/NX-OS keeps interface counters on standard IF-MIB/IF-X-MIB.
        # Hardware inventory is exposed through ENTITY-MIB. NX-OS Nexus 9K
        # sensors commonly use CISCO-ENTITY-SENSOR-MIB instead of the standard
        # ENTITY-SENSOR-MIB table.
        "cpu_usage_oids": [
            "1.3.6.1.4.1.9.9.109.1.1.1.1.7.1",  # cpmCPUTotal1minRev
        ],
        "memory_used_oid": "1.3.6.1.4.1.9.9.48.1.1.1.5.1",  # ciscoMemoryPoolUsed
        "memory_free_oid": "1.3.6.1.4.1.9.9.48.1.1.1.6.1",  # ciscoMemoryPoolFree
        # NX-OS 10.x on Nexus 9K may not expose CISCO-MEMORY-POOL-MIB
        # (.9.9.48), but does expose CISCO-ENHANCED-MEMPOOL-MIB (.9.9.221).
        # Walk and aggregate all processor pools so the device detail/resource
        # pages can show memory even when the legacy scalar OIDs are absent.
        "memory_pool_used_oid": "1.3.6.1.4.1.9.9.221.1.1.1.1.7",
        "memory_pool_free_oid": "1.3.6.1.4.1.9.9.221.1.1.1.1.8",
        "entity_class_oid": "1.3.6.1.2.1.47.1.1.1.1.5",
        "entity_descr_oid": "1.3.6.1.2.1.47.1.1.1.1.2",
        "entity_name_oid": "1.3.6.1.2.1.47.1.1.1.1.7",
        "entity_software_oid": "1.3.6.1.2.1.47.1.1.1.1.10",
        "entity_serial_oid": "1.3.6.1.2.1.47.1.1.1.1.11",
        "entity_model_oid": "1.3.6.1.2.1.47.1.1.1.1.13",
        "entity_sensor_type_oid": "1.3.6.1.4.1.9.9.91.1.1.1.1.1",
        "entity_sensor_scale_oid": "1.3.6.1.4.1.9.9.91.1.1.1.1.2",
        "entity_sensor_precision_oid": "1.3.6.1.4.1.9.9.91.1.1.1.1.3",
        "entity_sensor_value_oid": "1.3.6.1.4.1.9.9.91.1.1.1.1.4",
        "entity_sensor_status_oid": "1.3.6.1.4.1.9.9.91.1.1.1.1.5",
        "optical_entity_sensor": True,
        "cisco_power_status_oid": "1.3.6.1.4.1.9.9.117.1.1.2.1.2",  # cefcFRUPowerOperStatus
        "cisco_power_ok_values": [2],  # on(2)
        "cisco_fan_status_oid": "1.3.6.1.4.1.9.9.117.1.4.1.1.1",  # cefcFanTrayOperStatus
        "cisco_fan_ok_values": [1, 2],  # up/ok values differ slightly between NX-OS trains
        "bgp_state_oids": [
            "1.3.6.1.2.1.15.3.1.2",
            "1.3.6.1.4.1.9.9.187.1.2.5.1.3",  # cbgpPeer2State
        ],
    }



    RUIJIE_PRIVATE_OIDS = {
        # RUIJIE-SMI: enterprises.4881.1.1.10.2 = ruijieMgmt
        # Source: RGOS 11.0(5)B9P62 / 12.5(1)B0605 private MIB package.
        "cpu_usage_oids": [
            "1.3.6.1.4.1.4881.1.1.10.2.36.1.1.2.0",  # ruijieCPUUtilization1Min
            "1.3.6.1.4.1.4881.1.1.10.2.36.1.1.10.0", # ruijieCPUUtilizationCurrent
        ],
        "cpu_usage_table_oids": [
            "1.3.6.1.4.1.4881.1.1.10.2.36.1.2.1.1.4",  # ruijieNodeCPUTotal1min
            "1.3.6.1.4.1.4881.1.1.10.2.36.1.2.1.1.5",  # ruijieNodeCPUTotal5min
        ],
        "cpu_usage_aggregate": "max",
        # Some RGOS 12.x devices expose ruijieMemoryPoolCurrentUtilization as an exact scalar-like
        # object when walked, so collect it with GET first and keep node pool as an optional table.
        "memory_usage_oids": [
            "1.3.6.1.4.1.4881.1.1.10.2.35.1.1.1.1.3",  # ruijieMemoryPoolCurrentUtilization
        ],
        "memory_usage_table_oids": [
            "1.3.6.1.4.1.4881.1.1.10.2.35.1.2.1.1.3",  # ruijieNodeMemoryPoolCurrentUtilization
        ],
        "memory_usage_aggregate": "max",
        "temperature_oids": [
            "1.3.6.1.4.1.4881.1.1.10.2.1.1.16.0",      # ruijieSystemTemperature
            "1.3.6.1.4.1.4881.1.1.10.2.1.1.23.1.1.3",  # ruijieSystemTemperatureCurrent table
        ],
        "system_version_oid": "1.3.6.1.4.1.4881.1.1.10.2.1.1.2.0",
        "system_model_oid": "1.3.6.1.4.1.4881.1.1.10.2.21.1.2.1.2.1",
        "system_serial_oid": "1.3.6.1.4.1.4881.1.1.10.2.21.1.2.1.10.1",
        "fan_state_oid": "1.3.6.1.4.1.4881.1.1.10.2.21.1.6.1.3",
        "fan_name_oid": "1.3.6.1.4.1.4881.1.1.10.2.21.1.6.1.4",
        "fan_ok_values": [1],
        "power_state_oid": "1.3.6.1.4.1.4881.1.1.10.2.21.1.5.1.3",
        "power_name_oid": "1.3.6.1.4.1.4881.1.1.10.2.21.1.5.1.4",
        "power_ok_values": [4],
        "bgp_state_oids": [
            "1.3.6.1.2.1.15.3.1.2",
            "1.3.6.1.4.1.4881.1.1.10.2.73.2.5.1.5",
        ],
        # RUIJIE-FIBER-MIB::ruijieFiberEntry. RGOS exposes a large table with
        # more than 160 columns; walking the whole subtree can time out. The
        # collector therefore reads only the required identity/DOM columns.
        "optical_ruijie_fiber_entry_oid": "1.3.6.1.4.1.4881.1.1.10.2.105.1.1.1",
    }

    DENSIVELO_PRIVATE_OIDS = {
        # S9867/DensiveloOS exposes Yillion device-management objects under:
        #   1.3.6.1.4.1.64812.8.35.18
        # Source: 03-设备管理 / YLDC-LSW-DEV-ADM-MIB.
        "cpu_usage_oid": "1.3.6.1.4.1.64812.8.35.18.1.3.0",
        "memory_usage_oid": "1.3.6.1.4.1.64812.8.35.18.1.16.0",
        "system_version_oid": "1.3.6.1.4.1.64812.8.35.18.1.4.0",
        "system_release_oid": "1.3.6.1.4.1.64812.8.35.18.1.24.0",
        "system_model_oid": "1.3.6.1.4.1.64812.8.35.18.1.23.0",
        "system_serial_oid": "1.3.6.1.4.1.64812.8.35.18.1.21.0",
        # The documented yldcLswSysTemperature (.1.17.0) returns No Such Object
        # on tested S9867-128DH devices. Real hotspot temperatures are exposed by
        # the vendor environment table below; invalid sensors report 65535.
        "temperature_oid": "1.3.6.1.4.1.64812.2.6.1.1.1.1.12",
        "temperature_ignore_values": [65535],
        #
        # S9867 ports expose 64-bit IF-MIB HC counters. In high-frequency interface
        # sampling, avoid the extra 32-bit ifInOctets/ifOutOctets walks unless a
        # device-specific override disables this flag.
        "skip_32bit_interface_counters": True,
        "bgp_state_oids": [
            "1.3.6.1.2.1.15.3.1.2",
        ],
        "bgp_contexts": [
            "bgp-underlay",
        ],
        # S9867 underlay BGP is exposed in the bgp-underlay SNMP context. Reading
        # both the default context and bgp-underlay can produce duplicate peers in
        # overview/detail pages, so prefer the context result when it is present.
        "prefer_bgp_contexts": True,
        "entity_class_oid": "1.3.6.1.2.1.47.1.1.1.1.5",
        "entity_name_oid": "1.3.6.1.2.1.47.1.1.1.1.7",
    }
    
    def __init__(self):
        self.timeout = settings.SNMP_DEFAULT_TIMEOUT
        self.retries = settings.SNMP_DEFAULT_RETRIES

    def _get_private_oid_config(self, device: Any) -> Dict[str, Any]:
        identity = " ".join([
            str(getattr(device, "vendor", "") or ""),
            str(getattr(device, "model", "") or ""),
            str(getattr(device, "name", "") or ""),
            str(getattr(device, "hostname", "") or ""),
        ]).lower()
        if any(marker in identity for marker in ["hillstone", "sg-6000", "山石"]):
            defaults = self.HILLSTONE_PRIVATE_OIDS.copy()
        elif any(marker in identity for marker in ["cisco", "nexus", "nx-os", "nxos", "n9k", "9364d"]):
            defaults = self.CISCO_PRIVATE_OIDS.copy()
        elif any(marker in identity for marker in ["ruijie", "锐捷", "rgos"]):
            defaults = self.RUIJIE_PRIVATE_OIDS.copy()
        elif any(marker in identity for marker in ["densivelo", "yillion", "deepcompute", "s9867"]):
            defaults = self.DENSIVELO_PRIVATE_OIDS.copy()
        elif (
            any(marker in identity for marker in ["h3c", "comware", "华三", "新华三"])
            or re.search(r"\bs(?:51|55|58|65|68|98)\d{2}", identity)
        ):
            defaults = self.H3C_PRIVATE_OIDS.copy()
        else:
            defaults = {}
        custom_fields = getattr(device, "custom_fields", None) or {}
        if isinstance(custom_fields, str):
            try:
                custom_fields = json.loads(custom_fields)
            except Exception:
                custom_fields = {}
        if not isinstance(custom_fields, dict):
            return defaults
        private_oids = custom_fields.get("snmp_private_oids") or {}
        if isinstance(private_oids, dict):
            defaults.update(private_oids)
        return defaults

    def _extract_peer_from_index(self, index: str, address_less_tail: bool = False) -> str:
        parts = [part for part in str(index).split(".") if part]
        if address_less_tail and len(parts) >= 5 and all(part.isdigit() for part in parts[-5:]):
            return ".".join(parts[-5:-1])
        if len(parts) >= 4 and all(part.isdigit() for part in parts[-4:]):
            return ".".join(parts[-4:])
        return str(index)

    def _normalize_numeric(self, value: Any, scale: float = 1.0) -> Optional[float]:
        if value is None:
            return None
        try:
            return round(float(value) * scale, 4)
        except Exception:
            return None

    def _is_ignored_value(self, value: Any, ignore_values: set[str]) -> bool:
        if value is None:
            return False
        if str(value) in ignore_values:
            return True
        try:
            return str(int(float(value))) in ignore_values
        except Exception:
            return False

    def _aggregate_numeric_values(self, values: List[float], mode: str = "max") -> Optional[float]:
        cleaned = [float(value) for value in values if value is not None]
        if not cleaned:
            return None
        if mode == "avg":
            return round(sum(cleaned) / len(cleaned), 2)
        if mode == "first":
            return round(cleaned[0], 2)
        return round(max(cleaned), 2)

    def _entity_sensor_scale_multiplier(self, scale: Any) -> float:
        """ENTITY-SENSOR-MIB EntitySensorDataScale enum to numeric multiplier."""
        scale_map = {
            1: 1e-24, 2: 1e-21, 3: 1e-18, 4: 1e-15, 5: 1e-12, 6: 1e-9,
            7: 1e-6, 8: 1e-3, 9: 1.0, 10: 1e3, 11: 1e6, 12: 1e9,
            13: 1e12, 14: 1e15, 15: 1e18, 16: 1e21, 17: 1e24,
        }
        try:
            return scale_map.get(int(scale), 1.0)
        except Exception:
            return 1.0

    def _convert_entity_sensor_value(self, value: Any, scale: Any = None, precision: Any = None) -> Optional[float]:
        try:
            numeric = float(value)
            numeric *= self._entity_sensor_scale_multiplier(scale)
            numeric /= 10 ** int(precision or 0)
            return round(numeric, 4)
        except Exception:
            return None

    def _first_entity_inventory_value(
        self,
        class_map: Dict[str, Any],
        value_map: Dict[str, Any],
        preferred_classes: Optional[set[int]] = None,
        name_map: Optional[Dict[str, Any]] = None,
    ) -> Optional[str]:
        preferred_classes = preferred_classes or {3}
        preferred_indexes = [
            index for index, entity_class in class_map.items()
            if entity_class in preferred_classes and str(value_map.get(index) or "").strip()
        ]
        if name_map:
            chassis_like = [
                index for index in preferred_indexes
                if any(marker in str(name_map.get(index) or "").lower() for marker in ["chassis", "switch", "module"])
            ]
            if chassis_like:
                preferred_indexes = chassis_like
        for index in preferred_indexes:
            text = str(value_map.get(index) or "").strip()
            if text:
                return text
        for value in value_map.values():
            text = str(value or "").strip()
            if text:
                return text
        return None

    def _normalize_interface_token(self, value: Any) -> str:
        return re.sub(r"[^a-z0-9]", "", str(value or "").lower())

    def _extract_cisco_interface_from_sensor_name(self, sensor_name: str, if_name_map: Dict[str, str]) -> Optional[Tuple[str, str]]:
        text = str(sensor_name or "").strip()
        if not text:
            return None
        for index, if_name in sorted(if_name_map.items(), key=lambda item: len(str(item[1])), reverse=True):
            if if_name and self._normalize_interface_token(if_name) in self._normalize_interface_token(text):
                return str(index), str(if_name)
        match = re.search(r"\b(?:Eth|Ethernet)\s*\d+(?:/\d+){1,3}\b", text, re.IGNORECASE)
        if not match:
            return None
        interface_name = re.sub(r"\s+", "", match.group(0))
        return "0", interface_name

    def _collect_private_numeric_metric(
        self,
        device: Any,
        private_oids: Dict[str, Any],
        scalar_key: str,
        table_key: str,
        scale_key: str,
        aggregate_key: str,
        ignore_key: Optional[str] = None,
    ) -> Optional[float]:
        values: List[float] = []
        ignore_values = {str(value) for value in private_oids.get(ignore_key or "") or []}
        table_oids = private_oids.get(table_key) or []
        if table_oids and not isinstance(table_oids, list):
            table_oids = [table_oids]
        scale = float(private_oids.get(scale_key, 1.0) or 1.0)
        for oid in table_oids:
            table_values = self._walk_indexed_map(device, str(oid), float)
            for value in table_values.values():
                if self._is_ignored_value(value, ignore_values):
                    continue
                normalized = self._normalize_numeric(value, scale)
                if normalized is not None:
                    values.append(normalized)
            if values:
                return self._aggregate_numeric_values(values, str(private_oids.get(aggregate_key) or "max"))

        scalar_oids = private_oids.get(scalar_key) or []
        if scalar_oids and not isinstance(scalar_oids, list):
            scalar_oids = [scalar_oids]
        for oid in scalar_oids:
            value = self._snmp_get_scalar(device, str(oid))
            if value is None or self._is_ignored_value(value, ignore_values):
                continue
            normalized = self._normalize_numeric(value, scale)
            if normalized is not None:
                values.append(normalized)
        return self._aggregate_numeric_values(values, str(private_oids.get(aggregate_key) or "max"))

    def _snmp_get_scalar(self, device: Any, oid: str) -> Any:
        value = self.snmp_get(device, oid)
        if value is not None:
            return value
        if not str(oid).endswith(".0"):
            return self.snmp_get(device, f"{oid}.0")
        return None

    def _build_snmp_command(self, tool: str, device: Any, oid: str, context_name: Optional[str] = None) -> List[str]:
        version = (device.snmp_version or "v2c").lower()
        port = str(device.snmp_port or settings.SNMP_DEFAULT_PORT)
        agent = f"{device.ip_address}:{port}"
        base_command = [
            tool,
            "-v",
            "3" if version == "v3" else version.replace("v", ""),
            "-t",
            str(self.timeout),
            "-r",
            str(self.retries),
            "-On",
        ]

        if version == "v3":
            security_level = device.snmp_security_level or "noAuthNoPriv"
            base_command.extend(["-l", security_level])
            if device.snmp_username:
                base_command.extend(["-u", device.snmp_username])
            if device.snmp_auth_protocol and device.snmp_auth_password:
                base_command.extend(["-a", device.snmp_auth_protocol.upper(), "-A", device.snmp_auth_password])
            if device.snmp_priv_protocol and device.snmp_priv_password:
                base_command.extend(["-x", device.snmp_priv_protocol.upper(), "-X", device.snmp_priv_password])
        else:
            community = device.snmp_community or "para@2026"
            base_command.extend(["-c", community])

        if context_name:
            base_command.extend(["-n", context_name])

        base_command.extend([agent, oid])
        return base_command

    def _normalize_snmp_value(self, value: str) -> Any:
        normalized = value.strip()
        type_prefixes = [
            "STRING:",
            "INTEGER:",
            "Counter32:",
            "Counter64:",
            "Gauge32:",
            "Hex-STRING:",
            "IpAddress:",
            "OID:",
            "OBJECT IDENTIFIER:",
            "BITS:",
            "Unsigned32:",
        ]
        for prefix in type_prefixes:
            if normalized.startswith(prefix):
                normalized = normalized[len(prefix):].strip()
                break

        if normalized.startswith('"') and normalized.endswith('"'):
            normalized = normalized[1:-1]

        timeticks_match = re.match(r"Timeticks:\s*\((\d+)\)", value.strip())
        if timeticks_match:
            return int(timeticks_match.group(1))

        if re.fullmatch(r"-?\d+", normalized):
            try:
                return int(normalized)
            except Exception:
                return normalized

        return normalized

    def _run_snmp_command(self, command: List[str], device: Any, quiet_no_such: bool = False) -> Optional[str]:
        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=max(self.timeout * (self.retries + 1) + 2, 5),
                check=False,
            )
        except FileNotFoundError:
            return None
        except subprocess.TimeoutExpired:
            self.logger.warning("SNMP命令执行超时", device=device.ip_address, command=command[0])
            return ""
        except Exception as exc:
            self.logger.error(f"SNMP命令执行异常: {exc}", device=device.ip_address)
            return ""

        if result.returncode != 0:
            stderr = (result.stderr or "").strip()
            if stderr:
                harmless_markers = [
                    "No Such Instance",
                    "No Such Object",
                    "No more variables left",
                    "No Such Instance currently exists",
                ]
                if quiet_no_such and any(marker in stderr for marker in harmless_markers):
                    return ""
                self.logger.warning(f"SNMP命令失败: {stderr}", device=device.ip_address, command=command[0])
            return ""

        return result.stdout

    def _snmp_get_text_value(self, device: Any, oid: str) -> Optional[str]:
        """使用 net-snmp 获取完整文本值，保留多行 sysDescr。"""
        command = [part for part in self._build_snmp_command("snmpget", device, oid) if part != "-On"]
        command.insert(1, "-Oqv")
        output = self._run_snmp_command(command, device, quiet_no_such=True)
        if output is None:
            value = self.snmp_get(device, oid)
            return str(value).strip() if value is not None else None
        text = output.strip()
        if text.startswith('"') and text.endswith('"'):
            text = text[1:-1]
        return text.strip() or None
    
    def _get_snmp_target(self, device: Any) -> Tuple[Any, CommunityData or UsmUserData]:
        """获取SNMP目标配置"""
        ip = device.ip_address
        port = device.snmp_port or settings.SNMP_DEFAULT_PORT
        
        target = UdpTransportTarget((ip, port), timeout=self.timeout, retries=self.retries)
        
        if device.snmp_version == "v3":
            # SNMPv3配置
            auth_protocol = None
            priv_protocol = None
            
            if device.snmp_auth_protocol == "MD5":
                auth_protocol = usmHMACMD5AuthProtocol
            elif device.snmp_auth_protocol == "SHA":
                auth_protocol = usmHMACSHAAuthProtocol
            
            if device.snmp_priv_protocol == "DES":
                priv_protocol = usmDESPrivProtocol
            elif device.snmp_priv_protocol == "AES":
                priv_protocol = usmAesCfb128Protocol
            
            security_level = {
                "noAuthNoPriv": "noAuthNoPriv",
                "authNoPriv": "authNoPriv",
                "authPriv": "authPriv"
            }.get(device.snmp_security_level, "noAuthNoPriv")
            
            auth_data = UsmUserData(
                userName=device.snmp_username or "",
                authProtocol=auth_protocol,
                authKey=device.snmp_auth_password or "",
                privProtocol=priv_protocol,
                privKey=device.snmp_priv_password or "",
                securityLevel=security_level
            )
        else:
            # SNMPv1/v2c配置
            community = device.snmp_community or "para@2026"
            auth_data = CommunityData(community)
        
        return target, auth_data
    
    def snmp_get(self, device: Any, oid: str, context_name: Optional[str] = None) -> Optional[Any]:
        """执行SNMP GET操作"""
        command = self._build_snmp_command("snmpget", device, oid, context_name=context_name)
        output = self._run_snmp_command(command, device)
        if output is not None:
            if not output.strip():
                return None
            line = output.strip().splitlines()[-1]
            if " = " in line:
                _, raw_value = line.split(" = ", 1)
            else:
                raw_value = line
            return self._normalize_snmp_value(raw_value)

        try:
            target, auth = self._get_snmp_target(device)
            
            iterator = getCmd(
                SnmpEngine(),
                auth,
                target,
                ContextData(contextName=context_name or ""),
                ObjectType(ObjectIdentity(oid))
            )
            
            errorIndication, errorStatus, errorIndex, varBinds = next(iterator)
            
            if errorIndication:
                self.logger.warning(f"SNMP GET错误: {errorIndication}", device=device.ip_address)
                return None
            elif errorStatus:
                self.logger.warning(f"SNMP GET错误状态: {errorStatus}", device=device.ip_address)
                return None
            else:
                for varBind in varBinds:
                    return varBind[1]  # 返回值
            
            return None
            
        except Exception as e:
            self.logger.error(f"SNMP GET异常: {e}", device=device.ip_address)
            return None
    
    def snmp_walk(self, device: Any, oid: str, context_name: Optional[str] = None) -> List[Tuple[str, Any]]:
        """执行SNMP WALK操作"""
        results = []
        command = self._build_snmp_command("snmpbulkwalk", device, oid, context_name=context_name)
        output = self._run_snmp_command(command, device)
        if output is not None:
            if not output.strip():
                return []
            for line in output.strip().splitlines():
                if " = " not in line:
                    continue
                item_oid, raw_value = line.split(" = ", 1)
                results.append((item_oid.lstrip("."), self._normalize_snmp_value(raw_value)))
            return results

        try:
            target, auth = self._get_snmp_target(device)
            
            for (errorIndication, errorStatus, errorIndex, varBinds) in nextCmd(
                SnmpEngine(),
                auth,
                target,
                ContextData(contextName=context_name or ""),
                ObjectType(ObjectIdentity(oid)),
                lexicographicMode=False
            ):
                if errorIndication:
                    self.logger.warning(f"SNMP WALK错误: {errorIndication}", device=device.ip_address)
                    break
                elif errorStatus:
                    self.logger.warning(f"SNMP WALK错误状态: {errorStatus}", device=device.ip_address)
                    break
                else:
                    for varBind in varBinds:
                        results.append((str(varBind[0]), varBind[1]))
            
            return results
            
        except Exception as e:
            self.logger.error(f"SNMP WALK异常: {e}", device=device.ip_address)
            return []

    def _walk_indexed_map(self, device: Any, oid: str, cast=None, context_name: Optional[str] = None) -> Dict[str, Any]:
        results = {}
        for item_oid, value in self.snmp_walk(device, oid, context_name=context_name):
            if item_oid == oid or item_oid.lstrip(".") == oid.lstrip("."):
                continue
            normalized_oid = item_oid.lstrip(".")
            base_oid = oid.lstrip(".")
            if normalized_oid.startswith(base_oid + "."):
                index = normalized_oid[len(base_oid) + 1:]
            else:
                index = normalized_oid.split('.')[-1]
            normalized_value = value
            if cast:
                try:
                    normalized_value = cast(value)
                except Exception:
                    continue
            results[index] = normalized_value
        return results

    def _snmp_int(self, device: Any, oid: str) -> Optional[int]:
        value = self.snmp_get(device, oid)
        if value is None:
            return None
        try:
            return int(value)
        except Exception:
            return None

    def list_interfaces(self, device: Any) -> List[Dict[str, Any]]:
        """获取设备接口列表"""
        walk_jobs = {
            "if_name_map": ("1.3.6.1.2.1.31.1.1.1.1", str),
            "if_descr_map": ("1.3.6.1.2.1.2.2.1.2", str),
            "if_type_map": ("1.3.6.1.2.1.2.2.1.3", int),
            "if_mtu_map": ("1.3.6.1.2.1.2.2.1.4", int),
            "if_alias_map": ("1.3.6.1.2.1.31.1.1.1.18", str),
            "admin_status_map": ("1.3.6.1.2.1.2.2.1.7", int),
            "oper_status_map": ("1.3.6.1.2.1.2.2.1.8", int),
            "high_speed_map": ("1.3.6.1.2.1.31.1.1.1.15", int),
            "speed_map": ("1.3.6.1.2.1.2.2.1.5", int),
            "ip_ifindex_map": ("1.3.6.1.2.1.4.20.1.2", str),
            "ip_netmask_map": ("1.3.6.1.2.1.4.20.1.3", str),
        }
        with ThreadPoolExecutor(max_workers=len(walk_jobs)) as executor:
            futures = {
                name: executor.submit(self._walk_indexed_map, device, oid, cast)
                for name, (oid, cast) in walk_jobs.items()
            }
            walk_results = {name: future.result() for name, future in futures.items()}

        if_name_map = walk_results["if_name_map"]
        if_descr_map = walk_results["if_descr_map"]
        if_type_map = walk_results["if_type_map"]
        if_mtu_map = walk_results["if_mtu_map"]
        if_alias_map = walk_results["if_alias_map"]
        admin_status_map = walk_results["admin_status_map"]
        oper_status_map = walk_results["oper_status_map"]
        high_speed_map = walk_results["high_speed_map"]
        speed_map = walk_results["speed_map"]
        ip_ifindex_map = walk_results["ip_ifindex_map"]
        ip_netmask_map = walk_results["ip_netmask_map"]

        ifindex_ip_map: Dict[str, List[str]] = {}
        for ip_addr, ifindex in ip_ifindex_map.items():
            if not ip_addr or not ifindex:
                continue
            mask = ip_netmask_map.get(str(ip_addr)) or ""
            ifindex_ip_map.setdefault(str(ifindex), []).append(self._format_interface_ip(str(ip_addr), str(mask)))

        status_map = {
            1: "up",
            2: "down",
            3: "testing",
            4: "unknown",
            5: "dormant",
            6: "notPresent",
            7: "lowerLayerDown",
        }
        if_type_map_text = {
            6: "ethernet",
            24: "loopback",
            53: "virtual",
            135: "vlan",
            136: "l3vlan",
            161: "aggregation",
        }

        indexes = sorted(
            set(if_name_map) | set(if_descr_map),
            key=lambda item: int(item) if str(item).isdigit() else item,
        )
        interfaces: List[Dict[str, Any]] = []

        for index in indexes:
            name = if_name_map.get(index) or if_descr_map.get(index) or f"if{index}"
            descr = if_descr_map.get(index) or name
            alias = if_alias_map.get(index) or ""
            if_type = if_type_map.get(index)
            high_speed = high_speed_map.get(index)
            speed = speed_map.get(index)
            if high_speed and high_speed > 0:
                speed_bps = high_speed * 1_000_000
            elif speed and speed > 0:
                speed_bps = speed
            else:
                speed_bps = None

            interfaces.append({
                "index": int(index),
                "name": name,
                "description": descr,
                "alias": alias,
                "type": if_type_map_text.get(if_type, str(if_type) if if_type is not None else ""),
                "interface_type": if_type_map_text.get(if_type, str(if_type) if if_type is not None else ""),
                "mtu": if_mtu_map.get(index),
                "ip_address": ", ".join(ifindex_ip_map.get(str(index), [])),
                "admin_status": status_map.get(admin_status_map.get(index), "unknown"),
                "oper_status": status_map.get(oper_status_map.get(index), "unknown"),
                "speed_bps": speed_bps,
            })

        return interfaces

    def _format_interface_ip(self, ip_addr: str, mask: str = "") -> str:
        ip_text = str(ip_addr or "").strip()
        if not ip_text:
            return ""
        if "/" in ip_text:
            return ip_text
        mask_text = str(mask or "").strip()
        if not mask_text:
            return ip_text
        try:
            prefix = ipaddress.IPv4Network(f"0.0.0.0/{mask_text}").prefixlen
            return f"{ip_text}/{prefix}"
        except Exception:
            return ip_text

    def get_interface_snapshot(self, device: Any, interface_index: int) -> Dict[str, Any]:
        """获取单个接口当前SNMP快照"""
        index = str(interface_index)
        get_jobs = {
            "name": (self.snmp_get, f"1.3.6.1.2.1.31.1.1.1.1.{index}"),
            "descr": (self.snmp_get, f"1.3.6.1.2.1.2.2.1.2.{index}"),
            "alias": (self.snmp_get, f"1.3.6.1.2.1.31.1.1.1.18.{index}"),
            "in_octets_64": (self._snmp_int, f"1.3.6.1.2.1.31.1.1.1.6.{index}"),
            "out_octets_64": (self._snmp_int, f"1.3.6.1.2.1.31.1.1.1.10.{index}"),
            "high_speed": (self._snmp_int, f"1.3.6.1.2.1.31.1.1.1.15.{index}"),
            "speed": (self._snmp_int, f"1.3.6.1.2.1.2.2.1.5.{index}"),
            "admin_status": (self._snmp_int, f"1.3.6.1.2.1.2.2.1.7.{index}"),
            "oper_status": (self._snmp_int, f"1.3.6.1.2.1.2.2.1.8.{index}"),
            "in_discards": (self._snmp_int, f"1.3.6.1.2.1.2.2.1.13.{index}"),
            "out_discards": (self._snmp_int, f"1.3.6.1.2.1.2.2.1.19.{index}"),
            "crc_errors": (self._snmp_int, f"1.3.6.1.2.1.10.7.2.1.3.{index}"),
            "in_errors": (self._snmp_int, f"1.3.6.1.2.1.2.2.1.14.{index}"),
            "out_errors": (self._snmp_int, f"1.3.6.1.2.1.2.2.1.20.{index}"),
            "queue_length": (self._snmp_int, f"1.3.6.1.2.1.2.2.1.21.{index}"),
        }
        with ThreadPoolExecutor(max_workers=len(get_jobs)) as executor:
            futures = {
                name: executor.submit(fn, device, oid)
                for name, (fn, oid) in get_jobs.items()
            }
            get_results = {name: future.result() for name, future in futures.items()}

        name = get_results["name"]
        descr = get_results["descr"]
        alias = get_results["alias"]
        in_octets = get_results["in_octets_64"]
        out_octets = get_results["out_octets_64"]
        if in_octets is None:
            in_octets = self._snmp_int(device, f"1.3.6.1.2.1.2.2.1.10.{index}")
        if out_octets is None:
            out_octets = self._snmp_int(device, f"1.3.6.1.2.1.2.2.1.16.{index}")

        high_speed = get_results["high_speed"]
        speed = get_results["speed"]
        if high_speed and high_speed > 0:
            speed_bps = high_speed * 1_000_000
        elif speed and speed > 0:
            speed_bps = speed
        else:
            speed_bps = None

        admin_status = get_results["admin_status"]
        oper_status = get_results["oper_status"]
        in_discards = get_results["in_discards"]
        out_discards = get_results["out_discards"]
        crc_errors = get_results["crc_errors"]
        in_errors = get_results["in_errors"]
        out_errors = get_results["out_errors"]
        queue_length = get_results["queue_length"]

        status_map = {
            1: "up",
            2: "down",
            3: "testing",
            4: "unknown",
            5: "dormant",
            6: "notPresent",
            7: "lowerLayerDown",
        }

        return {
            "index": interface_index,
            "name": str(name) if name is not None else (str(descr) if descr is not None else f"if{index}"),
            "description": str(descr) if descr is not None else f"if{index}",
            "alias": str(alias) if alias is not None else "",
            "in_octets": in_octets,
            "out_octets": out_octets,
            "speed_bps": speed_bps,
            "admin_status": status_map.get(admin_status, "unknown"),
            "oper_status": status_map.get(oper_status, "unknown"),
            "in_discards": in_discards,
            "out_discards": out_discards,
            "crc_errors": crc_errors,
            "in_errors": in_errors,
            "out_errors": out_errors,
            "queue_length": queue_length,
        }

    def get_interface_metrics(self, device: Any, interface_index: int, sample_seconds: float = 5.0) -> Dict[str, Any]:
        """获取接口指标并计算实时流量速率"""
        first_snapshot = self.get_interface_snapshot(device, interface_index)
        started_at = time.monotonic()
        time.sleep(sample_seconds)
        second_snapshot = self.get_interface_snapshot(device, interface_index)
        elapsed = max(time.monotonic() - started_at, 1)

        in_bps = None
        out_bps = None

        if first_snapshot.get("in_octets") is not None and second_snapshot.get("in_octets") is not None:
            delta_in = second_snapshot["in_octets"] - first_snapshot["in_octets"]
            if delta_in >= 0:
                in_bps = round((delta_in * 8) / elapsed, 2)

        if first_snapshot.get("out_octets") is not None and second_snapshot.get("out_octets") is not None:
            delta_out = second_snapshot["out_octets"] - first_snapshot["out_octets"]
            if delta_out >= 0:
                out_bps = round((delta_out * 8) / elapsed, 2)

        speed_bps = second_snapshot.get("speed_bps")
        in_bps, out_bps = self._sanitize_interface_rates(in_bps, out_bps, speed_bps)
        in_utilization = round((in_bps / speed_bps) * 100, 2) if in_bps is not None and speed_bps else None
        out_utilization = round((out_bps / speed_bps) * 100, 2) if out_bps is not None and speed_bps else None

        return {
            **second_snapshot,
            "sample_seconds": round(elapsed, 2),
            "in_bps": in_bps,
            "out_bps": out_bps,
            "in_utilization_percent": in_utilization,
            "out_utilization_percent": out_utilization,
            "buffer_usage": second_snapshot.get("queue_length"),
            "buffer_usage_unit": "queue_length",
        }


    def collect_lldp_neighbors(self, device: Any) -> List[Dict[str, Any]]:
        """Collect LLDP neighbor mappings: local interface -> remote system/interface.

        Uses standard LLDP-MIB. This is intentionally on-demand for detail views to avoid
        adding load to the regular SNMP polling round.
        """
        local_port_id_map = self._walk_indexed_map(device, "1.0.8802.1.1.2.1.3.7.1.3", str)
        local_port_desc_map = self._walk_indexed_map(device, "1.0.8802.1.1.2.1.3.7.1.4", str)
        rem_chassis_map = self._walk_indexed_map(device, "1.0.8802.1.1.2.1.4.1.1.5", str)
        rem_port_id_map = self._walk_indexed_map(device, "1.0.8802.1.1.2.1.4.1.1.7", str)
        rem_port_desc_map = self._walk_indexed_map(device, "1.0.8802.1.1.2.1.4.1.1.8", str)
        rem_sys_name_map = self._walk_indexed_map(device, "1.0.8802.1.1.2.1.4.1.1.9", str)
        rem_sys_desc_map = self._walk_indexed_map(device, "1.0.8802.1.1.2.1.4.1.1.10", str)
        rem_mgmt_addr_index_map = self._walk_indexed_map(device, "1.0.8802.1.1.2.1.4.2.1.4", str)

        def local_port_num(index: str) -> str:
            # lldpRemTable index is timeMark.localPortNum.remIndex
            parts = str(index or "").split(".")
            return parts[1] if len(parts) >= 3 else str(index or "")

        def mgmt_for_neighbor(index: str) -> Optional[str]:
            prefix = f"{index}."
            for mgmt_index in rem_mgmt_addr_index_map.keys():
                mgmt_index_text = str(mgmt_index)
                if not mgmt_index_text.startswith(prefix):
                    continue
                # LLDP-MIB lldpRemManAddrTable indexes management address as:
                # timeMark.localPortNum.remIndex.addrSubtype.addrLength.addrOctets...
                # The object value itself is usually ifId, so the real address must be decoded from index.
                suffix = mgmt_index_text[len(prefix):]
                parts = [int(part) for part in suffix.split(".") if part.isdigit()]
                if len(parts) >= 6 and parts[0] == 1 and parts[1] == 4:
                    return ".".join(str(part) for part in parts[2:6])
                if len(parts) >= 5 and parts[0] == 1:
                    return ".".join(str(part) for part in parts[1:5])
                if len(parts) >= 18 and parts[0] == 2 and parts[1] == 16:
                    octets = parts[2:18]
                    return ":".join(f"{octets[i]:02x}{octets[i + 1]:02x}" for i in range(0, 16, 2))
            return None

        rows: List[Dict[str, Any]] = []
        for index in sorted(set(rem_port_id_map) | set(rem_sys_name_map) | set(rem_port_desc_map), key=str):
            local_index = local_port_num(index)
            local_port = local_port_desc_map.get(local_index) or local_port_id_map.get(local_index) or local_index
            remote_port = rem_port_desc_map.get(index) or rem_port_id_map.get(index) or "-"
            remote_system = rem_sys_name_map.get(index) or rem_chassis_map.get(index) or "-"
            rows.append({
                "protocol": "lldp",
                "local_port": str(local_port),
                "local_port_id": str(local_port_id_map.get(local_index) or local_index),
                "local_port_num": str(local_index),
                "remote_system": str(remote_system),
                "remote_port": str(remote_port),
                "remote_port_id": str(rem_port_id_map.get(index) or ""),
                "remote_chassis_id": str(rem_chassis_map.get(index) or ""),
                "remote_mgmt_addr": mgmt_for_neighbor(index),
                "remote_sys_desc": str(rem_sys_desc_map.get(index) or ""),
                "peer": str(remote_system),
                "interface": str(local_port),
                "index": str(index),
                "state": "up",
                "status": "up",
                "source": "snmp",
            })
        return rows

    def collect_bgp_peer_details(self, device: Any) -> List[Dict[str, Any]]:
        """Collect BGP peer detail fields that are useful in the protocol drawer.

        Standard BGP4-MIB exposes peer state, local address and remote AS. Interface is
        not a direct BGP-MIB field, so we safely infer it by matching bgpPeerLocalAddr
        against IP-MIB ipAdEntIfIndex. If the mapping is unavailable, interface remains
        empty instead of guessing.
        """
        private_oids = self._get_private_oid_config(device)
        bgp_state_oids = private_oids.get("bgp_state_oids") or ["1.3.6.1.2.1.15.3.1.2"]
        if not isinstance(bgp_state_oids, list):
            bgp_state_oids = [bgp_state_oids]

        bgp_state_text = {
            1: "idle",
            2: "connect",
            3: "active",
            4: "opensent",
            5: "openconfirm",
            6: "established",
        }
        ip_to_ifindex = self._walk_indexed_map(device, "1.3.6.1.2.1.4.20.1.2", str)
        if_name_map = self._walk_indexed_map(device, "1.3.6.1.2.1.31.1.1.1.1", str)
        if_descr_map = self._walk_indexed_map(device, "1.3.6.1.2.1.2.2.1.2", str)

        def interface_for_local_addr(local_addr: Optional[str]) -> Optional[str]:
            if not local_addr:
                return None
            ifindex = ip_to_ifindex.get(str(local_addr))
            if not ifindex:
                return None
            return if_name_map.get(str(ifindex)) or if_descr_map.get(str(ifindex)) or f"if{ifindex}"

        def build_rows(context_name: Optional[str] = None) -> List[Dict[str, Any]]:
            state_map: Dict[str, int] = {}
            for oid in bgp_state_oids:
                state_map.update(self._walk_indexed_map(device, str(oid), int, context_name=context_name))
            local_addr_map = self._walk_indexed_map(device, "1.3.6.1.2.1.15.3.1.5", str, context_name=context_name)
            remote_as_map = self._walk_indexed_map(device, "1.3.6.1.2.1.15.3.1.9", int, context_name=context_name)
            rows: List[Dict[str, Any]] = []
            for index in sorted(set(state_map) | set(local_addr_map) | set(remote_as_map), key=str):
                peer = self._extract_peer_from_index(index)
                state = state_map.get(index)
                local_addr = local_addr_map.get(index)
                interface_name = interface_for_local_addr(str(local_addr)) if local_addr else None
                rows.append({
                    "protocol": "bgp",
                    "peer": peer,
                    "neighbor": peer,
                    "remote_as": remote_as_map.get(index),
                    "local_addr": str(local_addr) if local_addr else None,
                    "local_address": str(local_addr) if local_addr else None,
                    "interface": interface_name,
                    "instance": context_name,
                    "state": bgp_state_text.get(state, str(state)) if state is not None else "-",
                    "status": "up" if state == 6 else "down" if state is not None else "unknown",
                    "source": "snmp",
                })
            return rows

        rows = build_rows(None)
        bgp_contexts = private_oids.get("bgp_contexts") or []
        if isinstance(bgp_contexts, str):
            bgp_contexts = [bgp_contexts]
        context_rows: List[Dict[str, Any]] = []
        for context_name in bgp_contexts:
            context = str(context_name or "").strip()
            if context:
                context_rows.extend(build_rows(context))
        if private_oids.get("prefer_bgp_contexts") and context_rows:
            return context_rows
        return rows + context_rows

    def collect_protocol_status(self, device: Any) -> Dict[str, Any]:
        """采集 BGP/OSPF/BFD 协议状态"""
        now = datetime.utcnow()
        points: List[Dict[str, Any]] = []

        private_oids = self._get_private_oid_config(device)
        bgp_state_map: Dict[str, int] = {}
        bgp_state_oids = private_oids.get("bgp_state_oids") or ["1.3.6.1.2.1.15.3.1.2"]
        if not isinstance(bgp_state_oids, list):
            bgp_state_oids = [bgp_state_oids]
        for oid in bgp_state_oids:
            bgp_state_map.update(self._walk_indexed_map(device, str(oid), int))
        bgp_local_addr_map = self._walk_indexed_map(device, "1.3.6.1.2.1.15.3.1.5", str)
        bgp_remote_as_map = self._walk_indexed_map(device, "1.3.6.1.2.1.15.3.1.9", int)
        bgp_context_maps: Dict[Tuple[str, str], int] = {}
        bgp_context_local_addr_maps: Dict[Tuple[str, str], str] = {}
        bgp_context_remote_as_maps: Dict[Tuple[str, str], int] = {}
        bgp_contexts = private_oids.get("bgp_contexts") or []
        if isinstance(bgp_contexts, str):
            bgp_contexts = [bgp_contexts]
        for context_name in bgp_contexts:
            context = str(context_name or "").strip()
            if not context:
                continue
            for oid in bgp_state_oids:
                for index, state in self._walk_indexed_map(device, str(oid), int, context_name=context).items():
                    bgp_context_maps[(context, index)] = state
            for index, local_addr in self._walk_indexed_map(device, "1.3.6.1.2.1.15.3.1.5", str, context_name=context).items():
                bgp_context_local_addr_maps[(context, index)] = str(local_addr)
            for index, remote_as in self._walk_indexed_map(device, "1.3.6.1.2.1.15.3.1.9", int, context_name=context).items():
                bgp_context_remote_as_maps[(context, index)] = remote_as
        ip_to_ifindex = self._walk_indexed_map(device, "1.3.6.1.2.1.4.20.1.2", str)
        if_name_map = self._walk_indexed_map(device, "1.3.6.1.2.1.31.1.1.1.1", str)
        if_descr_map = self._walk_indexed_map(device, "1.3.6.1.2.1.2.2.1.2", str)

        def interface_for_local_addr(local_addr: Optional[str]) -> Optional[str]:
            if not local_addr:
                return None
            ifindex = ip_to_ifindex.get(str(local_addr))
            if not ifindex:
                return None
            return if_name_map.get(str(ifindex)) or if_descr_map.get(str(ifindex)) or f"if{ifindex}"

        bgp_state_text = {
            1: "idle",
            2: "connect",
            3: "active",
            4: "opensent",
            5: "openconfirm",
            6: "established",
        }
        emit_default_bgp = not (private_oids.get("prefer_bgp_contexts") and bgp_context_maps)
        if emit_default_bgp:
            for index, state in bgp_state_map.items():
                peer = self._extract_peer_from_index(index)
                points.append({
                    "measurement": "protocol_status",
                    "tags": {
                        "device_id": str(device.id),
                        "device_name": device.name,
                        "protocol": "bgp",
                        "peer": peer,
                        "local_addr": str(bgp_local_addr_map.get(index) or ""),
                        "remote_as": str(bgp_remote_as_map.get(index) or ""),
                        "interface": str(interface_for_local_addr(bgp_local_addr_map.get(index)) or ""),
                        "state_text": bgp_state_text.get(state, str(state)),
                    },
                    "fields": {
                        "state_value": float(state),
                        "state_up": 1.0 if state == 6 else 0.0,
                    },
                    "timestamp": now,
                })
        for (context_name, index), state in bgp_context_maps.items():
            peer = self._extract_peer_from_index(index)
            local_addr = bgp_context_local_addr_maps.get((context_name, index))
            points.append({
                "measurement": "protocol_status",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "protocol": "bgp",
                    "peer": peer,
                    "instance": context_name,
                    "local_addr": str(local_addr or ""),
                    "remote_as": str(bgp_context_remote_as_maps.get((context_name, index)) or ""),
                    "interface": str(interface_for_local_addr(local_addr) or ""),
                    "state_text": bgp_state_text.get(state, str(state)),
                },
                "fields": {
                    "state_value": float(state),
                    "state_up": 1.0 if state == 6 else 0.0,
                },
                "timestamp": now,
            })

        ospf_state_map = self._walk_indexed_map(device, "1.3.6.1.2.1.14.10.1.6", int)
        ospf_state_text = {
            1: "down",
            2: "attempt",
            3: "init",
            4: "twoWay",
            5: "exchangeStart",
            6: "exchange",
            7: "loading",
            8: "full",
        }
        for index, state in ospf_state_map.items():
            peer = self._extract_peer_from_index(index, address_less_tail=True)
            points.append({
                "measurement": "protocol_status",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "protocol": "ospf",
                    "peer": peer,
                    "state_text": ospf_state_text.get(state, str(state)),
                },
                "fields": {
                    "state_value": float(state),
                    "state_up": 1.0 if state == 8 else 0.0,
                },
                "timestamp": now,
            })

        bfd_oid = private_oids.get("bfd_session_state_oid")
        bfd_up_values = private_oids.get("bfd_up_values") or [1, 2, "up", "Up", "UP", "established", "Established"]
        if bfd_oid:
            for index, value in self._walk_indexed_map(device, str(bfd_oid)).items():
                peer = self._extract_peer_from_index(index)
                state_up = 1.0 if value in bfd_up_values else 0.0
                points.append({
                    "measurement": "protocol_status",
                    "tags": {
                        "device_id": str(device.id),
                        "device_name": device.name,
                        "protocol": "bfd",
                        "peer": peer,
                        "state_text": str(value),
                    },
                    "fields": {
                        "state_value": 1.0 if state_up else 0.0,
                        "state_up": state_up,
                    },
                    "timestamp": now,
                })

        protocol_summary = {
            "bgp": {"total": 0, "up": 0, "down": 0},
            "ospf": {"total": 0, "up": 0, "down": 0},
        }
        protocol_peer_states: Dict[Tuple[str, str], bool] = {}
        for point in points:
            protocol = str(point.get("tags", {}).get("protocol") or "").lower()
            if protocol not in protocol_summary:
                continue
            peer = str(point.get("tags", {}).get("peer") or "").strip()
            if not peer:
                continue
            is_up = float(point.get("fields", {}).get("state_up") or 0) >= 1
            key = (protocol, peer)
            # 同一个 peer 可能因为不同 BGP OID / 地址族 / context 被采到多次。
            # 总览要按真实邻居数统计，而不是按采样行数统计；如果同一 peer 有
            # 任一 down 状态，以 down 优先，避免掩盖异常。
            protocol_peer_states[key] = bool(protocol_peer_states.get(key, True) and is_up)

        for (protocol, _peer), is_up in protocol_peer_states.items():
            protocol_summary[protocol]["total"] += 1
            protocol_summary[protocol]["up" if is_up else "down"] += 1

        if points:
            influx_client.write_points(points, sync=False)
        return {"points_written": len(points), "protocols": protocol_summary}

    def collect_optical_monitoring(self, device: Any) -> Dict[str, Any]:
        """Collect and cache normalized optical module information.

        H3C uses HH3C-TRANSCEIVER-INFO-MIB, while Ruijie uses selected columns
        from RUIJIE-FIBER-MIB. Other vendors retain configurable RX/TX OIDs.
        """
        private_oids = self._get_private_oid_config(device)
        ruijie_table_oid = private_oids.get("optical_ruijie_fiber_entry_oid")
        if ruijie_table_oid:
            return self._collect_ruijie_optical_monitoring(device, str(ruijie_table_oid))
        table_oid = private_oids.get("optical_info_table_oid")
        if table_oid:
            return self._collect_h3c_optical_monitoring(device, str(table_oid))
        if private_oids.get("optical_entity_sensor"):
            return self._collect_cisco_entity_sensor_optical_monitoring(device, private_oids)

        rx_oid = private_oids.get("optical_rx_oid")
        tx_oid = private_oids.get("optical_tx_oid")
        if not rx_oid and not tx_oid:
            return {"points_written": 0}

        scale = float(private_oids.get("optical_power_scale", 1.0) or 1.0)
        if_name_map = self._walk_indexed_map(device, "1.3.6.1.2.1.31.1.1.1.1", str)
        rx_map = self._walk_indexed_map(device, str(rx_oid)) if rx_oid else {}
        tx_map = self._walk_indexed_map(device, str(tx_oid)) if tx_oid else {}
        now = datetime.utcnow()
        points: List[Dict[str, Any]] = []
        indexes = sorted(set(rx_map) | set(tx_map), key=str)

        for index in indexes:
            interface_index = index.split(".")[-1]
            interface_name = if_name_map.get(interface_index, f"if{interface_index}")
            rx_power = self._normalize_numeric(rx_map.get(index), scale)
            tx_power = self._normalize_numeric(tx_map.get(index), scale)
            if rx_power is None and tx_power is None:
                continue
            points.append({
                "measurement": "optical_monitoring",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "interface_index": str(interface_index),
                    "interface_name": interface_name,
                },
                "fields": {
                    "rx_power": rx_power,
                    "tx_power": tx_power,
                },
                "timestamp": now,
            })

        if points:
            influx_client.write_points(points, sync=False)
        return {"points_written": len(points)}

    def _collect_cisco_entity_sensor_optical_monitoring(self, device: Any, private_oids: Dict[str, Any]) -> Dict[str, Any]:
        """Collect Nexus optical DOM values from ENTITY-SENSOR-MIB when available."""
        value_oid = private_oids.get("entity_sensor_value_oid")
        if not value_oid:
            return {"points_written": 0}

        if_name_map = self._walk_indexed_map(device, "1.3.6.1.2.1.31.1.1.1.1", str)
        name_map = self._walk_indexed_map(device, str(private_oids.get("entity_name_oid")), str) if private_oids.get("entity_name_oid") else {}
        descr_map = self._walk_indexed_map(device, str(private_oids.get("entity_descr_oid")), str) if private_oids.get("entity_descr_oid") else {}
        type_map = self._walk_indexed_map(device, str(private_oids.get("entity_sensor_type_oid")), int) if private_oids.get("entity_sensor_type_oid") else {}
        scale_map = self._walk_indexed_map(device, str(private_oids.get("entity_sensor_scale_oid")), int) if private_oids.get("entity_sensor_scale_oid") else {}
        precision_map = self._walk_indexed_map(device, str(private_oids.get("entity_sensor_precision_oid")), int) if private_oids.get("entity_sensor_precision_oid") else {}
        status_map = self._walk_indexed_map(device, str(private_oids.get("entity_sensor_status_oid")), int) if private_oids.get("entity_sensor_status_oid") else {}
        value_map = self._walk_indexed_map(device, str(value_oid), float)

        grouped: Dict[str, Dict[str, Any]] = {}
        for index, raw_value in value_map.items():
            sensor_name = name_map.get(index) or descr_map.get(index) or str(index)
            resolved_interface = self._extract_cisco_interface_from_sensor_name(str(sensor_name), if_name_map)
            if not resolved_interface:
                continue
            if status_map.get(index) not in {None, 1}:
                continue
            converted = self._convert_entity_sensor_value(raw_value, scale_map.get(index), precision_map.get(index))
            if converted is None:
                continue
            interface_index, interface_name = resolved_interface
            item = grouped.setdefault(interface_name, {
                "interface_index": interface_index,
                "interface_name": interface_name,
                "rx_values": [],
                "tx_values": [],
                "temperature_values": [],
                "voltage_values": [],
            })
            sensor_type = type_map.get(index)
            sensor_text = str(sensor_name or "").lower()
            if sensor_type == 14:  # dBm
                if re.search(r"\b(rx|receive|received|input)\b", sensor_text):
                    item["rx_values"].append(converted)
                elif re.search(r"\b(tx|transmit|transmitted|output)\b", sensor_text):
                    item["tx_values"].append(converted)
            elif sensor_type == 8:
                item["temperature_values"].append(converted)
            elif sensor_type == 4:
                item["voltage_values"].append(converted)

        now = datetime.utcnow()
        points: List[Dict[str, Any]] = []
        for item in grouped.values():
            fields: Dict[str, Any] = {}
            if item["rx_values"]:
                fields["rx_power"] = round(sum(item["rx_values"]) / len(item["rx_values"]), 2)
                fields["rx_power_min"] = round(min(item["rx_values"]), 2)
                fields["rx_power_max"] = round(max(item["rx_values"]), 2)
            if item["tx_values"]:
                fields["tx_power"] = round(sum(item["tx_values"]) / len(item["tx_values"]), 2)
                fields["tx_power_min"] = round(min(item["tx_values"]), 2)
                fields["tx_power_max"] = round(max(item["tx_values"]), 2)
            if item["temperature_values"]:
                fields["temperature"] = round(max(item["temperature_values"]), 2)
            if item["voltage_values"]:
                fields["voltage"] = round(sum(item["voltage_values"]) / len(item["voltage_values"]), 4)
            if not fields:
                continue
            points.append({
                "measurement": "optical_monitoring",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "interface_index": str(item["interface_index"]),
                    "interface_name": item["interface_name"],
                },
                "fields": fields,
                "timestamp": now,
            })

        if points:
            influx_client.write_points(points, sync=False)
        return {"points_written": len(points)}

    def _collect_ruijie_optical_monitoring(self, device: Any, table_oid: str) -> Dict[str, Any]:
        """Collect Ruijie optical modules from RUIJIE-FIBER-MIB.

        Power values and power thresholds are 0.01 dBm, voltage is mV and
        bias current is uA. ``-10000`` is the vendor-defined invalid value.
        Selected columns are walked concurrently because a full 164-column
        table walk is slow enough to time out on RGOS devices.
        """
        column_names = {
            2: "interface_name",
            3: "transceiver_type_code",
            5: "wavelength_nm",
            15: "ddm_supported",
            16: "serial_number",
            17: "temperature_c",
            19: "voltage_mv",
            21: "bias_current_ua",
            76: "rx_power_dbm",
            77: "channel_1_rx_power_dbm",
            78: "channel_2_rx_power_dbm",
            79: "channel_3_rx_power_dbm",
            80: "channel_4_rx_power_dbm",
            81: "tx_power_dbm",
            82: "channel_1_tx_power_dbm",
            83: "channel_2_tx_power_dbm",
            84: "channel_3_tx_power_dbm",
            85: "channel_4_tx_power_dbm",
            86: "wavelength_exact",
            89: "speed_mbps",
            91: "rx_low_warning_dbm",
            92: "rx_high_warning_dbm",
            93: "rx_low_alarm_dbm",
            94: "rx_high_alarm_dbm",
            95: "tx_low_warning_dbm",
            96: "tx_high_warning_dbm",
            97: "tx_low_alarm_dbm",
            98: "tx_high_alarm_dbm",
            143: "channel_5_rx_power_dbm",
            144: "channel_6_rx_power_dbm",
            145: "channel_7_rx_power_dbm",
            146: "channel_8_rx_power_dbm",
            147: "channel_5_tx_power_dbm",
            148: "channel_6_tx_power_dbm",
            149: "channel_7_tx_power_dbm",
            150: "channel_8_tx_power_dbm",
            153: "temperature_low_warning_c",
            154: "temperature_high_warning_c",
            155: "temperature_low_alarm_c",
            156: "temperature_high_alarm_c",
            157: "voltage_low_warning_mv",
            158: "voltage_high_warning_mv",
            159: "voltage_low_alarm_mv",
            160: "voltage_high_alarm_mv",
        }
        transceiver_types = {
            1: "Unknown",
            36: "100G QSFP28 DAC",
            37: "100G LR4 QSFP28",
            38: "100G SR4 QSFP28",
            39: "100G ER4 QSFP28",
            62: "100G ZR QSFP28",
            63: "100G CWDM4 QSFP28",
            64: "10G SFP+ Passive DAC",
            65: "10G SFP+ Active DAC",
            78: "200G QSFP56 Passive DAC",
            79: "200G QSFP56 Active DAC",
            80: "400G SR8 QSFP-DD",
            81: "400G DR4 QSFP-DD",
            82: "400G FR4 QSFP-DD",
            83: "400G QSFP-DD Passive DAC",
            84: "400G QSFP-DD Active Cable",
            85: "400G LR8 QSFP-DD",
            86: "200G QSFP56 Loopback",
            87: "400G QSFP-DD Active DAC",
            88: "400G QSFP-DD Loopback",
            95: "400G ZR QSFP-DD",
            104: "400G ZR+ QSFP-DD",
            105: "400G LR4 QSFP-DD",
        }

        def walk_column(column: int) -> Tuple[int, Dict[str, Any]]:
            return column, self._walk_indexed_map(device, f"{table_oid}.{column}")

        column_maps: Dict[int, Dict[str, Any]] = {}
        with ThreadPoolExecutor(max_workers=12) as executor:
            for column, values in executor.map(walk_column, column_names):
                column_maps[column] = values

        indexes = sorted(
            {index for values in column_maps.values() for index in values},
            key=lambda value: tuple(int(part) if part.isdigit() else 0 for part in str(value).split(".")),
        )
        now = datetime.utcnow()
        items: List[Dict[str, Any]] = []
        points: List[Dict[str, Any]] = []

        def number(column: int, index: str, scale: float = 1.0) -> Optional[float]:
            value = self._normalize_numeric(column_maps.get(column, {}).get(index))
            if value is None or value in {-10000, 2147483647, -2147483648}:
                return None
            return round(value * scale, 4)

        for index in indexes:
            interface_name = str(column_maps.get(2, {}).get(index) or f"if{index}").strip().strip('"')
            serial_number = str(column_maps.get(16, {}).get(index) or "").strip().strip('"')
            type_code_value = number(3, index)
            type_code = int(type_code_value) if type_code_value is not None else 1
            ddm_supported = number(15, index) == 1
            if not serial_number and type_code in {0, 1}:
                continue

            channels: List[Dict[str, Any]] = []
            for channel in range(1, 9):
                rx_column = 76 + channel if channel <= 4 else 138 + channel
                tx_column = 81 + channel if channel <= 4 else 142 + channel
                rx_power = number(rx_column, index, 0.01)
                tx_power = number(tx_column, index, 0.01)
                if rx_power is None and tx_power is None:
                    continue
                channels.append({
                    "channel": channel,
                    "rx_power_dbm": rx_power,
                    "tx_power_dbm": tx_power,
                })

            channel_rx = [row["rx_power_dbm"] for row in channels if row.get("rx_power_dbm") is not None]
            channel_tx = [row["tx_power_dbm"] for row in channels if row.get("tx_power_dbm") is not None]
            module_rx = number(76, index, 0.01)
            module_tx = number(81, index, 0.01)
            rx_power = min(channel_rx) if channel_rx else module_rx
            tx_power = min(channel_tx) if channel_tx else module_tx
            # Copper/active-cable/loopback modules may expose placeholder DOM
            # values (commonly -40 dBm). Keep their inventory/temperature, but
            # do not present those placeholders as real optical power.
            if type_code in {36, 64, 65, 78, 79, 83, 84, 86, 87, 88, 89, 90, 91, 92, 98}:
                channels = []
                rx_power = None
                tx_power = None
            wavelength_exact = str(column_maps.get(86, {}).get(index) or "").strip().strip('"')
            wavelength = self._normalize_numeric(wavelength_exact) if wavelength_exact else number(5, index)

            item = {
                "device_id": int(device.id),
                "device_name": device.name,
                "device_ip": device.ip_address,
                "device_vendor": device.vendor,
                "interface_index": str(index),
                "interface_name": interface_name,
                "source": "snmp",
                "collected_at": now.isoformat() + "Z",
                "serial_number": serial_number or None,
                "transceiver_type": transceiver_types.get(type_code, f"Ruijie transceiver type {type_code}"),
                "transceiver_type_code": type_code,
                "ddm_supported": ddm_supported,
                "wavelength_nm": wavelength,
                "speed_mbps": number(89, index),
                "temperature_c": number(17, index) if ddm_supported else None,
                "voltage_v": number(19, index, 0.001) if ddm_supported else None,
                "bias_current_ma": number(21, index, 0.001) if ddm_supported else None,
                "rx_power_dbm": rx_power,
                "tx_power_dbm": tx_power,
                "rx_low_warning_dbm": number(91, index, 0.01),
                "rx_high_warning_dbm": number(92, index, 0.01),
                "rx_low_alarm_dbm": number(93, index, 0.01),
                "rx_high_alarm_dbm": number(94, index, 0.01),
                "tx_low_warning_dbm": number(95, index, 0.01),
                "tx_high_warning_dbm": number(96, index, 0.01),
                "tx_low_alarm_dbm": number(97, index, 0.01),
                "tx_high_alarm_dbm": number(98, index, 0.01),
                "temperature_low_warning_c": number(153, index),
                "temperature_high_warning_c": number(154, index),
                "temperature_low_alarm_c": number(155, index),
                "temperature_high_alarm_c": number(156, index),
                "voltage_low_warning_v": number(157, index, 0.001),
                "voltage_high_warning_v": number(158, index, 0.001),
                "voltage_low_alarm_v": number(159, index, 0.001),
                "voltage_high_alarm_v": number(160, index, 0.001),
                "channels": channels,
            }
            items.append(item)
            fields = {
                "rx_power": rx_power,
                "tx_power": tx_power,
                "temperature": item.get("temperature_c"),
                "voltage": item.get("voltage_v"),
                "bias_current_ma": item.get("bias_current_ma"),
                "wavelength_nm": item.get("wavelength_nm"),
            }
            fields = {key: value for key, value in fields.items() if value is not None}
            if fields:
                points.append({
                    "measurement": "optical_monitoring",
                    "tags": {
                        "device_id": str(device.id),
                        "device_name": device.name,
                        "device_ip": device.ip_address,
                        "vendor": device.vendor or "Ruijie",
                        "interface_index": str(index),
                        "interface_name": interface_name,
                        "source": "snmp",
                        "scope": "module",
                    },
                    "fields": fields,
                    "timestamp": now,
                })

        if points:
            influx_client.write_points(points, sync=False)
        redis_client.setex(
            f"monitor:cache:optical_modules:{device.id}",
            7 * 24 * 60 * 60,
            json.dumps({"items": items, "collected_at": now.isoformat() + "Z", "source": "snmp"}, ensure_ascii=False),
        )
        return {"points_written": len(points), "modules": len(items)}

    def _collect_h3c_optical_monitoring(self, device: Any, table_oid: str) -> Dict[str, Any]:
        column_names = {
            1: "hardware_type",
            2: "transceiver_type",
            3: "wavelength_nm",
            4: "vendor_name",
            5: "serial_number",
            7: "distance_m",
            9: "tx_power_dbm",
            12: "rx_power_dbm",
            15: "temperature_c",
            16: "voltage_v",
            17: "bias_current_ma",
            60: "manufacturer",
            61: "manufactured_at",
            64: "tx_power_dbm",
            65: "rx_power_dbm",
        }
        rows: Dict[str, Dict[str, Any]] = {}
        base = table_oid.lstrip(".") + "."
        for item_oid, value in self.snmp_walk(device, table_oid):
            normalized_oid = str(item_oid).lstrip(".")
            if not normalized_oid.startswith(base):
                continue
            suffix = normalized_oid[len(base):].split(".")
            if len(suffix) < 2:
                continue
            try:
                column = int(suffix[0])
            except ValueError:
                continue
            field = column_names.get(column)
            if not field:
                continue
            interface_index = ".".join(suffix[1:])
            row = rows.setdefault(interface_index, {"interface_index": interface_index})
            if field in {"hardware_type", "transceiver_type", "vendor_name", "serial_number", "manufacturer", "manufactured_at"}:
                row[field] = str(value).strip().strip('"')
                continue
            number = self._normalize_numeric(value)
            if number is None or number >= 2147483647 or number <= -2147483648:
                continue
            if field in {"rx_power_dbm", "tx_power_dbm", "voltage_v", "bias_current_ma"}:
                number = round(number * 0.01, 4)
            # Prefer total optical power (.64/.65) over unsupported aggregate
            # values (.9/.12) when both are present on multi-lane modules.
            if field not in row or column in {64, 65}:
                row[field] = number

        if_name_map = self._walk_indexed_map(device, "1.3.6.1.2.1.31.1.1.1.1", str)
        now = datetime.utcnow()
        items: List[Dict[str, Any]] = []
        points: List[Dict[str, Any]] = []
        for interface_index, row in rows.items():
            interface_name = if_name_map.get(interface_index, f"if{interface_index}")
            if not any(row.get(key) not in (None, "") for key in ("serial_number", "transceiver_type", "rx_power_dbm", "tx_power_dbm")):
                continue
            item = {
                "device_id": int(device.id),
                "device_name": device.name,
                "device_ip": device.ip_address,
                "device_vendor": device.vendor,
                "interface_index": interface_index,
                "interface_name": interface_name,
                "source": "snmp",
                "collected_at": now.isoformat() + "Z",
                **row,
            }
            items.append(item)
            fields = {
                name: item.get(name) for name in (
                    "rx_power_dbm", "tx_power_dbm", "temperature_c", "voltage_v",
                    "bias_current_ma", "wavelength_nm", "distance_m",
                ) if item.get(name) is not None
            }
            if fields:
                points.append({
                    "measurement": "optical_monitoring",
                    "tags": {
                        "device_id": str(device.id),
                        "device_name": device.name,
                        "device_ip": device.ip_address,
                        "vendor": device.vendor or "",
                        "interface_index": interface_index,
                        "interface_name": interface_name,
                        "source": "snmp",
                        "scope": "module",
                    },
                    "fields": {
                        "rx_power": fields.get("rx_power_dbm"),
                        "tx_power": fields.get("tx_power_dbm"),
                        "temperature": fields.get("temperature_c"),
                        "voltage": fields.get("voltage_v"),
                        "bias_current_ma": fields.get("bias_current_ma"),
                        "wavelength_nm": fields.get("wavelength_nm"),
                        "distance_m": fields.get("distance_m"),
                    },
                    "timestamp": now,
                })
        if points:
            influx_client.write_points(points, sync=False)
        if items:
            redis_client.setex(
                f"monitor:cache:optical_modules:{device.id}",
                7 * 24 * 60 * 60,
                json.dumps({"items": items, "collected_at": now.isoformat() + "Z", "source": "snmp"}, ensure_ascii=False),
            )
        return {"points_written": len(points), "modules": len(items)}

    def _interface_snapshot_cache_key(self, device_id: int, interface_index: int) -> str:
        return f"interface_monitoring:last:{device_id}:{interface_index}"

    def _interface_initialized_cache_key(self, device_id: int, interface_index: int) -> str:
        return f"interface_monitoring:initialized:{device_id}:{interface_index}"

    def collect_interface_health(self, device: Any) -> Dict[str, Any]:
        """低频采集接口错误/丢弃计数，不与高优先级流量轮询争抢速率字段。"""
        walk_jobs = {
            "if_name_map": ("1.3.6.1.2.1.31.1.1.1.1", str),
            "in_discards_map": ("1.3.6.1.2.1.2.2.1.13", int),
            "out_discards_map": ("1.3.6.1.2.1.2.2.1.19", int),
            "crc_errors_map": ("1.3.6.1.2.1.10.7.2.1.3", int),
            "in_errors_map": ("1.3.6.1.2.1.2.2.1.14", int),
            "out_errors_map": ("1.3.6.1.2.1.2.2.1.20", int),
        }
        with ThreadPoolExecutor(max_workers=len(walk_jobs)) as executor:
            futures = {
                name: executor.submit(self._walk_indexed_map, device, oid, cast)
                for name, (oid, cast) in walk_jobs.items()
            }
            walk_results = {name: future.result() for name, future in futures.items()}

        indexes = sorted(
            set().union(*(set(values.keys()) for values in walk_results.values())),
            key=lambda item: int(item) if str(item).isdigit() else str(item),
        )
        now = datetime.utcnow()
        now_ts = time.time()
        points = []

        def delta(current: Any, previous: Any) -> Optional[float]:
            if current is None or previous is None:
                return None
            value = float(current) - float(previous)
            return round(value, 2) if value >= 0 else None

        for index in indexes:
            current = {
                "in_discards": walk_results["in_discards_map"].get(index),
                "out_discards": walk_results["out_discards_map"].get(index),
                "crc_errors": walk_results["crc_errors_map"].get(index),
                "in_errors": walk_results["in_errors_map"].get(index),
                "out_errors": walk_results["out_errors_map"].get(index),
            }
            if all(value is None for value in current.values()):
                continue
            cache_key = f"interface_health:last:{device.id}:{index}"
            previous_raw = redis_client.get(cache_key)
            redis_client.setex(cache_key, 172800, json.dumps({"timestamp": now_ts, **current}))
            if not previous_raw:
                continue
            try:
                previous = json.loads(previous_raw)
            except Exception:
                continue

            fields = {
                **{key: float(value) for key, value in current.items() if value is not None},
                "in_discards_delta": delta(current["in_discards"], previous.get("in_discards")),
                "out_discards_delta": delta(current["out_discards"], previous.get("out_discards")),
                "crc_errors_delta": delta(current["crc_errors"], previous.get("crc_errors")),
                "in_errors_delta": delta(current["in_errors"], previous.get("in_errors")),
                "out_errors_delta": delta(current["out_errors"], previous.get("out_errors")),
                "sample_seconds": round(max(now_ts - float(previous.get("timestamp") or now_ts), 0.0), 2),
            }
            fields = {key: value for key, value in fields.items() if value is not None}
            points.append({
                "measurement": "interface_monitoring",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "interface_index": str(index),
                    "interface_name": walk_results["if_name_map"].get(index) or f"if{index}",
                    "source": "snmp_roce_health",
                },
                "fields": fields,
                "timestamp": now,
            })

        if points:
            redis_client.setex(
                f"monitor:alert_interface_quality:{device.id}",
                10 * 60,
                json.dumps({
                    "collected_at": datetime.utcnow().isoformat() + "Z",
                    "interfaces": [
                        {
                            "index": (point.get("tags") or {}).get("interface_index"),
                            "name": (point.get("tags") or {}).get("interface_name"),
                            **(point.get("fields") or {}),
                        }
                        for point in points
                    ],
                }, ensure_ascii=False),
            )
            influx_client.write_points(points, sync=True)
        return {
            "device_id": device.id,
            "interfaces_total": len(indexes),
            "points_written": len(points),
            "baseline": not bool(points),
        }

    def collect_interface_monitoring(
        self,
        device: Any,
        suppress_rate_interface_names: Optional[set[str]] = None,
        realtime: bool = False,
    ) -> Dict[str, Any]:
        """批量采集接口历史监控数据，并基于上一次快照计算速率"""
        suppress_rate_interface_names = suppress_rate_interface_names or set()
        private_oids = self._get_private_oid_config(device)
        skip_32bit_counters = bool(private_oids.get("skip_32bit_interface_counters"))
        walk_jobs = {
            "if_name_map": ("1.3.6.1.2.1.31.1.1.1.1", str),
            "if_descr_map": ("1.3.6.1.2.1.2.2.1.2", str),
            "in_octets_64_map": ("1.3.6.1.2.1.31.1.1.1.6", int),
            "out_octets_64_map": ("1.3.6.1.2.1.31.1.1.1.10", int),
            "high_speed_map": ("1.3.6.1.2.1.31.1.1.1.15", int),
            "speed_map": ("1.3.6.1.2.1.2.2.1.5", int),
            "admin_status_map": ("1.3.6.1.2.1.2.2.1.7", int),
            "oper_status_map": ("1.3.6.1.2.1.2.2.1.8", int),
        }
        if not realtime:
            # 这些 OID 对“端口实时速率”不是必需项，而且部分高端口设备在并发 walk
            # 这些表时会偶发超时，拖慢整轮 60 秒采集。实时队列只保留带宽/状态
            # 必需 OID，错误包/丢弃/广播/队列长度由低频全量采集补齐。
            walk_jobs.update({
                "in_discards_map": ("1.3.6.1.2.1.2.2.1.13", int),
                "out_discards_map": ("1.3.6.1.2.1.2.2.1.19", int),
                "crc_errors_map": ("1.3.6.1.2.1.10.7.2.1.3", int),
                "in_errors_map": ("1.3.6.1.2.1.2.2.1.14", int),
                "out_errors_map": ("1.3.6.1.2.1.2.2.1.20", int),
                "queue_length_map": ("1.3.6.1.2.1.2.2.1.21", int),
                "in_broadcast_map": ("1.3.6.1.2.1.31.1.1.1.3", int),
                "out_broadcast_map": ("1.3.6.1.2.1.31.1.1.1.5", int),
            })

        if not realtime and not skip_32bit_counters:
            walk_jobs.update({
                "in_octets_32_map": ("1.3.6.1.2.1.2.2.1.10", int),
                "out_octets_32_map": ("1.3.6.1.2.1.2.2.1.16", int),
            })

        max_walk_workers = min(len(walk_jobs), 6 if realtime else len(walk_jobs))
        with ThreadPoolExecutor(max_workers=max_walk_workers) as executor:
            futures = {
                name: executor.submit(self._walk_indexed_map, device, oid, cast)
                for name, (oid, cast) in walk_jobs.items()
            }
            walk_results = {name: future.result() for name, future in futures.items()}
        for optional_name in (
            "in_discards_map",
            "out_discards_map",
            "crc_errors_map",
            "in_errors_map",
            "out_errors_map",
            "queue_length_map",
            "in_broadcast_map",
            "out_broadcast_map",
            "in_octets_32_map",
            "out_octets_32_map",
        ):
            walk_results.setdefault(optional_name, {})

        indexes = sorted(
            set(walk_results["if_name_map"]) | set(walk_results["if_descr_map"]),
            key=lambda item: int(item) if str(item).isdigit() else item,
        )
        status_map = {
            1: "up",
            2: "down",
            3: "testing",
            4: "unknown",
            5: "dormant",
            6: "notPresent",
            7: "lowerLayerDown",
        }

        def valid_status_pair(index: Any) -> tuple[Optional[str], Optional[str]]:
            """Return statuses only when both SNMP table values are trustworthy."""
            admin_code = walk_results["admin_status_map"].get(index)
            oper_code = walk_results["oper_status_map"].get(index)
            # A timed-out walk produces no value. RFC2863 value 4 is explicitly
            # unknown. Neither condition may be converted into a physical Down.
            if admin_code not in status_map or oper_code not in status_map or oper_code == 4:
                return None, None
            return status_map[admin_code], status_map[oper_code]
        now = datetime.utcnow()
        now_ts = time.time()
        points = []
        monitored_count = 0
        in_octets_32_map = walk_results.get("in_octets_32_map", {})
        out_octets_32_map = walk_results.get("out_octets_32_map", {})

        for index in indexes:
            name = walk_results["if_name_map"].get(index) or walk_results["if_descr_map"].get(index) or f"if{index}"
            current_in = walk_results["in_octets_64_map"].get(index)
            current_out = walk_results["out_octets_64_map"].get(index)
            if current_in is None:
                current_in = in_octets_32_map.get(index)
            if current_out is None:
                current_out = out_octets_32_map.get(index)

            high_speed = walk_results["high_speed_map"].get(index)
            speed = walk_results["speed_map"].get(index)
            if high_speed and high_speed > 0:
                speed_bps = high_speed * 1_000_000
            elif speed and speed > 0:
                speed_bps = speed
            else:
                speed_bps = None

            cache_key = self._interface_snapshot_cache_key(device.id, int(index))
            initialized_key = self._interface_initialized_cache_key(device.id, int(index))
            previous_raw = redis_client.get(cache_key)
            redis_client.setex(
                cache_key,
                172800,
                json.dumps({
                    "timestamp": now_ts,
                    "in_octets": current_in,
                    "out_octets": current_out,
                    "in_discards": walk_results["in_discards_map"].get(index),
                    "out_discards": walk_results["out_discards_map"].get(index),
                    "crc_errors": walk_results["crc_errors_map"].get(index),
                    "in_errors": walk_results["in_errors_map"].get(index),
                    "out_errors": walk_results["out_errors_map"].get(index),
                    "in_broadcast": walk_results["in_broadcast_map"].get(index),
                    "out_broadcast": walk_results["out_broadcast_map"].get(index),
                }),
            )

            if not previous_raw:
                first_seen = bool(redis_client.set(initialized_key, "1", ex=2592000, nx=True))
                if not first_seen:
                    continue

                admin_status, oper_status = valid_status_pair(index)
                # 接口流量图以原始端口速率为最高优先级。线路/专线统计如果需要去重，
                # 应在上层聚合查询里处理，不能在底层接口历史中把端口速率写成空值，
                # 否则接口查询会出现“有采集行但无流量值”。
                in_bps = 0.0
                out_bps = 0.0
                monitored_count += 1
                fields = {
                    "in_octets": int(current_in) if current_in is not None else 0,
                    "out_octets": int(current_out) if current_out is not None else 0,
                    "in_bps": in_bps,
                    "out_bps": out_bps,
                    "speed_bps": float(speed_bps) if speed_bps is not None else None,
                    "in_utilization_percent": round((in_bps / speed_bps) * 100, 2) if speed_bps and in_bps is not None else None,
                    "out_utilization_percent": round((out_bps / speed_bps) * 100, 2) if speed_bps and out_bps is not None else None,
                    "in_discards": float(walk_results["in_discards_map"].get(index) or 0),
                    "out_discards": float(walk_results["out_discards_map"].get(index) or 0),
                    "crc_errors": float(walk_results["crc_errors_map"].get(index) or 0),
                    "in_errors": float(walk_results["in_errors_map"].get(index) or 0),
                    "out_errors": float(walk_results["out_errors_map"].get(index) or 0),
                    "queue_length": float(walk_results["queue_length_map"].get(index) or 0),
                    "sample_seconds": 0.0,
                    "in_discards_delta": 0.0,
                    "out_discards_delta": 0.0,
                    "crc_errors_delta": 0.0,
                    "in_errors_delta": 0.0,
                    "out_errors_delta": 0.0,
                }
                if admin_status is not None and oper_status is not None:
                    fields.update({
                        "admin_status_code": float(walk_results["admin_status_map"][index]),
                        "oper_status_code": float(walk_results["oper_status_map"][index]),
                        "admin_up": 1.0 if admin_status == "up" else 0.0,
                        "oper_up": 1.0 if oper_status == "up" else 0.0,
                        "admin_status": 1.0 if admin_status == "up" else 0.0,
                        "oper_status": 1.0 if oper_status == "up" else 0.0,
                        "admin_up_oper_down": 1.0 if admin_status == "up" and oper_status != "up" else 0.0,
                        # 兼容早期字段名，避免已有查询或面板短期内断层。
                        "interface_admin_up_oper_down": 1.0 if admin_status == "up" and oper_status != "up" else 0.0,
                    })
                points.append({
                    "measurement": "interface_monitoring",
                    "tags": {
                        "device_id": str(device.id),
                        "device_name": device.name,
                        "interface_index": str(index),
                        "interface_name": name,
                        "vendor": device.vendor or "",
                    },
                    "fields": fields,
                    "timestamp": now,
                })
                continue

            try:
                previous = json.loads(previous_raw)
            except Exception:
                continue

            previous_in = previous.get("in_octets")
            previous_out = previous.get("out_octets")
            previous_in_discards = previous.get("in_discards")
            previous_out_discards = previous.get("out_discards")
            previous_crc_errors = previous.get("crc_errors")
            previous_in_errors = previous.get("in_errors")
            previous_out_errors = previous.get("out_errors")
            previous_in_broadcast = previous.get("in_broadcast")
            previous_out_broadcast = previous.get("out_broadcast")
            previous_ts = previous.get("timestamp")
            if previous_ts is None:
                continue

            elapsed = now_ts - float(previous_ts)
            if elapsed <= 0:
                continue

            in_bps = None
            out_bps = None
            if current_in is not None and previous_in is not None:
                delta_in = current_in - int(previous_in)
                if delta_in >= 0:
                    in_bps = round((delta_in * 8) / elapsed, 2)
            if current_out is not None and previous_out is not None:
                delta_out = current_out - int(previous_out)
                if delta_out >= 0:
                    out_bps = round((delta_out * 8) / elapsed, 2)

            admin_status_text, oper_status_text = valid_status_pair(index)
            if in_bps is None and out_bps is None and admin_status_text is None:
                continue

            def compute_delta(current: Any, old: Any) -> Optional[float]:
                if current is None or old is None:
                    return None
                delta = float(current) - float(old)
                return round(delta, 2) if delta >= 0 else None

            in_discards_delta = compute_delta(walk_results["in_discards_map"].get(index), previous_in_discards)
            out_discards_delta = compute_delta(walk_results["out_discards_map"].get(index), previous_out_discards)
            crc_errors_delta = compute_delta(walk_results["crc_errors_map"].get(index), previous_crc_errors)
            in_errors_delta = compute_delta(walk_results["in_errors_map"].get(index), previous_in_errors)
            out_errors_delta = compute_delta(walk_results["out_errors_map"].get(index), previous_out_errors)
            in_broadcast_delta = compute_delta(walk_results["in_broadcast_map"].get(index), previous_in_broadcast)
            out_broadcast_delta = compute_delta(walk_results["out_broadcast_map"].get(index), previous_out_broadcast)
            in_broadcast_pps = round((in_broadcast_delta or 0.0) / elapsed, 2) if in_broadcast_delta is not None else None
            out_broadcast_pps = round((out_broadcast_delta or 0.0) / elapsed, 2) if out_broadcast_delta is not None else None

            monitored_count += 1
            # 不再压制线路关联端口的接口速率。接口历史是底层事实数据，必须完整保留；
            # 是否参与线路汇总应由线路查询/聚合逻辑决定。
            sample_seconds = round(elapsed, 2)
            in_bps, out_bps = self._sanitize_interface_rates(in_bps, out_bps, speed_bps)
            in_utilization = round((in_bps / speed_bps) * 100, 2) if in_bps is not None and speed_bps else None
            out_utilization = round((out_bps / speed_bps) * 100, 2) if out_bps is not None and speed_bps else None
            fields = {
                "in_bps": in_bps,
                "out_bps": out_bps,
                "in_utilization_percent": in_utilization,
                "out_utilization_percent": out_utilization,
                "in_discards": walk_results["in_discards_map"].get(index),
                "out_discards": walk_results["out_discards_map"].get(index),
                "in_discards_delta": in_discards_delta,
                "out_discards_delta": out_discards_delta,
                "crc_errors": walk_results["crc_errors_map"].get(index),
                "crc_errors_delta": crc_errors_delta,
                "in_errors": walk_results["in_errors_map"].get(index),
                "out_errors": walk_results["out_errors_map"].get(index),
                "in_errors_delta": in_errors_delta,
                "out_errors_delta": out_errors_delta,
                "in_broadcast_packets": walk_results["in_broadcast_map"].get(index),
                "out_broadcast_packets": walk_results["out_broadcast_map"].get(index),
                "in_broadcast_delta": in_broadcast_delta,
                "out_broadcast_delta": out_broadcast_delta,
                "in_broadcast_pps": in_broadcast_pps,
                "out_broadcast_pps": out_broadcast_pps,
                "buffer_usage": walk_results["queue_length_map"].get(index),
                "queue_length": walk_results["queue_length_map"].get(index),
                "speed_bps": speed_bps,
                "sample_seconds": sample_seconds,
            }
            if admin_status_text is not None and oper_status_text is not None:
                fields.update({
                    "admin_status": 1.0 if admin_status_text == "up" else 0.0,
                    "oper_status": 1.0 if oper_status_text == "up" else 0.0,
                    "admin_up_oper_down": 1.0 if admin_status_text == "up" and oper_status_text != "up" else 0.0,
                })
            points.append({
                "measurement": "interface_monitoring",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "interface_index": str(index),
                    "interface_name": name,
                },
                "fields": fields,
                "timestamp": now,
            })

        if points:
            snapshot_rows = []
            for point in points:
                tags = point.get("tags") or {}
                fields = dict(point.get("fields") or {})
                if fields.get("admin_status") is not None:
                    fields["admin_status"] = "up" if float(fields["admin_status"]) >= 1.0 else "down"
                if fields.get("oper_status") is not None:
                    fields["oper_status"] = "up" if float(fields["oper_status"]) >= 1.0 else "down"
                snapshot_rows.append({
                    "index": tags.get("interface_index"),
                    "name": tags.get("interface_name"),
                    **fields,
                })
            snapshot_kind = "state" if realtime else "quality"
            redis_client.setex(
                f"monitor:alert_interface_{snapshot_kind}:{device.id}",
                10 * 60,
                json.dumps({
                    "collected_at": datetime.utcnow().isoformat() + "Z",
                    "interfaces": snapshot_rows,
                }, ensure_ascii=False),
            )
            influx_client.write_points(points, sync=True)

        return {
            "interfaces_total": len(indexes),
            "interfaces_monitored": monitored_count,
            "points_written": len(points),
        }
    
    def collect_cpu(self, device: Any) -> Optional[float]:
        """采集CPU使用率"""
        private_oids = self._get_private_oid_config(device)
        private_cpu = self._collect_private_numeric_metric(
            device,
            private_oids,
            scalar_key="cpu_usage_oids",
            table_key="cpu_usage_table_oids",
            scale_key="cpu_usage_scale",
            aggregate_key="cpu_usage_aggregate",
        )
        if private_cpu is None and private_oids.get("cpu_usage_oid"):
            private_cpu = self._collect_private_numeric_metric(
                device,
                private_oids,
                scalar_key="cpu_usage_oid",
                table_key="cpu_usage_table_oid",
                scale_key="cpu_usage_scale",
                aggregate_key="cpu_usage_aggregate",
            )
        if private_cpu is not None:
            return private_cpu
        oids = self.OID_TEMPLATES["cpu"]["oids"]
        for oid, vendor in oids.items():
            value = self.snmp_get(device, oid)
            if value is not None:
                try:
                    return float(value)
                except:
                    continue
        return None
    
    def collect_memory(self, device: Any) -> Optional[Dict[str, float]]:
        """采集内存使用率"""
        private_oids = self._get_private_oid_config(device)
        private_memory = self._collect_private_numeric_metric(
            device,
            private_oids,
            scalar_key="memory_usage_oids",
            table_key="memory_usage_table_oids",
            scale_key="memory_usage_scale",
            aggregate_key="memory_usage_aggregate",
        )
        if private_memory is None and private_oids.get("memory_usage_oid"):
            private_memory = self._collect_private_numeric_metric(
                device,
                private_oids,
                scalar_key="memory_usage_oid",
                table_key="memory_usage_table_oid",
                scale_key="memory_usage_scale",
                aggregate_key="memory_usage_aggregate",
            )
        if private_memory is not None:
            return {"usage_percent": round(private_memory, 2)}

        pool_used_oid = private_oids.get("memory_pool_used_oid")
        pool_free_oid = private_oids.get("memory_pool_free_oid")
        if pool_used_oid and pool_free_oid:
            try:
                used_map = self._walk_indexed_map(device, str(pool_used_oid), float)
                free_map = self._walk_indexed_map(device, str(pool_free_oid), float)
                used_total = 0.0
                free_total = 0.0
                for index, used_value in used_map.items():
                    free_value = free_map.get(index)
                    if used_value is None or free_value is None:
                        continue
                    used_total += float(used_value)
                    free_total += float(free_value)
                total = used_total + free_total
                if total > 0:
                    usage_percent = (used_total / total) * 100
                    return {
                        "used": used_total,
                        "free": free_total,
                        "total": total,
                        "usage_percent": round(usage_percent, 2),
                    }
            except Exception as exc:
                logger.debug("私有内存池采集失败", device_id=getattr(device, "id", None), error=str(exc))

        # 尝试Cisco方式 (used/free)
        used_oid = private_oids.get("memory_used_oid") or "1.3.6.1.4.1.9.9.48.1.1.1.5.1"
        free_oid = private_oids.get("memory_free_oid") or "1.3.6.1.4.1.9.9.48.1.1.1.6.1"
        
        used = self.snmp_get(device, used_oid)
        free = self.snmp_get(device, free_oid)
        
        if used is not None and free is not None:
            try:
                used_val = float(used)
                free_val = float(free)
                total = used_val + free_val
                if total > 0:
                    usage_percent = (used_val / total) * 100
                    return {
                        "used": used_val,
                        "free": free_val,
                        "total": total,
                        "usage_percent": round(usage_percent, 2)
                    }
            except:
                pass
        
        # 尝试其他OID
        oids = self.OID_TEMPLATES["memory"]["oids"]
        for oid, vendor in oids.items():
            value = self.snmp_get(device, oid)
            if value is not None:
                try:
                    return {"usage_percent": float(value)}
                except:
                    continue
        
        return None

    def collect_temperature(self, device: Any) -> List[Dict[str, Any]]:
        """采集设备温度，支持私有OID walk配置。"""
        private_oids = self._get_private_oid_config(device)
        sensor_value_oid = private_oids.get("entity_sensor_value_oid")
        if sensor_value_oid:
            type_map = self._walk_indexed_map(device, str(private_oids.get("entity_sensor_type_oid")), int) if private_oids.get("entity_sensor_type_oid") else {}
            scale_map = self._walk_indexed_map(device, str(private_oids.get("entity_sensor_scale_oid")), int) if private_oids.get("entity_sensor_scale_oid") else {}
            precision_map = self._walk_indexed_map(device, str(private_oids.get("entity_sensor_precision_oid")), int) if private_oids.get("entity_sensor_precision_oid") else {}
            value_map = self._walk_indexed_map(device, str(sensor_value_oid), float)
            status_map = self._walk_indexed_map(device, str(private_oids.get("entity_sensor_status_oid")), int) if private_oids.get("entity_sensor_status_oid") else {}
            name_map = self._walk_indexed_map(device, str(private_oids.get("entity_name_oid")), str) if private_oids.get("entity_name_oid") else {}
            sensor_rows: List[Dict[str, Any]] = []
            for index, raw_value in value_map.items():
                # ENTITY-SENSOR-MIB::EntitySensorDataType celsius(8).
                if type_map and type_map.get(index) != 8:
                    continue
                # entPhySensorOperStatus ok(1); keep unknown/missing statuses, skip known bad ones.
                if status_map.get(index) not in {None, 1}:
                    continue
                converted = self._convert_entity_sensor_value(raw_value, scale_map.get(index), precision_map.get(index))
                if converted is None:
                    continue
                sensor_rows.append({"sensor": name_map.get(index) or str(index), "temperature": round(float(converted), 2)})
            if sensor_rows:
                return sensor_rows

        temperature_oid = private_oids.get("temperature_oid") or private_oids.get("temperature_oids")
        if not temperature_oid:
            return []
        if not isinstance(temperature_oid, list):
            temperature_oid = [temperature_oid]
        scale = float(private_oids.get("temperature_scale", 1.0) or 1.0)
        ignore_values = {str(value) for value in private_oids.get("temperature_ignore_values") or []}
        results: List[Dict[str, Any]] = []
        for oid in temperature_oid:
            values = self._walk_indexed_map(device, str(oid), float)
            if values:
                for index, value in values.items():
                    if self._is_ignored_value(value, ignore_values):
                        continue
                    results.append({"sensor": str(index), "temperature": round(float(value) * scale, 2)})
                continue
            value = self.snmp_get(device, str(oid))
            if value is not None:
                try:
                    if self._is_ignored_value(value, ignore_values):
                        continue
                    results.append({"sensor": str(oid), "temperature": round(float(value) * scale, 2)})
                except Exception:
                    continue
        return results

    def collect_sessions(self, device: Any) -> Optional[Dict[str, float]]:
        """采集会话数。"""
        private_oids = self._get_private_oid_config(device)
        total = self._normalize_numeric(self._snmp_get_scalar(device, str(private_oids.get("session_total_oid")))) if private_oids.get("session_total_oid") else None
        current = self._normalize_numeric(self._snmp_get_scalar(device, str(private_oids.get("session_current_oid")))) if private_oids.get("session_current_oid") else None
        if total is None and current is None:
            return None
        usage_percent = round((current / total) * 100, 2) if total and current is not None else None
        result = {
            "total": total,
            "current": current,
        }
        if usage_percent is not None:
            result["usage_percent"] = usage_percent
        return result

    def collect_system_private_status(self, device: Any) -> Dict[str, float]:
        """采集设备私有系统状态，如 HA 状态和会话队列满丢包计数。"""
        private_oids = self._get_private_oid_config(device)
        fields: Dict[str, float] = {}
        if private_oids.get("ha_status_oid"):
            value = self._normalize_numeric(self._snmp_get_scalar(device, str(private_oids["ha_status_oid"])))
            if value is not None:
                fields["ha_status"] = value
        if private_oids.get("pending_session_queue_full_drop_oid"):
            value = self._normalize_numeric(self._snmp_get_scalar(device, str(private_oids["pending_session_queue_full_drop_oid"])))
            if value is not None:
                fields["pending_session_queue_full_drop"] = value
        return fields

    def collect_pak_buffer(self, device: Any) -> List[Dict[str, Any]]:
        """采集山石 Packet Buffer 当前总量和已用量。"""
        private_oids = self._get_private_oid_config(device)
        total_oid = private_oids.get("pak_buffer_total_oid")
        used_oid = private_oids.get("pak_buffer_used_oid")
        if not total_oid and not used_oid:
            return []
        total_map = self._walk_indexed_map(device, str(total_oid), float) if total_oid else {}
        used_map = self._walk_indexed_map(device, str(used_oid), float) if used_oid else {}
        rows: List[Dict[str, Any]] = []
        for index in sorted(set(total_map) | set(used_map), key=str):
            total = self._normalize_numeric(total_map.get(index))
            used = self._normalize_numeric(used_map.get(index))
            usage_percent = round((used / total) * 100, 2) if total and used is not None else None
            rows.append({
                "buffer": str(index),
                "total": total,
                "used": used,
                "usage_percent": usage_percent,
            })
        return rows

    def collect_ipsec_tunnels(self, device: Any) -> List[Dict[str, Any]]:
        """采集山石 IPSec 隧道状态。"""
        private_oids = self._get_private_oid_config(device)
        status_oid = private_oids.get("ipsec_tunnel_status_oid")
        if not status_oid:
            return []
        status_map = self._walk_indexed_map(device, str(status_oid), int)
        name_map = self._walk_indexed_map(device, str(private_oids.get("ipsec_tunnel_name_oid")), str) if private_oids.get("ipsec_tunnel_name_oid") else {}
        peer_map = self._walk_indexed_map(device, str(private_oids.get("ipsec_tunnel_peer_oid")), str) if private_oids.get("ipsec_tunnel_peer_oid") else {}
        rows: List[Dict[str, Any]] = []
        for index, status in status_map.items():
            rows.append({
                "tunnel": name_map.get(index) or str(index),
                "peer": peer_map.get(index),
                "status": float(status),
                "up": 1.0 if status == 1 else 0.0,
            })
        return rows

    def collect_nat_status(self, device: Any) -> Dict[str, List[Dict[str, Any]]]:
        """采集山石 SNAT 资源和 DNAT Server 状态。"""
        private_oids = self._get_private_oid_config(device)
        snat_rows: List[Dict[str, Any]] = []
        protocol_oids = {
            "tcp": ("snat_tcp_total_oid", "snat_tcp_used_oid"),
            "udp": ("snat_udp_total_oid", "snat_udp_used_oid"),
            "icmp": ("snat_icmp_total_oid", "snat_icmp_used_oid"),
        }
        for protocol, (total_key, used_key) in protocol_oids.items():
            total_oid = private_oids.get(total_key)
            used_oid = private_oids.get(used_key)
            if not total_oid and not used_oid:
                continue
            total_map = self._walk_indexed_map(device, str(total_oid), float) if total_oid else {}
            used_map = self._walk_indexed_map(device, str(used_oid), float) if used_oid else {}
            for index in sorted(set(total_map) | set(used_map), key=str):
                total = self._normalize_numeric(total_map.get(index))
                used = self._normalize_numeric(used_map.get(index))
                usage_percent = round((used / total) * 100, 2) if total and used is not None else None
                snat_rows.append({
                    "rule": str(index),
                    "protocol": protocol,
                    "total": total,
                    "used": used,
                    "usage_percent": usage_percent,
                })

        dnat_rows: List[Dict[str, Any]] = []
        status_oid = private_oids.get("dnat_server_status_oid")
        if status_oid:
            status_map = self._walk_indexed_map(device, str(status_oid), int)
            address_map = self._walk_indexed_map(device, str(private_oids.get("dnat_server_address_oid")), str) if private_oids.get("dnat_server_address_oid") else {}
            connections_map = self._walk_indexed_map(device, str(private_oids.get("dnat_server_connections_oid")), float) if private_oids.get("dnat_server_connections_oid") else {}
            for index, status in status_map.items():
                dnat_rows.append({
                    "server": address_map.get(index) or str(index),
                    "status": float(status),
                    "up": 1.0 if status == 1 else 0.0,
                    "connections": self._normalize_numeric(connections_map.get(index)),
                })
        return {"snat": snat_rows, "dnat": dnat_rows}

    def collect_slb_virtual_servers(self, device: Any) -> List[Dict[str, Any]]:
        """采集山石 SLB 虚拟服务状态。"""
        private_oids = self._get_private_oid_config(device)
        status_oid = private_oids.get("slb_vs_status_oid")
        if not status_oid:
            return []
        status_map = self._walk_indexed_map(device, str(status_oid), int)
        name_map = self._walk_indexed_map(device, str(private_oids.get("slb_vs_name_oid")), str) if private_oids.get("slb_vs_name_oid") else {}
        connections_map = self._walk_indexed_map(device, str(private_oids.get("slb_vs_connections_oid")), float) if private_oids.get("slb_vs_connections_oid") else {}
        rows: List[Dict[str, Any]] = []
        for index, status in status_map.items():
            rows.append({
                "virtual_server": name_map.get(index) or str(index),
                "status": float(status),
                "up": 1.0 if status == 1 else 0.0,
                "connections": self._normalize_numeric(connections_map.get(index)),
            })
        return rows

    def collect_storage(self, device: Any) -> List[Dict[str, Any]]:
        """采集存储空间。"""
        private_oids = self._get_private_oid_config(device)
        total_oid = private_oids.get("storage_total_oid")
        free_oid = private_oids.get("storage_free_oid")
        if not total_oid and not free_oid:
            return self._collect_standard_storage(device)
        total_map = self._walk_indexed_map(device, str(total_oid), float) if total_oid else {}
        free_map = self._walk_indexed_map(device, str(free_oid), float) if free_oid else {}
        indexes = sorted(set(total_map) | set(free_map), key=str)
        rows: List[Dict[str, Any]] = []
        for index in indexes:
            total = self._normalize_numeric(total_map.get(index))
            free = self._normalize_numeric(free_map.get(index))
            used = total - free if total is not None and free is not None else None
            usage_percent = round((used / total) * 100, 2) if total and used is not None else None
            rows.append({
                "storage": str(index),
                "total": total,
                "free": free,
                "used": used,
                "usage_percent": usage_percent,
            })
        return rows

    def _collect_standard_storage(self, device: Any) -> List[Dict[str, Any]]:
        """通过 HOST-RESOURCES-MIB 采集通用存储，设备不支持时返回空。"""
        descr_map = self._walk_indexed_map(device, "1.3.6.1.2.1.25.2.3.1.3", str)
        allocation_map = self._walk_indexed_map(device, "1.3.6.1.2.1.25.2.3.1.4", float)
        size_map = self._walk_indexed_map(device, "1.3.6.1.2.1.25.2.3.1.5", float)
        used_map = self._walk_indexed_map(device, "1.3.6.1.2.1.25.2.3.1.6", float)
        if not descr_map:
            return []
        rows: List[Dict[str, Any]] = []
        storage_keywords = ["flash", "disk", "cf", "sd", "storage", "boot", "logfile", "filesystem"]
        for index, descr in descr_map.items():
            descr_text = str(descr or "")
            if not any(keyword in descr_text.lower() for keyword in storage_keywords):
                continue
            allocation_unit = self._normalize_numeric(allocation_map.get(index)) or 1.0
            total = self._normalize_numeric((size_map.get(index) or 0) * allocation_unit)
            used = self._normalize_numeric((used_map.get(index) or 0) * allocation_unit)
            free = total - used if total is not None and used is not None else None
            usage_percent = round((used / total) * 100, 2) if total and used is not None else None
            rows.append({
                "storage": descr_text or str(index),
                "total": total,
                "free": free,
                "used": used,
                "usage_percent": usage_percent,
            })
        return rows

    def collect_hardware_status(self, device: Any) -> List[Dict[str, Any]]:
        """采集风扇、电源状态。"""
        private_oids = self._get_private_oid_config(device)
        rows: List[Dict[str, Any]] = []
        entity_name_map = self._walk_indexed_map(device, str(private_oids.get("entity_name_oid")), str) if private_oids.get("entity_name_oid") else {}
        entity_descr_map = self._walk_indexed_map(device, str(private_oids.get("entity_descr_oid")), str) if private_oids.get("entity_descr_oid") else {}

        cisco_power_status_oid = private_oids.get("cisco_power_status_oid")
        if cisco_power_status_oid:
            ok_values = set(private_oids.get("cisco_power_ok_values") or [2])
            for index, state in self._walk_indexed_map(device, str(cisco_power_status_oid), int).items():
                rows.append({
                    "component_type": "power",
                    "component": entity_name_map.get(index) or entity_descr_map.get(index) or str(index),
                    "state": float(state),
                    "up": 1.0 if state in ok_values else 0.0,
                    "speed": None,
                    "present": 1.0,
                    "status_known": 1.0,
                })

        cisco_fan_status_oid = private_oids.get("cisco_fan_status_oid")
        if cisco_fan_status_oid:
            ok_values = set(private_oids.get("cisco_fan_ok_values") or [1, 2])
            for index, state in self._walk_indexed_map(device, str(cisco_fan_status_oid), int).items():
                rows.append({
                    "component_type": "fan",
                    "component": entity_name_map.get(index) or entity_descr_map.get(index) or str(index),
                    "state": float(state),
                    "up": 1.0 if state in ok_values else 0.0,
                    "speed": None,
                    "present": 1.0,
                    "status_known": 1.0,
                })
            if rows:
                return rows

        entity_class_oid = private_oids.get("entity_class_oid")
        entity_name_oid = private_oids.get("entity_name_oid")
        entity_oper_status_oid = private_oids.get("entity_oper_status_oid")
        entity_error_status_oid = private_oids.get("entity_error_status_oid")
        if entity_class_oid:
            class_map = self._walk_indexed_map(device, str(entity_class_oid), int)
            name_map = entity_name_map or (self._walk_indexed_map(device, str(entity_name_oid), str) if entity_name_oid else {})
            if entity_oper_status_oid or entity_error_status_oid:
                oper_map = self._walk_indexed_map(device, str(entity_oper_status_oid), int) if entity_oper_status_oid else {}
                error_map = self._walk_indexed_map(device, str(entity_error_status_oid), int) if entity_error_status_oid else {}
                ok_values = set(private_oids.get("hardware_ok_values") or [2, 3])
                for index, entity_class in class_map.items():
                    if entity_class not in {6, 7}:
                        continue
                    state = error_map.get(index, oper_map.get(index))
                    component_type = "power" if entity_class == 6 else "fan"
                    rows.append({
                        "component_type": component_type,
                        "component": name_map.get(index) or str(index),
                        "state": float(state) if state is not None else None,
                        "up": 1.0 if state in ok_values else 0.0 if state is not None else None,
                        "speed": None,
                        "present": 1.0,
                        "status_known": 1.0 if state is not None else 0.0,
                    })
                if rows:
                    return rows

            inventory_rows: List[Dict[str, Any]] = []
            for index, entity_class in class_map.items():
                if entity_class not in {6, 7}:
                    continue
                component_type = "power" if entity_class == 6 else "fan"
                inventory_rows.append({
                    "component_type": component_type,
                    "component": name_map.get(index) or str(index),
                    "state": None,
                    "up": None,
                    "speed": None,
                    "present": 1.0,
                    "status_known": 0.0,
                })
            if inventory_rows:
                return rows + inventory_rows if rows else inventory_rows

        fan_state_oid = private_oids.get("fan_state_oid")
        fan_speed_oid = private_oids.get("fan_speed_oid")
        if fan_state_oid or fan_speed_oid:
            state_map = self._walk_indexed_map(device, str(fan_state_oid), int) if fan_state_oid else {}
            speed_map = self._walk_indexed_map(device, str(fan_speed_oid), float) if fan_speed_oid else {}
            name_map = self._walk_indexed_map(device, str(private_oids.get("fan_name_oid")), str) if private_oids.get("fan_name_oid") else {}
            ok_values = set(private_oids.get("fan_ok_values") or [0])
            for index in sorted(set(state_map) | set(speed_map), key=str):
                state = state_map.get(index)
                rows.append({
                    "component_type": "fan",
                    "component": name_map.get(index) or str(index),
                    "state": float(state) if state is not None else None,
                    "up": 1.0 if state in ok_values else 0.0 if state is not None else None,
                    "speed": self._normalize_numeric(speed_map.get(index)),
                    "present": 1.0,
                    "status_known": 1.0 if state is not None else 0.0,
                })
        power_state_oid = private_oids.get("power_state_oid")
        if power_state_oid:
            name_map = self._walk_indexed_map(device, str(private_oids.get("power_name_oid")), str) if private_oids.get("power_name_oid") else {}
            ok_values = set(private_oids.get("power_ok_values") or [0])
            for index, state in self._walk_indexed_map(device, str(power_state_oid), int).items():
                rows.append({
                    "component_type": "power",
                    "component": name_map.get(index) or str(index),
                    "state": float(state),
                    "up": 1.0 if state in ok_values else 0.0,
                    "present": 1.0,
                    "status_known": 1.0,
                })
        return rows
    
    def collect_interface_traffic(self, device: Any) -> List[Dict[str, Any]]:
        """采集接口流量"""
        interfaces = []
        
        # 获取接口描述
        descr_results = self.snmp_walk(device, "1.3.6.1.2.1.2.2.1.2")
        in_octets_results = self.snmp_walk(device, "1.3.6.1.2.1.2.2.1.10")
        out_octets_results = self.snmp_walk(device, "1.3.6.1.2.1.2.2.1.16")
        
        # 构建接口数据字典
        descr_map = {oid.split('.')[-1]: str(value) for oid, value in descr_results}
        in_octets_map = {oid.split('.')[-1]: int(value) for oid, value in in_octets_results if str(value).isdigit()}
        out_octets_map = {oid.split('.')[-1]: int(value) for oid, value in out_octets_results if str(value).isdigit()}
        
        for idx, name in descr_map.items():
            if idx in in_octets_map or idx in out_octets_map:
                interfaces.append({
                    "index": idx,
                    "name": name,
                    "in_octets": in_octets_map.get(idx, 0),
                    "out_octets": out_octets_map.get(idx, 0)
                })
        
        return interfaces
    
    def collect_uptime(self, device: Any) -> Optional[int]:
        """采集运行时间"""
        oid = next(iter(self.OID_TEMPLATES["uptime"]["oids"].keys()), None)
        if not oid:
            return None
        value = self.snmp_get(device, oid)
        if value is not None:
            try:
                # sysUpTime 是 TimeTicks (1/100秒)
                return int(value) // 100
            except:
                pass
        return None

    def collect_system_info(self, device: Any) -> Dict[str, Optional[str]]:
        """采集标准 SNMP system 信息"""
        sys_descr_text = self._snmp_get_text_value(device, "1.3.6.1.2.1.1.1.0")
        sys_name_text = self._snmp_get_text_value(device, "1.3.6.1.2.1.1.5.0")
        private_oids = self._get_private_oid_config(device)
        software_version = None
        snmp_model = None
        serial_number = None
        if sys_descr_text:
            version_match = re.search(
                r"Software\s+Version\s+([^,\r\n]+)(?:,\s*(Release\s+[^\r\n,]+))?",
                sys_descr_text,
                re.IGNORECASE,
            )
            if version_match:
                software_version = f"Software Version {version_match.group(1).strip()}"
                if version_match.group(2):
                    software_version = f"{software_version}, {version_match.group(2).strip()}"
            snmp_model = extract_snmp_model(sys_descr_text)

        private_version = (
            self._snmp_get_text_value(device, str(private_oids["system_version_oid"]))
            if private_oids.get("system_version_oid")
            else None
        )
        private_release = (
            self._snmp_get_text_value(device, str(private_oids["system_release_oid"]))
            if private_oids.get("system_release_oid")
            else None
        )
        if private_version:
            software_version = f"Software Version {private_version.strip()}"
            if private_release:
                release_text = private_release.strip()
                if release_text.upper().startswith("R") and release_text[1:].isdigit():
                    release_text = f"Release {release_text[1:]}"
                elif not release_text.lower().startswith("release"):
                    release_text = f"Release {release_text}"
                software_version = f"{software_version}, {release_text}"

        private_model = (
            self._snmp_get_text_value(device, str(private_oids["system_model_oid"]))
            if private_oids.get("system_model_oid")
            else None
        )
        if private_model:
            snmp_model = extract_snmp_model(private_model) or private_model.strip()

        serial_number = (
            self._snmp_get_text_value(device, str(private_oids["system_serial_oid"]))
            if private_oids.get("system_serial_oid")
            else None
        )

        if private_oids.get("entity_class_oid") and (
            private_oids.get("entity_model_oid")
            or private_oids.get("entity_serial_oid")
            or private_oids.get("entity_software_oid")
        ):
            class_map = self._walk_indexed_map(device, str(private_oids["entity_class_oid"]), int)
            name_map = self._walk_indexed_map(device, str(private_oids.get("entity_name_oid")), str) if private_oids.get("entity_name_oid") else {}
            if not snmp_model and private_oids.get("entity_model_oid"):
                model_map = self._walk_indexed_map(device, str(private_oids["entity_model_oid"]), str)
                entity_model = self._first_entity_inventory_value(class_map, model_map, {3}, name_map)
                if entity_model:
                    snmp_model = extract_snmp_model(entity_model) or entity_model
            if not serial_number and private_oids.get("entity_serial_oid"):
                serial_map = self._walk_indexed_map(device, str(private_oids["entity_serial_oid"]), str)
                serial_number = self._first_entity_inventory_value(class_map, serial_map, {3}, name_map)
            if not software_version and private_oids.get("entity_software_oid"):
                software_map = self._walk_indexed_map(device, str(private_oids["entity_software_oid"]), str)
                software_version = self._first_entity_inventory_value(class_map, software_map, {3}, name_map)
        return {
            "sys_descr": sys_descr_text,
            "sys_name": sys_name_text,
            "software_version": software_version,
            "snmp_model": snmp_model,
            "serial_number": serial_number,
        }

    def collect_overview_gap_fill(self, device: Any) -> Dict[str, Any]:
        """Lightweight SNMP fallback for Telemetry-primary devices.

        Telemetry is the authoritative source for high-frequency interface and
        resource metrics, but some devices do not currently expose/parse uptime
        and fan/PSU state through Telemetry.  This method deliberately collects
        only low-cardinality overview fields and avoids interface/NAT/storage
        walks so Telemetry devices do not fall back to the expensive full SNMP
        collector.
        """
        timestamp = datetime.utcnow()
        points: List[Dict[str, Any]] = []

        hardware_rows = self.collect_hardware_status(device)
        for item in hardware_rows:
            points.append({
                "measurement": "snmp_hardware",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "component_type": item.get("component_type"),
                    "component": item.get("component"),
                },
                "fields": {
                    "state": item.get("state"),
                    "up": item.get("up"),
                    "speed": item.get("speed"),
                    "present": item.get("present"),
                    "status_known": item.get("status_known"),
                },
                "timestamp": timestamp,
            })

        uptime = self.collect_uptime(device)
        system_info = self.collect_system_info(device)
        if uptime is not None:
            points.append({
                "measurement": "snmp_metrics",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "metric_type": "uptime",
                },
                "fields": {"seconds": uptime},
                "timestamp": timestamp,
            })

        if uptime is not None or system_info.get("sys_name") or system_info.get("sys_descr"):
            points.append({
                "measurement": "snmp_system_info",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "sys_name": system_info.get("sys_name"),
                    "sys_descr": system_info.get("sys_descr"),
                    "software_version": system_info.get("software_version"),
                    "snmp_model": system_info.get("snmp_model"),
                    "serial_number": system_info.get("serial_number"),
                },
                "fields": {"uptime_seconds": float(uptime or 0)},
                "timestamp": timestamp,
            })

        if points:
            influx_client.write_points(points)

        hardware = {
            "fan_total": sum(1 for item in hardware_rows if item.get("component_type") == "fan" and item.get("present", 1) != 0),
            "fan_down": sum(1 for item in hardware_rows if item.get("component_type") == "fan" and item.get("up") == 0),
            "fan_status_known": all(item.get("status_known", 1) != 0 for item in hardware_rows if item.get("component_type") == "fan"),
            "power_total": sum(1 for item in hardware_rows if item.get("component_type") == "power" and item.get("present", 1) != 0),
            "power_down": sum(1 for item in hardware_rows if item.get("component_type") == "power" and item.get("up") == 0),
            "power_status_known": all(item.get("status_known", 1) != 0 for item in hardware_rows if item.get("component_type") == "power"),
        }
        return {
            "device_id": device.id,
            "timestamp": timestamp,
            "hardware": hardware,
            "hardware_count": len(hardware_rows),
            "uptime": uptime,
            "system_info": {
                **system_info,
                "uptime_seconds": uptime,
            },
            "points_written": len(points),
        }
    
    def collect_device(self, device: Any) -> Dict[str, Any]:
        """采集设备所有SNMP指标"""
        timestamp = datetime.utcnow()
        points = []
        
        # 采集CPU
        cpu = self.collect_cpu(device)
        if cpu is not None:
            points.append({
                "measurement": "snmp_metrics",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "metric_type": "cpu"
                },
                "fields": {"usage": cpu},
                "timestamp": timestamp
            })
        
        # 采集内存
        memory = self.collect_memory(device)
        if memory:
            points.append({
                "measurement": "snmp_metrics",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "metric_type": "memory"
                },
                "fields": memory,
                "timestamp": timestamp
            })

        # 采集温度
        temperatures = self.collect_temperature(device)
        for item in temperatures:
            points.append({
                "measurement": "snmp_temperature",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "sensor": item.get("sensor") or "temperature",
                },
                "fields": {"temperature": item["temperature"]},
                "timestamp": timestamp
            })

        # 采集会话数
        sessions = self.collect_sessions(device)
        if sessions:
            points.append({
                "measurement": "snmp_sessions",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                },
                "fields": sessions,
                "timestamp": timestamp
            })

        # 采集私有系统状态
        system_private_status = self.collect_system_private_status(device)
        if system_private_status:
            points.append({
                "measurement": "snmp_system",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                },
                "fields": system_private_status,
                "timestamp": timestamp
            })

        # 采集存储
        storage_rows = self.collect_storage(device)
        for item in storage_rows:
            fields = {key: item.get(key) for key in ["total", "free", "used", "usage_percent"]}
            points.append({
                "measurement": "snmp_storage",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "storage": item.get("storage"),
                },
                "fields": fields,
                "timestamp": timestamp
            })

        # 采集 Packet Buffer
        pak_buffer_rows = self.collect_pak_buffer(device)
        for item in pak_buffer_rows:
            fields = {key: item.get(key) for key in ["total", "used", "usage_percent"]}
            points.append({
                "measurement": "snmp_pak_buffer",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "buffer": item.get("buffer"),
                },
                "fields": fields,
                "timestamp": timestamp
            })

        # 采集 IPSec 隧道状态
        ipsec_rows = self.collect_ipsec_tunnels(device)
        for item in ipsec_rows:
            points.append({
                "measurement": "snmp_ipsec_tunnel",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "tunnel": item.get("tunnel"),
                    "peer": item.get("peer"),
                },
                "fields": {
                    "status": item.get("status"),
                    "up": item.get("up"),
                },
                "timestamp": timestamp
            })

        # 采集 NAT 状态和资源使用率
        nat_rows = self.collect_nat_status(device)
        for item in nat_rows.get("snat", []):
            fields = {key: item.get(key) for key in ["total", "used", "usage_percent"]}
            points.append({
                "measurement": "snmp_snat_resource",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "rule": item.get("rule"),
                    "protocol": item.get("protocol"),
                },
                "fields": fields,
                "timestamp": timestamp
            })
        for item in nat_rows.get("dnat", []):
            points.append({
                "measurement": "snmp_dnat_server",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "server": item.get("server"),
                },
                "fields": {
                    "status": item.get("status"),
                    "up": item.get("up"),
                    "connections": item.get("connections"),
                },
                "timestamp": timestamp
            })

        # 采集 SLB 虚拟服务状态
        slb_vs_rows = self.collect_slb_virtual_servers(device)
        for item in slb_vs_rows:
            points.append({
                "measurement": "snmp_slb_virtual_server",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "virtual_server": item.get("virtual_server"),
                },
                "fields": {
                    "status": item.get("status"),
                    "up": item.get("up"),
                    "connections": item.get("connections"),
                },
                "timestamp": timestamp
            })

        # 采集风扇/电源
        hardware_rows = self.collect_hardware_status(device)
        for item in hardware_rows:
            points.append({
                "measurement": "snmp_hardware",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "component_type": item.get("component_type"),
                    "component": item.get("component"),
                },
                "fields": {
                    "state": item.get("state"),
                    "up": item.get("up"),
                    "speed": item.get("speed"),
                    "present": item.get("present"),
                    "status_known": item.get("status_known"),
                },
                "timestamp": timestamp
            })
        
        # 采集接口流量
        interfaces = self.collect_interface_traffic(device)
        for iface in interfaces:
            points.append({
                "measurement": "snmp_metrics",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "metric_type": "interface_traffic",
                    "interface": iface["name"],
                    "interface_index": iface["index"]
                },
                "fields": {
                    "in_octets": iface["in_octets"],
                    "out_octets": iface["out_octets"]
                },
                "timestamp": timestamp
            })
        
        # 采集运行时间
        uptime = self.collect_uptime(device)
        if uptime is not None:
            points.append({
                "measurement": "snmp_metrics",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "metric_type": "uptime"
                },
                "fields": {"seconds": uptime},
                "timestamp": timestamp
            })

        # 采集标准系统信息。文本值写入 tag，避免和数值型指标混在同一 field 中。
        system_info = self.collect_system_info(device)
        if uptime is not None or system_info.get("sys_name") or system_info.get("sys_descr"):
            points.append({
                "measurement": "snmp_system_info",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "sys_name": system_info.get("sys_name"),
                    "sys_descr": system_info.get("sys_descr"),
                    "software_version": system_info.get("software_version"),
                    "snmp_model": system_info.get("snmp_model"),
                    "serial_number": system_info.get("serial_number"),
                },
                "fields": {"uptime_seconds": float(uptime or 0)},
                "timestamp": timestamp
            })
        
        # 批量写入InfluxDB
        if points:
            success = influx_client.write_points(points)
            if success:
                self.logger.debug(
                    f"SNMP数据写入成功",
                    device=device.ip_address,
                    points=len(points)
                )
        
        return {
            "device_id": device.id,
            "timestamp": timestamp,
            "cpu": cpu,
            "memory": memory,
            "temperature": max(
                (float(item["temperature"]) for item in temperatures if item.get("temperature") is not None),
                default=None,
            ),
            "temperature_details": temperatures,
            "storage_percent": max(
                (float(item["usage_percent"]) for item in storage_rows if item.get("usage_percent") is not None),
                default=None,
            ),
            "sessions": sessions,
            "hardware": {
                "fan_total": sum(1 for item in hardware_rows if item.get("component_type") == "fan" and item.get("present", 1) != 0),
                "fan_down": sum(1 for item in hardware_rows if item.get("component_type") == "fan" and item.get("up") == 0),
                "fan_status_known": all(item.get("status_known", 1) != 0 for item in hardware_rows if item.get("component_type") == "fan"),
                "power_total": sum(1 for item in hardware_rows if item.get("component_type") == "power" and item.get("present", 1) != 0),
                "power_down": sum(1 for item in hardware_rows if item.get("component_type") == "power" and item.get("up") == 0),
                "power_status_known": all(item.get("status_known", 1) != 0 for item in hardware_rows if item.get("component_type") == "power"),
            },
            "storage_count": len(storage_rows),
            "pak_buffer_count": len(pak_buffer_rows),
            "ipsec_tunnel_count": len(ipsec_rows),
            "snat_resource_count": len(nat_rows.get("snat", [])),
            "dnat_server_count": len(nat_rows.get("dnat", [])),
            "slb_virtual_server_count": len(slb_vs_rows),
            "hardware_count": len(hardware_rows),
            "interfaces_count": len(interfaces),
            "uptime": uptime,
            "system_info": {
                **system_info,
                "uptime_seconds": uptime,
            },
            "points_written": len(points)
        }


# 全局SNMP采集器实例
snmp_collector = SNMPCollector()
