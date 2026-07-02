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
import signal
import time
from concurrent import futures
from pathlib import Path
from typing import Iterable, Iterator

import grpc


LOGGER = logging.getLogger("telemetry_receiver")


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

    def __init__(self, recorder: TelemetryRawRecorder, log_every: int = 100) -> None:
        self.recorder = recorder
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


def serve(host: str, port: int, spool_dir: str, max_workers: int, log_every: int) -> None:
    recorder = TelemetryRawRecorder(spool_dir=spool_dir)
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=max_workers))
    server.add_generic_rpc_handlers((CatchAllTelemetryHandler(recorder, log_every=log_every),))
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
        server.stop(grace=1)


def main() -> None:
    _configure_logging()
    parser = argparse.ArgumentParser(description="Telemetry gRPC dial-out probe receiver")
    parser.add_argument("--host", default=os.getenv("TELEMETRY_RECEIVER_HOST", "0.0.0.0"))
    parser.add_argument("--port", type=int, default=int(os.getenv("TELEMETRY_RECEIVER_PORT", "50051")))
    parser.add_argument("--spool-dir", default=os.getenv("TELEMETRY_RECEIVER_SPOOL_DIR", "/app/data/telemetry"))
    parser.add_argument("--max-workers", type=int, default=int(os.getenv("TELEMETRY_RECEIVER_MAX_WORKERS", "16")))
    parser.add_argument("--log-every", type=int, default=int(os.getenv("TELEMETRY_RECEIVER_LOG_EVERY", "100")))
    args = parser.parse_args()
    serve(args.host, args.port, args.spool_dir, args.max_workers, args.log_every)


if __name__ == "__main__":
    main()
