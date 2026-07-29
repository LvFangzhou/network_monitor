"""Enable baseline Cisco Nexus SNMP monitoring support in existing data.

The collector code recognizes Cisco/Nexus OIDs after deployment. This script
fixes existing DB rows that were created before that recognition existed:

- infer vendor Cisco for Nexus/N9K/9364D devices without a vendor;
- ensure the Cisco vendor dictionary exists;
- create Cisco-specific baseline alert rules so SNMP-unreachable alarms are
  evaluated without being mixed into H3C/Ruijie/Hillstone/Asteros rules.
"""
from __future__ import annotations

import copy
from typing import Any, Dict, Iterable, List, Optional

from sqlalchemy import func, or_

from app.database import SessionLocal
from app.models import AlertRule, Device, DeviceVendor


CISCO_MARKERS = ["cisco", "nexus", "nx-os", "nxos", "n9k", "9364d"]
BASELINE_CISCO_RULES = [
    {
        "name": "【Cisco】Ping不可达",
        "description": "Cisco/Nexus 设备 Ping 连续不可达。",
        "metric_type": "device_reachability",
        "condition": "<",
        "threshold": 1,
        "duration": 60,
        "severity": "P0",
    },
    {
        "name": "【Cisco】SNMP不可达",
        "description": "Cisco/Nexus 设备 SNMP 连续不可达。",
        "metric_type": "snmp_reachability",
        "condition": "<",
        "threshold": 1,
        "duration": 60,
        "severity": "P0",
    },
    {
        "name": "【Cisco】CPU使用率超过 70%",
        "description": "Cisco/Nexus 设备 CPU 使用率持续超过 70%。",
        "metric_type": "snmp_cpu",
        "condition": ">",
        "threshold": 0.7,
        "duration": 180,
        "severity": "P1",
    },
    {
        "name": "【Cisco】内存使用率高",
        "description": "Cisco/Nexus 设备内存使用率持续偏高。",
        "metric_type": "snmp_memory",
        "condition": ">",
        "threshold": 0.85,
        "duration": 300,
        "severity": "P1",
    },
    {
        "name": "【Cisco】系统状态异常",
        "description": "Cisco/Nexus 设备硬件或系统状态异常。",
        "metric_type": "device_status",
        "condition": "<",
        "threshold": 1,
        "duration": 30,
        "severity": "P0",
    },
    {
        "name": "【Cisco】接口AdminUp但物理Down",
        "description": "Cisco/Nexus 物理接口管理开启但运行状态 Down。",
        "metric_type": "interface_admin_up_oper_down",
        "condition": ">=",
        "threshold": 1,
        "duration": 10,
        "severity": "P1",
    },
]
BASELINE_CISCO_METRICS = {str(rule["metric_type"]) for rule in BASELINE_CISCO_RULES}


def _normalize_vendor_list(values: Any) -> List[str]:
    if not values:
        return []
    if isinstance(values, str):
        values = [item.strip() for item in values.split(",") if item.strip()]
    if not isinstance(values, list):
        return []
    result: List[str] = []
    seen = set()
    for item in values:
        text = str(item or "").strip()
        if not text:
            continue
        key = text.lower()
        if key in seen:
            continue
        seen.add(key)
        result.append(text)
    return result


def _contains_any(text: str, markers: Iterable[str]) -> bool:
    lower = text.lower()
    return any(marker in lower for marker in markers)


def _ensure_cisco_vendor(db) -> None:
    vendor = db.query(DeviceVendor).filter(func.lower(DeviceVendor.name) == "cisco").first()
    if not vendor:
        db.add(DeviceVendor(name="Cisco", display_name="Cisco", is_active=True))
    elif not vendor.is_active:
        vendor.is_active = True


def _rule_vendor_scope(rule: AlertRule) -> List[str]:
    config = dict(rule.extra_config or {})
    return _normalize_vendor_list(config.get("applicable_vendors") or config.get("vendors"))


def _ensure_rule(db, template: Dict[str, Any], source_rule: Optional[AlertRule] = None) -> AlertRule:
    rule = db.query(AlertRule).filter(AlertRule.name == template["name"]).first()
    if not rule:
        rule = AlertRule(
            name=template["name"],
            description=template.get("description"),
            rule_type=template.get("rule_type", source_rule.rule_type if source_rule else "threshold"),
            metric_type=template["metric_type"],
            condition=template.get("condition", source_rule.condition if source_rule else ">"),
            threshold=float(template.get("threshold", source_rule.threshold if source_rule else 0)),
            duration=int(template.get("duration", source_rule.duration if source_rule else 0) or 0),
            severity=template.get("severity", source_rule.severity if source_rule else "P1"),
            suppress_duration=int(source_rule.suppress_duration if source_rule else 600),
            enabled=1,
            extra_config={
                "generated_by": "ensure_cisco_nexus_support",
                "mention_users": [],
                "applicable_vendors": ["Cisco"],
            },
        )
        db.add(rule)
        db.flush()
        return rule

    rule.description = template.get("description") or rule.description
    rule.rule_type = template.get("rule_type", rule.rule_type or (source_rule.rule_type if source_rule else "threshold"))
    rule.metric_type = template["metric_type"]
    rule.condition = template.get("condition", rule.condition)
    rule.threshold = float(template.get("threshold", rule.threshold))
    rule.duration = int(template.get("duration", rule.duration) or 0)
    rule.severity = template.get("severity", rule.severity)
    rule.enabled = 1
    config = dict(rule.extra_config or {})
    config["generated_by"] = config.get("generated_by") or "ensure_cisco_nexus_support"
    config.setdefault("mention_users", [])
    config["applicable_vendors"] = ["Cisco"]
    config.pop("vendors", None)
    rule.extra_config = config
    return rule


def _strip_vendor_prefix(name: str) -> str:
    text = str(name or "").strip()
    if text.startswith("【") and "】" in text:
        return text.split("】", 1)[1].strip()
    return text


def _ensure_cisco_copy_from_mixed_rule(db, source: AlertRule) -> Optional[AlertRule]:
    """Create a Cisco-only copy when an existing rule mixes Cisco with other vendors."""
    if str(source.name or "").startswith("【Cisco】"):
        return None
    cisco_name = f"【Cisco】{_strip_vendor_prefix(source.name)}"
    existing = (
        db.query(AlertRule)
        .filter(AlertRule.id != source.id)
        .filter(AlertRule.name == cisco_name)
        .filter(AlertRule.metric_type == source.metric_type)
        .first()
    )
    source_config = copy.deepcopy(source.extra_config or {})
    cisco_config = copy.deepcopy(source_config)
    cisco_config["applicable_vendors"] = ["Cisco"]
    cisco_config.pop("vendors", None)
    cisco_config["generated_by"] = cisco_config.get("generated_by") or "ensure_cisco_nexus_support"
    if existing:
        existing.extra_config = cisco_config
        return existing
    rule = AlertRule(
        name=cisco_name,
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
        extra_config=cisco_config,
    )
    db.add(rule)
    db.flush()
    return rule


def main() -> Dict[str, Any]:
    db = SessionLocal()
    try:
        _ensure_cisco_vendor(db)
        device_candidates = db.query(Device).filter(
            or_(
                Device.vendor.ilike("%Cisco%"),
                Device.model.ilike("%Nexus%"),
                Device.model.ilike("%9364%"),
                Device.name.ilike("%Nexus%"),
                Device.name.ilike("%Cisco%"),
                Device.hostname.ilike("%Nexus%"),
                Device.hostname.ilike("%Cisco%"),
            )
        ).all()
        fixed_devices = []
        for device in device_candidates:
            identity = " ".join([
                str(device.vendor or ""),
                str(device.model or ""),
                str(device.name or ""),
                str(device.hostname or ""),
            ])
            if _contains_any(identity, CISCO_MARKERS) and device.vendor != "Cisco":
                device.vendor = "Cisco"
                fixed_devices.append({"id": device.id, "name": device.name, "ip": device.ip_address})
            if device.vendor == "Cisco" and not device.monitor_source:
                device.monitor_source = "snmp"

        touched_rules = []
        removed_cross_scope = []
        split_cisco_rules = []
        for rule in db.query(AlertRule).all():
            config = dict(rule.extra_config or {})
            vendors = _normalize_vendor_list(config.get("applicable_vendors") or config.get("vendors"))
            non_cisco_vendors = [item for item in vendors if item.lower() != "cisco"]
            if vendors and len(non_cisco_vendors) != len(vendors) and non_cisco_vendors:
                if non_cisco_vendors:
                    cisco_rule = _ensure_cisco_copy_from_mixed_rule(db, rule)
                    if cisco_rule:
                        split_cisco_rules.append({"source_id": rule.id, "source_name": rule.name, "cisco_rule_id": cisco_rule.id, "cisco_rule_name": cisco_rule.name})
                config["applicable_vendors"] = non_cisco_vendors
                config.pop("vendors", None)
                rule.extra_config = config
                removed_cross_scope.append({"id": rule.id, "name": rule.name, "metric_type": rule.metric_type})

        for template in BASELINE_CISCO_RULES:
            source_rule = None
            for rule in db.query(AlertRule).filter(AlertRule.metric_type == template["metric_type"]).order_by(AlertRule.id).all():
                vendors = _rule_vendor_scope(rule)
                if vendors and any(item.lower() != "cisco" for item in vendors):
                    source_rule = rule
                    break
            rule = _ensure_rule(db, template, source_rule)
            config = dict(rule.extra_config or {})
            vendors = _normalize_vendor_list(config.get("applicable_vendors") or config.get("vendors"))
            if vendors != ["Cisco"]:
                config["applicable_vendors"] = ["Cisco"]
                config.pop("vendors", None)
                rule.extra_config = config
            touched_rules.append({"id": rule.id, "name": rule.name, "metric_type": rule.metric_type})

        db.commit()
        return {
            "success": True,
            "fixed_devices": fixed_devices,
            "updated_rules": touched_rules,
            "fixed_device_count": len(fixed_devices),
            "updated_rule_count": len(touched_rules),
            "removed_cross_scope_rules": removed_cross_scope,
            "removed_cross_scope_rule_count": len(removed_cross_scope),
            "split_cisco_rules": split_cisco_rules,
            "split_cisco_rule_count": len(split_cisco_rules),
        }
    except Exception as exc:
        db.rollback()
        return {"success": False, "error": str(exc)}
    finally:
        db.close()


if __name__ == "__main__":
    print(main())
