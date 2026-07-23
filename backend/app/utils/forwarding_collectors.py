"""Collect current ARP and route snapshots without requiring Telemetry."""

from __future__ import annotations

import asyncio
import ipaddress
import json
import re
from datetime import datetime, timezone
from typing import Any, Dict, List, Tuple

from app.collectors import snmp_collector
from app.utils import redis_client
from app.utils.asternos_exporter_client import asternos_exporter_client
from app.utils.telemetry_forwarding import (
    annotate_ecmp,
    build_forwarding_summary,
    forwarding_cache_key,
)


FORWARDING_CACHE_TTL_SECONDS = 12 * 60 * 60


def _valid_walk_map(device: Any, oid: str, cast=None) -> Dict[str, Any]:
    values = snmp_collector._walk_indexed_map(device, oid, cast)
    return {
        str(index): value
        for index, value in values.items()
        if "no such" not in str(value or "").lower()
    }


def _cached_interface_names(device_id: int) -> Dict[str, str]:
    raw = redis_client.get(f"monitor:cache:interfaces:{int(device_id)}")
    if not raw:
        return {}
    try:
        payload = json.loads(raw)
    except (TypeError, ValueError):
        return {}
    rows = payload.get("interfaces") if isinstance(payload, dict) else []
    return {
        str(row.get("index")): str(row.get("name") or row.get("description") or "")
        for row in rows or []
        if isinstance(row, dict) and row.get("index") is not None
    }


def _interface_names(device: Any) -> Dict[str, str]:
    cached = _cached_interface_names(int(device.id))
    if cached:
        return cached
    return _valid_walk_map(device, "1.3.6.1.2.1.31.1.1.1.1", str)


def _format_mac(value: Any) -> str:
    text = str(value or "").strip().replace("-", ":")
    if re.fullmatch(r"(?:[0-9A-Fa-f]{2}\s+){5}[0-9A-Fa-f]{2}", text):
        text = ":".join(text.split())
    return text.lower()


def collect_snmp_arp(device: Any, if_names: Dict[str, str] | None = None) -> List[Dict[str, Any]]:
    if_names = if_names or _interface_names(device)
    base = "1.3.6.1.2.1.4.22.1"
    ip_map = _valid_walk_map(device, f"{base}.3", str)
    mac_map = _valid_walk_map(device, f"{base}.2", str)
    type_map = _valid_walk_map(device, f"{base}.4", int)
    rows: List[Dict[str, Any]] = []
    for index, ip_address in ip_map.items():
        parts = index.split(".")
        if_index = parts[0] if parts else "0"
        rows.append({
            "ip_address": str(ip_address),
            "mac_address": _format_mac(mac_map.get(index)),
            "interface": if_names.get(str(if_index), ""),
            "if_index": int(if_index) if str(if_index).isdigit() else 0,
            "vrf": "default",
            "vrf_index": 0,
            "arp_type": int(type_map.get(index, -1)),
            "state": "resolved" if mac_map.get(index) else "incomplete",
            "age_seconds": -1,
        })
    return rows


def _mask_prefix_length(mask: Any) -> int:
    try:
        return ipaddress.IPv4Network(f"0.0.0.0/{mask}").prefixlen
    except (ValueError, TypeError):
        return 0


def _route_rows_from_maps(
    dest_map: Dict[str, Any],
    mask_map: Dict[str, Any],
    next_hop_map: Dict[str, Any],
    if_index_map: Dict[str, Any],
    proto_map: Dict[str, Any],
    if_names: Dict[str, str],
) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for index, destination in dest_map.items():
        prefix_length = _mask_prefix_length(mask_map.get(index, "0.0.0.0"))
        if_index = int(if_index_map.get(index, 0) or 0)
        interface = if_names.get(str(if_index), "")
        next_hop = str(next_hop_map.get(index) or "")
        address = str(destination)
        rows.append({
            "prefix": f"{address}/{prefix_length}",
            "address": address,
            "prefix_length": prefix_length,
            "vrf": "default",
            "topology": "",
            "next_hop": next_hop,
            "interface": interface,
            "if_index": if_index,
            "protocol_id": int(proto_map.get(index, -1) or -1),
            "sub_protocol_id": -1,
            "process_id": 0,
            "preference": 0,
            "metric": 0,
            "age_seconds": -1,
            "flags": "",
            "neighbor": "",
            "origin_as": "",
            "last_as": "",
            "blackhole": interface.lower() in {"null0", "null", "discard"},
        })
    return annotate_ecmp(rows)


def collect_snmp_ipv4_routes(device: Any, if_names: Dict[str, str] | None = None) -> Tuple[List[Dict[str, Any]], str]:
    if_names = if_names or _interface_names(device)
    cidr = "1.3.6.1.2.1.4.24.4.1"
    dest_map = _valid_walk_map(device, f"{cidr}.1", str)
    if dest_map:
        rows = _route_rows_from_maps(
            dest_map,
            _valid_walk_map(device, f"{cidr}.2", str),
            _valid_walk_map(device, f"{cidr}.4", str),
            _valid_walk_map(device, f"{cidr}.5", int),
            _valid_walk_map(device, f"{cidr}.7", int),
            if_names,
        )
        return rows, "ipCidrRouteTable"

    # Some Hillstone releases expose only the older MIB-II ipRouteTable.
    old = "1.3.6.1.2.1.4.21.1"
    dest_map = _valid_walk_map(device, f"{old}.1", str)
    rows = _route_rows_from_maps(
        dest_map,
        _valid_walk_map(device, f"{old}.11", str),
        _valid_walk_map(device, f"{old}.7", str),
        _valid_walk_map(device, f"{old}.2", int),
        _valid_walk_map(device, f"{old}.9", int),
        if_names,
    )
    return rows, "ipRouteTable"


def _strip_cli(text: str) -> str:
    text = re.sub(r"\x1b\[[0-9;?]*[A-Za-z]", "", text or "")
    return text.replace("\r", "")


def parse_asternos_arp(output: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for line in _strip_cli(output).splitlines():
        match = re.match(r"^\s*(\d{1,3}(?:\.\d{1,3}){3})\s+([0-9A-Fa-f:]{17})\s+(.+?)\s+(\S+)\s*$", line)
        if not match:
            continue
        ip_address, mac_address, interface, vlan = match.groups()
        rows.append({
            "ip_address": ip_address,
            "mac_address": mac_address.lower(),
            "interface": interface.strip(),
            "if_index": 0,
            "vrf": f"VLAN {vlan}" if vlan != "-" else "default",
            "vrf_index": int(vlan) if vlan.isdigit() else 0,
            "arp_type": -1,
            "state": "resolved",
            "age_seconds": -1,
            "vlan": vlan,
        })
    return rows


def parse_asternos_routes(output: str) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    current: Dict[str, Any] | None = None
    for raw_line in _strip_cli(output).splitlines():
        line = raw_line.rstrip()
        route_match = re.match(r"^\s*([A-Za-z]+[^\d]*?)\s+(\d{1,3}(?:\.\d{1,3}){3}/\d+)\s+(.+)$", line)
        if route_match:
            flags, prefix, detail = route_match.groups()
            if "*" not in flags:
                current = None
                continue
            current = {"flags": flags.strip(), "prefix": prefix, "detail": detail}
        elif current and re.match(r"^\s*\*\s+", line):
            detail = re.sub(r"^\s*\*\s+", "", line)
            current = {**current, "detail": detail}
        else:
            continue

        prefix = current["prefix"]
        detail = current["detail"]
        address, prefix_length_text = prefix.split("/", 1)
        preference = metric = 0
        pref_match = re.search(r"\[(\d+)/(\d+)\]", detail)
        if pref_match:
            preference, metric = int(pref_match.group(1)), int(pref_match.group(2))
        via_match = re.search(r"\bvia\s+([^,\s]+),\s*(.*?)(?:,\s*weight\b|,\s*\d+[wdhms]|$)", detail)
        direct_match = re.search(r"directly connected,\s*(.*?)(?:,\s*\d+[wdhms]|$)", detail)
        if via_match:
            next_hop, interface = via_match.group(1), via_match.group(2).strip()
        elif direct_match:
            next_hop, interface = "0.0.0.0", direct_match.group(1).strip()
        else:
            continue
        rows.append({
            "prefix": prefix,
            "address": address,
            "prefix_length": int(prefix_length_text),
            "vrf": "default",
            "topology": "",
            "next_hop": next_hop,
            "interface": interface,
            "if_index": 0,
            "protocol_id": -1,
            "sub_protocol_id": -1,
            "process_id": 0,
            "preference": preference,
            "metric": metric,
            "age_seconds": -1,
            "flags": current["flags"],
            "protocol": re.sub(r"[^A-Za-z]", "", current["flags"]),
            "neighbor": "",
            "origin_as": "",
            "last_as": "",
            "blackhole": interface.lower() in {"null0", "null", "discard"},
        })
    return annotate_ecmp(rows)


def _exporter_forwarding_summary(metrics: Dict[str, List[Dict[str, Any]]]) -> Dict[str, Any]:
    def total(name: str) -> float:
        return float(sum(float(row.get("value") or 0) for row in asternos_exporter_client._rows(metrics, name)))

    return {
        "evpn_arp_cached": total("evpn_arps_cached_num"),
        "evpn_mac_learned": total("evpn_mac_learned_vni_num"),
        "evpn_remote_vteps": total("evpn_remote_vteps_num"),
    }


def _store_snapshot(
    device: Any,
    table_name: str,
    rows: List[Dict[str, Any]],
    source: str,
    message: str = "",
    extra_summary: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    key = forwarding_cache_key(int(device.id), table_name)
    previous_rows: List[Dict[str, Any]] = []
    previous_raw = redis_client.get(key)
    if previous_raw:
        try:
            previous = json.loads(previous_raw)
            previous_rows = previous.get("rows") if isinstance(previous.get("rows"), list) else []
        except (TypeError, ValueError):
            previous_rows = []
    summary = build_forwarding_summary(table_name, rows, previous_rows)
    if extra_summary:
        summary.update(extra_summary)
    payload = {
        "device_id": int(device.id),
        "device_name": device.name,
        "device_ip": device.ip_address,
        "vendor": device.vendor,
        "table": table_name,
        "source": source,
        "message": message,
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "summary": summary,
        "rows": rows,
    }
    redis_client.setex(key, FORWARDING_CACHE_TTL_SECONDS, json.dumps(payload, ensure_ascii=False, default=str))
    return payload


def collect_snmp_forwarding(device: Any) -> Dict[str, Any]:
    if_names = _interface_names(device)
    arp_rows = collect_snmp_arp(device, if_names)
    route_rows, route_table = collect_snmp_ipv4_routes(device, if_names)
    identity = " ".join([str(device.vendor or ""), str(device.model or "")]).lower()
    limited = any(marker in identity for marker in ("hillstone", "山石", "sg-6000"))
    message = f"通过SNMP标准MIB采集；IPv4路由来源 {route_table}"
    if limited:
        message += "；山石默认SNMP上下文可能只返回部分路由实例"
    arp = _store_snapshot(device, "arp", arp_rows, "snmp", message)
    ipv4 = _store_snapshot(device, "ipv4_routes", route_rows, "snmp", message)
    return {"device_id": device.id, "source": "snmp", "arp": len(arp_rows), "ipv4_routes": len(route_rows), "route_table": route_table, "collected_at": ipv4["collected_at"]}


def collect_asternos_forwarding(device: Any) -> Dict[str, Any]:
    metrics = asyncio.run(asternos_exporter_client.scrape(device))
    exporter_summary = _exporter_forwarding_summary(metrics)
    from app.routers.metrics import _run_lldp_cli_command

    output = _run_lldp_cli_command(device, ["show arp", "show ip route"])
    arp_rows = parse_asternos_arp(output)
    route_rows = parse_asternos_routes(output)
    message = "AsterNOS Exporter提供EVPN汇总，ARP与FIB明细通过CLI辅助采集"
    arp = _store_snapshot(device, "arp", arp_rows, "asternos_exporter_cli", message, exporter_summary)
    ipv4 = _store_snapshot(device, "ipv4_routes", route_rows, "asternos_exporter_cli", message, exporter_summary)
    return {"device_id": device.id, "source": "asternos_exporter_cli", "arp": len(arp_rows), "ipv4_routes": len(route_rows), "exporter_summary": exporter_summary, "collected_at": ipv4["collected_at"]}


def collect_device_forwarding(device: Any) -> Dict[str, Any]:
    source = re.sub(r"[^a-z0-9]", "", str(device.monitor_source or "").lower())
    vendor = re.sub(r"[^a-z0-9]", "", str(device.vendor or "").lower())
    if "asternos" in source or "aster" in vendor:
        return collect_asternos_forwarding(device)
    return collect_snmp_forwarding(device)
