"""
Public quality probe helpers.
"""
from __future__ import annotations

import time
import json
import re
import hashlib
import shutil
import subprocess
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from typing import Any, Dict, List

from app.core import get_logger
from app.utils import influx_client, redis_client

try:
    from ping3 import ping
    PING3_AVAILABLE = True
except ImportError:
    ping = None
    PING3_AVAILABLE = False


logger = get_logger(__name__)
QUALITY_LOSS_WINDOW_SECONDS = 5 * 60
QUALITY_LOSS_WINDOW_MAX_SAMPLES = 1200
QUALITY_LOSS_WINDOW_TTL_SECONDS = 24 * 60 * 60
DISMAN_PING_RESULTS_ENTRY_OID = "1.3.6.1.2.1.80.1.3.1"
DISMAN_PING_CONTROL_ENTRY_OID = "1.3.6.1.2.1.80.1.2.1"


def _safe_mtr_target(target: str) -> str:
    target_text = str(target or "").strip()
    if not target_text or not re.fullmatch(r"[A-Za-z0-9_.:-]+", target_text):
        raise ValueError("目标地址不合法")
    return target_text


def _normalize_mtr_asn(as_info: str | None) -> str | None:
    as_text = str(as_info or "").strip()
    if not as_text or as_text in {"-", "???", "AS???"}:
        return None
    match = re.search(r"AS\s*(\d+)", as_text, re.IGNORECASE)
    if match:
        return f"AS{match.group(1)}"
    if re.fullmatch(r"\d+", as_text):
        return f"AS{as_text}"
    return as_text


def _parse_mtr_number(value: str) -> float:
    return float(str(value).strip().rstrip("%"))


def parse_mtr_report(output: str) -> List[Dict[str, Any]]:
    """Parse `mtr -r -n -z -c N -w` report into hop rows.

    mtr output differs by version. Without `-z` rows look like:
      1.|-- 172.18.0.1 0.0% 5 ...
    With `-z` rows usually look like:
      1. AS9808 111.4.243.193 0.0% 5 ...
    Keep the parser position-based around the Loss% column so old snapshots and
    newer AS-enabled reports are both accepted.
    """
    hops: List[Dict[str, Any]] = []
    for raw_line in str(output or "").splitlines():
        line = raw_line.strip()
        row_match = re.match(r"^(?P<hop>\d+)\.(?:\|--)?\s+(?P<rest>.+)$", line)
        if not row_match:
            continue
        tokens = row_match.group("rest").split()
        loss_index = next(
            (idx for idx, token in enumerate(tokens) if re.fullmatch(r"\d+(?:\.\d+)?%?", token) and idx + 6 < len(tokens)),
            None,
        )
        if loss_index is None or loss_index < 1:
            continue
        host = tokens[loss_index - 1]
        as_info = " ".join(tokens[:loss_index - 1]).strip() or None
        asn = _normalize_mtr_asn(as_info)
        if host in {"???", "*"}:
            host = None
        try:
            hops.append({
                "hop": int(row_match.group("hop")),
                "ip": host,
                "asn": asn,
                "as_info": as_info,
                "loss_percent": _parse_mtr_number(tokens[loss_index]),
                "sent": int(tokens[loss_index + 1]),
                "last_ms": _parse_mtr_number(tokens[loss_index + 2]),
                "avg_ms": _parse_mtr_number(tokens[loss_index + 3]),
                "best_ms": _parse_mtr_number(tokens[loss_index + 4]),
                "worst_ms": _parse_mtr_number(tokens[loss_index + 5]),
                "stdev_ms": _parse_mtr_number(tokens[loss_index + 6]),
            })
        except (TypeError, ValueError):
            continue
    return hops


def summarize_mtr_path(hops: List[Dict[str, Any]]) -> Dict[str, Any]:
    visible_hops = [hop for hop in hops if hop.get("ip")]
    path_ips = [str(hop.get("ip")) for hop in visible_hops]
    path_hash = hashlib.sha256("|".join(path_ips).encode("utf-8")).hexdigest()[:32] if path_ips else ""
    final_hop = visible_hops[-1] if visible_hops else None
    avg_values = [float(hop.get("avg_ms")) for hop in visible_hops if hop.get("avg_ms") is not None]
    return {
        "path_hash": path_hash,
        "hop_count": len(visible_hops),
        "final_hop_ip": final_hop.get("ip") if final_hop else None,
        "final_avg_latency_ms": final_hop.get("avg_ms") if final_hop else None,
        "final_loss_percent": final_hop.get("loss_percent") if final_hop else None,
        "max_avg_latency_ms": max(avg_values) if avg_values else None,
        "path_ips": path_ips,
    }


def run_mtr_or_trace(target: str, count: int = 5, timeout_seconds: int = 30, allow_ping_fallback: bool = True) -> Dict[str, Any]:
    """Run MTR/traceroute/ping once and return raw output plus parsed hops when possible."""
    try:
        target_text = _safe_mtr_target(target)
    except ValueError as exc:
        return {"success": False, "command": "", "output": str(exc), "tool": "none", "hops": [], "error": str(exc)}

    candidates = []
    if shutil.which("mtr"):
        sample_count = str(max(1, min(int(count or 5), 20)))
        candidates.append(("mtr", ["mtr", "-r", "-n", "-z", "-c", sample_count, "-w", target_text]))
        candidates.append(("mtr", ["mtr", "-r", "-n", "-c", sample_count, "-w", target_text]))
    if shutil.which("traceroute"):
        candidates.append(("traceroute", ["traceroute", "-n", "-m", "20", "-w", "2", target_text]))
    if allow_ping_fallback and shutil.which("ping"):
        candidates.append(("ping", ["ping", "-c", "5", "-W", "2", target_text]))
    if not candidates:
        missing_tools = "mtr/traceroute" if not allow_ping_fallback else "mtr/traceroute/ping"
        return {
            "success": False,
            "command": "",
            "output": f"服务器未安装 {missing_tools} 工具，无法执行路径探测。请在后端容器中安装 mtr 或 traceroute。",
            "tool": "none",
            "hops": [],
            "error": f"缺少{missing_tools}工具",
        }

    last_error = "unknown"
    for tool, command in candidates:
        try:
            completed = subprocess.run(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                timeout=max(5, min(int(timeout_seconds or 30), 120)),
                check=False,
            )
            output = (completed.stdout or "").strip()
            if not output:
                continue
            hops = parse_mtr_report(output) if tool == "mtr" else []
            summary = summarize_mtr_path(hops)
            return {
                "success": completed.returncode == 0 or bool(output),
                "command": " ".join(command),
                "output": output,
                "tool": tool,
                "hops": hops,
                "error": None if completed.returncode == 0 else None,
                **summary,
            }
        except Exception as exc:
            last_error = str(exc)
            continue
    return {"success": False, "command": "", "output": f"MTR/Trace 执行失败: {last_error}", "tool": "none", "hops": [], "error": last_error}


def _decode_disman_ping_index(parts: List[int]) -> tuple[str, str] | None:
    """Decode pingCtlOwnerIndex/pingCtlTestName from an OID suffix."""
    try:
        owner_length = parts[0]
        owner_end = 1 + owner_length
        owner = bytes(parts[1:owner_end]).decode("utf-8", errors="replace")
        tag_length = parts[owner_end]
        tag_start = owner_end + 1
        tag = bytes(parts[tag_start:tag_start + tag_length]).decode("utf-8", errors="replace")
        return owner, tag
    except (IndexError, TypeError, ValueError):
        return None


def _encode_disman_ping_index(owner: str, operation_tag: str) -> str:
    owner_bytes = str(owner).encode("utf-8")
    tag_bytes = str(operation_tag).encode("utf-8")
    parts = [len(owner_bytes), *owner_bytes, len(tag_bytes), *tag_bytes]
    return ".".join(str(part) for part in parts)


def _read_h3c_native_jitter(
    collector: Any,
    device: Any,
    owner: str,
    operation_tag: str,
) -> Dict[str, Any]:
    """Read native H3C ICMP-jitter statistics (legacy and new enterprise IDs)."""
    index_suffix = _encode_disman_ping_index(owner, operation_tag)
    type_oid = collector.snmp_get(
        device,
        f"{DISMAN_PING_CONTROL_ENTRY_OID}.16.{index_suffix}",
    )
    type_text = str(type_oid or "").strip().lstrip(".")
    root_match = re.match(r"^(1\.3\.6\.1\.4\.1\.\d+\.8\.3)\.2\.3$", type_text)
    if not root_match:
        return {}

    table_oid = f"{root_match.group(1)}.1.4.1"
    rows = collector.snmp_walk(device, table_oid) or []
    values: Dict[int, Any] = {}
    prefix = table_oid + "."
    for oid, value in rows:
        oid_text = str(oid or "").lstrip(".")
        if not oid_text.startswith(prefix) or "No Such" in str(value):
            continue
        try:
            suffix = [int(part) for part in oid_text[len(prefix):].split(".")]
            column = suffix[0]
        except (TypeError, ValueError, IndexError):
            continue
        if _decode_disman_ping_index(suffix[1:]) == (owner, operation_tag):
            values[column] = value

    def _number(column: int) -> float | None:
        try:
            return float(values[column])
        except (KeyError, TypeError, ValueError):
            return None

    if not values:
        return {}
    jitter_sd = _number(31)
    jitter_ds = _number(32)
    directional = [value for value in (jitter_sd, jitter_ds) if value is not None]
    return {
        "jitter_ms": round(max(directional), 2) if directional else None,
        "jitter_sd_ms": jitter_sd,
        "jitter_ds_ms": jitter_ds,
        "jitter_source": "h3c_nqa_native",
        "jitter_rtt_samples": int(_number(1) or 0),
        "packet_loss_sd": int(_number(22) or 0),
        "packet_loss_ds": int(_number(23) or 0),
    }


def discover_quality_nqa_snmp_instances(device: Any) -> List[Dict[str, Any]]:
    """Discover configured DISMAN-PING-MIB NQA jobs and their latest results."""
    from app.collectors.snmp_collector import SNMPCollector

    collector = SNMPCollector()
    control_rows = collector.snmp_walk(device, DISMAN_PING_CONTROL_ENTRY_OID) or []
    result_rows = collector.snmp_walk(device, DISMAN_PING_RESULTS_ENTRY_OID) or []
    instances: Dict[tuple[str, str], Dict[str, Any]] = {}

    def _consume(rows: List[Any], base_oid: str, bucket: str) -> None:
        prefix = base_oid + "."
        for oid, value in rows:
            oid_text = str(oid or "")
            if not oid_text.startswith(prefix) or "No Such" in str(value):
                continue
            try:
                suffix = [int(part) for part in oid_text[len(prefix):].split(".")]
                column = suffix[0]
            except (TypeError, ValueError, IndexError):
                continue
            index = _decode_disman_ping_index(suffix[1:])
            if not index:
                continue
            item = instances.setdefault(index, {"admin_name": index[0], "operation_tag": index[1]})
            item.setdefault(bucket, {})[column] = value

    _consume(control_rows, DISMAN_PING_CONTROL_ENTRY_OID, "control")
    _consume(result_rows, DISMAN_PING_RESULTS_ENTRY_OID, "result")

    output: List[Dict[str, Any]] = []
    for (admin_name, operation_tag), item in sorted(instances.items()):
        control = item.get("control", {})
        result = item.get("result", {})

        def _number(values: Dict[int, Any], column: int) -> float | None:
            try:
                return float(values[column])
            except (KeyError, TypeError, ValueError):
                return None

        sent = int(_number(result, 8) or 0)
        received = max(0, min(int(_number(result, 7) or 0), sent)) if sent else 0
        loss = round((sent - received) * 100.0 / sent, 2) if sent else None
        frequency = int(_number(control, 10) or 0)
        timeout_seconds = _number(control, 6)
        output.append({
            "key": f"{admin_name}\u0000{operation_tag}",
            "admin_name": admin_name,
            "operation_tag": operation_tag,
            "target": str(control.get(4) or "").strip(),
            "source": str(control.get(19) or "").strip() or None,
            "frequency_seconds": frequency or None,
            "packet_count": int(_number(control, 7) or 0) or None,
            "timeout_ms": int(timeout_seconds * 1000) if timeout_seconds is not None else None,
            "is_enabled": int(_number(control, 8) or 0) == 1,
            "has_result": bool(result),
            "avg_latency_ms": _number(result, 6),
            "min_latency_ms": _number(result, 4),
            "max_latency_ms": _number(result, 5),
            "packet_loss_percent": loss,
            "sent": sent,
            "received": received,
        })
    return output


def run_quality_nqa_snmp(target: Any, device: Any) -> Dict[str, Any]:
    """Read the latest device-generated NQA result through DISMAN-PING-MIB.

    H3C ICMP-jitter uses the device-native SD/DS value when available. ICMP
    echo falls back to the absolute change between consecutive average RTTs.
    """
    from app.collectors.snmp_collector import SNMPCollector

    owner = str(getattr(target, "nqa_admin_name", None) or "test")
    operation_tag = str(getattr(target, "nqa_operation_tag", None) or "1")
    collector = SNMPCollector()
    try:
        rows = collector.snmp_walk(device, DISMAN_PING_RESULTS_ENTRY_OID) or []
    except Exception as exc:
        return {
            "success": False,
            "avg_latency_ms": None,
            "min_latency_ms": None,
            "max_latency_ms": None,
            "jitter_ms": None,
            "packet_loss_percent": 100.0,
            "availability_percent": 0.0,
            "received": 0,
            "sent": 0,
            "error": f"SNMP读取NQA失败：{exc}",
        }

    values: Dict[int, Any] = {}
    prefix = DISMAN_PING_RESULTS_ENTRY_OID + "."
    for oid, value in rows:
        oid_text = str(oid or "")
        if not oid_text.startswith(prefix) or "No Such" in str(value):
            continue
        try:
            suffix = [int(part) for part in oid_text[len(prefix):].split(".")]
            column = suffix[0]
        except (TypeError, ValueError, IndexError):
            continue
        index = _decode_disman_ping_index(suffix[1:])
        if index == (owner, operation_tag):
            values[column] = value

    def _number(column: int) -> float | None:
        try:
            return float(values[column])
        except (KeyError, TypeError, ValueError):
            return None

    min_latency = _number(4)
    max_latency = _number(5)
    avg_latency = _number(6)
    received = int(_number(7) or 0)
    sent = int(_number(8) or 0)
    if not values or sent <= 0:
        return {
            "success": False,
            "avg_latency_ms": avg_latency,
            "min_latency_ms": min_latency,
            "max_latency_ms": max_latency,
            "jitter_ms": None,
            "packet_loss_percent": 100.0 if sent > 0 else None,
            "availability_percent": 0.0 if sent > 0 else None,
            "received": received,
            "sent": sent,
            "error": f"未读取到NQA任务 {owner}/{operation_tag} 的有效结果",
        }

    received = max(0, min(received, sent))
    loss = round((sent - received) * 100.0 / sent, 2)
    availability = round(received * 100.0 / sent, 2)
    native_jitter: Dict[str, Any] = {}
    try:
        native_jitter = _read_h3c_native_jitter(collector, device, owner, operation_tag)
    except Exception as exc:
        logger.debug("读取H3C原生NQA抖动失败，使用RTT变化估算", device=getattr(device, "ip_address", None), error=str(exc))

    jitter = native_jitter.get("jitter_ms")
    jitter_source = native_jitter.get("jitter_source")
    if jitter is None and avg_latency is not None:
        previous_key = f"quality_probe:nqa_prev_latency:{target.id}"
        try:
            previous = redis_client.get(previous_key)
            if previous is not None:
                jitter = round(abs(avg_latency - float(previous)), 2)
                jitter_source = "rtt_delta_estimate"
            redis_client.set(previous_key, str(avg_latency), ex=24 * 60 * 60)
        except Exception:
            pass

    return {
        "success": received > 0,
        "avg_latency_ms": avg_latency,
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "jitter_ms": jitter,
        "packet_loss_percent": loss,
        "availability_percent": availability,
        "received": received,
        "sent": sent,
        "error": None if received > 0 else "NQA探测未收到响应",
        "probe_source": "device_nqa_snmp",
        "nqa_admin_name": owner,
        "nqa_operation_tag": operation_tag,
        "jitter_sd_ms": native_jitter.get("jitter_sd_ms"),
        "jitter_ds_ms": native_jitter.get("jitter_ds_ms"),
        "jitter_source": jitter_source,
        "jitter_rtt_samples": native_jitter.get("jitter_rtt_samples"),
    }


SERVER_ICMP_MIN_INTERVAL_SECONDS = 30
SERVER_ICMP_MIN_PACKET_COUNT = 10
SERVER_ICMP_MIN_TIMEOUT_MS = 1500
SERVER_ICMP_MIN_MTR_INTERVAL_SECONDS = 300
SERVER_ICMP_PING_BATCH_WORKERS = 3


def normalize_server_icmp_probe_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Clamp server-side ICMP quality probe settings to avoid single-packet false positives."""
    normalized = dict(config or {})
    probe_source = str(normalized.get("probe_source") or "server_icmp")
    if probe_source != "server_icmp":
        return normalized
    normalized["interval_seconds"] = max(int(normalized.get("interval_seconds") or 60), SERVER_ICMP_MIN_INTERVAL_SECONDS)
    normalized["packet_count"] = max(int(normalized.get("packet_count") or SERVER_ICMP_MIN_PACKET_COUNT), SERVER_ICMP_MIN_PACKET_COUNT)
    normalized["timeout_ms"] = max(int(normalized.get("timeout_ms") or 1000), SERVER_ICMP_MIN_TIMEOUT_MS)
    if bool(normalized.get("mtr_enabled")):
        normalized["mtr_interval_seconds"] = max(
            int(normalized.get("mtr_interval_seconds") or SERVER_ICMP_MIN_MTR_INTERVAL_SECONDS),
            SERVER_ICMP_MIN_MTR_INTERVAL_SECONDS,
        )
    return normalized


def _run_fping(target_host: str, packet_count: int, timeout_ms: int) -> Dict[str, Any] | None:
    """Run fping as the preferred server-side ICMP probe engine.

    fping is a small native tool designed for repeated/batch ICMP probes. It is
    less prone than in-process raw-socket libraries to producing false loss when
    many targets are collected concurrently.
    """
    fping_bin = shutil.which("fping")
    if not fping_bin:
        return None
    host = _safe_mtr_target(target_host)
    sent = max(1, min(int(packet_count or SERVER_ICMP_MIN_PACKET_COUNT), 50))
    timeout_ms_int = max(200, min(int(timeout_ms or 1000), 10000))
    interval_ms = max(50, min(timeout_ms_int // 10, 300))
    command = [
        fping_bin,
        "-C",
        str(sent),
        "-q",
        "-p",
        str(interval_ms),
        "-t",
        str(timeout_ms_int),
        host,
    ]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=max(5, int((sent * interval_ms + timeout_ms_int + 3000) / 1000)),
            check=False,
        )
    except Exception as exc:
        logger.debug("fping执行失败，回退到系统ping", target=host, error=str(exc))
        return None

    output = "\n".join(part.strip() for part in (completed.stdout, completed.stderr) if part and part.strip())
    result_line = next((line for line in output.splitlines() if ":" in line), "")
    samples_text = result_line.split(":", 1)[1].strip() if result_line else ""
    sample_tokens = samples_text.split()
    latencies: List[float] = []
    for token in sample_tokens:
        token_text = token.strip()
        if token_text in {"-", "-nan", "nan"}:
            continue
        try:
            latencies.append(float(token_text))
        except (TypeError, ValueError):
            continue

    transmitted = len(sample_tokens) if sample_tokens else sent
    received = len(latencies)
    loss = round((transmitted - received) * 100.0 / transmitted, 2) if transmitted else 100.0
    avg_latency = round(sum(latencies) / received, 2) if received else None
    min_latency = round(min(latencies), 2) if received else None
    max_latency = round(max(latencies), 2) if received else None
    if len(latencies) >= 2:
        diffs = [abs(latencies[index] - latencies[index - 1]) for index in range(1, len(latencies))]
        jitter = round(sum(diffs) / len(diffs), 2)
    elif len(latencies) == 1:
        jitter = 0.0
    else:
        jitter = None

    if not sample_tokens and completed.returncode not in (0, 1):
        logger.debug("fping未返回可解析结果，回退到系统ping", target=host, output=output[-500:])
        return None

    return {
        "success": received > 0,
        "avg_latency_ms": avg_latency,
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "jitter_ms": jitter,
        "packet_loss_percent": loss,
        "availability_percent": round(received * 100.0 / transmitted, 2) if transmitted else 0.0,
        "received": received,
        "sent": transmitted,
        "error": None if received > 0 else (output.strip().splitlines()[-1] if output.strip() else "fping未收到响应"),
        "probe_source": "server_icmp",
        "probe_engine": "fping",
    }


def _quality_ping_result_from_latencies(
    latencies: List[float],
    transmitted: int,
    engine: str,
    error: str | None = None,
) -> Dict[str, Any]:
    received = len(latencies)
    safe_sent = max(0, int(transmitted or 0))
    loss = round((safe_sent - received) * 100.0 / safe_sent, 2) if safe_sent else 100.0
    avg_latency = round(sum(latencies) / received, 2) if received else None
    min_latency = round(min(latencies), 2) if received else None
    max_latency = round(max(latencies), 2) if received else None
    if len(latencies) >= 2:
        diffs = [abs(latencies[index] - latencies[index - 1]) for index in range(1, len(latencies))]
        jitter = round(sum(diffs) / len(diffs), 2)
    elif len(latencies) == 1:
        jitter = 0.0
    else:
        jitter = None
    return {
        "success": received > 0,
        "avg_latency_ms": avg_latency,
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "jitter_ms": jitter,
        "packet_loss_percent": loss,
        "availability_percent": round(received * 100.0 / safe_sent, 2) if safe_sent else 0.0,
        "received": received,
        "sent": safe_sent,
        "error": None if received > 0 else (error or "目标无响应"),
        "probe_source": "server_icmp",
        "probe_engine": engine,
    }


def normalize_quality_ping_result(result: Dict[str, Any]) -> Dict[str, Any]:
    """Normalize ICMP probe loss/availability from sent/received counters.

    fping/system ping output can vary across versions/locales. The reliable
    source for the current card is the packet accounting we store with every
    probe sample, so keep packet_loss_percent derived from sent/received.
    """
    normalized = dict(result or {})
    try:
        sent = int(float(normalized.get("sent") or 0))
        received = int(float(normalized.get("received") or 0))
    except (TypeError, ValueError):
        return normalized
    if sent <= 0:
        return normalized
    received = max(0, min(received, sent))
    loss = round((sent - received) * 100.0 / sent, 2)
    normalized["sent"] = sent
    normalized["received"] = received
    normalized["packet_loss_percent"] = loss
    normalized["availability_percent"] = round(received * 100.0 / sent, 2)
    return normalized


def _parse_fping_sample_tokens(samples_text: str) -> tuple[int, List[float]]:
    sample_tokens = str(samples_text or "").split()
    latencies: List[float] = []
    for token in sample_tokens:
        token_text = token.strip()
        if token_text in {"-", "-nan", "nan"}:
            continue
        try:
            latencies.append(float(token_text))
        except (TypeError, ValueError):
            continue
    return len(sample_tokens), latencies


def run_quality_ping_batch(targets: List[Dict[str, Any]]) -> Dict[Any, Dict[str, Any]]:
    """Run server-side ICMP quality probes with limited concurrent system ping.

    In this deployment fping has repeatedly shown false packet loss while host
    ping remains stable, so system ping is the authoritative periodic engine.
    Concurrency is capped to avoid probe bursts and process pressure.
    """
    results: Dict[Any, Dict[str, Any]] = {}
    valid_items: List[Dict[str, Any]] = []
    for item in targets:
        target_id = item.get("id")
        if target_id is None:
            continue
        try:
            host = _safe_mtr_target(str(item.get("target") or ""))
        except ValueError as exc:
            results[target_id] = {
                "success": False,
                "avg_latency_ms": None,
                "min_latency_ms": None,
                "max_latency_ms": None,
                "jitter_ms": None,
                "packet_loss_percent": 100.0,
                "availability_percent": 0.0,
                "received": 0,
                "sent": 0,
                "error": str(exc) or "目标地址不合法",
                "probe_source": "server_icmp",
                "probe_engine": "system_ping_batch",
            }
            continue
        valid_items.append({**item, "_host": host})

    def _probe_one(item: Dict[str, Any]) -> tuple[Any, Dict[str, Any]]:
        target_id = item.get("id")
        host = str(item.get("_host") or "")
        # 周期任务固定控制在每轮最多 10 包；用户手工“立即测试”仍可按配置
        # 发更多包。这样可以避免旧目标保留 packet_count=20 时继续形成批量突发。
        sent = max(5, min(int(item.get("packet_count") or SERVER_ICMP_MIN_PACKET_COUNT), SERVER_ICMP_MIN_PACKET_COUNT))
        timeout_ms_int = max(200, min(int(item.get("timeout_ms") or 1000), 10000))
        result = _run_system_ping(host, sent, timeout_ms_int)
        if result is not None:
            result = normalize_quality_ping_result(result or {})
            try:
                result_sent = int(float(result.get("sent") or 0))
                result_received = int(float(result.get("received") or 0))
            except (TypeError, ValueError):
                result_sent, result_received = 0, 0
            # 公网 ICMP 偶发会出现单轮系统 ping 进程被调度/超时影响，造成与人工连续 ping
            # 不一致的假丢包。异常样本立即二次确认：只有复测仍异常，才把丢包写入结果。
            if result_sent > 0 and result_received < result_sent:
                confirm = _run_system_ping(host, sent, timeout_ms_int)
                if confirm is not None:
                    confirm = normalize_quality_ping_result(confirm or {})
                    try:
                        confirm_received = int(float(confirm.get("received") or 0))
                    except (TypeError, ValueError):
                        confirm_received = 0
                    if confirm_received >= result_received:
                        confirm["probe_confirmation"] = "system_ping_recheck"
                        confirm["probe_first_received"] = result_received
                        confirm["probe_first_sent"] = result_sent
                        result = confirm
        if result is None:
            result = _run_fping(host, sent, timeout_ms_int)
        if result is None:
            result = run_quality_ping(host, sent, timeout_ms_int)
        result = normalize_quality_ping_result(result or {})
        result["probe_engine"] = f"{result.get('probe_engine') or 'unknown'}_limited_batch"
        return target_id, result

    max_workers = max(1, min(SERVER_ICMP_PING_BATCH_WORKERS, len(valid_items) or 1))
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = [executor.submit(_probe_one, item) for item in valid_items]
        for future in as_completed(futures):
            try:
                target_id, result = future.result()
                results[target_id] = result
            except Exception as exc:
                logger.warning("系统ping批量探测失败", error=str(exc))

    return results


def _run_system_ping(target_host: str, packet_count: int, timeout_ms: int) -> Dict[str, Any] | None:
    """Run iputils ping as the primary server-side ICMP engine."""
    ping_bin = shutil.which("ping")
    if not ping_bin:
        return None
    host = _safe_mtr_target(target_host)
    sent = max(1, min(int(packet_count or SERVER_ICMP_MIN_PACKET_COUNT), 50))
    timeout_seconds = max(1, int(round(max(float(timeout_ms or 1000), 1000.0) / 1000.0)))
    # 公网目标集中到期时，过密的并发 ICMP burst 容易被中间设备或目标侧
    # ICMP policer 丢弃，形成与人工 ping 不一致的假丢包。保持每目标 2pps，
    # 再配合较低批量并发，15 个目标的总速率也只有约 6pps。
    interval_seconds = 0.5
    command = [ping_bin, "-c", str(sent), "-i", str(interval_seconds), "-W", str(timeout_seconds), host]
    try:
        completed = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            timeout=max(int(sent * interval_seconds + timeout_seconds + 5), timeout_seconds + 3),
            check=False,
        )
    except Exception as exc:
        logger.debug("系统ping执行失败，回退到fping/ping3", target=host, error=str(exc))
        return None

    output = completed.stdout or ""
    summary_match = re.search(
        r"(\d+)\s+packets transmitted,\s+(\d+)\s+(?:packets\s+)?received,\s+([0-9.]+)%\s+packet loss",
        output,
    )
    if not summary_match:
        summary_match = re.search(
            r"(\d+)\s+packets transmitted,\s+(\d+)\s+received,.*?([0-9.]+)%\s+packet loss",
            output,
            re.S,
        )
    if summary_match:
        transmitted = int(summary_match.group(1))
        received = int(summary_match.group(2))
        loss = round(float(summary_match.group(3)), 2)
    else:
        transmitted = sent
        received = len(re.findall(r"time[=<]([0-9.]+)\s*ms", output))
        loss = round((transmitted - received) * 100.0 / transmitted, 2)

    rtt_match = re.search(r"(?:rtt|round-trip) min/avg/max/(?:mdev|stddev) = ([0-9.]+)/([0-9.]+)/([0-9.]+)/([0-9.]+) ms", output)
    latencies = [float(value) for value in re.findall(r"time[=<]([0-9.]+)\s*ms", output)]
    if rtt_match:
        min_latency = round(float(rtt_match.group(1)), 2)
        avg_latency = round(float(rtt_match.group(2)), 2)
        max_latency = round(float(rtt_match.group(3)), 2)
        jitter = round(float(rtt_match.group(4)), 2)
    elif latencies:
        min_latency = round(min(latencies), 2)
        avg_latency = round(sum(latencies) / len(latencies), 2)
        max_latency = round(max(latencies), 2)
        jitter = round(max_latency - min_latency, 2)
    else:
        min_latency = avg_latency = max_latency = jitter = None

    return normalize_quality_ping_result({
        "success": received > 0,
        "avg_latency_ms": avg_latency,
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "jitter_ms": jitter,
        "packet_loss_percent": loss,
        "availability_percent": round(received * 100.0 / transmitted, 2) if transmitted else 0.0,
        "received": received,
        "sent": transmitted,
        "error": None if received > 0 else (output.strip().splitlines()[-1] if output.strip() else "ping未收到响应"),
        "probe_source": "server_icmp",
        "probe_engine": "system_ping",
    })


def run_quality_ping(target_host: str, packet_count: int = SERVER_ICMP_MIN_PACKET_COUNT, timeout_ms: int = 1000) -> Dict[str, Any]:
    """Run one ICMP quality probe and return latency/loss/jitter.

    Probe engine priority: system ping -> fping -> ping3. ping3 is kept only as
    a final compatibility fallback because it can report false packet loss under
    concurrent raw-socket probing.
    """
    host = str(target_host or "").strip()
    sent = max(1, min(int(packet_count or SERVER_ICMP_MIN_PACKET_COUNT), 50))
    if not host:
        return {
            "success": False,
            "avg_latency_ms": None,
            "min_latency_ms": None,
            "max_latency_ms": None,
            "jitter_ms": None,
            "packet_loss_percent": 100.0,
            "availability_percent": 0.0,
            "received": 0,
            "sent": 0,
            "error": "目标地址为空",
        }

    system_result = _run_system_ping(host, sent, timeout_ms)
    if system_result is not None:
        return system_result

    fping_result = _run_fping(host, sent, timeout_ms)
    if fping_result is not None:
        return normalize_quality_ping_result(fping_result)

    if not PING3_AVAILABLE or ping is None:
        return {
            "success": False,
            "avg_latency_ms": None,
            "min_latency_ms": None,
            "max_latency_ms": None,
            "jitter_ms": None,
            "packet_loss_percent": 100.0,
            "availability_percent": 0.0,
            "received": 0,
            "sent": sent,
            "error": "服务器缺少 fping/ping/ping3，无法执行 ICMP 探测",
        }

    timeout_seconds = max(0.2, min(float(timeout_ms or 1000) / 1000.0, 10.0))
    latencies: List[float] = []
    errors: List[str] = []
    for _ in range(sent):
        try:
            result = ping(host, timeout=timeout_seconds, unit="ms")
            if result is not None and result is not False:
                latencies.append(float(result))
        except Exception as e:
            errors.append(str(e))
        time.sleep(0.05)

    received = len(latencies)
    packet_loss = round((sent - received) * 100.0 / sent, 2)

    availability = round(received * 100.0 / sent, 2)
    avg_latency = round(sum(latencies) / received, 2) if received else None
    min_latency = round(min(latencies), 2) if received else None
    max_latency = round(max(latencies), 2) if received else None
    if len(latencies) >= 2:
        diffs = [abs(latencies[index] - latencies[index - 1]) for index in range(1, len(latencies))]
        jitter = round(sum(diffs) / len(diffs), 2)
    elif len(latencies) == 1:
        jitter = 0.0
    else:
        jitter = None

    return {
        "success": received > 0,
        "avg_latency_ms": avg_latency,
        "min_latency_ms": min_latency,
        "max_latency_ms": max_latency,
        "jitter_ms": jitter,
        "packet_loss_percent": packet_loss,
        "availability_percent": availability,
        "received": received,
        "sent": sent,
        "error": None if received > 0 else (errors[-1] if errors else "目标无响应"),
        "probe_source": "server_icmp",
        "probe_engine": "ping3",
    }


def apply_quality_loss_window(
    target_id: Any,
    result: Dict[str, Any],
    window_seconds: int = QUALITY_LOSS_WINDOW_SECONDS,
) -> Dict[str, Any]:
    """Convert single-probe loss into a rolling time-window loss rate.

    A 1-packet probe naturally produces either 0% or 100% loss. For the UI and
    alert snapshot we show packet loss over a recent time window instead:
    (sum(sent) - sum(received)) / sum(sent).
    """
    smoothed = dict(result or {})
    try:
        sent = int(float(smoothed.get("sent") or 0))
        received = int(float(smoothed.get("received") or 0))
    except (TypeError, ValueError):
        return smoothed
    if not target_id or sent <= 0:
        return smoothed

    now_ts = time.time()
    sample = {
        "ts": now_ts,
        "sent": sent,
        "received": max(0, min(received, sent)),
    }
    key = f"quality_probe:loss_window:{target_id}"
    raw_sample = json.dumps(sample, ensure_ascii=False)

    try:
        redis_client.lpush(key, raw_sample)
        redis_client.ltrim(key, 0, QUALITY_LOSS_WINDOW_MAX_SAMPLES - 1)
        redis_client.expire(key, QUALITY_LOSS_WINDOW_TTL_SECONDS)
        raw_items = redis_client.lrange(key, 0, QUALITY_LOSS_WINDOW_MAX_SAMPLES - 1) or []
    except Exception:
        raw_items = [raw_sample]

    cutoff = now_ts - max(30, int(window_seconds or QUALITY_LOSS_WINDOW_SECONDS))
    total_sent = 0
    total_received = 0
    for raw in raw_items:
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            item = json.loads(raw)
            item_ts = float(item.get("ts") or 0)
            item_sent = int(float(item.get("sent") or 0))
            item_received = int(float(item.get("received") or 0))
        except Exception:
            continue
        if item_ts < cutoff or item_sent <= 0:
            continue
        total_sent += item_sent
        total_received += max(0, min(item_received, item_sent))

    if total_sent > 0:
        total_received = max(0, min(total_received, total_sent))
        current_loss = smoothed.get("packet_loss_percent")
        current_availability = smoothed.get("availability_percent")
        smoothed["packet_loss_percent"] = round((total_sent - total_received) * 100.0 / total_sent, 2)
        smoothed["availability_percent"] = round(total_received * 100.0 / total_sent, 2)
        smoothed["current_packet_loss_percent"] = current_loss
        smoothed["current_availability_percent"] = current_availability
        smoothed["loss_window_seconds"] = max(30, int(window_seconds or QUALITY_LOSS_WINDOW_SECONDS))
        smoothed["loss_window_sent"] = total_sent
        smoothed["loss_window_received"] = total_received

    return smoothed


def write_quality_probe_result(target: Any, result: Dict[str, Any]) -> None:
    """Write quality probe result to InfluxDB."""
    try:
        influx_client.write_point(
            "quality_probe",
            tags={
                "target_id": str(target.id),
                "target": target.target,
                "name": target.name,
                "datacenter": target.datacenter_ref.name if getattr(target, "datacenter_ref", None) else "",
                "operator": target.operator_name or "",
                "probe_source": getattr(target, "probe_source", None) or "server_icmp",
                "device_id": str(getattr(target, "device_id", None) or ""),
            },
            fields={
                "success": bool(result.get("success")),
                "avg_latency_ms": result.get("avg_latency_ms"),
                "min_latency_ms": result.get("min_latency_ms"),
                "max_latency_ms": result.get("max_latency_ms"),
                "jitter_ms": result.get("jitter_ms"),
                "jitter_sd_ms": result.get("jitter_sd_ms"),
                "jitter_ds_ms": result.get("jitter_ds_ms"),
                "packet_loss_percent": result.get("current_packet_loss_percent", result.get("packet_loss_percent")),
                "availability_percent": result.get("current_availability_percent", result.get("availability_percent")),
                "rolling_packet_loss_percent": result.get("packet_loss_percent"),
                "rolling_availability_percent": result.get("availability_percent"),
                "received": result.get("received"),
                "sent": result.get("sent"),
            },
            timestamp=datetime.now(timezone.utc),
            sync=False,
        )
    except Exception as e:
        logger.warning("写入质量探测结果失败", target_id=getattr(target, "id", None), error=str(e))
