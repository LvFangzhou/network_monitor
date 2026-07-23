"""Normalize H3C ARP and route Telemetry snapshots.

The device sends complete current-state tables.  Keep the current snapshot in
Redis and write only compact counts/change metrics to InfluxDB; this avoids
turning prefixes and next hops into high-cardinality time-series tags.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from typing import Any, Dict, Iterable, List


FORWARDING_SENSOR_PATHS = {
    "arp/arptable",
    "route/ipv4routes",
}


def forwarding_table_name(sensor_path: str) -> str:
    path = (sensor_path or "").lower()
    if path.startswith("arp/"):
        return "arp"
    return "ipv6_routes" if "ipv6" in path else "ipv4_routes"


def forwarding_cache_key(device_id: int, table_name: str) -> str:
    return f"monitor:cache:telemetry_forwarding:{int(device_id)}:{table_name}"


def _as_list(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, dict):
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return []


def _integer(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _mac(value: Any) -> str:
    text = str(value or "").strip().replace("-", ":").lower()
    return text


def normalize_forwarding_payload(sensor_path: str, obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    notification = obj.get("Notification") or {}
    table_name = forwarding_table_name(sensor_path)
    if table_name == "arp":
        source = notification.get("ARP", {}).get("ArpTable", {}).get("ArpEntry", [])
        rows = []
        for item in _as_list(source):
            ip_address = str(item.get("Ipv4Address") or "").strip()
            if not ip_address:
                continue
            rows.append({
                "ip_address": ip_address,
                "mac_address": _mac(item.get("MacAddress")),
                "interface": str(item.get("IfName") or "").strip(),
                "if_index": _integer(item.get("IfIndex")),
                "vrf": str(item.get("VRF") or item.get("VrfName") or "").strip(),
                "vrf_index": _integer(item.get("VrfIndex")),
                "arp_type": _integer(item.get("ArpType"), -1),
                "state": str(item.get("State") or item.get("Status") or "").strip(),
                "age_seconds": _integer(item.get("Age"), -1),
            })
        return rows

    branch = "Ipv6Routes" if table_name == "ipv6_routes" else "Ipv4Routes"
    source = notification.get("Route", {}).get(branch, {}).get("RouteEntry", [])
    rows = []
    for item in _as_list(source):
        prefix_data = item.get("Ipv6Prefix") if table_name == "ipv6_routes" else item.get("Ipv4")
        prefix_data = prefix_data if isinstance(prefix_data, dict) else {}
        address = str(prefix_data.get("Ipv6Address") or prefix_data.get("Ipv4Address") or "").strip()
        prefix_length_value = (
            prefix_data.get("Ipv6PrefixLength")
            if table_name == "ipv6_routes"
            else prefix_data.get("Ipv4PrefixLength")
        )
        prefix_length = _integer(prefix_length_value, -1)
        if not address or prefix_length < 0:
            continue
        protocol = item.get("Protocol") if isinstance(item.get("Protocol"), dict) else {}
        as_number = item.get("ASNumber") if isinstance(item.get("ASNumber"), dict) else {}
        interface = str(item.get("IfName") or "").strip()
        next_hop = str(item.get("Nexthop") or "").strip()
        rows.append({
            "prefix": f"{address}/{prefix_length}",
            "address": address,
            "prefix_length": prefix_length,
            "vrf": str(item.get("VRF") or "default").strip() or "default",
            "topology": str(item.get("Topology") or "").strip(),
            "next_hop": next_hop,
            "interface": interface,
            "if_index": _integer(item.get("IfIndex")),
            "protocol_id": _integer(protocol.get("ProtocolID"), -1),
            "sub_protocol_id": _integer(protocol.get("SubProtocolID"), -1),
            "process_id": _integer(item.get("ProcessID")),
            "preference": _integer(item.get("Preference")),
            "metric": _integer(item.get("Metric")),
            "age_seconds": _integer(item.get("Age"), -1),
            "flags": str(item.get("Flags") or "").strip(),
            "neighbor": str(item.get("Neighbor") or "").strip(),
            "origin_as": str(as_number.get("OriginASExt") or as_number.get("OriginAS") or "").strip(),
            "last_as": str(as_number.get("LastASExt") or as_number.get("LastAS") or "").strip(),
            "blackhole": interface.lower() in {"null0", "null", "discard"},
        })
    return rows


def _route_prefix_counts(rows: Iterable[Dict[str, Any]]) -> Counter:
    return Counter((str(row.get("vrf") or "default"), str(row.get("prefix") or "")) for row in rows)


def build_forwarding_summary(
    table_name: str,
    rows: List[Dict[str, Any]],
    previous_rows: List[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    previous_rows = previous_rows or []
    if table_name == "arp":
        current = {(row.get("vrf_index"), row.get("ip_address")): row for row in rows}
        previous = {(row.get("vrf_index"), row.get("ip_address")): row for row in previous_rows}
        common = current.keys() & previous.keys()
        ip_macs: Dict[tuple[Any, Any], set[str]] = defaultdict(set)
        for row in rows:
            mac_address = str(row.get("mac_address") or "").strip()
            if mac_address:
                ip_macs[(row.get("vrf_index"), row.get("ip_address"))].add(mac_address)
        return {
            "total": len(rows),
            "incomplete": sum(1 for row in rows if not row.get("mac_address") or str(row.get("state", "")).lower() in {"incomplete", "failed"}),
            "added": len(current.keys() - previous.keys()),
            "removed": len(previous.keys() - current.keys()),
            "mac_changed": sum(1 for key in common if current[key].get("mac_address") != previous[key].get("mac_address")),
            "duplicate_ip": sum(1 for mac_addresses in ip_macs.values() if len(mac_addresses) > 1),
        }

    current_keys = {
        (row.get("vrf"), row.get("prefix"), row.get("next_hop"), row.get("interface"))
        for row in rows
    }
    previous_keys = {
        (row.get("vrf"), row.get("prefix"), row.get("next_hop"), row.get("interface"))
        for row in previous_rows
    }
    prefix_counts = _route_prefix_counts(rows)
    previous_prefix_counts = _route_prefix_counts(previous_rows)
    return {
        "total": len(rows),
        "prefix_total": len(prefix_counts),
        "ecmp_prefixes": sum(1 for count in prefix_counts.values() if count > 1),
        "blackhole_routes": sum(1 for row in rows if row.get("blackhole")),
        "added": len(current_keys - previous_keys),
        "removed": len(previous_keys - current_keys),
        "ecmp_changed": sum(1 for key in prefix_counts.keys() & previous_prefix_counts.keys() if prefix_counts[key] != previous_prefix_counts[key]),
    }


def annotate_ecmp(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    counts: Dict[tuple[str, str], int] = defaultdict(int)
    for row in rows:
        counts[(str(row.get("vrf") or "default"), str(row.get("prefix") or ""))] += 1
    return [{**row, "ecmp_count": counts[(str(row.get("vrf") or "default"), str(row.get("prefix") or ""))]} for row in rows]
