"""回填设备监控模板，并修复历史告警规则的适用范围。

用法：
  python backend/scripts/backfill_monitoring_profiles.py          # 预览
  python backend/scripts/backfill_monitoring_profiles.py --apply  # 提交
"""
from __future__ import annotations

import argparse
from collections import Counter

from app.database import SessionLocal
from app.models import AlertRule, Device
from app.utils.monitor_profile import get_device_monitor_profile, infer_monitor_profile, normalize_monitoring_profile


ASTERNOS_ROCE_RULE_IDS = {38, 39, 40, 122, 123, 124}
ROCE_MARKERS = ("pfc", "ecn", "roce", "headroom")


def _is_legacy_cx_models(value) -> bool:
    if not isinstance(value, list) or not value:
        return False
    normalized = [str(item or "").strip().lower() for item in value]
    return bool(normalized) and all(item.startswith("cx") for item in normalized)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--apply", action="store_true", help="提交数据库变更")
    parser.add_argument("--reclassify", action="store_true", help="按当前现场映射重新推断全部设备模板")
    args = parser.parse_args()
    db = SessionLocal()
    changed_devices = 0
    changed_rules = 0
    disabled_rules = []
    profile_counts = Counter()
    try:
        for device in db.query(Device).order_by(Device.id.asc()).all():
            source_fields = dict(device.custom_fields or {})
            if args.reclassify:
                monitoring = dict(source_fields.get("monitoring") or {})
                monitoring["monitor_profile"] = infer_monitor_profile(device.vendor, device.model, device.device_role)
                source_fields["monitoring"] = monitoring
            normalized = normalize_monitoring_profile(
                source_fields,
                device.vendor,
                device.model,
                device.device_role,
            )
            if normalized != (device.custom_fields or {}):
                device.custom_fields = normalized
                changed_devices += 1
            profile_counts[get_device_monitor_profile(device)] += 1

        for rule in db.query(AlertRule).order_by(AlertRule.id.asc()).all():
            config = dict(rule.extra_config or {})
            changed = False
            if _is_legacy_cx_models(config.get("models")):
                config.pop("models", None)
                changed = True

            searchable = f"{rule.name or ''} {rule.metric_type or ''}".lower()
            if any(marker in searchable for marker in ROCE_MARKERS):
                features = config.get("required_features") or []
                if isinstance(features, str):
                    features = [item.strip() for item in features.split(",") if item.strip()]
                if "roce" not in features:
                    config["required_features"] = [*features, "roce"]
                    changed = True

            if rule.id in ASTERNOS_ROCE_RULE_IDS and bool(rule.enabled):
                rule.enabled = 0
                disabled_rules.append((rule.id, rule.name))
                changed = True

            if changed:
                rule.extra_config = config
                changed_rules += 1

        print("设备模板分布:", dict(profile_counts))
        print("待更新设备:", changed_devices)
        print("待更新规则:", changed_rules)
        print("待停用 AsterNOS RoCE 规则:", disabled_rules)
        if args.apply:
            db.commit()
            print("已提交数据库变更")
        else:
            db.rollback()
            print("仅预览，未修改数据库；加 --apply 执行")
    finally:
        db.close()


if __name__ == "__main__":
    main()
