"""Split legacy all-vendor alert rules into vendor-scoped rules.

This script is intentionally idempotent. It keeps the original rule for the
first inferred vendor, creates missing copies for the remaining vendors, and
moves existing alert history to the rule that matches each device vendor.
"""
from __future__ import annotations

import copy
import re
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Optional

from app.database import SessionLocal
from app.models import AlertHistory, AlertRule, Device


CANONICAL_VENDORS = ["H3C", "Ruijie", "Asteros", "Hillstone"]

HILLSTONE_ONLY_METRICS = {
    "snmp_session_usage",
    "snmp_session_queue_full_drop_delta",
    "snmp_ha_status",
    "snmp_pak_buffer_usage",
    "snmp_ipsec_tunnel_status",
    "snmp_snat_resource_usage",
    "snmp_dnat_server_status",
    "snmp_slb_virtual_server_status",
}


def normalize_vendor_text(value: Optional[str]) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    if any(marker in text for marker in ["ruijie", "锐捷", "rgos"]):
        return "ruijie 锐捷 rgos"
    if any(marker in text for marker in ["h3c", "华三", "新华三", "comware"]):
        return "h3c 华三 新华三 comware"
    if any(marker in text for marker in ["hillstone", "山石"]):
        return "hillstone 山石"
    if any(marker in text for marker in ["aster", "asternos", "asterfusion", "星融元"]):
        return "aster asternos asterfusion 星融元"
    return text


def normalize_vendor_list(values: Any) -> List[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",")]
    if not isinstance(values, list):
        return []
    return [str(item).strip() for item in values if str(item or "").strip()]


def vendor_matches(raw_vendor: Optional[str], allowed_vendor: str) -> bool:
    normalized_vendor = normalize_vendor_text(raw_vendor)
    normalized_allowed = normalize_vendor_text(allowed_vendor)
    if not normalized_vendor or not normalized_allowed:
        return False
    return (
        normalized_allowed.split()[0] in normalized_vendor
        or allowed_vendor.strip().lower() in normalized_vendor
    )


def rule_applicable_vendors(rule: AlertRule) -> List[str]:
    extra_config = rule.extra_config or {}
    return normalize_vendor_list(extra_config.get("applicable_vendors") or extra_config.get("vendors"))


def strip_vendor_prefix(name: str) -> str:
    return re.sub(r"^【[^】]+】", "", name or "").strip()


def vendor_rule_name(base_name: str, vendor: str) -> str:
    base = strip_vendor_prefix(base_name)
    return f"【{vendor}】{base}"


def scoped_extra_config(extra_config: Optional[Dict[str, Any]], vendor: str) -> Dict[str, Any]:
    config = copy.deepcopy(extra_config or {})
    config["applicable_vendors"] = [vendor]
    config.pop("vendors", None)
    return config


def infer_vendors(rule: AlertRule) -> List[str]:
    name = (rule.name or "").lower()
    metric_type = (rule.metric_type or "").strip()
    if metric_type in HILLSTONE_ONLY_METRICS or metric_type == "snmp_trap" or "山石" in (rule.name or ""):
        return ["Hillstone"]
    if metric_type in {"exporter_metric", "exporter_reachability"}:
        return ["Asteros"]
    if metric_type == "telemetry_reachability":
        return ["H3C"]
    if any(marker in name for marker in ["asteros", "asternos", "星融元"]):
        return ["Asteros"]
    if any(marker in name for marker in ["hillstone", "山石"]):
        return ["Hillstone"]
    if any(marker in name for marker in ["ruijie", "锐捷", "rgos"]):
        return ["Ruijie"]
    if any(marker in name for marker in ["h3c", "华三", "新华三"]):
        return ["H3C"]
    return CANONICAL_VENDORS


def clone_rule_for_vendor(source: AlertRule, base_name: str, vendor: str) -> AlertRule:
    return AlertRule(
        name=vendor_rule_name(base_name, vendor),
        description=source.description,
        rule_type=source.rule_type,
        metric_type=source.metric_type,
        condition=source.condition,
        threshold=source.threshold,
        duration=source.duration,
        change_rate_threshold=source.change_rate_threshold,
        change_rate_window=source.change_rate_window,
        severity=source.severity,
        notification_channels=copy.deepcopy(source.notification_channels or []),
        suppress_duration=source.suppress_duration,
        enabled=source.enabled,
        device_group_id=source.device_group_id,
        device_ids=copy.deepcopy(source.device_ids or []),
        extra_config=scoped_extra_config(source.extra_config, vendor),
    )


def find_existing_vendor_rule(db, source: AlertRule, base_name: str, vendor: str) -> Optional[AlertRule]:
    return (
        db.query(AlertRule)
        .filter(AlertRule.id != source.id)
        .filter(AlertRule.name == vendor_rule_name(base_name, vendor))
        .filter(AlertRule.metric_type == source.metric_type)
        .first()
    )


def first_matching_vendor(device_vendor: Optional[str], vendors: Iterable[str]) -> Optional[str]:
    for vendor in vendors:
        if vendor_matches(device_vendor, vendor):
            return vendor
    return None


def split_rules() -> Dict[str, int]:
    db = SessionLocal()
    stats = {
        "checked": 0,
        "updated_original": 0,
        "created": 0,
        "history_moved": 0,
        "already_scoped": 0,
    }
    try:
        device_vendors = {
            device.id: device.vendor
            for device in db.query(Device.id, Device.vendor).all()
        }
        rules = db.query(AlertRule).order_by(AlertRule.id.asc()).all()
        now = datetime.now(timezone.utc)
        for rule in rules:
            stats["checked"] += 1
            if rule_applicable_vendors(rule):
                stats["already_scoped"] += 1
                continue

            base_name = strip_vendor_prefix(rule.name)
            vendors = infer_vendors(rule)
            if not vendors:
                continue

            vendor_to_rule_id: Dict[str, int] = {}
            first_vendor = vendors[0]
            rule.name = vendor_rule_name(base_name, first_vendor)
            rule.extra_config = scoped_extra_config(rule.extra_config, first_vendor)
            rule.updated_at = now
            vendor_to_rule_id[first_vendor] = rule.id
            stats["updated_original"] += 1

            for vendor in vendors[1:]:
                existing = find_existing_vendor_rule(db, rule, base_name, vendor)
                if existing:
                    if not rule_applicable_vendors(existing):
                        existing.extra_config = scoped_extra_config(existing.extra_config, vendor)
                        existing.updated_at = now
                    vendor_to_rule_id[vendor] = existing.id
                    continue
                clone = clone_rule_for_vendor(rule, base_name, vendor)
                db.add(clone)
                db.flush()
                vendor_to_rule_id[vendor] = clone.id
                stats["created"] += 1

            histories = db.query(AlertHistory).filter(AlertHistory.rule_id == rule.id).all()
            for history in histories:
                matched_vendor = first_matching_vendor(device_vendors.get(history.device_id), vendor_to_rule_id.keys())
                if not matched_vendor:
                    continue
                new_rule_id = vendor_to_rule_id.get(matched_vendor)
                if new_rule_id and new_rule_id != history.rule_id:
                    history.rule_id = new_rule_id
                    stats["history_moved"] += 1

        db.commit()
        return stats
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    print(split_rules())
