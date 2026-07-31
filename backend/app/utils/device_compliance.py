"""设备上线合规评估。"""
from __future__ import annotations

import fnmatch
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from app.models import Device, DeviceComplianceSnapshot, DeviceModelProfile, VersionBaseline
from app.utils.redis_client import redis_client


TACACS_LOG_FILE = Path("/app/data/tacacs/logs/tacacs.log")
TACACS_LOG_PATTERN = re.compile(
    r"(\w+\s+\d+\s+\d+:\d+:\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+\w+\s+\S+\s+\S+.*?cmd="
)
DEFAULT_REQUIRED_CHECKS = ["model_profile", "version", "snmp", "syslog", "tacacs"]
CHECK_LABELS = {
    "model_profile": "型号能力模板",
    "version": "版本基线",
    "patch": "补丁基线",
    "snmp": "SNMP",
    "exporter": "Exporter",
    "syslog": "Syslog",
    "tacacs": "TACACS",
}


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _matches(value: Any, pattern: Any) -> bool:
    normalized_value = _normalize(value)
    normalized_pattern = _normalize(pattern)
    if not normalized_pattern:
        return True
    if "*" in normalized_pattern or "?" in normalized_pattern:
        return fnmatch.fnmatch(normalized_value, normalized_pattern)
    return normalized_value == normalized_pattern or normalized_pattern in normalized_value


def canonical_model_name(value: Any, vendor: Any = None) -> str:
    """去掉型号字段里重复携带的厂商前缀。"""
    text = str(value or "").strip()
    vendor_text = str(vendor or "").strip()
    prefixes = [vendor_text] if vendor_text else []
    prefixes.extend(["H3C", "Cisco", "Ruijie", "Hillstone", "AsterNOS", "Asteros", "Asterfusion"])
    changed = True
    while text and changed:
        changed = False
        for prefix in prefixes:
            lowered_text = text.lower()
            lowered_prefix = prefix.lower()
            if prefix and (lowered_text == lowered_prefix or lowered_text.startswith(f"{lowered_prefix} ")):
                text = text[len(prefix):].strip()
                changed = True
                break
    return text


def _model_matches(value: Any, pattern: Any, vendor: Any = None) -> bool:
    normalized_value = _normalize(canonical_model_name(value, vendor))
    normalized_pattern = _normalize(canonical_model_name(pattern, vendor))
    if not normalized_pattern:
        return True
    if "*" in normalized_pattern or "?" in normalized_pattern:
        return fnmatch.fnmatch(normalized_value, normalized_pattern)
    return normalized_value == normalized_pattern or normalized_pattern in normalized_value


def match_model_profile(device: Device, profiles: Iterable[DeviceModelProfile]) -> Optional[DeviceModelProfile]:
    candidates = [
        profile for profile in profiles
        if profile.is_active
        and _matches(device.vendor, profile.vendor)
        and _model_matches(device.model, profile.model_pattern, device.vendor)
    ]
    candidates.sort(key=lambda item: (
        item.priority,
        0 if _normalize(device.model) == _normalize(item.model_pattern) else 1,
        -len(canonical_model_name(item.model_pattern, item.vendor)),
        item.id,
    ))
    return candidates[0] if candidates else None


def match_version_baseline(
    device: Device,
    profile: Optional[DeviceModelProfile],
    baselines: Iterable[VersionBaseline],
) -> Optional[VersionBaseline]:
    candidates = []
    for baseline in baselines:
        if not baseline.is_active:
            continue
        if baseline.model_profile_id and (not profile or baseline.model_profile_id != profile.id):
            # 历史CMDB里同一型号可能同时存在“S6805-54HF”和
            # “H3C S6805-54HF”两种写法。基线同时填写了厂商/型号范围时，
            # 允许按显式范围回退匹配，避免仅因模板ID不同而漏判。
            if not (baseline.vendor or baseline.model_pattern):
                continue
        if baseline.vendor and not _matches(device.vendor, baseline.vendor):
            continue
        if baseline.model_pattern and not _model_matches(device.model, baseline.model_pattern, device.vendor):
            continue
        if baseline.device_role and not _matches(device.device_role, baseline.device_role):
            continue
        candidates.append(baseline)
    candidates.sort(key=lambda item: (
        item.priority,
        0 if item.model_profile_id else 1,
        -len(item.model_pattern or ""),
        item.id,
    ))
    return candidates[0] if candidates else None


def load_overview(device_id: int) -> Dict[str, Any]:
    try:
        raw = redis_client.get(f"monitor:cache:overview:{device_id}")
        return json.loads(raw) if raw else {}
    except Exception:
        return {}


def load_tacacs_device_ips() -> set[str]:
    if not TACACS_LOG_FILE.exists():
        return set()
    try:
        text = TACACS_LOG_FILE.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return set()
    # 只读取TACACS accounting格式中的NAS地址，避免把运维客户端地址
    # 误认为设备已经接入TACACS。
    return {match.group(2) for match in TACACS_LOG_PATTERN.finditer(text)}


def _extract_version(system_info: Dict[str, Any]) -> Optional[str]:
    direct = str(system_info.get("software_version") or "").strip()
    if direct:
        return direct
    sys_descr = str(system_info.get("sys_descr") or "").strip()
    patterns = [
        r"\bVersion\s+([^,\s;]+)",
        r"\bSoftware\s+(?:Version\s+)?([^,\s;]+)",
        r"\bRelease\s+([^,\s;]+)",
    ]
    for pattern in patterns:
        match = re.search(pattern, sys_descr, re.IGNORECASE)
        if match:
            return match.group(1).strip()
    return None


def _version_numbers(value: str) -> tuple[int, ...]:
    return tuple(int(item) for item in re.findall(r"\d+", value or ""))


def _version_matches(version: str, patterns: Iterable[str]) -> bool:
    normalized = _normalize(version)
    for pattern in patterns:
        candidate = _normalize(pattern)
        if fnmatch.fnmatch(normalized, candidate) or candidate in normalized:
            return True
    return False


def _extract_h3c_version(value: Any) -> Dict[str, Optional[str]]:
    """将H3C回显拆为Comware平台版本和Release设备软件版本。"""
    text = str(value or "").strip()
    platform_match = re.search(
        r"(?:Software\s+)?Version\s+([0-9]+(?:\.[0-9A-Za-z]+)+)",
        text,
        re.IGNORECASE,
    )
    if not platform_match:
        platform_match = re.search(r"^\s*([0-9]+(?:\.[0-9A-Za-z]+)+)\b", text)
    release_match = re.search(r"\bRelease\s+([0-9A-Za-z._-]+)", text, re.IGNORECASE)
    return {
        "platform_version": platform_match.group(1).strip() if platform_match else None,
        "software_release": release_match.group(1).strip() if release_match else None,
    }


def _is_h3c(device: Device, baseline: Optional[VersionBaseline]) -> bool:
    return "h3c" in _normalize(device.vendor) or (baseline is not None and "h3c" in _normalize(baseline.vendor))


def _check(key: str, status: str, message: str, evidence: Any = None, required: bool = True) -> Dict[str, Any]:
    return {
        "key": key,
        "label": CHECK_LABELS[key],
        "status": status,
        "message": message,
        "evidence": evidence,
        "required": required,
    }


def evaluate_device(
    device: Device,
    profiles: Iterable[DeviceModelProfile],
    baselines: Iterable[VersionBaseline],
    latest_syslog_at: Optional[datetime],
    tacacs_device_ips: set[str],
) -> Dict[str, Any]:
    profile = match_model_profile(device, profiles)
    baseline = match_version_baseline(device, profile, baselines)
    overview = load_overview(device.id)
    system_info = overview.get("system_info") if isinstance(overview.get("system_info"), dict) else {}
    observed_model = str(system_info.get("snmp_model") or device.model or "").strip() or None
    observed_vendor = str(device.vendor or "").strip() or None
    observed_version = _extract_version(system_info)
    custom_fields = device.custom_fields if isinstance(device.custom_fields, dict) else {}
    patch_evidence_available = (
        "software_patches" in system_info
        or "software_patches" in custom_fields
        or "patches" in custom_fields
    )
    observed_patches = system_info.get("software_patches")
    if observed_patches is None:
        observed_patches = custom_fields.get("software_patches")
    if observed_patches is None:
        observed_patches = custom_fields.get("patches")
    if observed_patches is None:
        observed_patches = []
    if isinstance(observed_patches, str):
        observed_patches = [part.strip() for part in re.split(r"[,;\n]", observed_patches) if part.strip()]

    capabilities = profile.capabilities if profile and isinstance(profile.capabilities, dict) else {}
    asternos = any(marker in _normalize(device.vendor) for marker in ("asternos", "asterfusion", "asteros", "星融元"))
    inferred_capabilities = {
        "snmp": not asternos,
        "exporter": asternos,
        "syslog": True,
        "tacacs": not asternos,
    }
    effective_capabilities = {**inferred_capabilities, **capabilities}
    required_checks = list(profile.required_checks or DEFAULT_REQUIRED_CHECKS) if profile else DEFAULT_REQUIRED_CHECKS.copy()
    if baseline and baseline.required_patches and "patch" not in required_checks:
        required_checks.append("patch")

    checks = []
    checks.append(_check(
        "model_profile",
        "passed" if profile else "pending",
        f"匹配模板：{profile.name}" if profile else "尚未匹配型号能力模板",
        {"profile_id": profile.id, "network_type": profile.network_type} if profile else None,
        "model_profile" in required_checks,
    ))

    if "version" not in required_checks:
        checks.append(_check("version", "skipped", "该型号未要求版本检查", required=False))
    elif not baseline:
        checks.append(_check("version", "pending", "尚未配置适用的版本基线", observed_version, True))
    elif not observed_version:
        checks.append(_check("version", "pending", "尚未采集到软件版本", None, True))
    else:
        use_h3c_fields = _is_h3c(device, baseline) and bool(
            baseline.platform_version or baseline.allowed_releases
        )
        if use_h3c_fields:
            h3c_version = _extract_h3c_version(observed_version)
            observed_platform = h3c_version["platform_version"]
            observed_release = h3c_version["software_release"]
            platform_ok = (
                not baseline.platform_version
                or _version_matches(observed_platform or "", [baseline.platform_version])
            )
            release_ok = (
                not baseline.allowed_releases
                or _version_matches(observed_release or "", baseline.allowed_releases or [])
            )
            forbidden = _version_matches(observed_release or observed_version, baseline.forbidden_versions or [])
            version_ok = bool(observed_platform and observed_release and platform_ok and release_ok and not forbidden)
            evidence = {
                "current": observed_version,
                "current_comware_platform": observed_platform,
                "current_software_release": observed_release,
                "baseline": baseline.name,
                "required_comware_platform": baseline.platform_version,
                "allowed_software_releases": baseline.allowed_releases or [],
                "forbidden_software_releases": baseline.forbidden_versions or [],
            }
            message = "Comware平台和Release软件版本符合基线" if version_ok else "Comware平台或Release软件版本不符合基线"
        else:
            forbidden = _version_matches(observed_version, baseline.forbidden_versions or [])
            allowed = not baseline.allowed_versions or _version_matches(observed_version, baseline.allowed_versions or [])
            minimum_ok = True
            if baseline.minimum_version:
                current_numbers = _version_numbers(observed_version)
                minimum_numbers = _version_numbers(baseline.minimum_version)
                minimum_ok = bool(current_numbers and minimum_numbers and current_numbers >= minimum_numbers)
            version_ok = allowed and minimum_ok and not forbidden
            evidence = {
                "current": observed_version,
                "baseline": baseline.name,
                "allowed_versions": baseline.allowed_versions or [],
                "minimum_version": baseline.minimum_version,
                "forbidden_versions": baseline.forbidden_versions or [],
            }
            message = "软件版本符合基线" if version_ok else "软件版本不符合基线"
        checks.append(_check(
            "version",
            "passed" if version_ok else "failed",
            message,
            evidence,
            True,
        ))

    required_patches = list(baseline.required_patches or []) if baseline else []
    if "patch" not in required_checks and not required_patches:
        checks.append(_check("patch", "skipped", "当前基线未要求补丁检查", required=False))
    elif not baseline:
        checks.append(_check("patch", "pending", "尚未配置补丁基线", required=True))
    elif not required_patches:
        checks.append(_check("patch", "passed", "当前基线没有必需补丁", required=True))
    elif not patch_evidence_available:
        checks.append(_check(
            "patch",
            "pending",
            "尚未采集补丁列表，不能判定补丁缺失",
            {"required": required_patches, "observed": None},
            True,
        ))
    else:
        observed_normalized = {_normalize(item) for item in observed_patches}
        missing = [item for item in required_patches if _normalize(item) not in observed_normalized]
        checks.append(_check(
            "patch",
            "failed" if missing else "passed",
            f"缺少补丁：{', '.join(missing)}" if missing else "必需补丁完整",
            {
                "required": required_patches,
                "observed": observed_patches,
                "packages": system_info.get("software_patch_packages") or [],
                "missing": missing,
            },
            True,
        ))

    if asternos:
        exporter_ok = (
            device.monitor_source == "asternos_exporter"
            and bool(overview.get("collected_at"))
            and bool(system_info.get("sys_name") or system_info.get("sys_descr"))
        )
        checks.append(_check(
            "exporter",
            "passed" if exporter_ok else "pending",
            "已通过Exporter获取设备系统信息" if exporter_ok else "尚未获得有效Exporter系统信息",
            {
                "collected_at": overview.get("collected_at"),
                "sys_name": system_info.get("sys_name"),
                "model": system_info.get("snmp_model"),
                "software_version": system_info.get("software_version"),
            } if overview else None,
            "exporter" in required_checks or "snmp" in required_checks,
        ))
    elif not effective_capabilities.get("snmp", True):
        checks.append(_check("snmp", "skipped", "型号能力模板声明不使用SNMP", required=False))
    else:
        sources = overview.get("data_sources") if isinstance(overview.get("data_sources"), dict) else {}
        system_sources = sources.get("system_info") if isinstance(sources.get("system_info"), dict) else {}
        snmp_evidence = any(str(value).lower() == "snmp" for value in system_sources.values())
        if not snmp_evidence and device.monitor_source == "snmp":
            snmp_evidence = bool(system_info.get("sys_name") or system_info.get("sys_descr"))
        checks.append(_check(
            "snmp",
            "passed" if snmp_evidence else "pending",
            "已读取SNMP系统信息" if snmp_evidence else "尚未获得有效SNMP系统信息",
            {"collected_at": overview.get("collected_at"), "sys_name": system_info.get("sys_name")},
            "snmp" in required_checks,
        ))

    if not effective_capabilities.get("syslog", True):
        checks.append(_check("syslog", "skipped", "型号能力模板声明不使用Syslog", required=False))
    else:
        checks.append(_check(
            "syslog",
            "passed" if latest_syslog_at else "pending",
            "已收到设备Syslog" if latest_syslog_at else "尚未收到设备Syslog",
            latest_syslog_at.isoformat() if latest_syslog_at else None,
            "syslog" in required_checks,
        ))

    if not effective_capabilities.get("tacacs", True):
        checks.append(_check("tacacs", "skipped", "型号能力模板声明不使用TACACS", required=False))
    else:
        tacacs_ok = device.ip_address in tacacs_device_ips
        checks.append(_check(
            "tacacs",
            "passed" if tacacs_ok else "pending",
            "已有该设备TACACS记录" if tacacs_ok else "尚未发现该设备TACACS记录",
            {"device_ip": device.ip_address} if tacacs_ok else None,
            "tacacs" in required_checks,
        ))

    required_items = [item for item in checks if item["required"] and item["status"] != "skipped"]
    blockers = [
        {"key": item["key"], "label": item["label"], "status": item["status"], "message": item["message"]}
        for item in required_items if item["status"] != "passed"
    ]
    passed = sum(1 for item in required_items if item["status"] == "passed")
    score = round(passed * 100 / len(required_items)) if required_items else 100
    if not device.is_monitored:
        overall_status = "not_monitored"
    elif any(item["status"] == "failed" for item in required_items):
        overall_status = "non_compliant"
    elif blockers:
        overall_status = "pending"
    else:
        overall_status = "compliant"

    return {
        "device_id": device.id,
        "model_profile_id": profile.id if profile else None,
        "version_baseline_id": baseline.id if baseline else None,
        "overall_status": overall_status,
        "score": score,
        "observed_vendor": observed_vendor,
        "observed_model": observed_model,
        "observed_version": observed_version,
        "observed_patches": observed_patches,
        "checks": checks,
        "blockers": blockers,
        "evaluated_at": datetime.now(timezone.utc),
        "profile": profile.to_dict() if profile else None,
        "baseline": baseline.to_dict() if baseline else None,
    }


def persist_snapshot(db, result: Dict[str, Any]) -> DeviceComplianceSnapshot:
    snapshot = db.query(DeviceComplianceSnapshot).filter(
        DeviceComplianceSnapshot.device_id == result["device_id"]
    ).first()
    if not snapshot:
        snapshot = DeviceComplianceSnapshot(device_id=result["device_id"])
        db.add(snapshot)
    for key in (
        "model_profile_id", "version_baseline_id", "overall_status", "score",
        "observed_vendor", "observed_model", "observed_version", "observed_patches",
        "checks", "blockers", "evaluated_at",
    ):
        setattr(snapshot, key, result[key])
    return snapshot
