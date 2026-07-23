"""
Telemetry dial-out probe receiver.

This service intentionally accepts unknown gRPC methods and stores/logs the raw
payload envelope.  It is the first safe step for vendor telemetry integration:
we can confirm that switches can establish a gRPC session and inspect the
message shape before binding the stream to a concrete protobuf decoder.
"""
from __future__ import annotations

import argparse
import binascii
import json
import logging
import math
import os
import queue
import re
import signal
import time
import threading
from datetime import datetime, timezone
from concurrent import futures
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import grpc

from app.utils.telemetry_lossless import (
    LOSSLESS_SENSOR_PATHS,
    compact_lossless_rows,
    normalize_lossless_payload,
    sensor_cache_suffix,
)
from app.utils.telemetry_forwarding import (
    FORWARDING_SENSOR_PATHS,
    annotate_ecmp,
    build_forwarding_summary,
    forwarding_cache_key,
    forwarding_table_name,
    normalize_forwarding_payload,
)


LOGGER = logging.getLogger("telemetry_receiver")

PATH_PATTERN = re.compile(r"([A-Za-z][A-Za-z0-9]+/[A-Za-z0-9_./-]+)")
JSON_MARKER = b'{"Notification"'
MONITOR_CACHE_TTL_SECONDS = 7 * 24 * 60 * 60
SUPPORTED_CACHE_SENSOR_PATHS = {
    "diagnostic/cpuhistory",
    "diagnostic/memories",
    "ifmgr/interfaces",
    "device/physicalentities",
    "device/transceivers",
    "device/transceiverschannels",
}

# Keep one bounded diagnostic sample for the lossless paths while developing
# field mappings. This is opt-in and never records credentials or every packet.
LOSSLESS_SAMPLE_SENSOR_PATHS = {
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
    "device/transceivers",
    "device/transceiverschannels",
    "components/component/optical-channel/state/esnr",
    "components/component/optical-channel/state/pre-fec-ber",
    "terminal-device/logical-channels/channel/ethernet/state/pre-fec-ber",
    "ifmgr/iffecdata",
    "arp/arptable",
    "arp/arptableevent",
    "route/ipv4routes",
    "route/ipv6routes",
}


def _configure_logging() -> None:
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO").upper(),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )


def _safe_preview(payload: bytes, max_bytes: int = 256) -> dict:
    sample = payload[:max_bytes]
    text = sample.decode("utf-8", errors="replace")
    return {
        "length": len(payload),
        "hex": binascii.hexlify(sample).decode("ascii"),
        "text": text,
        "truncated": len(payload) > max_bytes,
    }


def _extract_json_payload(payload: bytes) -> Optional[dict]:
    start = payload.find(JSON_MARKER)
    if start < 0:
        return None
    text = payload[start:].decode("utf-8", errors="ignore")
    try:
        obj, _end = json.JSONDecoder().raw_decode(text)
        return obj
    except Exception:
        return None


def _extract_sensor_path(payload: bytes) -> str:
    # H3C grpc_dialout.DialoutMsg uses protobuf field 2 for sensorPath.
    # Read that field directly so large field-3 JSON bodies are not decoded and
    # regex-scanned on every incoming sample.
    try:
        offset = 0
        size = len(payload)
        while offset < size:
            tag, offset = _read_protobuf_varint(payload, offset)
            field_number = tag >> 3
            wire_type = tag & 0x07
            if wire_type == 0:
                _value, offset = _read_protobuf_varint(payload, offset)
            elif wire_type == 1:
                offset += 8
            elif wire_type == 2:
                length, offset = _read_protobuf_varint(payload, offset)
                end = offset + length
                if end > size:
                    break
                if field_number == 2:
                    return payload[offset:end].decode("utf-8", errors="ignore")
                offset = end
            elif wire_type == 5:
                offset += 4
            else:
                break
    except Exception:
        pass

    # Fallback for vendor variants or diagnostic payloads that do not use the
    # documented DialoutMsg envelope.
    text = payload.decode("utf-8", errors="ignore")
    matches = PATH_PATTERN.findall(text)
    for item in matches:
        if not item.startswith(("http/", "https/")):
            return item
    return ""


def _read_protobuf_varint(payload: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(payload) and shift < 70:
        byte = payload[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
    raise ValueError("invalid protobuf varint")


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _safe_int(value: Any) -> Optional[int]:
    if value is None:
        return None
    try:
        return int(float(str(value).strip().rstrip("%")))
    except (TypeError, ValueError):
        return None


def _percent_value(value: Any) -> Optional[float]:
    number = _safe_float(str(value).strip().rstrip("%") if value is not None else None)
    if number is None:
        return None
    return round(number * 100, 1) if 0 <= number <= 1 else round(number, 1)


def _speed_from_interface_name(name: str) -> Optional[float]:
    lowered = (name or "").lower()
    if "fourhundred" in lowered or lowered.startswith("400ge") or "400g" in lowered:
        return 400_000_000_000.0
    if "twohundred" in lowered or lowered.startswith("200ge") or "200g" in lowered:
        return 200_000_000_000.0
    if "hundred" in lowered or lowered.startswith("100ge") or "100g" in lowered:
        return 100_000_000_000.0
    if "twentyfive" in lowered or lowered.startswith("25ge") or "25g" in lowered:
        return 25_000_000_000.0
    if "tengigabit" in lowered or lowered.startswith("10ge") or "10g" in lowered:
        return 10_000_000_000.0
    if "gigabit" in lowered or lowered.startswith("ge") or "1g" in lowered:
        return 1_000_000_000.0
    return None


class DeviceResolver:
    def __init__(self) -> None:
        self._cache: dict[str, dict[str, Any]] = {}
        self._marked_telemetry: set[str] = set()

    def get(self, ip_address: str) -> Optional[dict[str, Any]]:
        if not ip_address:
            return None
        cached = self._cache.get(ip_address)
        if cached:
            return cached
        try:
            from app.database import SessionLocal
            from app.models import Device

            db = SessionLocal()
            try:
                device = db.query(Device).filter(Device.ip_address == ip_address).first()
                if not device:
                    return None
                data = {
                    "id": device.id,
                    "name": device.name,
                    "ip_address": device.ip_address,
                    "vendor": device.vendor or "",
                }
                self._cache[ip_address] = data
                self._mark_telemetry_seen(db, device)
                return data
            finally:
                db.close()
        except Exception as exc:
            LOGGER.warning("resolve telemetry device failed ip=%s error=%s", ip_address, exc)
            return None

    def _mark_telemetry_seen(self, db: Any, device: Any) -> None:
        """Record that Telemetry dial-out is reachable without changing source.

        Telemetry is still useful as supplemental data, but it must not make a
        device skip SNMP automatically.  SNMP remains the authoritative baseline
        for basic monitoring and alerting unless an operator explicitly enables
        a future Telemetry-primary mode.
        """
        ip_address = getattr(device, "ip_address", "")
        if not ip_address or ip_address in self._marked_telemetry:
            return
        try:
            from copy import deepcopy
            from sqlalchemy.orm.attributes import flag_modified

            custom_fields = deepcopy(device.custom_fields or {}) if isinstance(device.custom_fields, dict) else {}
            monitoring = custom_fields.setdefault("monitoring", {})
            if not isinstance(monitoring, dict):
                monitoring = {}
                custom_fields["monitoring"] = monitoring
            telemetry = monitoring.setdefault("telemetry", {})
            if not isinstance(telemetry, dict):
                telemetry = {}
                monitoring["telemetry"] = telemetry

            desired = {
                "enabled": True,
                "source": "dialout",
                "last_seen": True,
                "interface_stats": False,
                "disable_snmp": False,
                "snmp_fallback_protocols": True,
                "snmp_fallback_optical": True,
            }
            changed = False
            for key, value in desired.items():
                if telemetry.get(key) != value:
                    telemetry[key] = value
                    changed = True
            if changed:
                device.custom_fields = custom_fields
                flag_modified(device, "custom_fields")
                db.commit()
                LOGGER.info("telemetry device auto-adapted ip=%s device_id=%s", ip_address, getattr(device, "id", None))
            self._marked_telemetry.add(ip_address)
        except Exception as exc:
            db.rollback()
            LOGGER.warning("mark telemetry primary failed ip=%s error=%s", ip_address, exc)


class TelemetryInfluxWriter:
    def __init__(
        self,
        enabled: bool = True,
        batch_size: int = 500,
        flush_interval: float = 2.0,
        write_interface_history: bool = False,
    ) -> None:
        self.enabled = enabled
        self.write_interface_history = write_interface_history
        self.batch_size = max(1, batch_size)
        self.flush_interval = max(0.1, flush_interval)
        self._points: list[dict[str, Any]] = []
        self._last_flush = time.time()
        self._resolver = DeviceResolver()
        self._written = 0
        self._lock = threading.Lock()
        # Main transceiver and channel payloads arrive concurrently on worker
        # threads. Serialize their read/merge/write cache cycle so one payload
        # cannot overwrite fields written by the other.
        self._optical_cache_lock = threading.Lock()
        self._forwarding_cache_lock = threading.Lock()
        self._forwarding_cycles: dict[tuple[int, str], dict[str, Any]] = {}

    def _set_monitor_cache(self, kind: str, device_id: int, payload: Any, suffix: str = "") -> None:
        try:
            from app.utils import redis_client

            redis_client.setex(
                f"monitor:cache:{kind}:{device_id}{suffix}",
                MONITOR_CACHE_TTL_SECONDS,
                json.dumps(payload, ensure_ascii=False, default=str),
            )
            if kind == "overview":
                try:
                    redis_client.incr("monitor:cache:overview_revision")
                except Exception:
                    pass
        except Exception as exc:
            LOGGER.warning("telemetry cache update failed kind=%s device_id=%s error=%s", kind, device_id, exc)

    def should_process(self, sensor_path: str) -> bool:
        sensor_path_lower = (sensor_path or "").lower()
        if sensor_path_lower in SUPPORTED_CACHE_SENSOR_PATHS:
            return True
        if sensor_path_lower in LOSSLESS_SENSOR_PATHS:
            return True
        if sensor_path_lower in FORWARDING_SENSOR_PATHS:
            return True
        return sensor_path_lower == "ifmgr/statistics" and self.write_interface_history

    def handle_payload(
        self,
        payload: bytes,
        received_at: datetime,
        sensor_path: str = "",
        peer_ip: str = "",
    ) -> int:
        if not self.enabled:
            return 0
        sensor_path = sensor_path or _extract_sensor_path(payload)
        sensor_path_lower = sensor_path.lower()

        # Most configured sensor paths are accepted for transport stability but
        # do not yet have a storage/display mapping. Avoid decoding their large
        # JSON bodies only to discard them afterwards. This keeps the processor
        # ahead of H3C burst traffic without changing any implemented metric.
        if (
            sensor_path_lower not in SUPPORTED_CACHE_SENSOR_PATHS
            and sensor_path_lower not in LOSSLESS_SENSOR_PATHS
            and sensor_path_lower not in FORWARDING_SENSOR_PATHS
            and sensor_path_lower != "ifmgr/statistics"
        ):
            return 0

        obj = _extract_json_payload(payload)
        if not obj:
            self._maybe_flush()
            return 0

        text = payload.decode("utf-8", errors="ignore")
        ip_match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", text)
        # Most buffer/PFC/queue payloads contain no IP address.  In Dial-out
        # mode the TCP peer is the authoritative device source address.
        device_ip = peer_ip or (ip_match.group(1) if ip_match else "")
        device = self._resolver.get(device_ip)
        if not device:
            return 0

        if sensor_path_lower in FORWARDING_SENSOR_PATHS:
            return self._handle_forwarding_payload(device, sensor_path_lower, obj, received_at)

        cache_written = self._handle_cache_payload(device, sensor_path_lower, obj, received_at)
        if sensor_path_lower in LOSSLESS_SENSOR_PATHS:
            rows = compact_lossless_rows(normalize_lossless_payload(sensor_path_lower, obj))
            self._set_monitor_cache(
                "telemetry_lossless",
                int(device["id"]),
                {
                    "sensor_path": sensor_path_lower,
                    "collected_at": received_at.isoformat(),
                    "rows": rows,
                },
                sensor_cache_suffix(sensor_path_lower),
            )
            cache_written += len(rows)
        if sensor_path_lower != "ifmgr/statistics" or not self.write_interface_history:
            self._maybe_flush()
            return cache_written

        try:
            interfaces = obj.get("Notification", {}).get("Ifmgr", {}).get("Statistics", {}).get("Interface", [])
            if isinstance(interfaces, dict):
                interfaces = [interfaces]
        except Exception:
            return 0
        if not interfaces:
            return 0

        added = 0
        for item in interfaces:
            point = self._interface_point(device, item, received_at)
            if point:
                with self._lock:
                    self._points.append(point)
                added += 1
        self._maybe_flush()
        return added + cache_written

    def _handle_forwarding_payload(
        self,
        device: dict[str, Any],
        sensor_path: str,
        obj: dict,
        received_at: datetime,
    ) -> int:
        incoming_rows = normalize_forwarding_payload(sensor_path, obj)
        table_name = forwarding_table_name(sensor_path)
        device_id = int(device["id"])
        cache_key = forwarding_cache_key(device_id, table_name)
        previous_rows: list[dict[str, Any]] = []

        with self._forwarding_cache_lock:
            try:
                from app.utils import redis_client

                previous_raw = redis_client.get(cache_key)
                if previous_raw:
                    previous_payload = json.loads(previous_raw)
                    if isinstance(previous_payload, dict) and isinstance(previous_payload.get("rows"), list):
                        previous_rows = previous_payload["rows"]
            except Exception as exc:
                LOGGER.warning("forwarding previous cache read failed device_id=%s table=%s error=%s", device_id, table_name, exc)

            # H3C splits large routing tables into several Telemetry messages.
            # Messages belonging to one sampling cycle arrive close together;
            # merge them instead of letting the last packet overwrite the table.
            cycle_key = (device_id, table_name)
            now_monotonic = time.monotonic()
            cycle = self._forwarding_cycles.get(cycle_key)
            if not cycle or now_monotonic - float(cycle.get("last_received", 0)) > 60:
                cycle = {
                    "last_received": now_monotonic,
                    "baseline_rows": previous_rows,
                    "rows": [],
                }
                self._forwarding_cycles[cycle_key] = cycle
            cycle["last_received"] = now_monotonic

            if table_name == "arp":
                row_key = lambda row: (row.get("vrf_index"), row.get("ip_address"))
            else:
                row_key = lambda row: (
                    row.get("vrf"), row.get("prefix"), row.get("next_hop"), row.get("interface")
                )
            merged = {row_key(row): row for row in cycle.get("rows", [])}
            merged.update({row_key(row): row for row in incoming_rows})
            rows = list(merged.values())
            if table_name != "arp":
                rows = annotate_ecmp(rows)
            cycle["rows"] = rows

            summary = build_forwarding_summary(table_name, rows, cycle.get("baseline_rows") or [])
            payload = {
                "device_id": device_id,
                "device_name": device.get("name"),
                "device_ip": device.get("ip_address"),
                "vendor": device.get("vendor"),
                "sensor_path": sensor_path,
                "table": table_name,
                "collected_at": received_at.isoformat(),
                "summary": summary,
                "rows": rows,
            }
            try:
                from app.utils import redis_client

                redis_client.setex(cache_key, MONITOR_CACHE_TTL_SECONDS, json.dumps(payload, ensure_ascii=False, default=str))
            except Exception as exc:
                LOGGER.warning("forwarding cache write failed device_id=%s table=%s error=%s", device_id, table_name, exc)

        # Large tables arrive in chunks. Persist only stable capacity/count
        # fields; per-cycle add/remove changes are kept in the completed Redis
        # snapshot and must not become transient false history spikes.
        historical_fields = {
            "total",
            "prefix_total",
            "ecmp_prefixes",
            "blackhole_routes",
            "incomplete",
            "duplicate_ip",
        }
        point_fields = {
            key: float(value)
            for key, value in summary.items()
            if key in historical_fields and isinstance(value, (int, float))
        }
        if point_fields:
            with self._lock:
                self._points.append({
                    "measurement": "forwarding_monitoring",
                    "tags": {
                        "device_id": str(device_id),
                        "device_name": str(device.get("name") or ""),
                        "device_ip": str(device.get("ip_address") or ""),
                        "forwarding_table": table_name,
                    },
                    "fields": point_fields,
                    "timestamp": received_at,
                })
        self._maybe_flush()
        return len(rows)

    def _handle_cache_payload(self, device: dict[str, Any], sensor_path: str, obj: dict, received_at: datetime) -> int:
        notification = obj.get("Notification") or {}
        collected_at = received_at.isoformat()
        device_id = int(device["id"])

        if sensor_path in {"device/transceivers", "device/transceiverschannels"}:
            with self._optical_cache_lock:
                return self._handle_optical_payload(device, sensor_path, notification, received_at)

        if sensor_path == "diagnostic/cpuhistory":
            cpus = notification.get("Diagnostic", {}).get("CPUHistory", {}).get("CPU", [])
            if isinstance(cpus, dict):
                cpus = [cpus]
            values = [_percent_value(cpu.get("CPUUsage") or cpu.get("Last1mUsage")) for cpu in cpus if isinstance(cpu, dict)]
            values = [value for value in values if value is not None]
            if values:
                self._merge_overview_cache(device, {"resources": {"cpu_percent": max(values)}}, collected_at)
                return 1

        if sensor_path == "diagnostic/memories":
            memories = notification.get("Diagnostic", {}).get("Memories", {}).get("Memory", [])
            if isinstance(memories, dict):
                memories = [memories]
            values = [_percent_value(mem.get("MemoryUsage")) for mem in memories if isinstance(mem, dict)]
            values = [value for value in values if value is not None]
            if values:
                self._merge_overview_cache(device, {"resources": {"memory_percent": max(values)}}, collected_at)
                return 1

        if sensor_path == "ifmgr/interfaces":
            interfaces = notification.get("Ifmgr", {}).get("Interfaces", {}).get("Interface", [])
            if isinstance(interfaces, dict):
                interfaces = [interfaces]
            normalized = []
            admin_up = oper_up = total = 0
            for item in interfaces:
                if not isinstance(item, dict):
                    continue
                index = _safe_int(item.get("IfIndex"))
                name = item.get("Name") or item.get("AbbreviatedName")
                if index is None or not name:
                    continue
                admin_status = "up" if _safe_int(item.get("AdminStatus")) == 1 else "down"
                oper_status = "up" if _safe_int(item.get("OperStatus")) == 1 else "down"
                speed_bps = _safe_float(item.get("Actual64Bandwidth") or item.get("ActualBandwidth") or item.get("ActualSpeed"))
                if speed_bps and speed_bps < 10_000_000_000 and _speed_from_interface_name(name):
                    speed_bps = _speed_from_interface_name(name)
                normalized.append({
                    "index": index,
                    "name": name,
                    "description": item.get("Description") or name,
                    "admin_status": admin_status,
                    "oper_status": oper_status,
                    "speed_bps": speed_bps or _speed_from_interface_name(name),
                    "alias": item.get("AbbreviatedName"),
                    "last_change": item.get("LastChange"),
                    "source": "telemetry",
                })
                total += 1
                admin_up += 1 if admin_status == "up" else 0
                oper_up += 1 if oper_status == "up" else 0
            if normalized:
                self._set_monitor_cache("interfaces", device_id, {"interfaces": normalized, "collected_at": collected_at, "source": "telemetry"})
                self._merge_overview_cache(device, {"interfaces_summary": {"total": total, "admin_up": admin_up, "oper_up": oper_up}}, collected_at)
                return len(normalized)

        if sensor_path == "device/physicalentities":
            entities = notification.get("Device", {}).get("PhysicalEntities", {}).get("Entity", [])
            if isinstance(entities, dict):
                entities = [entities]
            chassis = None
            for entity in entities:
                if not isinstance(entity, dict):
                    continue
                if entity.get("SerialNumber") or entity.get("SoftwareRev") or entity.get("Model"):
                    chassis = entity
                    break
            if chassis:
                self._merge_overview_cache(device, {
                    "system_info": {
                        "sys_name": device.get("name"),
                        "sys_descr": chassis.get("Description"),
                        "software_version": chassis.get("SoftwareRev"),
                        "snmp_model": chassis.get("Model") or chassis.get("Name"),
                        "serial_number": chassis.get("SerialNumber"),
                        "uptime_seconds": None,
                    }
                }, collected_at)
                return 1

        return 0

    @staticmethod
    def _valid_optical_number(value: Any, scale: float = 1.0) -> Optional[float]:
        number = _safe_float(value)
        if number is None or number >= 2147483647 or number <= -2147483648:
            return None
        return round(number * scale, 6)

    @staticmethod
    def _optical_microwatt_to_dbm(value: Any) -> Optional[float]:
        """Convert H3C DDM power thresholds (0.1 uW) to dBm."""
        number = _safe_float(value)
        if number is None or number <= 0 or number >= 2147483647:
            return None
        # 0.1 uW / 1000 = 0.0001 mW, so raw / 10000 gives mW.
        return round(10 * math.log10(number / 10000.0), 6)

    def _handle_optical_payload(
        self,
        device: dict[str, Any],
        sensor_path: str,
        notification: dict[str, Any],
        received_at: datetime,
    ) -> int:
        """Normalize H3C transceiver telemetry into one local cache/measurement.

        The device/transceivers main table contains module identity and aggregate
        values. device/transceiverschannels contains the individual optical
        lanes. H3C uses 0.01 dBm for power, 0.01 V for voltage and 0.01 mA for
        channel bias current.
        """
        device_id = int(device["id"])
        cache_key = f"monitor:cache:optical_modules:{device_id}"
        cached: dict[str, Any] = {"items": [], "collected_at": received_at.isoformat()}
        try:
            from app.utils import redis_client

            raw = redis_client.get(cache_key)
            if raw:
                parsed = json.loads(raw)
                if isinstance(parsed, dict):
                    cached = parsed
        except Exception:
            pass

        modules: dict[str, dict[str, Any]] = {}
        for existing in cached.get("items") or []:
            if isinstance(existing, dict):
                key = str(existing.get("interface_index") or existing.get("interface_name") or "")
                if key:
                    modules[key] = existing

        points: list[dict[str, Any]] = []
        if sensor_path == "device/transceivers":
            rows = notification.get("Device", {}).get("Transceivers", {}).get("Interface", [])
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                interface_index = _safe_int(row.get("IfIndex"))
                interface_name = str(row.get("Name") or "").strip()
                if interface_index is None or not interface_name:
                    continue
                key = str(interface_index)
                item = modules.setdefault(key, {})
                item.update({
                    "device_id": device_id,
                    "device_name": device.get("name"),
                    "device_ip": device.get("ip_address"),
                    "device_vendor": device.get("vendor"),
                    "interface_index": interface_index,
                    "interface_name": interface_name,
                    "vendor_name": row.get("VendorName"),
                    "serial_number": row.get("SerialNumber"),
                    "transceiver_type": row.get("TransceiverType"),
                    "hardware_type": row.get("HardwareType"),
                    "wavelength_nm": self._valid_optical_number(row.get("WaveLength")),
                    "distance_m": self._valid_optical_number(row.get("TransferDistance")),
                    "speed_mbps": self._valid_optical_number(row.get("TransceiverSpeed")),
                    "temperature_c": self._valid_optical_number(row.get("Temperature")),
                    "voltage_v": self._valid_optical_number(row.get("Voltage"), 0.01),
                    "total_rx_power_dbm": self._valid_optical_number(row.get("TotalRxPower"), 0.01),
                    "total_tx_power_dbm": self._valid_optical_number(row.get("TotalTxPower"), 0.01),
                    "rx_high_alarm_dbm": self._optical_microwatt_to_dbm(row.get("RcvPwrHiAlarm")),
                    "rx_high_warning_dbm": self._optical_microwatt_to_dbm(row.get("RcvPwrHiWarn")),
                    "rx_low_warning_dbm": self._optical_microwatt_to_dbm(row.get("RcvPwrLoWarn")),
                    "rx_low_alarm_dbm": self._optical_microwatt_to_dbm(row.get("RcvPwrLoAlarm")),
                    "tx_high_alarm_dbm": self._optical_microwatt_to_dbm(row.get("PwrOutHiAlarm")),
                    "tx_high_warning_dbm": self._optical_microwatt_to_dbm(row.get("PwrOutHiWarn")),
                    "tx_low_warning_dbm": self._optical_microwatt_to_dbm(row.get("PwrOutLoWarn")),
                    "tx_low_alarm_dbm": self._optical_microwatt_to_dbm(row.get("PwrOutLoAlarm")),
                    "temperature_high_alarm_c": self._valid_optical_number(row.get("TemperatureHiAlarm"), 0.001),
                    "temperature_high_warning_c": self._valid_optical_number(row.get("TemperatureHiWarn"), 0.001),
                    "temperature_low_warning_c": self._valid_optical_number(row.get("TemperatureLoWarn"), 0.001),
                    "temperature_low_alarm_c": self._valid_optical_number(row.get("TemperatureLoAlarm"), 0.001),
                    "voltage_high_alarm_v": self._valid_optical_number(row.get("VccHiAlarm"), 0.0001),
                    "voltage_high_warning_v": self._valid_optical_number(row.get("VccHiWarn"), 0.0001),
                    "voltage_low_warning_v": self._valid_optical_number(row.get("VccLoWarn"), 0.0001),
                    "voltage_low_alarm_v": self._valid_optical_number(row.get("VccLoAlarm"), 0.0001),
                    "manufacturer": row.get("RelySlotMfgName"),
                    "manufactured_at": row.get("RelySlotMfgDate"),
                    "source": "telemetry",
                    "collected_at": received_at.isoformat(),
                })
                numeric_fields = {
                    name: value for name, value in {
                        "rx_power": item.get("rx_power_dbm"),
                        "tx_power": item.get("tx_power_dbm"),
                        "temperature": item.get("temperature_c"),
                        "voltage": item.get("voltage_v"),
                        "speed_mbps": item.get("speed_mbps"),
                        "wavelength_nm": item.get("wavelength_nm"),
                    }.items() if value is not None
                }
                if numeric_fields:
                    points.append({
                        "measurement": "optical_monitoring",
                        "tags": {
                            "device_id": str(device_id),
                            "device_name": device.get("name") or "",
                            "device_ip": device.get("ip_address") or "",
                            "vendor": device.get("vendor") or "",
                            "interface_index": key,
                            "interface_name": interface_name,
                            "source": "telemetry",
                            "scope": "module",
                        },
                        "fields": numeric_fields,
                        "timestamp": received_at,
                    })
        else:
            rows = notification.get("Device", {}).get("TransceiversChannels", {}).get("Interface", [])
            if isinstance(rows, dict):
                rows = [rows]
            for row in rows:
                if not isinstance(row, dict):
                    continue
                interface_index = _safe_int(row.get("IfIndex"))
                channel_index = _safe_int(row.get("ChannelIndex"))
                interface_name = str(row.get("Name") or "").strip()
                if interface_index is None or channel_index is None or not interface_name:
                    continue
                key = str(interface_index)
                item = modules.setdefault(key, {
                    "device_id": device_id,
                    "device_name": device.get("name"),
                    "device_ip": device.get("ip_address"),
                    "device_vendor": device.get("vendor"),
                    "interface_index": interface_index,
                    "interface_name": interface_name,
                    "source": "telemetry",
                })
                channel = {
                    "channel": channel_index,
                    "rx_power_dbm": self._valid_optical_number(row.get("ChannelCurRXPower"), 0.01),
                    "tx_power_dbm": self._valid_optical_number(row.get("ChannelCurTXPower"), 0.01),
                    "temperature_c": self._valid_optical_number(row.get("ChannelTemperature")),
                    "bias_current_ma": self._valid_optical_number(row.get("ChannelBiasCurrent"), 0.01),
                    "collected_at": received_at.isoformat(),
                }
                channels = {
                    int(existing.get("channel")): existing
                    for existing in item.get("channels") or []
                    if isinstance(existing, dict) and _safe_int(existing.get("channel")) is not None
                }
                channels[channel_index] = channel
                item["channels"] = [channels[index] for index in sorted(channels)]
                rx_values = [entry.get("rx_power_dbm") for entry in item["channels"] if entry.get("rx_power_dbm") is not None]
                tx_values = [entry.get("tx_power_dbm") for entry in item["channels"] if entry.get("tx_power_dbm") is not None]
                # The module table's TotalRx/TotalTx values are aggregate power,
                # while alarm ranges are per lane. Display the weakest lane so
                # a degraded channel is not hidden by the aggregate value.
                item["rx_power_dbm"] = min(rx_values) if rx_values else item.get("total_rx_power_dbm")
                item["tx_power_dbm"] = min(tx_values) if tx_values else item.get("total_tx_power_dbm")
                item["collected_at"] = received_at.isoformat()
                numeric_fields = {
                    name: value for name, value in {
                        "rx_power": channel.get("rx_power_dbm"),
                        "tx_power": channel.get("tx_power_dbm"),
                        "temperature": channel.get("temperature_c"),
                        "bias_current_ma": channel.get("bias_current_ma"),
                    }.items() if value is not None
                }
                if numeric_fields:
                    points.append({
                        "measurement": "optical_monitoring",
                        "tags": {
                            "device_id": str(device_id),
                            "device_name": device.get("name") or "",
                            "device_ip": device.get("ip_address") or "",
                            "vendor": device.get("vendor") or "",
                            "interface_index": key,
                            "interface_name": interface_name,
                            "channel": str(channel_index),
                            "source": "telemetry",
                            "scope": "channel",
                        },
                        "fields": numeric_fields,
                        "timestamp": received_at,
                    })

        if points:
            with self._lock:
                self._points.extend(points)
        payload = {
            "items": sorted(modules.values(), key=lambda row: int(row.get("interface_index") or 0)),
            "collected_at": received_at.isoformat(),
            "source": "telemetry",
        }
        self._set_monitor_cache("optical_modules", device_id, payload)
        return len(points)

    def _merge_overview_cache(self, device: dict[str, Any], patch: dict[str, Any], collected_at: str) -> None:
        device_id = int(device["id"])
        overview: dict[str, Any] = {}
        try:
            from app.utils import redis_client

            raw = redis_client.get(f"monitor:cache:overview:{device_id}")
            if raw:
                overview = json.loads(raw)
        except Exception:
            overview = {}

        overview.setdefault("connectivity", {"type": "telemetry", "status": "reachable", "message": "Telemetry 正在推送"})
        overview["connectivity"] = {"type": "telemetry", "status": "reachable", "message": "Telemetry 正在推送"}
        overview.setdefault("resources", {"cpu_percent": None, "memory_percent": None, "temperature": None, "storage_percent": None})
        overview.setdefault("sessions", {"current": None, "total": None, "usage_percent": None})
        overview.setdefault("hardware", {"fan_total": 0, "fan_down": 0, "fan_status_known": True, "power_total": 0, "power_down": 0, "power_status_known": True})
        overview.setdefault("protocols", {"bgp": {"total": 0, "up": 0, "down": 0}, "ospf": {"total": 0, "up": 0, "down": 0}})
        overview.setdefault("system_info", {"sys_name": None, "sys_descr": None, "software_version": None, "snmp_model": None, "serial_number": None, "uptime_seconds": None})
        overview.setdefault("data_sources", {"resources": {}, "protocols": {}, "system_info": {}})

        for section, values in patch.items():
            if isinstance(values, dict):
                target = overview.setdefault(section, {})
                for key, value in values.items():
                    # Telemetry payloads do not always contain every overview
                    # field (for example physicalentities has no uptime on
                    # H3C S9867).  Keep an existing SNMP/Exporter value instead
                    # of replacing it with an empty Telemetry placeholder.
                    if value is None or value == "":
                        continue
                    target[key] = value
                    if section in {"resources", "protocols", "system_info"}:
                        overview.setdefault("data_sources", {}).setdefault(section, {})[key] = "telemetry"
            else:
                overview[section] = values
        overview["collected_at"] = collected_at
        self._set_monitor_cache("overview", device_id, overview)

    def _interface_point(self, device: dict[str, Any], item: dict[str, Any], timestamp: datetime) -> Optional[dict[str, Any]]:
        interface_index = item.get("IfIndex")
        interface_name = item.get("Name") or item.get("AbbreviatedName")
        if interface_index is None or not interface_name:
            return None

        speed_bps = _speed_from_interface_name(interface_name)
        in_bps = _safe_float(item.get("InBitRate"))
        out_bps = _safe_float(item.get("OutBitRate"))
        # Densivelo telemetry reports bit-rate fields directly in bps.
        in_util = round((in_bps / speed_bps) * 100, 4) if in_bps is not None and speed_bps else None
        out_util = round((out_bps / speed_bps) * 100, 4) if out_bps is not None and speed_bps else None

        return {
            "measurement": "interface_monitoring",
            "tags": {
                "device_id": str(device["id"]),
                "device_name": device["name"],
                "interface_index": str(interface_index),
                "interface_name": interface_name,
                "vendor": device.get("vendor") or "",
                "source": "telemetry",
            },
            "fields": {
                "in_bps": in_bps,
                "out_bps": out_bps,
                "in_octets": _safe_float(item.get("InOctets")),
                "out_octets": _safe_float(item.get("OutOctets")),
                "in_utilization_percent": in_util,
                "out_utilization_percent": out_util,
                "speed_bps": speed_bps,
                "in_discards": _safe_float(item.get("InDiscards")),
                "out_discards": _safe_float(item.get("OutDiscards")),
                "in_errors": _safe_float(item.get("InErrors")),
                "out_errors": _safe_float(item.get("OutErrors")),
                "in_packets": _safe_float(item.get("InPkts")),
                "out_packets": _safe_float(item.get("OutPkts")),
                "in_unicast_packets": _safe_float(item.get("InUcastPkts")),
                "out_unicast_packets": _safe_float(item.get("OutUcastPkts")),
                "in_pps": _safe_float(item.get("InPktRate")),
                "out_pps": _safe_float(item.get("OutPktRate")),
                "sample_seconds": 10.0,
                "telemetry_sample": 1.0,
            },
            "timestamp": timestamp,
        }

    def _maybe_flush(self, force: bool = False) -> None:
        now = time.time()
        with self._lock:
            if not force and len(self._points) < self.batch_size and now - self._last_flush < self.flush_interval:
                return
            if not self._points:
                self._last_flush = now
                return
            points = self._points
            self._points = []
            self._last_flush = now
        try:
            from app.utils import influx_client

            if influx_client.write_points(points, sync=False):
                self._written += len(points)
                LOGGER.info("telemetry points written count=%s total=%s", len(points), self._written)
            else:
                LOGGER.warning("telemetry points write returned false count=%s", len(points))
        except Exception as exc:
            LOGGER.exception("telemetry points write failed count=%s error=%s", len(points), exc)

    def flush(self) -> None:
        self._maybe_flush(force=True)

    def flush_if_needed(self) -> None:
        self._maybe_flush(force=False)


class TelemetryPayloadProcessor:
    """Decouple gRPC stream reads from JSON/cache/storage processing.

    H3C devices can send large bursts from many sensor groups at the same time.
    Processing those payloads inline applies HTTP/2 flow-control backpressure
    and eventually makes some devices close the Dial-out RPC.  The bounded
    queue lets the gRPC worker return to Read immediately while a separate
    worker pool performs the slower processing.
    """

    def __init__(self, writer: TelemetryInfluxWriter, workers: int = 16, max_queue: int = 8192) -> None:
        self.writer = writer
        self.workers = max(1, workers)
        self.queue: queue.Queue[Optional[tuple[bytes, datetime, str, str]]] = queue.Queue(maxsize=max(256, max_queue))
        self._lock = threading.Lock()
        self._submitted = 0
        self._processed = 0
        self._dropped = 0
        self._last_log = time.time()
        self._threads: list[threading.Thread] = []
        for index in range(self.workers):
            thread = threading.Thread(target=self._run, name=f"telemetry-payload-{index + 1}", daemon=True)
            thread.start()
            self._threads.append(thread)

    def submit(self, payload: bytes, received_at: datetime, sensor_path: str, peer_ip: str = "") -> bool:
        try:
            self.queue.put_nowait((payload, received_at, sensor_path, peer_ip))
            accepted = True
        except queue.Full:
            accepted = False
        now = time.time()
        with self._lock:
            self._submitted += 1
            if not accepted:
                self._dropped += 1
            should_log = now - self._last_log >= 60
            if should_log:
                self._last_log = now
                LOGGER.info(
                    "telemetry processor stats submitted=%s processed=%s dropped=%s queued=%s workers=%s",
                    self._submitted,
                    self._processed,
                    self._dropped,
                    self.queue.qsize(),
                    self.workers,
                )
        if not accepted and self._dropped % 100 == 1:
            LOGGER.warning("telemetry processor queue full dropped=%s queued=%s", self._dropped, self.queue.qsize())
        return accepted

    def _run(self) -> None:
        while True:
            item = self.queue.get()
            if item is None:
                self.queue.task_done()
                return
            payload, received_at, sensor_path, peer_ip = item
            try:
                self.writer.handle_payload(payload, received_at, sensor_path=sensor_path, peer_ip=peer_ip)
            except Exception:
                LOGGER.exception("telemetry payload processing failed bytes=%s", len(payload))
            finally:
                with self._lock:
                    self._processed += 1
                self.queue.task_done()

    def stop(self) -> None:
        for _thread in self._threads:
            try:
                self.queue.put_nowait(None)
            except queue.Full:
                break
        for thread in self._threads:
            thread.join(timeout=2)
        self.writer.flush()


class TelemetryRawRecorder:
    def __init__(
        self,
        spool_dir: str | Path,
        max_saved_messages: int = 0,
        record_messages: bool = False,
        record_payloads: bool = False,
        record_events: bool = True,
        record_sensor_samples: bool = False,
    ) -> None:
        self.spool_dir = Path(spool_dir)
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.max_saved_messages = max(0, max_saved_messages)
        self.record_messages = record_messages
        self.record_payloads = record_payloads
        self.record_events = record_events
        self.record_sensor_samples = record_sensor_samples
        self._saved_messages = 0
        self._session_seq = 0
        self._sampled_sensor_paths: set[str] = set()
        self._sample_lock = threading.Lock()

    def next_session_id(self) -> str:
        self._session_seq += 1
        return f"{int(time.time())}-{os.getpid()}-{self._session_seq}"

    def record_event(self, event: dict) -> None:
        if not self.record_events:
            return
        if event.get("event") == "message" and not self.record_messages:
            return
        day = time.strftime("%Y%m%d")
        path = self.spool_dir / f"telemetry-events-{day}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    def record_payload(self, session_id: str, index: int, payload: bytes) -> None:
        if not self.record_payloads or self.max_saved_messages <= 0 or self._saved_messages >= self.max_saved_messages:
            return
        self._saved_messages += 1
        payload_dir = self.spool_dir / "payloads" / session_id
        payload_dir.mkdir(parents=True, exist_ok=True)
        (payload_dir / f"{index:06d}.bin").write_bytes(payload)

    def record_sensor_sample(self, sensor_path: str, payload: bytes) -> None:
        """Persist only the first JSON sample for selected lossless paths."""
        normalized = (sensor_path or "").strip().lower()
        if not self.record_sensor_samples or normalized not in LOSSLESS_SAMPLE_SENSOR_PATHS:
            return
        with self._sample_lock:
            if normalized in self._sampled_sensor_paths:
                return
            obj = _extract_json_payload(payload)
            if not obj:
                return
            self._sampled_sensor_paths.add(normalized)
            sample_dir = self.spool_dir / "sensor-samples"
            sample_dir.mkdir(parents=True, exist_ok=True)
            filename = normalized.replace("/", "__") + ".json"
            (sample_dir / filename).write_text(
                json.dumps(obj, ensure_ascii=False, separators=(",", ":")),
                encoding="utf-8",
            )
            LOGGER.info("telemetry sensor sample recorded path=%s file=%s", normalized, filename)


def _protobuf_varint(value: int) -> bytes:
    chunks = bytearray()
    while True:
        to_write = value & 0x7F
        value >>= 7
        if value:
            chunks.append(to_write | 0x80)
        else:
            chunks.append(to_write)
            break
    return bytes(chunks)


def _dialout_response_payload(response: str = "ok") -> bytes:
    """Encode grpc_dialout.DialoutResponse without generated protobuf code.

    H3C Comware dial-out defines:

        message DialoutResponse { required string response = 1; }
        rpc Dialout(stream DialoutMsg) returns (DialoutResponse);

    The receiver only needs to acknowledge that the client-side stream was
    consumed.  Keeping this tiny encoder avoids adding a generated-code build
    step while still speaking the correct RPC shape.
    """
    data = response.encode("utf-8")
    return b"\x0a" + _protobuf_varint(len(data)) + data


class CatchAllTelemetryHandler(grpc.GenericRpcHandler):
    """Accept any gRPC method and treat requests as raw bytes."""

    def __init__(
        self,
        recorder: TelemetryRawRecorder,
        writer: TelemetryInfluxWriter,
        processor: TelemetryPayloadProcessor,
        log_every: int = 100,
        ack_every: int = 1,
    ) -> None:
        self.recorder = recorder
        self.writer = writer
        self.processor = processor
        self.log_every = max(1, log_every)
        self.ack_every = max(0, ack_every)
        self._active_lock = threading.Lock()
        self._active_sessions: set[str] = set()

    def service(self, handler_call_details: grpc.HandlerCallDetails):
        method = handler_call_details.method

        def stream_unary(request_iterator: Iterable[bytes], context: grpc.ServicerContext) -> bytes:
            peer = context.peer()
            peer_match = re.match(r"^(?:ipv4:)?(\d{1,3}(?:\.\d{1,3}){3}):\d+$", peer or "")
            peer_ip = peer_match.group(1) if peer_match else ""
            session_id = self.recorder.next_session_id()
            started = time.time()
            count = 0
            total_bytes = 0
            with self._active_lock:
                self._active_sessions.add(session_id)
                active_count = len(self._active_sessions)
            LOGGER.info("telemetry session opened peer=%s method=%s session=%s active=%s", peer, method, session_id, active_count)
            self.recorder.record_event(
                {
                    "event": "session_open",
                    "time": started,
                    "session_id": session_id,
                    "peer": peer,
                    "method": method,
                    "metadata": list(context.invocation_metadata() or []),
                }
            )
            rpc_done_logged = threading.Event()

            def on_rpc_done() -> None:
                if rpc_done_logged.is_set():
                    return
                rpc_done_logged.set()
                with self._active_lock:
                    self._active_sessions.discard(session_id)
                    active_count = len(self._active_sessions)
                self.writer.flush_if_needed()
                LOGGER.info(
                    "telemetry rpc terminated peer=%s method=%s session=%s messages=%s bytes=%s active=%s",
                    peer,
                    method,
                    session_id,
                    count,
                    total_bytes,
                    active_count,
                )
                self.recorder.record_event(
                    {
                        "event": "session_terminated",
                        "time": time.time(),
                        "session_id": session_id,
                        "peer": peer,
                        "method": method,
                        "messages": count,
                        "bytes": total_bytes,
                        "active": active_count,
                    }
                )

            try:
                context.add_callback(on_rpc_done)
            except Exception:
                LOGGER.debug("telemetry context callback unsupported peer=%s method=%s session=%s", peer, method, session_id)

            try:
                for payload in request_iterator:
                    count += 1
                    total_bytes += len(payload)
                    if count % self.log_every == 0:
                        preview = _safe_preview(payload, max_bytes=160)
                        LOGGER.info(
                            "telemetry message peer=%s method=%s session=%s index=%s bytes=%s text_preview=%r",
                            peer,
                            method,
                            session_id,
                            count,
                            len(payload),
                            preview["text"][:160],
                        )
                    else:
                        LOGGER.debug(
                            "telemetry message peer=%s method=%s session=%s index=%s bytes=%s",
                            peer,
                            method,
                            session_id,
                            count,
                            len(payload),
                        )
                    if self.recorder.record_messages:
                        self.recorder.record_event(
                            {
                                "event": "message",
                                "time": time.time(),
                                "session_id": session_id,
                                "peer": peer,
                                "method": method,
                                "index": count,
                                "preview": _safe_preview(payload, max_bytes=160),
                            }
                        )
                    self.recorder.record_payload(session_id, count, payload)
                    sensor_path = _extract_sensor_path(payload)
                    self.recorder.record_sensor_sample(sensor_path, payload)
                    if self.writer.should_process(sensor_path):
                        self.processor.submit(payload, datetime.now(timezone.utc), sensor_path, peer_ip=peer_ip)
            except grpc.RpcError as exc:  # pragma: no cover - runtime diagnostics only
                code = None
                details = ""
                try:
                    code = exc.code()
                    details = exc.details() or ""
                except Exception:
                    pass
                LOGGER.info(
                    "telemetry client closed stream peer=%s method=%s session=%s messages=%s bytes=%s code=%s details=%s",
                    peer,
                    method,
                    session_id,
                    count,
                    total_bytes,
                    code,
                    details,
                )
                self.recorder.record_event(
                    {
                        "event": "session_client_closed",
                        "time": time.time(),
                        "session_id": session_id,
                        "peer": peer,
                        "method": method,
                        "messages": count,
                        "bytes": total_bytes,
                        "code": str(code) if code is not None else "",
                        "details": details,
                    }
                )
            except BaseException as exc:  # pragma: no cover - runtime diagnostics only
                if isinstance(exc, (GeneratorExit, KeyboardInterrupt, SystemExit)):
                    log_method = LOGGER.info
                else:
                    log_method = LOGGER.exception
                log_method(
                    "telemetry session error peer=%s method=%s session=%s error_type=%s error=%r",
                    peer,
                    method,
                    session_id,
                    type(exc).__name__,
                    exc,
                )
                self.recorder.record_event(
                    {
                        "event": "session_error",
                        "time": time.time(),
                        "session_id": session_id,
                        "peer": peer,
                        "method": method,
                        "error_type": type(exc).__name__,
                        "error": repr(exc),
                    }
                )
                raise
            finally:
                finished = time.time()
                with self._active_lock:
                    self._active_sessions.discard(session_id)
                    active_count = len(self._active_sessions)
                LOGGER.info(
                    "telemetry session closed peer=%s method=%s session=%s messages=%s bytes=%s duration=%.3fs active=%s",
                    peer,
                    method,
                    session_id,
                    count,
                    total_bytes,
                    finished - started,
                    active_count,
                )
                self.writer.flush_if_needed()
                self.recorder.record_event(
                    {
                        "event": "session_close",
                        "time": finished,
                        "session_id": session_id,
                        "peer": peer,
                        "method": method,
                        "messages": count,
                        "bytes": total_bytes,
                        "duration_seconds": round(finished - started, 3),
                    }
                )

            return _dialout_response_payload("ok")

        return grpc.stream_unary_rpc_method_handler(
            stream_unary,
            request_deserializer=lambda raw: raw,
            response_serializer=lambda raw: raw,
        )


def serve(
    host: str,
    port: int,
    spool_dir: str,
    max_workers: int,
    log_every: int,
    write_influx: bool,
    record_messages: bool,
    record_payloads: bool,
    record_events: bool,
    record_sensor_samples: bool,
    max_saved_messages: int,
    ack_every: int,
    write_interface_history: bool,
    process_workers: int,
    process_queue_size: int,
) -> None:
    recorder = TelemetryRawRecorder(
        spool_dir=spool_dir,
        max_saved_messages=max_saved_messages,
        record_messages=record_messages,
        record_payloads=record_payloads,
        record_events=record_events,
        record_sensor_samples=record_sensor_samples,
    )
    writer = TelemetryInfluxWriter(
        enabled=write_influx,
        write_interface_history=write_interface_history,
    )
    processor = TelemetryPayloadProcessor(
        writer=writer,
        workers=process_workers,
        max_queue=process_queue_size,
    )
    server = grpc.server(
        futures.ThreadPoolExecutor(max_workers=max_workers),
        options=[
            ("grpc.max_receive_message_length", 64 * 1024 * 1024),
            ("grpc.max_send_message_length", 4 * 1024 * 1024),
            # H3C Dial-out sessions are designed to remain open. Permit enough
            # concurrent HTTP/2 streams for all subscribed devices. Do not send
            # server-initiated keepalive PINGs here: older Comware grpc-c++
            # clients have been observed closing the RPC on those PINGs.
            ("grpc.max_concurrent_streams", max(1024, max_workers * 2)),
        ],
    )
    server.add_generic_rpc_handlers((CatchAllTelemetryHandler(
        recorder,
        writer=writer,
        processor=processor,
        log_every=log_every,
        ack_every=ack_every,
    ),))
    bind_addr = f"{host}:{port}"
    bound_port = server.add_insecure_port(bind_addr)
    if bound_port == 0:
        raise RuntimeError(f"failed to bind telemetry receiver on {bind_addr}")

    stop_requested = False

    def _stop(signum, _frame) -> None:
        nonlocal stop_requested
        stop_requested = True
        LOGGER.info("telemetry receiver stopping signal=%s", signum)
        server.stop(grace=5)

    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)

    server.start()
    LOGGER.info("telemetry receiver listening on %s spool_dir=%s", bind_addr, spool_dir)
    try:
        while not stop_requested:
            time.sleep(1)
    finally:
        processor.stop()
        server.stop(grace=1)


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Telemetry gRPC dial-out probe receiver")
    parser.add_argument("--host", default=os.getenv("TELEMETRY_RECEIVER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("TELEMETRY_RECEIVER_PORT", "50051")))
    parser.add_argument("--spool-dir", default=os.getenv("TELEMETRY_RECEIVER_SPOOL_DIR", "/app/data/telemetry"))
    parser.add_argument("--max-workers", type=int, default=int(os.getenv("TELEMETRY_RECEIVER_MAX_WORKERS", "16")))
    parser.add_argument("--log-every", type=int, default=int(os.getenv("TELEMETRY_RECEIVER_LOG_EVERY", "100")))
    parser.add_argument("--write-influx", action="store_true", default=os.getenv("TELEMETRY_RECEIVER_WRITE_INFLUX", "true").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--record-messages", action="store_true", default=os.getenv("TELEMETRY_RECEIVER_RECORD_MESSAGES", "false").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--record-payloads", action="store_true", default=os.getenv("TELEMETRY_RECEIVER_RECORD_PAYLOADS", "false").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--record-events", action="store_true", default=os.getenv("TELEMETRY_RECEIVER_RECORD_EVENTS", "true").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--record-sensor-samples", action="store_true", default=os.getenv("TELEMETRY_RECEIVER_RECORD_SENSOR_SAMPLES", "false").lower() in {"1", "true", "yes", "on"})
    parser.add_argument("--max-saved-messages", type=int, default=int(os.getenv("TELEMETRY_RECEIVER_MAX_SAVED_MESSAGES", "0")))
    parser.add_argument("--ack-every", type=int, default=int(os.getenv("TELEMETRY_RECEIVER_ACK_EVERY", "1")))
    parser.add_argument(
        "--write-interface-history",
        action="store_true",
        default=os.getenv("TELEMETRY_RECEIVER_WRITE_INTERFACE_HISTORY", "false").lower() in {"1", "true", "yes", "on"},
    )
    parser.add_argument("--process-workers", type=int, default=int(os.getenv("TELEMETRY_RECEIVER_PROCESS_WORKERS", "16")))
    parser.add_argument("--process-queue-size", type=int, default=int(os.getenv("TELEMETRY_RECEIVER_PROCESS_QUEUE_SIZE", "8192")))
    args = parser.parse_args()
    serve(
        args.host,
        args.port,
        args.spool_dir,
        args.max_workers,
        args.log_every,
        args.write_influx,
        args.record_messages,
        args.record_payloads,
        args.record_events,
        args.record_sensor_samples,
        args.max_saved_messages,
        args.ack_every,
        args.write_interface_history,
        args.process_workers,
        args.process_queue_size,
    )


if __name__ == "__main__":
    main()
