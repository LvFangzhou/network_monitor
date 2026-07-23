"""Normalize H3C Dial-out Telemetry lossless-network payloads.

The vendor payloads use a mixture of scalar values, JSON-encoded arrays and
per-slice dictionaries.  This module converts them into stable port/queue rows
without inventing values that are not present in the source payload.
"""
from __future__ import annotations

import json
import re
from typing import Any, Dict, List


LOSSLESS_SENSOR_PATHS = {
    "ifmgr/interfaces",
    "ifmgr/statistics",
    "ifmgr/ethportstatistics",
    "buffermonitor/commbufferusages",
    "buffermonitor/commheadroomusages",
    "buffermonitor/ecnandwredstatistics",
    "buffermonitor/egressdrops",
    "buffermonitor/ingressdrops",
    "buffermonitor/pfcspeeds",
    "buffermonitor/pfcstatistics",
    "qstat/queuestat",
    "qos/interfaces/interface/input/queues/queue/state",
    "pfc/pfcports/port",
    "ifmgr/iffecdata",
}


def sensor_cache_suffix(sensor_path: str) -> str:
    return ":" + re.sub(r"[^a-z0-9]+", "_", (sensor_path or "").lower()).strip("_")


def _rows(value: Any) -> List[Dict[str, Any]]:
    if isinstance(value, list):
        return [item for item in value if isinstance(item, dict)]
    return [value] if isinstance(value, dict) else []


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return int(number) if number.is_integer() else number


def _array(value: Any) -> List[float | int | None]:
    if isinstance(value, dict):
        value = value.get("Slice0")
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError):
            return []
    if not isinstance(value, list):
        scalar = _number(value)
        return [scalar] if scalar is not None else []
    return [_number(item) for item in value]


def _speed_bps(name: str, item: Dict[str, Any]) -> float | int | None:
    raw = _number(item.get("Actual64Bandwidth") or item.get("ActualBandwidth"))
    # H3C reports these bandwidth fields in kbit/s.
    if raw and raw > 0:
        return int(float(raw) * 1000)
    lowered = (name or "").lower()
    for marker, speed in (
        (("fourhundred", "400ge", "400g"), 400_000_000_000),
        (("twohundred", "200ge", "200g"), 200_000_000_000),
        (("hundred", "100ge", "100g"), 100_000_000_000),
        (("twentyfive", "25ge", "25g"), 25_000_000_000),
        (("tengigabit", "10ge", "10g"), 10_000_000_000),
        (("gigabit", "1g"), 1_000_000_000),
    ):
        if any(token in lowered for token in marker):
            return speed
    return None


def _port(item: Dict[str, Any], name_key: str = "IfName") -> Dict[str, Any]:
    return {
        "scope": "port",
        "interface_index": _number(item.get("IfIndex")),
        "interface_name": item.get(name_key) or item.get("Name") or item.get("IfName") or "",
    }


def _queue_rows(base: Dict[str, Any], arrays: Dict[str, Any], extra: Dict[str, Any] | None = None) -> List[Dict[str, Any]]:
    normalized = {key: _array(value) for key, value in arrays.items()}
    count = max([len(values) for values in normalized.values()] or [0])
    result = []
    for queue_id in range(count):
        row = {**base, "scope": "queue", "queue_id": queue_id}
        if extra:
            row.update(extra)
        for key, values in normalized.items():
            row[key] = values[queue_id] if queue_id < len(values) else None
        result.append(row)
    return result


def compact_lossless_rows(rows: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Keep all port rows but make queue snapshots sparse.

    An S9867 payload can contain more than one thousand zero-valued queue rows
    every minute. Missing queue metrics in the latest cache mean zero; retaining
    only active/peak rows keeps Redis bounded without losing an event.
    """
    identifiers = {"scope", "interface_index", "interface_name", "queue_id", "direction_code"}
    compacted = []
    for row in rows:
        if row.get("scope") != "queue":
            compacted.append(row)
            continue
        values = [value for key, value in row.items() if key not in identifiers]
        if any(value not in (None, 0, 0.0, False, "") for value in values):
            compacted.append(row)
    return compacted


def normalize_lossless_payload(sensor_path: str, obj: Dict[str, Any]) -> List[Dict[str, Any]]:
    path = (sensor_path or "").lower()
    notification = obj.get("Notification") or {}

    if path == "ifmgr/interfaces":
        source = notification.get("Ifmgr", {}).get("Interfaces", {}).get("Interface")
        result = []
        for item in _rows(source):
            row = _port(item, "Name")
            admin = _number(item.get("AdminStatus"))
            oper = _number(item.get("OperStatus"))
            row.update({
                "abbreviated_name": item.get("AbbreviatedName") or "",
                "speed_bps": _speed_bps(row["interface_name"], item),
                "admin_status": "up" if admin == 1 else "down",
                "oper_status": "up" if oper == 1 else "down",
                "status_consistent": admin == oper,
                "description": item.get("Description") or "",
            })
            result.append(row)
        return result

    if path == "ifmgr/statistics":
        source = notification.get("Ifmgr", {}).get("Statistics", {}).get("Interface")
        result = []
        for item in _rows(source):
            row = _port(item, "Name")
            row.update({
                "in_utilization_percent": _number(item.get("InUsage")),
                "out_utilization_percent": _number(item.get("OutUsage")),
                "in_bps": _number(item.get("InBitRate")),
                "out_bps": _number(item.get("OutBitRate")),
                "in_pps": _number(item.get("InPktRate")),
                "out_pps": _number(item.get("OutPktRate")),
                "in_unicast_packets": _number(item.get("InUcastPkts")),
                "out_unicast_packets": _number(item.get("OutUcastPkts")),
                "in_error_packets": _number(item.get("InErrors")),
            })
            result.append(row)
        return result

    if path == "ifmgr/ethportstatistics":
        source = notification.get("Ifmgr", {}).get("EthPortStatistics", {}).get("Interface")
        result = []
        for item in _rows(source):
            row = _port(item, "Name")
            in_bytes = _number(item.get("InByteSpeed"))
            out_bytes = _number(item.get("OutByteSpeed"))
            row.update({
                "in_bps": float(in_bytes) * 8 if in_bytes is not None else None,
                "out_bps": float(out_bytes) * 8 if out_bytes is not None else None,
                "in_pps": _number(item.get("InPktSpeed")),
                "out_pps": _number(item.get("OutPktSpeed")),
                "in_broadcast_pps": _number(item.get("InBrdcastPktSpeed")),
                "out_broadcast_pps": _number(item.get("OutBrdcastPktSpeed")),
                "in_error_packets": _number(item.get("InErrorPkts")),
                "in_pause_packets": _number(item.get("InPauses")),
                "out_pause_packets": _number(item.get("OutPauses")),
                "out_no_buffer_packets": _number(item.get("OutBuffFailDropPkts")),
            })
            result.append(row)
        return result

    if path == "ifmgr/iffecdata":
        source = notification.get("Ifmgr", {}).get("IfFecData", {}).get("Interface")
        result = []
        for item in _rows(source):
            interface_name = item.get("IfName") or item.get("Name") or ""
            if not interface_name:
                continue
            result.append({
                "scope": "port",
                "interface_index": _number(item.get("IfIndex")),
                "interface_name": interface_name,
                "fec_correctable_packets": _number(item.get("Correctable")),
                "fec_uncorrectable_packets": _number(item.get("Uncorrectable")),
            })
        return result

    if path == "buffermonitor/commbufferusages":
        source = notification.get("BufferMonitor", {}).get("CommBufferUsages", {}).get("CommBufferUsage")
        result = []
        for item in _rows(source):
            base = _port(item)
            result.extend(_queue_rows(base, {
                "ingress_buffer_used": item.get("IngressUsed"),
                "egress_unicast_buffer_used": item.get("UnicastUsed"),
                "egress_multicast_buffer_used": item.get("MulticastUsed"),
                "ingress_buffer_bytes": item.get("IngressQueLenInBytes"),
                "egress_unicast_buffer_bytes": item.get("UnicastQueLenInBytes"),
                "egress_multicast_buffer_bytes": item.get("MulticastQueLenInBytes"),
                "queue_out_unicast_bps": item.get("UnicastBPS"),
                "queue_out_unicast_pps": item.get("UnicastPPS"),
                "ingress_interval_peak": item.get("InIntervalPeak"),
                "egress_interval_peak": item.get("OutIntervalPeak"),
            }))
        return result

    if path == "buffermonitor/commheadroomusages":
        source = notification.get("BufferMonitor", {}).get("CommHeadroomUsages", {}).get("CommHeadroomUsage")
        result = []
        for item in _rows(source):
            result.extend(_queue_rows(_port(item), {
                "headroom_used": item.get("HeadroomUsed"),
                "headroom_bytes": item.get("HeadroomQueLenInBytes"),
            }))
        return result

    if path == "buffermonitor/ecnandwredstatistics":
        source = notification.get("BufferMonitor", {}).get("EcnAndWredStatistics", {}).get("EcnAndWredStatistic")
        result = []
        for item in _rows(source):
            row = _port(item)
            row.update({"ecn_marked_packets": _number(item.get("EcnMarked")), "wred_dropped_packets": _number(item.get("WredDropped"))})
            result.append(row)
        return result

    if path == "buffermonitor/egressdrops":
        source = notification.get("BufferMonitor", {}).get("EgressDrops", {}).get("EgressDrop")
        result = []
        for item in _rows(source):
            result.extend(_queue_rows(_port(item), {
                "queue_out_no_buffer_packets": item.get("UnicastDroppedPkts"),
                "queue_out_no_buffer_pps": item.get("UnicastDroppedPPS"),
                "queue_out_no_buffer_bps": item.get("UnicastDroppedBPS"),
            }))
        return result

    if path == "buffermonitor/ingressdrops":
        source = notification.get("BufferMonitor", {}).get("IngressDrops", {}).get("IngressDrop")
        result = []
        for item in _rows(source):
            row = _port(item)
            row.update({"in_no_buffer_packets": _number(item.get("NoBufferPkts")), "in_other_dropped_packets": _number(item.get("OtherDroppedPkts"))})
            result.append(row)
        return result

    if path == "buffermonitor/pfcspeeds":
        source = notification.get("BufferMonitor", {}).get("PFCSpeeds", {}).get("PFCSpeed")
        result = []
        for item in _rows(source):
            result.extend(_queue_rows(_port(item), {"queue_pfc_send_pps": item.get("PfcOutPps"), "queue_pfc_recv_pps": item.get("PfcInPps")}))
        return result

    if path == "buffermonitor/pfcstatistics":
        source = notification.get("BufferMonitor", {}).get("PFCStatistics", {}).get("PFCStatistic")
        result = []
        for item in _rows(source):
            result.extend(_queue_rows(_port(item), {
                "queue_pfc_send_packets": item.get("PfcSend"),
                "queue_pfc_recv_packets": item.get("PfcRecv"),
                "queue_pfc_deadlocks": item.get("PfcDeadlockCounters"),
                "queue_pfc_recoveries": item.get("PfcDeadlockRecoverCounters"),
            }))
        return result

    if path == "qstat/queuestat":
        source = notification.get("QSTAT", {}).get("QueueStat", {}).get("Statistics")
        result = []
        for item in _rows(source):
            row = _port(item)
            row.update({
                "scope": "queue",
                "queue_id": _number(item.get("QueueID")),
                "direction_code": _number(item.get("Direction")),
                "queue_pass_packets": _number(item.get("PassPkt")),
                "queue_pass_bytes": _number(item.get("PassByte")),
                "queue_drop_packets": _number(item.get("DropPkt")),
                "queue_current_bytes": _number(item.get("CurrQueLenByte")),
                "queue_usage_ratio": _number(item.get("QueUseRatio")),
                "queue_peak_bytes": _number(item.get("QuePeakSize")),
            })
            result.append(row)
        return result

    if path == "qos/interfaces/interface/input/queues/queue/state":
        interfaces = notification.get("qos", {}).get("interfaces", {}).get("interface")
        result = []
        for interface in _rows(interfaces):
            name = interface.get("interface-id") or ""
            queues = interface.get("input", {}).get("queues", {}).get("queue")
            for queue in _rows(queues):
                state = queue.get("state") or {}
                queue_name = queue.get("name") or state.get("name") or ""
                match = re.search(r"(\d+)$", str(queue_name))
                result.append({
                    "scope": "queue",
                    "interface_index": None,
                    "interface_name": name,
                    "queue_id": int(match.group(1)) if match else queue_name,
                    "queue_pfc_packets": _number(state.get("pfc-pkts")),
                    "queue_pfc_deadlock_detect": _number(state.get("pfc-deadlock-detect")),
                    "queue_pfc_deadlock_recover": bool(state.get("pfc-deadlock-recover")),
                    "queue_pfc_overspeed": bool(state.get("pfc-overspeed")),
                })
        return result

    return []
