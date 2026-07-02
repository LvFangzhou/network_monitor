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
import os
import re
import signal
import time
from datetime import datetime, timezone
from concurrent import futures
from pathlib import Path
from typing import Any, Iterable, Iterator, Optional

import grpc


LOGGER = logging.getLogger("telemetry_receiver")

PATH_PATTERN = re.compile(r"([A-Za-z][A-Za-z0-9]+/[A-Za-z0-9_./-]+)")
JSON_MARKER = b'{"Notification"'


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
    text = payload.decode("utf-8", errors="ignore")
    matches = PATH_PATTERN.findall(text)
    for item in matches:
        if not item.startswith(("http/", "https/")):
            return item
    return ""


def _safe_float(value: Any) -> Optional[float]:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


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
                return data
            finally:
                db.close()
        except Exception as exc:
            LOGGER.warning("resolve telemetry device failed ip=%s error=%s", ip_address, exc)
            return None


class TelemetryInfluxWriter:
    def __init__(self, enabled: bool = True, batch_size: int = 500, flush_interval: float = 2.0) -> None:
        self.enabled = enabled
        self.batch_size = max(1, batch_size)
        self.flush_interval = max(0.1, flush_interval)
        self._points: list[dict[str, Any]] = []
        self._last_flush = time.time()
        self._resolver = DeviceResolver()
        self._written = 0

    def handle_payload(self, payload: bytes, received_at: datetime) -> int:
        if not self.enabled:
            return 0
        sensor_path = _extract_sensor_path(payload)
        if sensor_path.lower() != "ifmgr/statistics":
            self._maybe_flush()
            return 0
        obj = _extract_json_payload(payload)
        if not obj:
            return 0
        try:
            interfaces = obj.get("Notification", {}).get("Ifmgr", {}).get("Statistics", {}).get("Interface", [])
            if isinstance(interfaces, dict):
                interfaces = [interfaces]
        except Exception:
            return 0
        if not interfaces:
            return 0

        text = payload.decode("utf-8", errors="ignore")
        ip_match = re.search(r"(\d{1,3}(?:\.\d{1,3}){3})", text)
        device_ip = ip_match.group(1) if ip_match else ""
        device = self._resolver.get(device_ip)
        if not device:
            return 0

        added = 0
        for item in interfaces:
            point = self._interface_point(device, item, received_at)
            if point:
                self._points.append(point)
                added += 1
        self._maybe_flush()
        return added

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
                LOGGER.info("telemetry points written measurement=interface_monitoring count=%s total=%s", len(points), self._written)
            else:
                LOGGER.warning("telemetry points write returned false count=%s", len(points))
        except Exception as exc:
            LOGGER.exception("telemetry points write failed count=%s error=%s", len(points), exc)

    def flush(self) -> None:
        self._maybe_flush(force=True)


class TelemetryRawRecorder:
    def __init__(self, spool_dir: str | Path, max_saved_messages: int = 2000) -> None:
        self.spool_dir = Path(spool_dir)
        self.spool_dir.mkdir(parents=True, exist_ok=True)
        self.max_saved_messages = max_saved_messages
        self._saved_messages = 0
        self._session_seq = 0

    def next_session_id(self) -> str:
        self._session_seq += 1
        return f"{int(time.time())}-{os.getpid()}-{self._session_seq}"

    def record_event(self, event: dict) -> None:
        day = time.strftime("%Y%m%d")
        path = self.spool_dir / f"telemetry-events-{day}.jsonl"
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")) + "\n")

    def record_payload(self, session_id: str, index: int, payload: bytes) -> None:
        if self._saved_messages >= self.max_saved_messages:
            return
        self._saved_messages += 1
        payload_dir = self.spool_dir / "payloads" / session_id
        payload_dir.mkdir(parents=True, exist_ok=True)
        (payload_dir / f"{index:06d}.bin").write_bytes(payload)


class CatchAllTelemetryHandler(grpc.GenericRpcHandler):
    """Accept any gRPC method and treat requests as raw bytes."""

    def __init__(self, recorder: TelemetryRawRecorder, writer: TelemetryInfluxWriter, log_every: int = 100) -> None:
        self.recorder = recorder
        self.writer = writer
        self.log_every = max(1, log_every)

    def service(self, handler_call_details: grpc.HandlerCallDetails):
        method = handler_call_details.method

        def stream_stream(request_iterator: Iterable[bytes], context: grpc.ServicerContext) -> Iterator[bytes]:
            peer = context.peer()
            session_id = self.recorder.next_session_id()
            started = time.time()
            count = 0
            total_bytes = 0
            LOGGER.info("telemetry session opened peer=%s method=%s session=%s", peer, method, session_id)
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

            try:
                for payload in request_iterator:
                    count += 1
                    total_bytes += len(payload)
                    preview = _safe_preview(payload)
                    if count <= 5 or count % self.log_every == 0:
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
                    self.recorder.record_event(
                        {
                            "event": "message",
                            "time": time.time(),
                            "session_id": session_id,
                            "peer": peer,
                            "method": method,
                            "index": count,
                            "preview": preview,
                        }
                    )
                    self.recorder.record_payload(session_id, count, payload)
                    written_candidates = self.writer.handle_payload(payload, datetime.now(timezone.utc))
                    if written_candidates and (count <= 5 or count % self.log_every == 0):
                        LOGGER.info("telemetry payload mapped to interface points session=%s index=%s points=%s", session_id, count, written_candidates)
            except Exception as exc:  # pragma: no cover - runtime diagnostics only
                LOGGER.exception("telemetry session error peer=%s method=%s session=%s error=%s", peer, method, session_id, exc)
                self.recorder.record_event(
                    {
                        "event": "session_error",
                        "time": time.time(),
                        "session_id": session_id,
                        "peer": peer,
                        "method": method,
                        "error": str(exc),
                    }
                )
                raise
            finally:
                finished = time.time()
                LOGGER.info(
                    "telemetry session closed peer=%s method=%s session=%s messages=%s bytes=%s duration=%.3fs",
                    peer,
                    method,
                    session_id,
                    count,
                    total_bytes,
                    finished - started,
                )
                self.writer.flush()
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

            return iter(())

        return grpc.stream_stream_rpc_method_handler(
            stream_stream,
            request_deserializer=lambda raw: raw,
            response_serializer=lambda raw: raw,
        )


def serve(host: str, port: int, spool_dir: str, max_workers: int, log_every: int, write_influx: bool) -> None:
    recorder = TelemetryRawRecorder(spool_dir=spool_dir)
    writer = TelemetryInfluxWriter(enabled=write_influx)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    server.add_generic_rpc_handlers((CatchAllTelemetryHandler(recorder, writer=writer, log_every=log_every),))
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
        writer.flush()
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
    args = parser.parse_args()
    serve(args.host, args.port, args.spool_dir, args.max_workers, args.log_every, args.write_influx)


if __name__ == "__main__":
    main()
