"""Host resource sampling shared by the dashboard API and the system worker."""
from __future__ import annotations

import json
import os
import platform
import socket
import time
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Tuple

import psutil

from app.utils import redis_client


HISTORY_KEY = "system:resources:history"
NETWORK_COUNTER_KEY = "system:resources:network:last"
HISTORY_RETENTION_SECONDS = 7 * 24 * 60 * 60
HOST_PROC = "/host/proc" if os.path.isdir("/host/proc") else "/proc"
HOST_SYS = "/host/sys" if os.path.isdir("/host/sys") else "/sys"
HOST_ROOT = "/host-root" if os.path.isdir("/host-root") else "/"


def _read_text(path: str, default: str = "") -> str:
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            return handle.read().strip()
    except OSError:
        return default


def _meminfo() -> Dict[str, int]:
    values: Dict[str, int] = {}
    for line in _read_text(f"{HOST_PROC}/meminfo").splitlines():
        if ":" not in line:
            continue
        key, raw = line.split(":", 1)
        parts = raw.strip().split()
        if not parts:
            continue
        try:
            values[key] = int(parts[0]) * 1024
        except ValueError:
            continue
    return values


def _cpu_times() -> Tuple[int, int]:
    first = next((line for line in _read_text(f"{HOST_PROC}/stat").splitlines() if line.startswith("cpu ")), "")
    values = []
    for raw in first.split()[1:]:
        try:
            values.append(int(raw))
        except ValueError:
            values.append(0)
    total = sum(values)
    idle = (values[3] if len(values) > 3 else 0) + (values[4] if len(values) > 4 else 0)
    return total, idle


def _network_counters() -> Dict[str, Dict[str, int]]:
    counters: Dict[str, Dict[str, int]] = {}
    sys_net_path = f"{HOST_SYS}/class/net"
    if os.path.isdir(sys_net_path):
        for name in os.listdir(sys_net_path):
            try:
                counters[name] = {
                    "rx_bytes": int(_read_text(f"{sys_net_path}/{name}/statistics/rx_bytes", "0")),
                    "tx_bytes": int(_read_text(f"{sys_net_path}/{name}/statistics/tx_bytes", "0")),
                }
            except ValueError:
                continue
        if counters:
            return counters
    for line in _read_text(f"{HOST_PROC}/net/dev").splitlines()[2:]:
        if ":" not in line:
            continue
        name, raw = line.split(":", 1)
        name = name.strip()
        fields = raw.split()
        if name == "lo" or len(fields) < 16:
            continue
        counters[name] = {"rx_bytes": int(fields[0]), "tx_bytes": int(fields[8])}
    return counters


def _network_is_visible(name: str) -> bool:
    # Hide container plumbing while retaining physical NICs, bonds, VLANs and tunnels.
    return name != "lo" and not name.startswith(("veth", "docker", "br-"))


def _network_rows(now: float) -> List[Dict[str, Any]]:
    current = _network_counters()
    previous: Dict[str, Any] = {}
    try:
        raw = redis_client.get(NETWORK_COUNTER_KEY)
        if raw:
            previous = json.loads(raw)
    except Exception:
        previous = {}

    elapsed = max(0.001, now - float(previous.get("timestamp") or now))
    previous_interfaces = previous.get("interfaces") or {}
    rows: List[Dict[str, Any]] = []
    for name, counters in sorted(current.items()):
        if not _network_is_visible(name):
            continue
        old = previous_interfaces.get(name) or {}
        rx_delta = max(0, counters["rx_bytes"] - int(old.get("rx_bytes") or counters["rx_bytes"]))
        tx_delta = max(0, counters["tx_bytes"] - int(old.get("tx_bytes") or counters["tx_bytes"]))
        operstate = _read_text(f"{HOST_SYS}/class/net/{name}/operstate", "unknown")
        speed_raw = _read_text(f"{HOST_SYS}/class/net/{name}/speed", "")
        try:
            speed_mbps = int(speed_raw)
        except ValueError:
            speed_mbps = None
        rows.append({
            "name": name,
            "operstate": operstate,
            "speed_mbps": speed_mbps,
            "rx_bytes": counters["rx_bytes"],
            "tx_bytes": counters["tx_bytes"],
            "rx_bps": round(rx_delta * 8 / elapsed, 2),
            "tx_bps": round(tx_delta * 8 / elapsed, 2),
        })

    redis_client.setex(
        NETWORK_COUNTER_KEY,
        300,
        json.dumps({"timestamp": now, "interfaces": current}, separators=(",", ":")),
    )
    return rows


def collect_host_resource_sample() -> Dict[str, Any]:
    now = time.time()
    cpu_total, cpu_idle = _cpu_times()
    previous_cpu: Dict[str, Any] = {}
    try:
        raw = redis_client.get("system:resources:cpu:last")
        if raw:
            previous_cpu = json.loads(raw)
    except Exception:
        previous_cpu = {}
    total_delta = max(0, cpu_total - int(previous_cpu.get("total") or cpu_total))
    idle_delta = max(0, cpu_idle - int(previous_cpu.get("idle") or cpu_idle))
    cpu_percent = 0.0 if total_delta <= 0 else max(0.0, min(100.0, (1 - idle_delta / total_delta) * 100))
    redis_client.setex("system:resources:cpu:last", 300, json.dumps({"total": cpu_total, "idle": cpu_idle}))

    mem = _meminfo()
    memory_total = int(mem.get("MemTotal", 0))
    memory_available = int(mem.get("MemAvailable", mem.get("MemFree", 0)))
    memory_used = max(0, memory_total - memory_available)
    memory_percent = memory_used * 100 / memory_total if memory_total else 0.0
    disk = psutil.disk_usage(HOST_ROOT)
    load_raw = _read_text(f"{HOST_PROC}/loadavg").split()
    load_avg = [float(value) for value in load_raw[:3]] if len(load_raw) >= 3 else []
    cpuinfo = _read_text(f"{HOST_PROC}/cpuinfo")
    logical_cores = max(1, sum(1 for line in cpuinfo.splitlines() if line.startswith("processor")))
    physical_pairs = set()
    physical_id = core_id = None
    for line in cpuinfo.splitlines() + [""]:
        if line.startswith("physical id"):
            physical_id = line.split(":", 1)[1].strip()
        elif line.startswith("core id"):
            core_id = line.split(":", 1)[1].strip()
        elif not line.strip() and (physical_id is not None or core_id is not None):
            physical_pairs.add((physical_id or "0", core_id or str(len(physical_pairs))))
            physical_id = core_id = None
    uptime_raw = _read_text(f"{HOST_PROC}/uptime", "0").split()
    hostname = _read_text("/host/etc/hostname", "") or socket.gethostname()

    return {
        "hostname": hostname,
        "platform": platform.platform(),
        "timestamp": datetime.fromtimestamp(now, timezone.utc).isoformat(),
        "timestamp_ms": int(now * 1000),
        "uptime_seconds": int(float(uptime_raw[0])) if uptime_raw else 0,
        "cpu": {
            "percent": round(cpu_percent, 2),
            "cores": logical_cores,
            "physical_cores": len(physical_pairs) or logical_cores,
            "load_avg": [round(value, 2) for value in load_avg],
        },
        "memory": {
            "total": memory_total,
            "used": memory_used,
            "available": memory_available,
            "percent": round(memory_percent, 2),
        },
        "disk": {
            "path": "/",
            "total": disk.total,
            "used": disk.used,
            "free": disk.free,
            "percent": round(disk.percent, 2),
        },
        "network": _network_rows(now),
    }


def store_host_resource_sample(sample: Dict[str, Any]) -> None:
    timestamp_ms = int(sample.get("timestamp_ms") or time.time() * 1000)
    redis_client.zadd(HISTORY_KEY, {json.dumps(sample, ensure_ascii=False, separators=(",", ":")): timestamp_ms})
    redis_client.zremrangebyscore(HISTORY_KEY, 0, timestamp_ms - HISTORY_RETENTION_SECONDS * 1000)


def load_host_resource_history(start_ms: int, max_points: int = 720) -> List[Dict[str, Any]]:
    raw_rows: Iterable[Any] = redis_client.zrangebyscore(HISTORY_KEY, start_ms, "+inf")
    rows: List[Dict[str, Any]] = []
    for raw in raw_rows:
        try:
            rows.append(json.loads(raw))
        except Exception:
            continue
    if len(rows) <= max_points:
        return rows
    step = max(1, (len(rows) + max_points - 1) // max_points)
    sampled = rows[::step]
    if rows and sampled[-1].get("timestamp_ms") != rows[-1].get("timestamp_ms"):
        sampled.append(rows[-1])
    return sampled
