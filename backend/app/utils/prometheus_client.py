"""
Prometheus HTTP API 客户端
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

import httpx

from app.core import get_logger

logger = get_logger(__name__)


class PrometheusClient:
    DEFAULT_METRICS = {
        "if_name": "ifName",
        "if_alias": "ifAlias",
        "if_admin_status": "ifAdminStatus",
        "if_oper_status": "ifOperStatus",
        "if_high_speed": "ifHighSpeed",
        "if_hc_in_octets": "ifHCInOctets",
        "if_hc_out_octets": "ifHCOutOctets",
        "if_in_errors": "ifInErrors",
        "if_out_errors": "ifOutErrors",
        "if_in_discards": "ifInDiscards",
        "if_out_discards": "ifOutDiscards",
    }
    ASTERNOS_PROFILE = "asternos"

    def _monitoring_config(self, device: Any) -> Dict[str, Any]:
        custom_fields = getattr(device, "custom_fields", None) or {}
        if not isinstance(custom_fields, dict):
            return {}
        monitoring = custom_fields.get("monitoring") or {}
        return monitoring if isinstance(monitoring, dict) else {}

    def _exporter_profile(self, device: Any) -> str:
        monitoring = self._monitoring_config(device)
        profile = str(
            monitoring.get("exporter_profile")
            or monitoring.get("profile")
            or ""
        ).strip().lower()
        vendor = str(getattr(device, "vendor", "") or "").strip().lower()
        if profile in {"asternos", "asterfusion"} or "aster" in vendor:
            return self.ASTERNOS_PROFILE
        return "generic"

    def _is_asternos(self, device: Any) -> bool:
        return self._exporter_profile(device) == self.ASTERNOS_PROFILE

    def _label_key(self, device: Any) -> str:
        monitoring = self._monitoring_config(device)
        if self._is_asternos(device):
            return str(monitoring.get("interface_label") or "device")
        return str(monitoring.get("interface_label") or "ifName")

    @staticmethod
    def _label_value(row: Dict[str, Any], *keys: str) -> Optional[str]:
        metric = row.get("metric", {}) or {}
        for key in keys:
            value = metric.get(key)
            if value not in (None, ""):
                return str(value)
        return None

    def _build_matchers(
        self,
        *,
        job: Optional[str],
        instance: Optional[str],
        extra: Optional[Dict[str, str]] = None,
    ) -> str:
        matchers: List[str] = []
        if job:
            matchers.append(f'job="{job}"')
        if instance:
            matchers.append(f'instance="{instance}"')
        for key, value in (extra or {}).items():
            if value:
                escaped_value = str(value).replace("\\", "\\\\").replace('"', '\\"')
                matchers.append(f'{key}="{escaped_value}"')
        return "{" + ",".join(matchers) + "}" if matchers else ""

    async def query(self, base_url: str, promql: str, query_time: Optional[datetime] = None) -> List[Dict[str, Any]]:
        params: Dict[str, Any] = {"query": promql}
        if query_time:
            if query_time.tzinfo is None:
                query_time = query_time.replace(tzinfo=timezone.utc)
            params["time"] = query_time.timestamp()
        async with httpx.AsyncClient(timeout=20.0) as client:
            response = await client.get(f"{base_url.rstrip('/')}/api/v1/query", params=params)
            response.raise_for_status()
            payload = response.json()
        if payload.get("status") != "success":
            raise ValueError(payload.get("error") or "Prometheus query failed")
        return payload.get("data", {}).get("result", [])

    async def query_range(
        self,
        base_url: str,
        promql: str,
        *,
        start: datetime,
        end: datetime,
        step: str,
    ) -> List[Dict[str, Any]]:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        async with httpx.AsyncClient(timeout=30.0) as client:
            response = await client.get(
                f"{base_url.rstrip('/')}/api/v1/query_range",
                params={
                    "query": promql,
                    "start": start.timestamp(),
                    "end": end.timestamp(),
                    "step": step,
                },
            )
            response.raise_for_status()
            payload = response.json()
        if payload.get("status") != "success":
            raise ValueError(payload.get("error") or "Prometheus range query failed")
        return payload.get("data", {}).get("result", [])

    def _metric_name(self, device: Any, key: str) -> str:
        monitoring = self._monitoring_config(device)
        metrics = monitoring.get("exporter_metrics") if isinstance(monitoring, dict) else {}
        return str((metrics or {}).get(key) or self.DEFAULT_METRICS[key])

    async def probe(self, device: Any) -> None:
        base_url = getattr(device, "prometheus_url", None)
        if not base_url:
            raise ValueError("设备未配置 Prometheus 地址")
        job = getattr(device, "prometheus_job", None)
        instance = getattr(device, "prometheus_instance", None)
        metric = "AsterNOS_interface_info" if self._is_asternos(device) else self._metric_name(device, "if_name")
        result = await self.query(base_url, f'{metric}{self._build_matchers(job=job, instance=instance)}')
        if not result:
            raise ValueError("Prometheus/Exporter 未返回接口数据")

    async def list_interfaces(self, device: Any) -> List[Dict[str, Any]]:
        if self._is_asternos(device):
            return await self._list_asternos_interfaces(device)

        base_url = getattr(device, "prometheus_url", None)
        job = getattr(device, "prometheus_job", None)
        instance = getattr(device, "prometheus_instance", None)
        matchers = self._build_matchers(job=job, instance=instance)
        queries = {
            "name": self._metric_name(device, "if_name"),
            "alias": self._metric_name(device, "if_alias"),
            "admin": self._metric_name(device, "if_admin_status"),
            "oper": self._metric_name(device, "if_oper_status"),
            "speed": self._metric_name(device, "if_high_speed"),
        }
        name_rows = await self.query(base_url, f'{queries["name"]}{matchers}')
        alias_rows = await self.query(base_url, f'{queries["alias"]}{matchers}')
        admin_rows = await self.query(base_url, f'{queries["admin"]}{matchers}')
        oper_rows = await self.query(base_url, f'{queries["oper"]}{matchers}')
        speed_rows = await self.query(base_url, f'{queries["speed"]}{matchers}')

        alias_map = {row.get("metric", {}).get("ifName"): row.get("value", [None, ""])[1] for row in alias_rows}
        admin_map = {row.get("metric", {}).get("ifName"): row.get("value", [None, ""])[1] for row in admin_rows}
        oper_map = {row.get("metric", {}).get("ifName"): row.get("value", [None, ""])[1] for row in oper_rows}
        speed_map = {row.get("metric", {}).get("ifName"): row.get("value", [None, ""])[1] for row in speed_rows}

        interfaces: List[Dict[str, Any]] = []
        for index, row in enumerate(name_rows, start=1):
            interface_name = row.get("value", [None, ""])[1]
            if not interface_name:
                continue
            admin_raw = str(admin_map.get(interface_name) or "")
            oper_raw = str(oper_map.get(interface_name) or "")
            speed_raw = speed_map.get(interface_name)
            interfaces.append(
                {
                    "index": index,
                    "name": interface_name,
                    "description": interface_name,
                    "alias": alias_map.get(interface_name) or None,
                    "admin_status": "up" if admin_raw in {"1", "up"} else "down",
                    "oper_status": "up" if oper_raw in {"1", "up"} else "down",
                    "speed_bps": float(speed_raw) * 1_000_000 if speed_raw not in (None, "") else None,
                }
            )
        interfaces.sort(key=lambda item: item["name"])
        return interfaces

    async def _list_asternos_interfaces(self, device: Any) -> List[Dict[str, Any]]:
        base_url = getattr(device, "prometheus_url", None)
        job = getattr(device, "prometheus_job", None)
        instance = getattr(device, "prometheus_instance", None)
        matchers = self._build_matchers(job=job, instance=instance)
        rows = await self.query(base_url, f"AsterNOS_interface_info{matchers}")

        interfaces: List[Dict[str, Any]] = []
        for position, row in enumerate(rows, start=1):
            metric = row.get("metric", {}) or {}
            interface_name = metric.get("device")
            if not interface_name:
                continue
            alias = metric.get("alias") or None
            description = metric.get("description") or alias or interface_name
            speed_mbps = metric.get("speed")
            speed_bps = None
            if speed_mbps not in (None, ""):
                try:
                    speed_bps = float(speed_mbps) * 1_000_000
                except ValueError:
                    speed_bps = None
            try:
                index = int(metric.get("index") or position)
            except ValueError:
                index = position

            interfaces.append(
                {
                    "index": index,
                    "name": str(interface_name),
                    "description": description,
                    "alias": alias,
                    "admin_status": "up" if metric.get("admin_status") == "up" else "down",
                    "oper_status": "up" if metric.get("operational_status") == "up" else "down",
                    "speed_bps": speed_bps,
                }
            )
        interfaces.sort(key=lambda item: item["index"])
        return interfaces

    async def get_interface_stats(self, device: Any, interface_name: str) -> Dict[str, Any]:
        if self._is_asternos(device):
            return await self._get_asternos_interface_stats(device, interface_name)

        base_url = getattr(device, "prometheus_url", None)
        job = getattr(device, "prometheus_job", None)
        instance = getattr(device, "prometheus_instance", None)
        metric_matchers = self._build_matchers(job=job, instance=instance, extra={"ifName": interface_name})
        metric_names = {
            "in_bps": f'rate({self._metric_name(device, "if_hc_in_octets")}{metric_matchers}[5m]) * 8',
            "out_bps": f'rate({self._metric_name(device, "if_hc_out_octets")}{metric_matchers}[5m]) * 8',
            "in_errors": f'{self._metric_name(device, "if_in_errors")}{metric_matchers}',
            "out_errors": f'{self._metric_name(device, "if_out_errors")}{metric_matchers}',
            "in_discards": f'{self._metric_name(device, "if_in_discards")}{metric_matchers}',
            "out_discards": f'{self._metric_name(device, "if_out_discards")}{metric_matchers}',
            "admin_status": f'{self._metric_name(device, "if_admin_status")}{metric_matchers}',
            "oper_status": f'{self._metric_name(device, "if_oper_status")}{metric_matchers}',
            "speed_mbps": f'{self._metric_name(device, "if_high_speed")}{metric_matchers}',
        }
        results: Dict[str, Any] = {"name": interface_name, "description": interface_name}
        for key, promql in metric_names.items():
            rows = await self.query(base_url, promql)
            if not rows:
                continue
            value = rows[0].get("value", [None, None])[1]
            if value in (None, ""):
                continue
            numeric = float(value)
            if key == "speed_mbps":
                results["speed_bps"] = numeric * 1_000_000
            elif key in {"admin_status", "oper_status"}:
                results[key] = "up" if numeric == 1 else "down"
            else:
                results[key] = numeric

        speed_bps = results.get("speed_bps") or 0
        if speed_bps > 0:
            if results.get("in_bps") is not None:
                results["in_utilization_percent"] = round((results["in_bps"] / speed_bps) * 100, 2)
            if results.get("out_bps") is not None:
                results["out_utilization_percent"] = round((results["out_bps"] / speed_bps) * 100, 2)
        return results

    async def _get_asternos_interface_stats(self, device: Any, interface_name: str) -> Dict[str, Any]:
        base_url = getattr(device, "prometheus_url", None)
        job = getattr(device, "prometheus_job", None)
        instance = getattr(device, "prometheus_instance", None)
        label_key = self._label_key(device)
        metric_matchers = self._build_matchers(job=job, instance=instance, extra={label_key: interface_name})
        interface_matchers = self._build_matchers(job=job, instance=instance, extra={"interface": interface_name})

        queries = {
            "in_bps": f"rate(AsterNOS_interface_receive_bytes_total{metric_matchers}[5m]) * 8",
            "out_bps": f"rate(AsterNOS_interface_transmit_bytes_total{metric_matchers}[5m]) * 8",
            "in_errors": f"AsterNOS_interface_receive_errs_total{metric_matchers}",
            "out_errors": f"AsterNOS_interface_transmit_errs_total{metric_matchers}",
            "in_discards": f"AsterNOS_interface_receive_drop_pkts_total{metric_matchers}",
            "out_discards": f"AsterNOS_interface_transmit_drop_pkts_total{metric_matchers}",
            "admin_status": f"AsterNOS_interface_admin_status{metric_matchers}",
            "oper_status": f"AsterNOS_interface_operational_status{metric_matchers}",
            "speed_bytes": f"AsterNOS_interface_speed_bytes{metric_matchers}",
            "rx_power": f"AsterNOS_dom_optic_rx_power{interface_matchers}",
            "tx_power": f"AsterNOS_dom_optic_tx_power{interface_matchers}",
        }

        interface_rows = await self.query(
            base_url,
            f"AsterNOS_interface_info{self._build_matchers(job=job, instance=instance, extra={label_key: interface_name})}",
        )
        interface_metric = interface_rows[0].get("metric", {}) if interface_rows else {}
        results: Dict[str, Any] = {
            "name": interface_name,
            "description": interface_metric.get("description") or interface_metric.get("alias") or interface_name,
            "alias": interface_metric.get("alias") or None,
        }

        if interface_metric:
            if interface_metric.get("admin_status"):
                results["admin_status"] = "up" if interface_metric.get("admin_status") == "up" else "down"
            if interface_metric.get("operational_status"):
                results["oper_status"] = "up" if interface_metric.get("operational_status") == "up" else "down"
            speed_mbps = interface_metric.get("speed")
            if speed_mbps not in (None, ""):
                try:
                    results["speed_bps"] = float(speed_mbps) * 1_000_000
                except ValueError:
                    pass

        for key, promql in queries.items():
            rows = await self.query(base_url, promql)
            if not rows:
                continue
            value = rows[0].get("value", [None, None])[1]
            if value in (None, "", "NaN", "+Inf", "-Inf"):
                continue
            try:
                numeric = float(value)
            except ValueError:
                continue
            if key == "speed_bytes":
                results["speed_bps"] = numeric * 8
            elif key in {"admin_status", "oper_status"}:
                results[key] = "up" if numeric == 1 else "down"
            else:
                results[key] = numeric

        speed_bps = results.get("speed_bps") or 0
        if speed_bps > 0:
            if results.get("in_bps") is not None:
                results["in_utilization_percent"] = round((results["in_bps"] / speed_bps) * 100, 2)
            if results.get("out_bps") is not None:
                results["out_utilization_percent"] = round((results["out_bps"] / speed_bps) * 100, 2)
        return results

    async def get_interface_history(
        self,
        device: Any,
        interface_name: str,
        *,
        start: datetime,
        end: datetime,
        step: str,
    ) -> List[Dict[str, Any]]:
        if self._is_asternos(device):
            return await self._get_asternos_interface_history(
                device,
                interface_name,
                start=start,
                end=end,
                step=step,
            )

        base_url = getattr(device, "prometheus_url", None)
        job = getattr(device, "prometheus_job", None)
        instance = getattr(device, "prometheus_instance", None)
        metric_matchers = self._build_matchers(job=job, instance=instance, extra={"ifName": interface_name})
        queries = {
            "in_bps": f'rate({self._metric_name(device, "if_hc_in_octets")}{metric_matchers}[5m]) * 8',
            "out_bps": f'rate({self._metric_name(device, "if_hc_out_octets")}{metric_matchers}[5m]) * 8',
        }
        history: Dict[int, Dict[str, Any]] = {}
        for field, promql in queries.items():
            rows = await self.query_range(base_url, promql, start=start, end=end, step=step)
            for series in rows:
                for ts, value in series.get("values", []):
                    timestamp_ms = int(float(ts) * 1000)
                    point = history.setdefault(timestamp_ms, {"_time": datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()})
                    point[field] = float(value)
        return [history[key] for key in sorted(history.keys())]

    async def get_device_metric(self, device: Any, metric_type: str) -> Optional[float]:
        if not self._is_asternos(device):
            return None

        metric_map = {
            "snmp_cpu": "AsterNOS_device_cpu_usage",
            "snmp_memory": "AsterNOS_device_memory_usage",
            "device_status": "AsterNOS_device_system_status",
        }
        metric_name = metric_map.get(metric_type)
        if not metric_name:
            return None

        base_url = getattr(device, "prometheus_url", None)
        job = getattr(device, "prometheus_job", None)
        instance = getattr(device, "prometheus_instance", None)
        rows = await self.query(base_url, f"{metric_name}{self._build_matchers(job=job, instance=instance)}")
        if not rows:
            return None
        value = rows[0].get("value", [None, None])[1]
        if value in (None, "", "NaN", "+Inf", "-Inf"):
            return None
        try:
            return float(value)
        except ValueError:
            return None

    async def _get_asternos_interface_history(
        self,
        device: Any,
        interface_name: str,
        *,
        start: datetime,
        end: datetime,
        step: str,
    ) -> List[Dict[str, Any]]:
        base_url = getattr(device, "prometheus_url", None)
        job = getattr(device, "prometheus_job", None)
        instance = getattr(device, "prometheus_instance", None)
        label_key = self._label_key(device)
        metric_matchers = self._build_matchers(job=job, instance=instance, extra={label_key: interface_name})
        queries = {
            "in_bps": f"rate(AsterNOS_interface_receive_bytes_total{metric_matchers}[5m]) * 8",
            "out_bps": f"rate(AsterNOS_interface_transmit_bytes_total{metric_matchers}[5m]) * 8",
        }
        history: Dict[int, Dict[str, Any]] = {}
        for field, promql in queries.items():
            rows = await self.query_range(base_url, promql, start=start, end=end, step=step)
            for series in rows:
                for ts, value in series.get("values", []):
                    timestamp_ms = int(float(ts) * 1000)
                    point = history.setdefault(timestamp_ms, {"_time": datetime.fromtimestamp(float(ts), tz=timezone.utc).isoformat()})
                    point[field] = float(value)
        return [history[key] for key in sorted(history.keys())]


prometheus_client = PrometheusClient()
