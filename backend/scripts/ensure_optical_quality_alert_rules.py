"""Create/update vendor-scoped optical quality rules.

Run inside the API container:
    python /app/scripts/ensure_optical_quality_alert_rules.py
"""
from __future__ import annotations

from copy import deepcopy

from app.database import SessionLocal
from app.models.alert import AlertRule


VENDORS = ("H3C", "Ruijie", "Asteros")


def _vendor_channels(db, vendor: str):
    rules = db.query(AlertRule).filter(AlertRule.metric_type == "optical_rx_power").all()
    for rule in rules:
        config = rule.extra_config if isinstance(rule.extra_config, dict) else {}
        values = config.get("applicable_vendors") or config.get("vendors") or []
        if isinstance(values, str):
            values = [values]
        if any(vendor.lower() in str(value).lower() for value in values):
            return deepcopy(rule.notification_channels or [])
    return []


def _upsert(db, definition: dict) -> tuple[AlertRule, bool]:
    rule = db.query(AlertRule).filter(AlertRule.name == definition["name"]).first()
    created = rule is None
    if rule is None:
        rule = AlertRule(name=definition["name"])
        db.add(rule)
    for field, value in definition.items():
        setattr(rule, field, value)
    return rule, created


def main() -> None:
    db = SessionLocal()
    created = 0
    updated = 0
    try:
        # Existing RX/TX rules now use dynamic thresholds, UP-only evaluation and
        # three independent samples. Their static threshold remains the final fallback.
        optical_rules = db.query(AlertRule).filter(
            AlertRule.metric_type.in_(["optical_rx_power", "optical_tx_power"])
        ).all()
        for rule in optical_rules:
            config = deepcopy(rule.extra_config or {})
            config.update({
                "dynamic_optical_thresholds": True,
                "require_interface_up": True,
                "required_samples": 3,
                "generated_by": "ensure_optical_quality_alert_rules",
            })
            rule.extra_config = config
            rule.duration = max(int(rule.duration or 0), 120)
            updated += 1

        for vendor in VENDORS:
            base = {
                "rule_type": "threshold",
                "condition": ">",
                "duration": 600,
                "severity": "P1",
                "notification_channels": _vendor_channels(db, vendor),
                "suppress_duration": 1800,
                "enabled": 1,
                "device_ids": [],
            }
            common_config = {
                "applicable_vendors": [vendor],
                "require_interface_up": True,
                "required_samples": 3,
                "generated_by": "ensure_optical_quality_alert_rules",
            }
            definitions = [
                {
                    **base,
                    "name": f"【{vendor}】光模块Lane收光功率差异常",
                    "description": "接口为UP时，连续3个独立样本的Lane最大与最小收光功率差超过阈值。",
                    "metric_type": "optical_lane_power_delta",
                    "threshold": 3.0,
                    "severity": "P2",
                    "extra_config": deepcopy(common_config),
                },
                {
                    **base,
                    "name": f"【{vendor}】光模块24小时收光衰减",
                    "description": "接口为UP时，当前收光功率相对约24小时前下降超过阈值。",
                    "metric_type": "optical_rx_power_drop_24h",
                    "threshold": 1.5,
                    "severity": "P2",
                    "extra_config": deepcopy(common_config),
                },
            ]
            for definition in definitions:
                _, was_created = _upsert(db, definition)
                created += int(was_created)
                updated += int(not was_created)

        h3c_channels = _vendor_channels(db, "H3C")
        _, was_created = _upsert(db, {
            "name": "【H3C S9867】光功率下降并伴随FEC增长",
            "description": "仅用于RoCE Fabric：同一模块在线会话内，接口UP、近1小时收光下降且同周期FEC不可纠错计数增长时触发；可纠错计数仅作诊断信息。",
            "rule_type": "threshold",
            "metric_type": "optical_rx_fec_correlation",
            "condition": ">",
            "threshold": 0.0,
            "duration": 600,
            "severity": "P1",
            "notification_channels": h3c_channels,
            "suppress_duration": 1800,
            "enabled": 1,
            "device_ids": [],
            "extra_config": {
                "applicable_vendors": ["H3C"],
                "models": ["S9867"],
                "monitor_profiles": ["roce_fabric"],
                "required_features": ["roce"],
                "require_interface_up": True,
                "required_samples": 3,
                "rx_drop_db": 1.0,
                "generated_by": "ensure_optical_quality_alert_rules",
            },
        })
        created += int(was_created)
        updated += int(not was_created)
        db.commit()
        print({"created": created, "updated": updated})
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()


if __name__ == "__main__":
    main()
