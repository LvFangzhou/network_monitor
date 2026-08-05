"""Parse H3C ``display vlan <id> statistics`` output.

The command reports Layer-3 forwarding counters collected after ``statistics
enable`` is configured in VLAN view.  These counters are authoritative for
H3C VLAN interfaces whose standard IF-MIB octet counters remain zero.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Optional


VLAN_INTERFACE_RE = re.compile(r"^vlan-interface\s*(\d+)$", re.IGNORECASE)
_DIRECTION_RE = re.compile(
    r"^\s*(Inbound|Outbound)\s*:?\s+([\d,]+)\s+([\d,]+)\s+([\d,.]+)\s+([\d,.]+)\s*$",
    re.IGNORECASE,
)
_DIRECTION_COUNTER_RE = re.compile(
    r"^\s*(Inbound|Outbound)\s*:?\s+([\d,]+)\s+([\d,]+)\s*$",
    re.IGNORECASE,
)
_RATE_RE = re.compile(r"^\s*([\d,.]+)\s+([\d,.]+)\s*$")


def vlan_id_from_interface_name(value: Any) -> Optional[int]:
    """Return the VLAN ID represented by an H3C Vlan-interface name."""
    match = VLAN_INTERFACE_RE.match(str(value or "").strip())
    return int(match.group(1)) if match else None


def _number(value: str) -> float:
    return float(str(value).replace(",", ""))


def parse_h3c_vlan_statistics(output: str) -> Dict[str, float]:
    """Parse the aggregate ``Total`` rows from H3C VLAN statistics output.

    Expected row columns are ``Direction / Total packets / Total bytes /
    Rate(pps) / Rate(Bps)``.  Some releases omit a ``Total:`` section when the
    chassis has only one slot, so the final complete inbound/outbound pair is
    accepted as a compatibility fallback.
    """
    text = str(output or "").replace("\r", "")
    if not text.strip():
        raise ValueError("VLAN statistics output is empty")
    if re.search(r"statistics\s+(?:is\s+)?not\s+enabled", text, re.IGNORECASE):
        raise ValueError("VLAN statistics is not enabled")

    sections = re.split(r"(?im)^\s*Total\s*:\s*$", text)
    candidate_sections = [sections[-1]] if len(sections) > 1 else [text]
    parsed: Dict[str, Dict[str, float]] = {}
    for section in candidate_sections:
        pending_direction: Optional[str] = None
        for line in section.splitlines():
            match = _DIRECTION_RE.match(line)
            if match:
                direction = match.group(1).lower()
                parsed[direction] = {
                    "packets": _number(match.group(2)),
                    "bytes": _number(match.group(3)),
                    "pps": _number(match.group(4)),
                    "Bps": _number(match.group(5)),
                }
                pending_direction = None
                continue

            counter_match = _DIRECTION_COUNTER_RE.match(line)
            if counter_match:
                pending_direction = counter_match.group(1).lower()
                parsed[pending_direction] = {
                    "packets": _number(counter_match.group(2)),
                    "bytes": _number(counter_match.group(3)),
                }
                continue

            rate_match = _RATE_RE.match(line)
            if pending_direction and rate_match:
                parsed[pending_direction].update({
                    "pps": _number(rate_match.group(1)),
                    "Bps": _number(rate_match.group(2)),
                })
                pending_direction = None

    required_fields = {"packets", "bytes", "pps", "Bps"}
    if (
        "inbound" not in parsed
        or "outbound" not in parsed
        or not required_fields.issubset(parsed["inbound"])
        or not required_fields.issubset(parsed["outbound"])
    ):
        raise ValueError("VLAN statistics output has no complete inbound/outbound total rows")

    inbound = parsed["inbound"]
    outbound = parsed["outbound"]
    return {
        "in_packets": inbound["packets"],
        "out_packets": outbound["packets"],
        "in_octets": inbound["bytes"],
        "out_octets": outbound["bytes"],
        "in_pps": inbound["pps"],
        "out_pps": outbound["pps"],
        "in_bps": inbound["Bps"] * 8.0,
        "out_bps": outbound["Bps"] * 8.0,
    }
