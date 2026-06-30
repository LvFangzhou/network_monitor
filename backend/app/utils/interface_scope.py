"""Helpers for per-device interface monitoring scope.

The scope is intentionally stored in ``devices.custom_fields`` to keep the first
iteration lightweight and compatible with existing APIs/imports.
"""
from __future__ import annotations

import re
from typing import Any, Dict, Iterable, Optional, Set


SEPARATORS_RE = re.compile(r"[\s,，;；]+")


def _normalize_text(value: Any) -> str:
    return re.sub(r"\s+", "", str(value or "")).strip().lower()


def _interface_aliases(interface_name: Optional[str], interface_index: Optional[Any] = None) -> Set[str]:
    aliases: Set[str] = set()
    raw_name = _normalize_text(interface_name)
    if raw_name:
        # Common H3C name variants.
        name_variants = {
            raw_name,
            raw_name.replace("fourhundredgige", "400g"),
            raw_name.replace("hundredgige", "100g"),
            raw_name.replace("twenty-fivegige", "25g"),
            raw_name.replace("tengige", "10g"),
        }
        for name_variant in name_variants:
            aliases.add(name_variant)
            # FourHundredGigE1/0/127 -> 1/0/127, 400G1/0/127 -> 1/0/127
            slash_tail = re.search(r"(\d+(?:/\d+)+)$", name_variant)
            if slash_tail:
                aliases.add(slash_tail.group(1))
                tail_parts = slash_tail.group(1).split("/")
                if len(tail_parts) >= 2:
                    aliases.add("/".join(tail_parts[-2:]))
                aliases.add(tail_parts[-1])
    if interface_index is not None and str(interface_index).strip():
        aliases.add(_normalize_text(interface_index))
    return {item for item in aliases if item}


def _split_tokens(text: Optional[str]) -> Iterable[str]:
    for token in SEPARATORS_RE.split(str(text or "")):
        token = token.strip()
        if token:
            yield token


def _expand_range_token(token: str, max_expand: int = 2048) -> Set[str]:
    normalized = _normalize_text(token)
    if not normalized:
        return set()
    if "-" not in normalized:
        return {normalized}

    left, right = normalized.split("-", 1)
    if not left or not right:
        return {normalized}

    left_match = re.match(r"^(.*?)(\d+)$", left)
    right_match = re.match(r"^(.*?)(\d+)$", right)
    if not left_match or not right_match:
        return {normalized, left, right}

    left_prefix, left_num_text = left_match.groups()
    right_prefix, right_num_text = right_match.groups()
    if right_prefix and left_prefix != right_prefix:
        return {normalized, left, right}

    start = int(left_num_text)
    end = int(right_num_text)
    if start > end:
        start, end = end, start
    if end - start + 1 > max_expand:
        return {normalized, left, right}

    width = max(len(left_num_text), len(right_num_text)) if left_num_text.startswith("0") or right_num_text.startswith("0") else 0
    return {f"{left_prefix}{number:0{width}d}" if width > 1 else f"{left_prefix}{number}" for number in range(start, end + 1)}


def _patterns_to_aliases(text: Optional[str]) -> Set[str]:
    result: Set[str] = set()
    for token in _split_tokens(text):
        expanded = _expand_range_token(token)
        for item in expanded:
            result.add(item)
            slash_tail = re.search(r"(\d+(?:/\d+)+)$", item)
            if slash_tail:
                result.add(slash_tail.group(1))
                parts = slash_tail.group(1).split("/")
                if len(parts) >= 2:
                    result.add("/".join(parts[-2:]))
                result.add(parts[-1])
    return {item for item in result if item}


def get_interface_scope(device: Any) -> Dict[str, Any]:
    custom_fields = getattr(device, "custom_fields", None) or {}
    if not isinstance(custom_fields, dict):
        return {"mode": "all"}
    monitoring = custom_fields.get("monitoring") or {}
    if not isinstance(monitoring, dict):
        return {"mode": "all"}
    scope = monitoring.get("interface_scope") or {}
    if not isinstance(scope, dict):
        return {"mode": "all"}
    mode = str(scope.get("mode") or "all").strip().lower()
    if mode not in {"all", "include", "exclude"}:
        mode = "all"
    return {
        "mode": mode,
        "include": str(scope.get("include") or scope.get("include_patterns") or ""),
        "exclude": str(scope.get("exclude") or scope.get("exclude_patterns") or ""),
    }


def is_interface_monitored(device: Any, interface_name: Optional[str], interface_index: Optional[Any] = None) -> bool:
    scope = get_interface_scope(device)
    mode = scope.get("mode") or "all"
    if mode == "all":
        return True

    aliases = _interface_aliases(interface_name, interface_index)
    include_aliases = _patterns_to_aliases(scope.get("include"))
    exclude_aliases = _patterns_to_aliases(scope.get("exclude"))

    if mode == "include":
        return bool(aliases & include_aliases)
    if mode == "exclude":
        return not bool(aliases & exclude_aliases)
    return True


def alert_target_interface_is_monitored(device: Any, target: Dict[str, Any]) -> bool:
    return is_interface_monitored(
        device,
        target.get("target_name"),
        target.get("target_key"),
    )
