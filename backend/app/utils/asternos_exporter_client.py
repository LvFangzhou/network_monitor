"""
AsterNOS Exporter direct client.
"""
from __future__ import annotations

import math
import re
import time
from typing import Any, Dict, List, Optional

import httpx


class AsterNOSExporterClient:
    LINE_RE = re.compile(r"^(?P<name>[a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{(?P<labels>.*)\})?\s+(?P<value>\S+)")
    LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\.|[^"\\])*)"')
    ASTERNOS_PREFIX = "AsterNOS_"

    def _base_url(self, device: Any) -> str:
        custom_fields = getattr(device, "custom_fields", None) or {}
        monitoring = custom_fields.get("monitoring") if isinstance(custom_fields, dict) else {}
        configured = ""
        if isinstance(monitoring, dict):
            configured = str(monitoring.get("exporter_url") or monitoring.get("asternos_exporter_url") or "")
        if configured:
            configured = configured.strip()
            if not configured.startswith(("http://", "https://")):
                configured = f"http://{configured}"
            return configured.rstrip("/")
        return f"http://{device.ip_address}:8101"

    def _metric_url(self, device: Any) -> str:
        base_url = self._base_url(device)
        return base_url if base_url.endswith("/metrics") else f"{base_url}/metrics"

    @staticmethod
    def _parse_value(value: str) -> Optional[float]:
        if value in {"NaN", "+Inf", "-Inf"}:
            return None
        try:
            parsed = float(value)
        except ValueError:
            return None
        if math.isinf(parsed) or math.isnan(parsed):
            return None
        return parsed

    @classmethod
    def _parse_labels(cls, raw_labels: Optional[str]) -> Dict[str, str]:
        if not raw_labels:
            return {}
        labels: Dict[str, str] = {}
        for key, value in cls.LABEL_RE.findall(raw_labels):
            labels[key] = value.replace(r"\"", '"').replace(r"\\", "\\")
        return labels

    async def scrape(self, device: Any) -> Dict[str, List[Dict[str, Any]]]:
        timeout = httpx.Timeout(8.0, connect=2.0)
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(self._metric_url(device))
            response.raise_for_status()

        metrics: Dict[str, List[Dict[str, Any]]] = {}
        for line in response.text.splitlines():
            line = line.strip()
            if not line or line.startswith("#"):
                continue
            match = self.LINE_RE.match(line)
            if not match:
                continue
            value = self._parse_value(match.group("value"))
            if value is None:
                continue
            name = match.group("name")
            metrics.setdefault(name, []).append(
                {
                    "metric": self._parse_labels(match.group("labels")),
                    "value": value,
                }
            )
        return metrics

    async def probe(self, device: Any) -> None:
        metrics = await self.scrape(device)
        if not self._rows(metrics, "interface_info"):
            raise ValueError("AsterNOS Exporter 未返回接口数据")

    @classmethod
    def _metric_name(cls, metrics: Dict[str, List[Dict[str, Any]]], base_name: str) -> str:
        prefixed = f"{cls.ASTERNOS_PREFIX}{base_name}"
        if prefixed in metrics:
            return prefixed
        return base_name

    @classmethod
    def _rows(cls, metrics: Dict[str, List[Dict[str, Any]]], base_name: str) -> List[Dict[str, Any]]:
        return metrics.get(cls._metric_name(metrics, base_name)) or []

    @classmethod
    def _first(cls, metrics: Dict[str, List[Dict[str, Any]]], base_name: str) -> Optional[float]:
        rows = cls._rows(metrics, base_name)
        return rows[0].get("value") if rows else None

    @classmethod
    def system_info(cls, metrics: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
        """提取 AsterNOS 设备基础信息，用于设备总览一致性对比。"""
        info_labels = (cls._rows(metrics, "device_info") or [{}])[0].get("metric", {}) or {}
        raw_uptime = cls._first(metrics, "device_up_time")
        uptime_seconds = None
        if raw_uptime is not None:
            now = time.time()
            # AsterNOS exporter 的 device_up_time 在现网中表现为启动时间戳；
            # 兼容少数 exporter 直接返回运行秒数的情况。
            if 946684800 <= raw_uptime <= now + 86400:
                uptime_seconds = max(0, now - raw_uptime)
            else:
                uptime_seconds = max(0, raw_uptime)

        hostname = info_labels.get("hostname")
        product_name = info_labels.get("product_name") or info_labels.get("platform_name")
        software_version = info_labels.get("software_version")
        serial_number = info_labels.get("serial_number")
        platform_name = info_labels.get("platform_name")
        sys_descr_parts = [
            part for part in [
                product_name,
                f"Software {software_version}" if software_version else None,
                f"Serial {serial_number}" if serial_number else None,
            ]
            if part
        ]
        return {
            "sys_name": hostname,
            "sys_descr": " / ".join(sys_descr_parts) or None,
            "software_version": software_version,
            "snmp_model": product_name,
            "serial_number": serial_number,
            "platform_name": platform_name,
            "uptime_seconds": uptime_seconds,
        }

    async def list_interfaces(self, device: Any) -> List[Dict[str, Any]]:
        metrics = await self.scrape(device)
        rows = self._rows(metrics, "interface_info")
        interfaces: List[Dict[str, Any]] = []
        for position, row in enumerate(rows, start=1):
            labels = row.get("metric", {}) or {}
            interface_name = labels.get("device")
            if not interface_name:
                continue
            try:
                index = int(labels.get("index") or position)
            except ValueError:
                index = position
            speed_bps = None
            speed_mbps = labels.get("speed")
            if speed_mbps not in (None, ""):
                try:
                    speed_bps = float(speed_mbps) * 1_000_000
                except ValueError:
                    speed_bps = None
            interfaces.append(
                {
                    "index": index,
                    "name": interface_name,
                    "description": labels.get("description") or labels.get("alias") or interface_name,
                    "alias": labels.get("alias") or None,
                    "admin_status": "up" if labels.get("admin_status") == "up" else "down",
                    "oper_status": "up" if labels.get("operational_status") == "up" else "down",
                    "speed_bps": speed_bps,
                }
            )
        interfaces.sort(key=lambda item: item["index"])
        return interfaces

    @staticmethod
    def _by_label(metrics: Dict[str, List[Dict[str, Any]]], metric_name: str, label: str, value: str) -> Optional[Dict[str, Any]]:
        for row in metrics.get(metric_name) or []:
            if (row.get("metric") or {}).get(label) == value:
                return row
        return None

    @classmethod
    def _by_base_metric_label(
        cls,
        metrics: Dict[str, List[Dict[str, Any]]],
        base_name: str,
        label: str,
        value: str,
    ) -> Optional[Dict[str, Any]]:
        return cls._by_label(metrics, cls._metric_name(metrics, base_name), label, value)

    async def get_interface_stats(self, device: Any, interface_name: str) -> Dict[str, Any]:
        metrics = await self.scrape(device)
        info = self._by_base_metric_label(metrics, "interface_info", "device", interface_name)
        labels = (info or {}).get("metric", {}) or {}
        result: Dict[str, Any] = {
            "name": interface_name,
            "description": labels.get("description") or labels.get("alias") or interface_name,
            "alias": labels.get("alias") or None,
        }
        if labels:
            result["admin_status"] = "up" if labels.get("admin_status") == "up" else "down"
            result["oper_status"] = "up" if labels.get("operational_status") == "up" else "down"
            if labels.get("speed"):
                result["speed_bps"] = float(labels["speed"]) * 1_000_000

        metric_map = {
            "in_octets": "AsterNOS_interface_receive_bytes_total",
            "out_octets": "AsterNOS_interface_transmit_bytes_total",
            "in_bps": "AsterNOS_interface_receive_rate_bps",
            "out_bps": "AsterNOS_interface_transmit_rate_bps",
            "in_errors": "AsterNOS_interface_receive_errs_total",
            "out_errors": "AsterNOS_interface_transmit_errs_total",
            "in_discards": "AsterNOS_interface_receive_drops_total",
            "out_discards": "AsterNOS_interface_transmit_drops_total",
            "in_utilization_percent": "AsterNOS_interface_receive_util",
            "out_utilization_percent": "AsterNOS_interface_transmit_util",
        }
        for field, metric_name in metric_map.items():
            base_name = metric_name.removeprefix(self.ASTERNOS_PREFIX)
            row = self._by_base_metric_label(metrics, base_name, "device", interface_name)
            if row:
                result[field] = row.get("value")

        for field, metric_name in {
            "rx_power": "AsterNOS_dom_optic_rx_power",
            "tx_power": "AsterNOS_dom_optic_tx_power",
            "optic_temperature": "AsterNOS_dom_optic_tempt",
        }.items():
            base_name = metric_name.removeprefix(self.ASTERNOS_PREFIX)
            row = self._by_base_metric_label(metrics, base_name, "interface", interface_name)
            if row:
                result[field] = row.get("value")

        return result

    async def get_device_metrics(self, device: Any) -> Dict[str, Any]:
        metrics = await self.scrape(device)

        device_info = (self._rows(metrics, "device_info") or [{}])[0].get("metric", {})
        return {
            "info": device_info,
            "cpu_usage": self._first(metrics, "device_cpu_usage"),
            "memory_usage": self._first(metrics, "device_memory_usage"),
            "system_status": self._first(metrics, "device_system_status"),
            "uptime": self._first(metrics, "device_up_time"),
            "temperature": self._rows(metrics, "device_sensor_tempt"),
            "fans": self._rows(metrics, "device_fan_operational_status"),
            "psu": self._rows(metrics, "psu_power_input"),
            "bgp": self._rows(metrics, "bgp_status"),
            "ospf": self._rows(metrics, "ospf_status"),
            "mclag": self._rows(metrics, "mclag_status_info"),
            "crm": self._rows(metrics, "crm_resource_percent"),
            "queue": {
                "egress_dropped_pkts": self._rows(metrics, "queue_egress_dropped_pkts"),
                "ingress_dropped_pkts": self._rows(metrics, "queue_ingress_dropped_pkts"),
            },
            "roce": {
                "pfc_rx_pkts": self._rows(metrics, "pfc_rx_pkts"),
                "pfc_tx_pkts": self._rows(metrics, "pfc_tx_pkts"),
                "ecn_marked_pkts": self._rows(metrics, "ecn_marked_pkts"),
            },
        }


asternos_exporter_client = AsterNOSExporterClient()
