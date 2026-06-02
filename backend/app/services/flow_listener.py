"""
Flow 接收器：接收 NetFlow v5 / sFlow v5，并按客户公网 IP 聚合流量。
"""
from __future__ import annotations

import asyncio
import ipaddress
import socket
import struct
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from app.config import settings
from app.core import get_logger
from app.database import SessionLocal
from app.models import Customer
from app.utils import influx_client

logger = get_logger(__name__)


@dataclass(frozen=True)
class CustomerNetwork:
    customer_id: int
    customer_name: str
    cidr: str
    network: ipaddress._BaseNetwork


@dataclass(frozen=True)
class FlowRecord:
    src_ip: str
    dst_ip: str
    bits: float
    protocol: Optional[int] = None
    input_interface: Optional[int] = None
    output_interface: Optional[int] = None


class FlowDatagramProtocol(asyncio.DatagramProtocol):
    def __init__(self, listener: "FlowListener", protocol_type: str):
        self.listener = listener
        self.protocol_type = protocol_type

    def datagram_received(self, data: bytes, addr) -> None:
        self.listener.handle_datagram(self.protocol_type, data, addr)

    def error_received(self, exc: Exception) -> None:
        logger.warning("Flow UDP 接收错误", protocol=self.protocol_type, error=str(exc))


class FlowListener:
    def __init__(self) -> None:
        self._transports: List[asyncio.DatagramTransport] = []
        self._flush_task: Optional[asyncio.Task] = None
        self._networks: List[CustomerNetwork] = []
        self._networks_loaded_at = 0.0
        self._buckets: Dict[Tuple[int, str, str, str], Dict[str, float | str]] = {}
        self._interface_buckets: Dict[Tuple[str, int, str], Dict[str, float]] = {}
        self._last_flush = datetime.now(timezone.utc)
        self._stats: Dict[str, Dict[str, object]] = {
            "netflow": self._new_protocol_stats(),
            "sflow": self._new_protocol_stats(),
        }

    def _new_protocol_stats(self) -> Dict[str, object]:
        return {
            "datagrams": 0,
            "records": 0,
            "matched_records": 0,
            "parse_errors": 0,
            "last_source": None,
            "last_datagram_at": None,
            "last_record_at": None,
            "last_error": None,
        }

    async def start(self) -> None:
        if not settings.FLOW_ENABLED:
            logger.info("Flow 接收器未启用")
            return

        loop = asyncio.get_running_loop()
        for protocol_type, port in [("netflow", settings.FLOW_NETFLOW_PORT), ("sflow", settings.FLOW_SFLOW_PORT)]:
            try:
                transport, _ = await loop.create_datagram_endpoint(
                    lambda t=protocol_type: FlowDatagramProtocol(self, t),
                    local_addr=(settings.FLOW_LISTEN_HOST, port),
                    family=socket.AF_INET,
                )
                self._transports.append(transport)
                logger.info("Flow UDP 监听已启动", protocol=protocol_type, host=settings.FLOW_LISTEN_HOST, port=port)
            except Exception as exc:
                logger.error("Flow UDP 监听启动失败", protocol=protocol_type, port=port, error=str(exc))

        self._flush_task = asyncio.create_task(self._flush_loop())

    async def stop(self) -> None:
        if self._flush_task:
            self._flush_task.cancel()
            try:
                await self._flush_task
            except asyncio.CancelledError:
                pass
            self._flush_task = None
        await self._flush(sync=True)
        for transport in self._transports:
            transport.close()
        self._transports.clear()
        logger.info("Flow 接收器已停止")

    def handle_datagram(self, protocol_type: str, data: bytes, addr) -> None:
        stats = self._stats.setdefault(protocol_type, self._new_protocol_stats())
        stats["datagrams"] = int(stats.get("datagrams") or 0) + 1
        stats["last_source"] = addr[0] if addr else None
        stats["last_datagram_at"] = datetime.now(timezone.utc).isoformat()
        try:
            if protocol_type == "netflow":
                records = self._parse_netflow_v5(data)
            else:
                records = self._parse_sflow_v5(data)
            stats["records"] = int(stats.get("records") or 0) + len(records)
            if records:
                stats["last_record_at"] = datetime.now(timezone.utc).isoformat()
                self._load_customer_networks_if_needed()
                for record in records:
                    self._account_interface_record(record, addr[0] if addr else None)
                    if self._account_record(record):
                        stats["matched_records"] = int(stats.get("matched_records") or 0) + 1
        except Exception as exc:
            stats["parse_errors"] = int(stats.get("parse_errors") or 0) + 1
            stats["last_error"] = str(exc)
            logger.warning("Flow 数据解析失败", protocol=protocol_type, source=addr[0] if addr else None, error=str(exc))

    def get_status(self) -> Dict[str, object]:
        try:
            self._load_customer_networks_if_needed()
        except Exception as exc:
            logger.warning("Flow 客户公网网段状态刷新失败", error=str(exc))
        return {
            "enabled": settings.FLOW_ENABLED,
            "listen_host": settings.FLOW_LISTEN_HOST,
            "netflow_port": settings.FLOW_NETFLOW_PORT,
            "sflow_port": settings.FLOW_SFLOW_PORT,
            "flush_interval_seconds": settings.FLOW_FLUSH_INTERVAL_SECONDS,
            "customer_networks": len(self._networks),
            "pending_buckets": len(self._buckets),
            "pending_interface_buckets": len(self._interface_buckets),
            "stats": self._stats,
        }

    def _load_customer_networks_if_needed(self) -> None:
        now = asyncio.get_running_loop().time()
        if self._networks and now - self._networks_loaded_at < settings.FLOW_CUSTOMER_CACHE_SECONDS:
            return

        db = SessionLocal()
        try:
            networks: List[CustomerNetwork] = []
            for customer in db.query(Customer).filter(Customer.is_active == True).all():  # noqa: E712
                for cidr in self._customer_public_cidrs(customer):
                    try:
                        network_text = cidr if "/" in cidr else f"{cidr}/32"
                        network = ipaddress.ip_network(network_text, strict=False)
                    except ValueError:
                        continue
                    networks.append(CustomerNetwork(customer.id, customer.name, str(network), network))
            networks.sort(key=lambda item: item.network.prefixlen, reverse=True)
            self._networks = networks
            self._networks_loaded_at = now
        finally:
            db.close()

    def _customer_public_cidrs(self, customer: Customer) -> List[str]:
        cidrs: List[str] = []
        for site in customer.customer_sites or []:
            if not isinstance(site, dict):
                continue
            for entry in site.get("public_address_entries") or []:
                if not isinstance(entry, dict):
                    continue
                cidr = entry.get("cidr") or (
                    f"{entry.get('prefix')}/{entry.get('mask')}" if entry.get("prefix") and entry.get("mask") else entry.get("prefix")
                )
                if cidr:
                    cidrs.append(str(cidr).strip())
        if customer.public_addresses:
            for item in str(customer.public_addresses).replace("，", ",").replace("；", ",").replace("\n", ",").split(","):
                text = item.strip()
                if not text:
                    continue
                cidrs.append(text.split(":", 1)[-1].strip())
        return list(dict.fromkeys([item for item in cidrs if item]))

    def _match_best(self, ip_text: str) -> Optional[CustomerNetwork]:
        try:
            ip_value = ipaddress.ip_address(ip_text)
        except ValueError:
            return None
        return next((item for item in self._networks if ip_value in item.network), None)

    def _account_record(self, record: FlowRecord) -> bool:
        src_match = self._match_best(record.src_ip)
        dst_match = self._match_best(record.dst_ip)
        matched = False

        if src_match and dst_match and src_match.customer_id == dst_match.customer_id:
            return False
        if src_match:
            self._add_bucket(src_match, ip_text=record.src_ip, out_bits=record.bits)
            matched = True
        if dst_match:
            self._add_bucket(dst_match, ip_text=record.dst_ip, in_bits=record.bits)
            matched = True
        return matched

    def _account_interface_record(self, record: FlowRecord, agent_ip: Optional[str]) -> None:
        if not agent_ip:
            return
        if record.input_interface and record.input_interface > 0:
            self._add_interface_bucket(agent_ip, record.input_interface, record.src_ip, in_bits=record.bits)
        if record.output_interface and record.output_interface > 0:
            self._add_interface_bucket(agent_ip, record.output_interface, record.dst_ip, out_bits=record.bits)

    def _add_interface_bucket(self, agent_ip: str, interface_index: int, ip_text: str, in_bits: float = 0.0, out_bits: float = 0.0) -> None:
        key = (agent_ip, int(interface_index), ip_text)
        bucket = self._interface_buckets.setdefault(key, {"in_bits": 0.0, "out_bits": 0.0})
        bucket["in_bits"] = float(bucket.get("in_bits") or 0.0) + in_bits
        bucket["out_bits"] = float(bucket.get("out_bits") or 0.0) + out_bits

    def _add_bucket(self, match: CustomerNetwork, ip_text: str, in_bits: float = 0.0, out_bits: float = 0.0) -> None:
        key = (match.customer_id, match.customer_name, match.cidr, ip_text)
        bucket = self._buckets.setdefault(key, {"in_bits": 0.0, "out_bits": 0.0})
        bucket["in_bits"] = float(bucket.get("in_bits") or 0.0) + in_bits
        bucket["out_bits"] = float(bucket.get("out_bits") or 0.0) + out_bits

    async def _flush_loop(self) -> None:
        while True:
            await asyncio.sleep(max(int(settings.FLOW_FLUSH_INTERVAL_SECONDS), 1))
            await self._flush()

    async def _flush(self, sync: bool = False) -> None:
        if not self._buckets:
            customer_buckets = {}
        else:
            customer_buckets = self._buckets
            self._buckets = {}
        if not self._interface_buckets:
            interface_buckets = {}
        else:
            interface_buckets = self._interface_buckets
            self._interface_buckets = {}
        if not customer_buckets and not interface_buckets:
            self._last_flush = datetime.now(timezone.utc)
            return
        now = datetime.now(timezone.utc)
        elapsed = max((now - self._last_flush).total_seconds(), 1.0)
        self._last_flush = now

        points = []
        for (customer_id, customer_name, cidr, ip_text), values in customer_buckets.items():
            in_bits = float(values.get("in_bits") or 0.0)
            out_bits = float(values.get("out_bits") or 0.0)
            points.append({
                "measurement": "customer_ip_traffic",
                "tags": {
                    "customer_id": str(customer_id),
                    "customer_name": customer_name,
                    "cidr": cidr,
                    "ip": ip_text,
                },
                "fields": {
                    "in_bps": in_bits / elapsed,
                    "out_bps": out_bits / elapsed,
                    "in_bits": in_bits,
                    "out_bits": out_bits,
                },
                "timestamp": now,
            })
        for (agent_ip, interface_index, ip_text), values in interface_buckets.items():
            in_bits = float(values.get("in_bits") or 0.0)
            out_bits = float(values.get("out_bits") or 0.0)
            points.append({
                "measurement": "sflow_interface_ip_traffic",
                "tags": {
                    "agent_ip": agent_ip,
                    "interface_index": str(interface_index),
                    "ip": ip_text,
                },
                "fields": {
                    "in_bps": in_bits / elapsed,
                    "out_bps": out_bits / elapsed,
                    "total_bps": (in_bits + out_bits) / elapsed,
                    "in_bits": in_bits,
                    "out_bits": out_bits,
                },
                "timestamp": now,
            })
        if points:
            await asyncio.to_thread(influx_client.write_points, points, sync)

    def _parse_netflow_v5(self, data: bytes) -> List[FlowRecord]:
        if len(data) < 24:
            return []
        version, count = struct.unpack_from("!HH", data, 0)
        if version != 5:
            return []
        count = min(count, (len(data) - 24) // 48)
        records: List[FlowRecord] = []
        offset = 24
        for _ in range(count):
            src_raw, dst_raw, _, input_if, output_if, _, octets = struct.unpack_from("!IIIHHII", data, offset)
            protocol = data[offset + 38]
            records.append(FlowRecord(
                src_ip=str(ipaddress.ip_address(src_raw)),
                dst_ip=str(ipaddress.ip_address(dst_raw)),
                bits=float(octets) * 8.0,
                protocol=protocol,
                input_interface=int(input_if) or None,
                output_interface=int(output_if) or None,
            ))
            offset += 48
        return records

    def _parse_sflow_v5(self, data: bytes) -> List[FlowRecord]:
        if len(data) < 28:
            return []
        version = struct.unpack_from("!I", data, 0)[0]
        if version != 5:
            return []
        offset = 4
        address_type = struct.unpack_from("!I", data, offset)[0]
        offset += 4 + (4 if address_type == 1 else 16)
        if len(data) < offset + 16:
            return []
        offset += 12
        sample_count = struct.unpack_from("!I", data, offset)[0]
        offset += 4

        records: List[FlowRecord] = []
        for _ in range(sample_count):
            if len(data) < offset + 8:
                break
            sample_type, sample_len = struct.unpack_from("!II", data, offset)
            offset += 8
            sample_data = data[offset: offset + sample_len]
            offset += sample_len
            sample_format = sample_type & 0x0FFF
            if sample_format in {1, 3}:
                records.extend(self._parse_sflow_flow_sample(sample_data, expanded=(sample_format == 3)))
        return records

    def _parse_sflow_flow_sample(self, data: bytes, expanded: bool = False) -> List[FlowRecord]:
        minimum = 44 if expanded else 32
        if len(data) < minimum:
            return []
        offset = 0
        offset += 4
        if expanded:
            offset += 8
        else:
            offset += 4
        sampling_rate = struct.unpack_from("!I", data, offset)[0] or 1
        offset += 4
        offset += 8
        if expanded:
            if len(data) < offset + 16:
                return []
            input_format, input_value, output_format, output_value = struct.unpack_from("!IIII", data, offset)
            input_interface = input_value if input_format == 0 else None
            output_interface = output_value if output_format == 0 else None
            offset += 16
        else:
            if len(data) < offset + 8:
                return []
            input_raw, output_raw = struct.unpack_from("!II", data, offset)
            input_interface = input_raw & 0x3FFFFFFF if (input_raw >> 30) == 0 else None
            output_interface = output_raw & 0x3FFFFFFF if (output_raw >> 30) == 0 else None
            offset += 8
        if len(data) < offset + 4:
            return []
        record_count = struct.unpack_from("!I", data, offset)[0]
        offset += 4

        records: List[FlowRecord] = []
        for _ in range(record_count):
            if len(data) < offset + 8:
                break
            record_type, record_len = struct.unpack_from("!II", data, offset)
            offset += 8
            record_data = data[offset: offset + record_len]
            offset += record_len
            record_format = record_type & 0x0FFF
            if record_format == 1:
                parsed = self._parse_sflow_raw_packet_record(record_data, sampling_rate)
                if parsed:
                    records.append(FlowRecord(
                        src_ip=parsed.src_ip,
                        dst_ip=parsed.dst_ip,
                        bits=parsed.bits,
                        protocol=parsed.protocol,
                        input_interface=input_interface,
                        output_interface=output_interface,
                    ))
        return records

    def _parse_sflow_raw_packet_record(self, data: bytes, sampling_rate: int) -> Optional[FlowRecord]:
        if len(data) < 16:
            return None
        _, frame_length, _, header_size = struct.unpack_from("!IIII", data, 0)
        header = data[16:16 + header_size]
        parsed = self._parse_ipv4_packet_header(header)
        if not parsed:
            return None
        src_ip, dst_ip, protocol = parsed
        return FlowRecord(src_ip=src_ip, dst_ip=dst_ip, bits=float(frame_length) * float(sampling_rate) * 8.0, protocol=protocol)

    def _parse_ipv4_packet_header(self, header: bytes) -> Optional[Tuple[str, str, int]]:
        offset = 0
        if len(header) >= 14 and (header[0] >> 4) != 4:
            eth_type = struct.unpack_from("!H", header, 12)[0]
            offset = 14
            if eth_type in {0x8100, 0x88A8} and len(header) >= 18:
                eth_type = struct.unpack_from("!H", header, 16)[0]
                offset = 18
            if eth_type != 0x0800:
                return None
        if len(header) < offset + 20 or (header[offset] >> 4) != 4:
            return None
        protocol = header[offset + 9]
        src_ip = str(ipaddress.ip_address(header[offset + 12: offset + 16]))
        dst_ip = str(ipaddress.ip_address(header[offset + 16: offset + 20]))
        return src_ip, dst_ip, protocol


flow_listener = FlowListener()
