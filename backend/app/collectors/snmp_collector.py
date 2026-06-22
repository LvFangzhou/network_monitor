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
from concurrent.futures import ThreadPoolExecutor
from app.config import settings
from app.utils import influx_client, redis_client
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
    }

    DENSIVELO_PRIVATE_OIDS = {
        # S9867/DensiveloOS exposes a Yillion private tree under 1.3.6.1.4.1.64812.
        # The currently available H3C 9867 documents only cover standard MIBs
        # (IF-MIB/BGP4-MIB/ENTITY-MIB, etc.), so do not reuse legacy H3C 25506
        # CPU/memory/environment OIDs here. Device-specific 64812 resource OIDs
        # can be added through custom_fields.snmp_private_oids when the private
        # MIB object names are available.
        "bgp_state_oids": [
            "1.3.6.1.2.1.15.3.1.2",
        ],
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

    def _build_snmp_command(self, tool: str, device: Any, oid: str) -> List[str]:
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

    def _run_snmp_command(self, command: List[str], device: Any) -> Optional[str]:
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
                self.logger.warning(f"SNMP命令失败: {stderr}", device=device.ip_address, command=command[0])
            return ""

        return result.stdout
    
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
    
    def snmp_get(self, device: Any, oid: str) -> Optional[Any]:
        """执行SNMP GET操作"""
        command = self._build_snmp_command("snmpget", device, oid)
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
                ContextData(),
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
    
    def snmp_walk(self, device: Any, oid: str) -> List[Tuple[str, Any]]:
        """执行SNMP WALK操作"""
        results = []
        command = self._build_snmp_command("snmpbulkwalk", device, oid)
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
                ContextData(),
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

    def _walk_indexed_map(self, device: Any, oid: str, cast=None) -> Dict[str, Any]:
        results = {}
        for item_oid, value in self.snmp_walk(device, oid):
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
            "if_alias_map": ("1.3.6.1.2.1.31.1.1.1.18", str),
            "admin_status_map": ("1.3.6.1.2.1.2.2.1.7", int),
            "oper_status_map": ("1.3.6.1.2.1.2.2.1.8", int),
            "high_speed_map": ("1.3.6.1.2.1.31.1.1.1.15", int),
            "speed_map": ("1.3.6.1.2.1.2.2.1.5", int),
        }
        with ThreadPoolExecutor(max_workers=len(walk_jobs)) as executor:
            futures = {
                name: executor.submit(self._walk_indexed_map, device, oid, cast)
                for name, (oid, cast) in walk_jobs.items()
            }
            walk_results = {name: future.result() for name, future in futures.items()}

        if_name_map = walk_results["if_name_map"]
        if_descr_map = walk_results["if_descr_map"]
        if_alias_map = walk_results["if_alias_map"]
        admin_status_map = walk_results["admin_status_map"]
        oper_status_map = walk_results["oper_status_map"]
        high_speed_map = walk_results["high_speed_map"]
        speed_map = walk_results["speed_map"]

        status_map = {
            1: "up",
            2: "down",
            3: "testing",
            4: "unknown",
            5: "dormant",
            6: "notPresent",
            7: "lowerLayerDown",
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
                "admin_status": status_map.get(admin_status_map.get(index), "unknown"),
                "oper_status": status_map.get(oper_status_map.get(index), "unknown"),
                "speed_bps": speed_bps,
            })

        return interfaces

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
        bgp_state_text = {
            1: "idle",
            2: "connect",
            3: "active",
            4: "opensent",
            5: "openconfirm",
            6: "established",
        }
        for index, state in bgp_state_map.items():
            peer = self._extract_peer_from_index(index)
            points.append({
                "measurement": "protocol_status",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "protocol": "bgp",
                    "peer": peer,
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
        for point in points:
            protocol = str(point.get("tags", {}).get("protocol") or "").lower()
            if protocol not in protocol_summary:
                continue
            is_up = float(point.get("fields", {}).get("state_up") or 0) >= 1
            protocol_summary[protocol]["total"] += 1
            protocol_summary[protocol]["up" if is_up else "down"] += 1

        if points:
            influx_client.write_points(points, sync=False)
        return {"points_written": len(points), "protocols": protocol_summary}

    def collect_optical_monitoring(self, device: Any) -> Dict[str, Any]:
        """采集光模块 RX/TX 功率，需提供私有 OID"""
        private_oids = self._get_private_oid_config(device)
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

    def _interface_snapshot_cache_key(self, device_id: int, interface_index: int) -> str:
        return f"interface_monitoring:last:{device_id}:{interface_index}"

    def _interface_initialized_cache_key(self, device_id: int, interface_index: int) -> str:
        return f"interface_monitoring:initialized:{device_id}:{interface_index}"

    def collect_interface_monitoring(
        self,
        device: Any,
        suppress_rate_interface_names: Optional[set[str]] = None,
    ) -> Dict[str, Any]:
        """批量采集接口历史监控数据，并基于上一次快照计算速率"""
        suppress_rate_interface_names = suppress_rate_interface_names or set()
        walk_jobs = {
            "if_name_map": ("1.3.6.1.2.1.31.1.1.1.1", str),
            "if_descr_map": ("1.3.6.1.2.1.2.2.1.2", str),
            "in_octets_64_map": ("1.3.6.1.2.1.31.1.1.1.6", int),
            "out_octets_64_map": ("1.3.6.1.2.1.31.1.1.1.10", int),
            "in_octets_32_map": ("1.3.6.1.2.1.2.2.1.10", int),
            "out_octets_32_map": ("1.3.6.1.2.1.2.2.1.16", int),
            "high_speed_map": ("1.3.6.1.2.1.31.1.1.1.15", int),
            "speed_map": ("1.3.6.1.2.1.2.2.1.5", int),
            "admin_status_map": ("1.3.6.1.2.1.2.2.1.7", int),
            "oper_status_map": ("1.3.6.1.2.1.2.2.1.8", int),
            "in_discards_map": ("1.3.6.1.2.1.2.2.1.13", int),
            "out_discards_map": ("1.3.6.1.2.1.2.2.1.19", int),
            "in_errors_map": ("1.3.6.1.2.1.2.2.1.14", int),
            "out_errors_map": ("1.3.6.1.2.1.2.2.1.20", int),
            "queue_length_map": ("1.3.6.1.2.1.2.2.1.21", int),
            "in_broadcast_map": ("1.3.6.1.2.1.31.1.1.1.3", int),
            "out_broadcast_map": ("1.3.6.1.2.1.31.1.1.1.5", int),
        }

        with ThreadPoolExecutor(max_workers=len(walk_jobs)) as executor:
            futures = {
                name: executor.submit(self._walk_indexed_map, device, oid, cast)
                for name, (oid, cast) in walk_jobs.items()
            }
            walk_results = {name: future.result() for name, future in futures.items()}

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
        now = datetime.utcnow()
        now_ts = time.time()
        points = []
        monitored_count = 0

        for index in indexes:
            name = walk_results["if_name_map"].get(index) or walk_results["if_descr_map"].get(index) or f"if{index}"
            current_in = walk_results["in_octets_64_map"].get(index)
            current_out = walk_results["out_octets_64_map"].get(index)
            if current_in is None:
                current_in = walk_results["in_octets_32_map"].get(index)
            if current_out is None:
                current_out = walk_results["out_octets_32_map"].get(index)

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

                admin_status = status_map.get(walk_results["admin_status_map"].get(index), "unknown")
                oper_status = status_map.get(walk_results["oper_status_map"].get(index), "unknown")
                in_bps = None if name in suppress_rate_interface_names else 0.0
                out_bps = None if name in suppress_rate_interface_names else 0.0
                monitored_count += 1
                points.append({
                    "measurement": "interface_monitoring",
                    "tags": {
                        "device_id": str(device.id),
                        "device_name": device.name,
                        "interface_index": str(index),
                        "interface_name": name,
                        "vendor": device.vendor or "",
                    },
                    "fields": {
                        "in_octets": int(current_in) if current_in is not None else 0,
                        "out_octets": int(current_out) if current_out is not None else 0,
                        "in_bps": in_bps,
                        "out_bps": out_bps,
                        "speed_bps": float(speed_bps) if speed_bps is not None else None,
                        "in_utilization_percent": round((in_bps / speed_bps) * 100, 2) if speed_bps and in_bps is not None else None,
                        "out_utilization_percent": round((out_bps / speed_bps) * 100, 2) if speed_bps and out_bps is not None else None,
                        "admin_status_code": float(walk_results["admin_status_map"].get(index) or 0),
                        "oper_status_code": float(walk_results["oper_status_map"].get(index) or 0),
                        "admin_up": 1.0 if admin_status == "up" else 0.0,
                        "oper_up": 1.0 if oper_status == "up" else 0.0,
                        "interface_admin_up_oper_down": 1.0 if admin_status == "up" and oper_status != "up" else 0.0,
                        "in_discards": float(walk_results["in_discards_map"].get(index) or 0),
                        "out_discards": float(walk_results["out_discards_map"].get(index) or 0),
                        "in_errors": float(walk_results["in_errors_map"].get(index) or 0),
                        "out_errors": float(walk_results["out_errors_map"].get(index) or 0),
                        "queue_length": float(walk_results["queue_length_map"].get(index) or 0),
                        "sample_seconds": 0.0,
                        "in_discards_delta": 0.0,
                        "out_discards_delta": 0.0,
                        "in_errors_delta": 0.0,
                        "out_errors_delta": 0.0,
                    },
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

            if in_bps is None and out_bps is None:
                continue

            def compute_delta(current: Any, old: Any) -> Optional[float]:
                if current is None or old is None:
                    return None
                delta = float(current) - float(old)
                return round(delta, 2) if delta >= 0 else None

            in_discards_delta = compute_delta(walk_results["in_discards_map"].get(index), previous_in_discards)
            out_discards_delta = compute_delta(walk_results["out_discards_map"].get(index), previous_out_discards)
            in_errors_delta = compute_delta(walk_results["in_errors_map"].get(index), previous_in_errors)
            out_errors_delta = compute_delta(walk_results["out_errors_map"].get(index), previous_out_errors)
            in_broadcast_delta = compute_delta(walk_results["in_broadcast_map"].get(index), previous_in_broadcast)
            out_broadcast_delta = compute_delta(walk_results["out_broadcast_map"].get(index), previous_out_broadcast)
            in_broadcast_pps = round((in_broadcast_delta or 0.0) / elapsed, 2) if in_broadcast_delta is not None else None
            out_broadcast_pps = round((out_broadcast_delta or 0.0) / elapsed, 2) if out_broadcast_delta is not None else None

            monitored_count += 1
            rate_suppressed = name in suppress_rate_interface_names or walk_results["if_descr_map"].get(index) in suppress_rate_interface_names
            if rate_suppressed:
                in_bps = None
                out_bps = None
                sample_seconds = None
            else:
                sample_seconds = round(elapsed, 2)
                in_bps, out_bps = self._sanitize_interface_rates(in_bps, out_bps, speed_bps)
            in_utilization = round((in_bps / speed_bps) * 100, 2) if in_bps is not None and speed_bps else None
            out_utilization = round((out_bps / speed_bps) * 100, 2) if out_bps is not None and speed_bps else None
            admin_status_text = status_map.get(walk_results["admin_status_map"].get(index), "unknown")
            oper_status_text = status_map.get(walk_results["oper_status_map"].get(index), "unknown")
            admin_status_up = admin_status_text == "up"
            oper_status_up = oper_status_text == "up"

            points.append({
                "measurement": "interface_monitoring",
                "tags": {
                    "device_id": str(device.id),
                    "device_name": device.name,
                    "interface_index": str(index),
                    "interface_name": name,
                },
                "fields": {
                    "in_bps": in_bps,
                    "out_bps": out_bps,
                    "in_utilization_percent": in_utilization,
                    "out_utilization_percent": out_utilization,
                    "in_discards": walk_results["in_discards_map"].get(index),
                    "out_discards": walk_results["out_discards_map"].get(index),
                    "in_discards_delta": in_discards_delta,
                    "out_discards_delta": out_discards_delta,
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
                    "admin_status": 1.0 if admin_status_up else 0.0,
                    "oper_status": 1.0 if oper_status_up else 0.0,
                    "admin_up_oper_down": 1.0 if admin_status_up and not oper_status_up else 0.0,
                    "sample_seconds": sample_seconds,
                },
                "timestamp": now,
            })

        if points:
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

        # 尝试Cisco方式 (used/free)
        used_oid = "1.3.6.1.4.1.9.9.48.1.1.1.5.1"
        free_oid = "1.3.6.1.4.1.9.9.48.1.1.1.6.1"
        
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
        entity_class_oid = private_oids.get("entity_class_oid")
        entity_name_oid = private_oids.get("entity_name_oid")
        entity_oper_status_oid = private_oids.get("entity_oper_status_oid")
        entity_error_status_oid = private_oids.get("entity_error_status_oid")
        if entity_class_oid:
            class_map = self._walk_indexed_map(device, str(entity_class_oid), int)
            name_map = self._walk_indexed_map(device, str(entity_name_oid), str) if entity_name_oid else {}
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
                return inventory_rows

        fan_state_oid = private_oids.get("fan_state_oid")
        fan_speed_oid = private_oids.get("fan_speed_oid")
        if fan_state_oid or fan_speed_oid:
            state_map = self._walk_indexed_map(device, str(fan_state_oid), int) if fan_state_oid else {}
            speed_map = self._walk_indexed_map(device, str(fan_speed_oid), float) if fan_speed_oid else {}
            for index in sorted(set(state_map) | set(speed_map), key=str):
                state = state_map.get(index)
                rows.append({
                    "component_type": "fan",
                    "component": str(index),
                    "state": float(state) if state is not None else None,
                    "up": 1.0 if state == 0 else 0.0 if state is not None else None,
                    "speed": self._normalize_numeric(speed_map.get(index)),
                    "present": 1.0,
                    "status_known": 1.0 if state is not None else 0.0,
                })
        power_state_oid = private_oids.get("power_state_oid")
        if power_state_oid:
            for index, state in self._walk_indexed_map(device, str(power_state_oid), int).items():
                rows.append({
                    "component_type": "power",
                    "component": str(index),
                    "state": float(state),
                    "up": 1.0 if state == 0 else 0.0,
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
    
    def collect_device(self, device: Any) -> Dict[str, Any]:
        """采集设备所有SNMP指标"""
        timestamp = datetime.now()
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
            "points_written": len(points)
        }


# 全局SNMP采集器实例
snmp_collector = SNMPCollector()
