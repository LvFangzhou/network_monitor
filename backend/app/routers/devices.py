"""
设备管理路由
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import case, cast, func, or_
from sqlalchemy.dialects.postgresql import INET
from sqlalchemy.orm import Session, joinedload
from typing import Any, Dict, List, Optional
from datetime import datetime, timedelta
from pydantic import BaseModel
import asyncio
import re
import json
from pathlib import Path
import ipaddress

from app.database import get_db
from app.models import AlertHistory, ConfigBackupResult, Device, DeviceGroup, SyslogEvent, Tag, Datacenter, DeviceType, DeviceRole, DeviceVendor
from app.collectors.snmp_collector import SNMPCollector
from app.utils.interface_scope import alert_target_interface_is_monitored
from app.utils.redis_client import redis_client
from app.utils import influx_client
from app.schemas import (
    DeviceCreate, DeviceUpdate, DeviceResponse,
    DeviceGroupCreate, DeviceGroupUpdate, DeviceGroupResponse,
    DeviceStatusUpdate,
    DatacenterCreate, DatacenterUpdate, DatacenterResponse,
    DeviceTypeCreate, DeviceTypeUpdate, DeviceTypeResponse,
    DeviceRoleCreate, DeviceRoleUpdate, DeviceRoleResponse,
    DeviceVendorCreate, DeviceVendorUpdate, DeviceVendorResponse
)
from app.core import get_logger

logger = get_logger(__name__)
router = APIRouter()


ACTIVE_ALERT_STATUSES = ("firing", "acknowledged", "ignored", "snoozed")
DEVICE_OVERVIEW_SNAPSHOT_CACHE_PREFIX = "monitor:cache:overview_snapshot"
DEVICE_OVERVIEW_LAST_SUCCESS_CACHE_PREFIX = "monitor:cache:last_success_overview_snapshot"
DEVICE_OVERVIEW_REVISION_KEY = "monitor:cache:overview_revision"
TACACS_LOG_FILE = Path("/app/data/tacacs/logs/tacacs.log")
TACACS_LOG_PATTERN = re.compile(r"(\w+\s+\d+\s+\d+:\d+:\d+)\s+(\d+\.\d+\.\d+\.\d+)\s+(\w+)\s+(\S+)\s+(\S+).*?cmd=(.*)")
TACACS_MONTH_MAP = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _invalidate_device_overview_response_cache() -> None:
    try:
        keys = []
        for prefix in [DEVICE_OVERVIEW_SNAPSHOT_CACHE_PREFIX, DEVICE_OVERVIEW_LAST_SUCCESS_CACHE_PREFIX]:
            cursor = 0
            while True:
                cursor, matched = redis_client.scan(cursor=cursor, match=f"{prefix}:*", count=200)
                if matched:
                    keys.extend(matched)
                if cursor == 0:
                    break
        if keys:
            redis_client.delete(*keys)
    except Exception:
        logger.warning("清理设备总览缓存失败", exc_info=True)


def _bump_device_overview_revision() -> None:
    try:
        redis_client.incr(DEVICE_OVERVIEW_REVISION_KEY)
    except Exception:
        logger.warning("递增设备总览版本失败", exc_info=True)


def _monitor_cache_key(kind: str, device_id: int, suffix: str = "") -> str:
    return f"monitor:cache:{kind}:{device_id}{suffix}"


def _load_monitor_cache(kind: str, device_id: int, suffix: str = "") -> Optional[Dict[str, Any]]:
    raw = redis_client.get(_monitor_cache_key(kind, device_id, suffix))
    if not raw:
        return None
    try:
        return json.loads(raw)
    except Exception:
        return None


def _parse_time_filter(value: Optional[str]) -> Optional[datetime]:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except Exception:
        return None


def _format_tacacs_time(raw_time: str) -> Optional[str]:
    try:
        month_text, day, time_text = raw_time.split()
        hour, minute, second = [int(item) for item in time_text.split(":")]
        parsed = datetime(datetime.now().year, TACACS_MONTH_MAP[month_text], int(day), hour, minute, second)
        return (parsed + timedelta(hours=8)).strftime("%Y-%m-%d %H:%M:%S")
    except Exception:
        return None


def _extract_tacacs_command(raw_command: str) -> str:
    text = (raw_command or "").strip()
    if not text:
        return ""
    marker_match = re.search(r"\s+(cmd-arg|err_msg|start_time)=", text)
    command = text[:marker_match.start()].strip() if marker_match else text
    rest = text[marker_match.start():] if marker_match else ""
    arg_match = re.search(r"\bcmd-arg=(.*?)(?=\s+(?:err_msg|start_time)=|$)", rest)
    cmd_arg = arg_match.group(1).strip() if arg_match else ""
    return f"{command} {cmd_arg}".strip() if cmd_arg else command


def _normalize_interface_key(value: Any) -> str:
    text = str(value or "").strip().lower()
    if not text:
        return ""
    replacements = {
        "hundredgige": "hge",
        "hundred-gigabitethernet": "hge",
        "hundredgigabitethernet": "hge",
        "fourhundredgige": "400g",
        "fourhundredgigabitethernet": "400g",
        "fourhundred-gigabitethernet": "400g",
        "ten-gigabitethernet": "tengige",
        "tengigabitethernet": "tengige",
        "gigabitethernet": "ge",
        "m-gigabitethernet": "mge",
    }
    normalized = re.sub(r"\s+", "", text)
    for src, dst in replacements.items():
        normalized = normalized.replace(src, dst)
    return re.sub(r"[^a-z0-9/._-]", "", normalized)


def _latest_device_config_text(db: Session, device_id: int) -> str:
    result = (
        db.query(ConfigBackupResult.config_content)
        .filter(
            ConfigBackupResult.device_id == device_id,
            ConfigBackupResult.status == "success",
            ConfigBackupResult.config_content.isnot(None),
        )
        .order_by(ConfigBackupResult.finished_at.desc().nullslast(), ConfigBackupResult.id.desc())
        .first()
    )
    return str(result[0] or "") if result else ""


def _parse_interface_config_fields(config_text: str) -> Dict[str, Dict[str, str]]:
    """从最新配置备份里提取接口描述/IP，作为 SNMP/LLDP 字段缺失时的兜底。"""
    fields: Dict[str, Dict[str, str]] = {}
    current_name = ""
    for raw_line in (config_text or "").splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            continue
        match = re.match(r"^(?:interface|Interface)\s+(.+)$", stripped, re.IGNORECASE)
        if match:
            current_name = match.group(1).strip()
            if current_name:
                fields.setdefault(_normalize_interface_key(current_name), {"name": current_name})
            continue
        if not current_name:
            continue
        key = _normalize_interface_key(current_name)
        bucket = fields.setdefault(key, {"name": current_name})
        desc_match = re.match(r"^(?:description|port\s+description)\s+(.+)$", stripped, re.IGNORECASE)
        if desc_match and not bucket.get("description"):
            bucket["description"] = desc_match.group(1).strip()
            continue
        ip_match = re.match(r"^ip\s+address\s+(\d+\.\d+\.\d+\.\d+)(?:\s+(\d+\.\d+\.\d+\.\d+)|/(\d+))?", stripped, re.IGNORECASE)
        if ip_match and not bucket.get("ip_address"):
            ip_addr = ip_match.group(1)
            mask_or_prefix = ip_match.group(2) or (f"/{ip_match.group(3)}" if ip_match.group(3) else "")
            bucket["ip_address"] = _format_interface_ip(ip_addr, mask_or_prefix)
            continue
        mtu_match = re.match(r"^(?:mtu|Maximum\s+frame\s+length:)\s+(\d+)", stripped, re.IGNORECASE)
        if mtu_match and not bucket.get("mtu"):
            bucket["mtu"] = mtu_match.group(1)
    return fields


def _mask_to_prefix(mask_or_prefix: str) -> str:
    text = str(mask_or_prefix or "").strip()
    if not text:
        return ""
    if text.startswith("/"):
        return text
    if text.isdigit():
        return f"/{text}"
    try:
        return f"/{ipaddress.IPv4Network(f'0.0.0.0/{text}').prefixlen}"
    except Exception:
        return text


def _format_interface_ip(ip_addr: Any, mask_or_prefix: Any = "") -> str:
    ip_text = str(ip_addr or "").strip()
    if not ip_text:
        return ""
    if "," in ip_text:
        return ", ".join(filter(None, (_format_interface_ip(part.strip()) for part in ip_text.split(","))))
    if " " in ip_text and "/" not in ip_text:
        parts = ip_text.split()
        if len(parts) >= 2:
            return _format_interface_ip(parts[0], parts[1])
    if "/" in ip_text:
        return ip_text
    prefix = _mask_to_prefix(str(mask_or_prefix or ""))
    return f"{ip_text}{prefix}" if prefix.startswith("/") else ip_text


def _best_interface_ip(snapshot_ip: Any, config_ip: Any) -> str:
    snapshot_text = _format_interface_ip(snapshot_ip)
    config_text = _format_interface_ip(config_ip)
    if snapshot_text and "/" not in snapshot_text and config_text:
        return config_text
    return snapshot_text or config_text


def _is_default_interface_description(name: str, description: Any) -> bool:
    text = str(description or "").strip()
    if not text:
        return True
    normalized_text = re.sub(r"\s+", "", text).lower()
    normalized_name = re.sub(r"\s+", "", str(name or "")).lower()
    default_values = {
        normalized_name,
        f"{normalized_name}interface",
    }
    return normalized_text in default_values


def _infer_tacacs_operation(command: str, raw: str) -> str:
    text = f"{command} {raw}".lower()
    if "login" in text:
        return "登录"
    if "logout" in text:
        return "退出"
    if command.startswith(("display", "show", "dis ")):
        return "查询操作"
    if command.startswith(("save", "write", "configure", "system-view", "interface", "undo", "set ")):
        return "配置操作"
    return "审计类操作"


def _flux_escape(value: str) -> str:
    return str(value).replace("\\", "\\\\").replace('"', '\\"')


def _trigger_monitor_refresh_for_device(device: Device) -> None:
    if not device or not device.id:
        return
    if not bool(device.is_monitored):
        return
    if str(device.status or "") not in {"active", "online"}:
        return

    _bump_device_overview_revision()
    _invalidate_device_overview_response_cache()

    try:
        if resolve_monitor_source_by_vendor(device.vendor, device.monitor_source) == "asternos_exporter":
            from app.tasks.snmp_tasks import collect_asternos_for_device

            collect_asternos_for_device.delay(device.id)
        else:
            if not device.snmp_version:
                logger.info("设备已加入监控但未配置SNMP参数，跳过自动触发采集", device_id=device.id)
                return
            from app.tasks.snmp_tasks import collect_snmp_for_device

            collect_snmp_for_device.delay(device.id)
    except Exception:
        logger.warning("自动触发设备采集失败", device_id=getattr(device, "id", None), exc_info=True)


def resolve_active_alerts_for_unmonitored_devices(db: Session, device_ids: list[int]) -> int:
    """设备退出监控后，自动恢复其仍在触发中的监控告警，避免告警历史继续误导。"""
    normalized_ids = [int(device_id) for device_id in dict.fromkeys(device_ids or []) if device_id]
    if not normalized_ids:
        return 0

    alerts = (
        db.query(AlertHistory)
        .filter(
            AlertHistory.device_id.in_(normalized_ids),
            AlertHistory.status.in_(ACTIVE_ALERT_STATUSES),
        )
        .all()
    )
    if not alerts:
        return 0

    now = datetime.now()
    for alert in alerts:
        alert.status = "resolved"
        alert.resolved_at = now
        alert.resolved_by = "system"
        alert.resolution_note = "设备已设置为未监控，系统自动恢复活动告警"
        alert.updated_at = now
    logger.info("未监控设备活动告警已自动恢复", device_count=len(normalized_ids), alert_count=len(alerts))
    return len(alerts)


def resolve_active_interface_alerts_outside_scope(db: Session, device: Device) -> int:
    """端口监控范围收窄后，自动恢复范围外仍在触发中的接口类告警。"""
    if not device or not device.id:
        return 0

    alerts = (
        db.query(AlertHistory)
        .filter(
            AlertHistory.device_id == device.id,
            AlertHistory.status.in_(ACTIVE_ALERT_STATUSES),
            AlertHistory.alert_target_type == "interface",
        )
        .all()
    )
    if not alerts:
        return 0

    now = datetime.now()
    resolved_count = 0
    for alert in alerts:
        target = {
            "target_key": alert.alert_target_key,
            "target_name": alert.alert_target_name,
        }
        if alert_target_interface_is_monitored(device, target):
            continue
        alert.status = "resolved"
        alert.resolved_at = now
        alert.resolved_by = "system"
        alert.resolution_note = "端口已设置为不监控，系统自动恢复活动告警"
        alert.updated_at = now
        resolved_count += 1

    if resolved_count:
        logger.info("端口监控范围外活动告警已自动恢复", device_id=device.id, alert_count=resolved_count)
    return resolved_count


def normalize_monitoring_config(
    monitor_source: str,
    ip_address: str,
    prometheus_url: str | None,
    prometheus_job: str | None,
    prometheus_instance: str | None,
    custom_fields: dict | None,
) -> tuple[str | None, str | None, str | None, dict]:
    """归一化不同监控源的设备参数，AsterNOS 直连复用 URL 字段存储 Exporter 地址。"""
    custom_fields = dict(custom_fields or {})
    if monitor_source != "asternos_exporter":
        return prometheus_url, prometheus_job, prometheus_instance, custom_fields

    exporter_url = (prometheus_url or "").strip()
    if not exporter_url:
        exporter_url = f"http://{ip_address}:8101"
    elif not exporter_url.startswith(("http://", "https://")):
        exporter_url = f"http://{exporter_url}"
    exporter_url = exporter_url.rstrip("/")

    monitoring = custom_fields.get("monitoring")
    if not isinstance(monitoring, dict):
        monitoring = {}
    monitoring.update(
        {
            "exporter_profile": "asternos",
            "exporter_url": exporter_url,
            "exporter_port": 8101,
        }
    )
    custom_fields["monitoring"] = monitoring
    return exporter_url, None, None, custom_fields


def is_asternos_vendor(vendor: str | None) -> bool:
    value = (vendor or "").strip().lower()
    return any(marker in value for marker in ["asternos", "asterfusion", "asteros", "aster", "星融元"])


def resolve_monitor_source_by_vendor(vendor: str | None, requested_source: str | None) -> str:
    if is_asternos_vendor(vendor):
        return "asternos_exporter"
    return "snmp"


class DeviceConnectionTestRequest(BaseModel):
    type: str


class DeviceBatchDeleteRequest(BaseModel):
    device_ids: List[int]


class DeviceBatchUpdateRequest(BaseModel):
    device_ids: List[int]
    field: str
    value: Optional[str] = None
    value_id: Optional[int] = None


def normalize_dictionary_name(raw_name: Optional[str]) -> str:
    return (raw_name or "").strip()


def normalize_inventory_status(raw_status: Optional[str]) -> str:
    value = (raw_status or "in_stock").strip().lower()
    status_aliases = {
        "启用": "active",
        "停用": "inactive",
        "库存": "in_stock",
        "上架": "deployed",
        "在线": "active",
        "离线": "inactive",
        "active": "active",
        "inactive": "inactive",
        "in_stock": "in_stock",
        "deployed": "deployed",
        "online": "active",
        "offline": "inactive",
    }
    return status_aliases.get(value, "in_stock")


def get_or_create_device_type(db: Session, raw_name: Optional[str]) -> tuple[Optional[int], Optional[str]]:
    name = normalize_dictionary_name(raw_name)
    if not name:
        return None, None

    device_type = (
        db.query(DeviceType)
        .filter((func.lower(DeviceType.name) == name.lower()) | (func.lower(DeviceType.display_name) == name.lower()))
        .first()
    )
    if not device_type:
        device_type = DeviceType(name=name, display_name=name, is_active=True)
        db.add(device_type)
        db.flush()

    return device_type.id, device_type.name


def ensure_device_role_catalog(db: Session, raw_name: Optional[str]) -> Optional[str]:
    name = normalize_dictionary_name(raw_name)
    if not name:
        return None

    device_role = db.query(DeviceRole).filter(func.lower(DeviceRole.name) == name.lower()).first()
    if not device_role:
        device_role = DeviceRole(name=name, display_name=name, is_active=True)
        db.add(device_role)
        db.flush()
    return device_role.name


def ensure_device_vendor_catalog(db: Session, raw_name: Optional[str]) -> Optional[str]:
    name = normalize_dictionary_name(raw_name)
    if not name:
        return None

    device_vendor = db.query(DeviceVendor).filter(func.lower(DeviceVendor.name) == name.lower()).first()
    if not device_vendor:
        device_vendor = DeviceVendor(name=name, display_name=name, is_active=True)
        db.add(device_vendor)
        db.flush()
    return device_vendor.name


def infer_device_vendor(raw_vendor: Optional[str], *hints: Optional[str]) -> Optional[str]:
    """Infer vendor from model/name when the UI payload misses the vendor field."""
    vendor = normalize_dictionary_name(raw_vendor)
    if vendor:
        return vendor

    text = " ".join(str(hint or "") for hint in hints).lower()
    if any(marker in text for marker in ["h3c", "comware", "新华三", "华三"]):
        return "H3C"
    if re.search(r"\b(?:s|ce|ls)-(?:s)?\d{4}", text) or re.search(r"\bs(?:51|55|58|65|68)\d{2}", text):
        return "H3C"
    if any(marker in text for marker in ["asternos", "asterfusion", "cx308", "cx532", "cx564", "cx664"]):
        return "Asteros"
    if any(marker in text for marker in ["hillstone", "sg-6000", "山石"]):
        return "Hillstone"
    if any(marker in text for marker in ["ruijie", "锐捷"]):
        return "Ruijie"
    return None


@router.get("", response_model=dict)
async def list_devices(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    group_id: Optional[int] = None,
    status: Optional[str] = None,
    device_type: Optional[str] = None,
    device_type_id: Optional[int] = None,
    device_role: Optional[str] = None,
    vendor: Optional[str] = None,
    datacenter_id: Optional[int] = None,
    network_owner: Optional[str] = None,
    ops_owner: Optional[str] = None,
    business_type: Optional[str] = None,
    is_monitored: Optional[bool] = None,
    search: Optional[str] = None,
    search_mode: str = Query("fuzzy", pattern="^(fuzzy|regex)$"),
    name_text: Optional[str] = None,
    ip_address_text: Optional[str] = None,
    status_text: Optional[str] = None,
    monitored_text: Optional[str] = None,
    datacenter_text: Optional[str] = None,
    model_text: Optional[str] = None,
    device_type_text: Optional[str] = None,
    serial_number_text: Optional[str] = None,
    sort_by: Optional[str] = Query(
        None,
        pattern="^(name|ip_address|status|is_monitored|datacenter|model|device_type|serial_number)$",
    ),
    sort_order: str = Query("asc", pattern="^(asc|desc)$"),
):
    """获取设备列表"""
    query = db.query(Device).options(
        joinedload(Device.tags),
        joinedload(Device.datacenter_ref),
        joinedload(Device.device_type_ref)
    )
    
    if group_id:
        query = query.filter(Device.group_id == group_id)
    if status:
        if status == "active":
            query = query.filter(Device.status.in_(["active", "online"]))
        elif status == "inactive":
            query = query.filter(Device.status.in_(["inactive", "offline"]))
        else:
            query = query.filter(Device.status == status)
    if device_type:
        query = query.filter(Device.device_type == device_type)
    if device_type_id:
        query = query.filter(Device.device_type_id == device_type_id)
    if device_role:
        query = query.filter(Device.device_role.ilike(f"%{device_role}%"))
    if vendor:
        query = query.filter(Device.vendor.ilike(f"%{vendor}%"))
    if datacenter_id:
        query = query.filter(Device.datacenter_id == datacenter_id)
    if network_owner:
        query = query.filter(Device.network_owner.ilike(f"%{network_owner}%"))
    if ops_owner:
        query = query.filter(Device.ops_owner.ilike(f"%{ops_owner}%"))
    if business_type:
        query = query.filter(Device.business_type.ilike(f"%{business_type}%"))
    if is_monitored is not None:
        query = query.filter(Device.is_monitored == is_monitored)
    if name_text:
        query = query.filter(Device.name.ilike(f"%{name_text.strip()}%"))
    if ip_address_text:
        query = query.filter(Device.ip_address.ilike(f"%{ip_address_text.strip()}%"))
    if model_text:
        query = query.filter(Device.model.ilike(f"%{model_text.strip()}%"))
    if serial_number_text:
        query = query.filter(Device.serial_number.ilike(f"%{serial_number_text.strip()}%"))
    if datacenter_text:
        value = datacenter_text.strip()
        query = query.filter(Device.datacenter_ref.has(
            Datacenter.name.ilike(f"%{value}%") |
            Datacenter.code.ilike(f"%{value}%") |
            Datacenter.location.ilike(f"%{value}%")
        ))
    if device_type_text:
        value = device_type_text.strip()
        query = query.filter(
            Device.device_type.ilike(f"%{value}%") |
            Device.device_type_ref.has(
                DeviceType.name.ilike(f"%{value}%") |
                DeviceType.display_name.ilike(f"%{value}%")
            )
        )
    if status_text:
        value = status_text.strip()
        status_aliases = {
            "active": "上线",
            "online": "上线",
            "inactive": "离线",
            "offline": "离线",
            "in_stock": "库存",
            "deployed": "上架",
        }
        matched_statuses = [key for key, label in status_aliases.items() if value.lower() in key.lower() or value in label]
        status_conditions = [Device.status.ilike(f"%{value}%")]
        if matched_statuses:
            status_conditions.append(Device.status.in_(matched_statuses))
        query = query.filter(or_(*status_conditions))
    if monitored_text:
        value = monitored_text.strip().lower()
        true_labels = ["监控", "监控中", "已监控", "是", "true", "yes", "1"]
        false_labels = ["未监控", "否", "false", "no", "0"]
        matches_true = any(value in label.lower() for label in true_labels)
        matches_false = any(value in label.lower() for label in false_labels)
        if matches_true and not matches_false:
            query = query.filter(Device.is_monitored.is_(True))
        elif matches_false and not matches_true:
            query = query.filter(Device.is_monitored.is_(False))
        elif value:
            query = query.filter(Device.id == -1)
    if search:
        if search_mode == "regex":
            try:
                re.compile(search)
            except re.error:
                raise HTTPException(status_code=400, detail="正则表达式格式无效")
            query = query.filter(
                Device.name.op("~*")(search) |
                Device.ip_address.op("~*")(search) |
                Device.hostname.op("~*")(search) |
                Device.serial_number.op("~*")(search) |
                Device.model.op("~*")(search) |
                Device.vendor.op("~*")(search) |
                Device.device_role.op("~*")(search) |
                Device.device_type.op("~*")(search) |
                Device.description.op("~*")(search) |
                Device.location.op("~*")(search) |
                Device.datacenter_ref.has(
                    Datacenter.name.op("~*")(search) |
                    Datacenter.code.op("~*")(search) |
                    Datacenter.location.op("~*")(search)
                )
            )
        else:
            query = query.filter(
                (Device.name.ilike(f"%{search}%")) |
                (Device.ip_address.ilike(f"%{search}%")) |
                (Device.hostname.ilike(f"%{search}%")) |
                (Device.serial_number.ilike(f"%{search}%")) |
                (Device.model.ilike(f"%{search}%")) |
                (Device.vendor.ilike(f"%{search}%")) |
                (Device.device_role.ilike(f"%{search}%")) |
                (Device.device_type.ilike(f"%{search}%")) |
                (Device.description.ilike(f"%{search}%")) |
                (Device.location.ilike(f"%{search}%")) |
                Device.datacenter_ref.has(
                    Datacenter.name.ilike(f"%{search}%") |
                    Datacenter.code.ilike(f"%{search}%") |
                    Datacenter.location.ilike(f"%{search}%")
                )
            )
    
    if sort_by:
        sort_columns = {
            "name": Device.name,
            "ip_address": Device.ip_address,
            "status": Device.status,
            "is_monitored": Device.is_monitored,
            "model": Device.model,
            "device_type": Device.device_type,
            "serial_number": Device.serial_number,
        }
        if sort_by == "datacenter":
            query = query.outerjoin(Datacenter, Device.datacenter_id == Datacenter.id)
            sort_column = Datacenter.name
        elif sort_by == "ip_address":
            sort_column = case(
                (
                    Device.ip_address.op("~")(r"^([0-9]{1,3}\.){3}[0-9]{1,3}$|^[0-9A-Fa-f:]+$"),
                    cast(Device.ip_address, INET),
                ),
                else_=None,
            )
        else:
            sort_column = sort_columns[sort_by]
        ordering = sort_column.desc().nullslast() if sort_order == "desc" else sort_column.asc().nullslast()
        query = query.order_by(ordering, Device.id.asc())
    else:
        query = query.order_by(Device.id.asc())

    total = query.count()
    devices = query.offset(skip).limit(limit).all()
    
    return {
        "total": total,
        "items": [device.to_dict() for device in devices]
    }


@router.get("/filters/options", response_model=dict)
async def get_filter_options(db: Session = Depends(get_db)):
    """获取设备筛选选项（从已录入设备中提取）"""
    # 获取机房列表
    datacenters = db.query(Datacenter).filter(Datacenter.is_active == True).all()
    datacenter_options = [{
        "id": dc.id,
        "name": dc.name,
        "code": dc.code,
        "location": dc.location,
        "contact_person": dc.contact_person,
    } for dc in datacenters]
    
    # 获取设备类型列表
    device_types = db.query(DeviceType).filter(DeviceType.is_active == True).all()
    device_type_options = [{"id": dt.id, "name": dt.name, "display_name": dt.display_name} for dt in device_types]
    
    # 获取其他选项
    vendors = sorted({
        *[r[0] for r in db.query(Device.vendor).distinct().filter(Device.vendor != None).filter(Device.vendor != '').all()],
        *[vendor.name for vendor in db.query(DeviceVendor).filter(DeviceVendor.is_active == True).all()],
    })
    device_roles = sorted({
        *[r[0] for r in db.query(Device.device_role).distinct().filter(Device.device_role != None).filter(Device.device_role != '').all()],
        *[role.name for role in db.query(DeviceRole).filter(DeviceRole.is_active == True).all()],
    })
    network_owners = [r[0] for r in db.query(Device.network_owner).distinct().filter(Device.network_owner != None).filter(Device.network_owner != '').all()]
    ops_owners = [r[0] for r in db.query(Device.ops_owner).distinct().filter(Device.ops_owner != None).filter(Device.ops_owner != '').all()]
    business_types = [r[0] for r in db.query(Device.business_type).distinct().filter(Device.business_type != None).filter(Device.business_type != '').all()]
    
    # 运行状态：active(上线), inactive(离线), in_stock(库存), deployed(上架)
    statuses = ['active', 'inactive', 'in_stock', 'deployed']
    
    return {
        "datacenters": datacenter_options,
        "device_types": device_type_options,
        "device_roles": device_roles,
        "vendors": vendors,
        "network_owners": sorted(network_owners),
        "ops_owners": sorted(ops_owners),
        "business_types": sorted(business_types),
        "statuses": statuses,
    }


@router.get("/datacenters", response_model=List[DatacenterResponse])
async def list_datacenters(db: Session = Depends(get_db)):
    """获取机房列表"""
    datacenters = db.query(Datacenter).all()
    return datacenters


@router.post("/datacenters", response_model=DatacenterResponse, status_code=status.HTTP_201_CREATED)
async def create_datacenter(datacenter: DatacenterCreate, db: Session = Depends(get_db)):
    """创建机房"""
    existing = db.query(Datacenter).filter(Datacenter.name == datacenter.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="机房名称已存在")
    if datacenter.code:
        existing_code = db.query(Datacenter).filter(Datacenter.code == datacenter.code).first()
        if existing_code:
            raise HTTPException(status_code=400, detail="机房编号已存在")
    
    db_datacenter = Datacenter(
        code=datacenter.code,
        name=datacenter.name,
        location=datacenter.location,
        address=datacenter.address,
        contact_person=datacenter.contact_person,
        contact_phone=datacenter.contact_phone,
        contact_email=datacenter.contact_email,
        network_owner=datacenter.network_owner,
        network_owner_email=datacenter.network_owner_email,
        robot_mention=datacenter.robot_mention,
        build_date=datacenter.build_date,
        description=datacenter.description,
        is_active=datacenter.is_active
    )
    db.add(db_datacenter)
    db.commit()
    db.refresh(db_datacenter)
    
    return db_datacenter


@router.put("/datacenters/{datacenter_id}", response_model=DatacenterResponse)
async def update_datacenter(
    datacenter_id: int,
    datacenter: DatacenterUpdate,
    db: Session = Depends(get_db)
):
    """更新机房"""
    db_datacenter = db.query(Datacenter).filter(Datacenter.id == datacenter_id).first()
    if not db_datacenter:
        raise HTTPException(status_code=404, detail="机房不存在")

    if datacenter.name and datacenter.name != db_datacenter.name:
        existing_name = db.query(Datacenter).filter(Datacenter.name == datacenter.name).first()
        if existing_name:
            raise HTTPException(status_code=400, detail="机房名称已存在")

    if datacenter.code and datacenter.code != db_datacenter.code:
        existing_code = db.query(Datacenter).filter(Datacenter.code == datacenter.code).first()
        if existing_code:
            raise HTTPException(status_code=400, detail="机房编号已存在")
    
    update_data = datacenter.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(db_datacenter, key, value)
    
    db.commit()
    db.refresh(db_datacenter)
    return db_datacenter


@router.delete("/datacenters/{datacenter_id}")
async def delete_datacenter(datacenter_id: int, db: Session = Depends(get_db)):
    """删除机房"""
    datacenter = db.query(Datacenter).filter(Datacenter.id == datacenter_id).first()
    if not datacenter:
        raise HTTPException(status_code=404, detail="机房不存在")
    
    device_count = db.query(Device).filter(Device.datacenter_id == datacenter_id).count()
    if device_count > 0:
        raise HTTPException(status_code=400, detail=f"该机房下有{device_count}个设备，无法删除")
    
    db.delete(datacenter)
    db.commit()
    return {"message": "机房已删除"}


@router.get("/device-types", response_model=List[DeviceTypeResponse])
async def list_device_types(db: Session = Depends(get_db)):
    """获取设备类型列表"""
    existing_names = {normalize_dictionary_name(item.name).lower() for item in db.query(DeviceType).all()}
    historical_names = [
        normalize_dictionary_name(value[0])
        for value in db.query(Device.device_type).distinct().filter(Device.device_type != None).filter(Device.device_type != '').all()
        if normalize_dictionary_name(value[0])
    ]
    missing_names = [name for name in historical_names if name.lower() not in existing_names]
    for name in missing_names:
        db.add(DeviceType(name=name, display_name=name, is_active=True))
    if missing_names:
        db.commit()
    device_types = db.query(DeviceType).order_by(DeviceType.name.asc()).all()
    return device_types


@router.post("/device-types", response_model=DeviceTypeResponse, status_code=status.HTTP_201_CREATED)
async def create_device_type(device_type: DeviceTypeCreate, db: Session = Depends(get_db)):
    """创建设备类型"""
    normalized_name = normalize_dictionary_name(device_type.name)
    existing = db.query(DeviceType).filter(func.lower(DeviceType.name) == normalized_name.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="设备类型名称已存在")
    
    db_device_type = DeviceType(
        name=normalized_name,
        display_name=device_type.display_name or normalized_name,
        icon=device_type.icon,
        description=device_type.description,
        is_active=device_type.is_active
    )
    db.add(db_device_type)
    db.commit()
    db.refresh(db_device_type)
    
    return db_device_type


@router.get("/device-roles", response_model=List[DeviceRoleResponse])
async def list_device_roles(db: Session = Depends(get_db)):
    """获取设备角色列表"""
    existing_names = {normalize_dictionary_name(item.name).lower() for item in db.query(DeviceRole).all()}
    historical_names = [
        normalize_dictionary_name(value[0])
        for value in db.query(Device.device_role).distinct().filter(Device.device_role != None).filter(Device.device_role != '').all()
        if normalize_dictionary_name(value[0])
    ]
    missing_names = [name for name in historical_names if name.lower() not in existing_names]
    for name in missing_names:
        db.add(DeviceRole(name=name, display_name=name, is_active=True))
    if missing_names:
        db.commit()
    return db.query(DeviceRole).order_by(DeviceRole.name.asc()).all()


@router.post("/device-roles", response_model=DeviceRoleResponse, status_code=status.HTTP_201_CREATED)
async def create_device_role(device_role: DeviceRoleCreate, db: Session = Depends(get_db)):
    normalized_name = normalize_dictionary_name(device_role.name)
    existing = db.query(DeviceRole).filter(func.lower(DeviceRole.name) == normalized_name.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="设备角色名称已存在")

    db_device_role = DeviceRole(
        name=normalized_name,
        display_name=device_role.display_name or normalized_name,
        description=device_role.description,
        is_active=device_role.is_active,
    )
    db.add(db_device_role)
    db.commit()
    db.refresh(db_device_role)
    return db_device_role


@router.put("/device-roles/{device_role_id}", response_model=DeviceRoleResponse)
async def update_device_role(device_role_id: int, device_role: DeviceRoleUpdate, db: Session = Depends(get_db)):
    db_device_role = db.query(DeviceRole).filter(DeviceRole.id == device_role_id).first()
    if not db_device_role:
        raise HTTPException(status_code=404, detail="设备角色不存在")

    update_data = device_role.model_dump(exclude_unset=True)
    if "name" in update_data and update_data["name"] != db_device_role.name:
        normalized_name = normalize_dictionary_name(update_data["name"])
        existing = db.query(DeviceRole).filter(func.lower(DeviceRole.name) == normalized_name.lower()).first()
        if existing and existing.id != db_device_role.id:
            raise HTTPException(status_code=400, detail="设备角色名称已存在")
        db.query(Device).filter(func.lower(Device.device_role) == db_device_role.name.lower()).update({Device.device_role: normalized_name})
        update_data["name"] = normalized_name
        if not update_data.get("display_name"):
            update_data["display_name"] = normalized_name

    for key, value in update_data.items():
        setattr(db_device_role, key, value)

    db.commit()
    db.refresh(db_device_role)
    return db_device_role


@router.delete("/device-roles/{device_role_id}")
async def delete_device_role(device_role_id: int, db: Session = Depends(get_db)):
    db_device_role = db.query(DeviceRole).filter(DeviceRole.id == device_role_id).first()
    if not db_device_role:
        raise HTTPException(status_code=404, detail="设备角色不存在")

    device_count = db.query(Device).filter(func.lower(Device.device_role) == db_device_role.name.lower()).count()
    if device_count > 0:
        raise HTTPException(status_code=400, detail=f"该角色下有{device_count}个设备，无法删除")

    db.delete(db_device_role)
    db.commit()
    return {"message": "设备角色已删除"}


@router.get("/device-vendors", response_model=List[DeviceVendorResponse])
async def list_device_vendors(db: Session = Depends(get_db)):
    """获取设备厂商列表"""
    existing_names = {normalize_dictionary_name(item.name).lower() for item in db.query(DeviceVendor).all()}
    historical_names = [
        normalize_dictionary_name(value[0])
        for value in db.query(Device.vendor).distinct().filter(Device.vendor != None).filter(Device.vendor != '').all()
        if normalize_dictionary_name(value[0])
    ]
    missing_names = [name for name in historical_names if name.lower() not in existing_names]
    for name in missing_names:
        db.add(DeviceVendor(name=name, display_name=name, is_active=True))
    if missing_names:
        db.commit()
    return db.query(DeviceVendor).order_by(DeviceVendor.name.asc()).all()


@router.post("/device-vendors", response_model=DeviceVendorResponse, status_code=status.HTTP_201_CREATED)
async def create_device_vendor(device_vendor: DeviceVendorCreate, db: Session = Depends(get_db)):
    normalized_name = normalize_dictionary_name(device_vendor.name)
    existing = db.query(DeviceVendor).filter(func.lower(DeviceVendor.name) == normalized_name.lower()).first()
    if existing:
        raise HTTPException(status_code=400, detail="设备厂商名称已存在")

    db_device_vendor = DeviceVendor(
        name=normalized_name,
        display_name=device_vendor.display_name or normalized_name,
        description=device_vendor.description,
        is_active=device_vendor.is_active,
    )
    db.add(db_device_vendor)
    db.commit()
    db.refresh(db_device_vendor)
    return db_device_vendor


@router.put("/device-vendors/{device_vendor_id}", response_model=DeviceVendorResponse)
async def update_device_vendor(device_vendor_id: int, device_vendor: DeviceVendorUpdate, db: Session = Depends(get_db)):
    db_device_vendor = db.query(DeviceVendor).filter(DeviceVendor.id == device_vendor_id).first()
    if not db_device_vendor:
        raise HTTPException(status_code=404, detail="设备厂商不存在")

    update_data = device_vendor.model_dump(exclude_unset=True)
    if "name" in update_data and update_data["name"] != db_device_vendor.name:
        normalized_name = normalize_dictionary_name(update_data["name"])
        existing = db.query(DeviceVendor).filter(func.lower(DeviceVendor.name) == normalized_name.lower()).first()
        if existing and existing.id != db_device_vendor.id:
            raise HTTPException(status_code=400, detail="设备厂商名称已存在")
        db.query(Device).filter(func.lower(Device.vendor) == db_device_vendor.name.lower()).update({Device.vendor: normalized_name})
        update_data["name"] = normalized_name
        if not update_data.get("display_name"):
            update_data["display_name"] = normalized_name

    for key, value in update_data.items():
        setattr(db_device_vendor, key, value)

    db.commit()
    db.refresh(db_device_vendor)
    return db_device_vendor


@router.delete("/device-vendors/{device_vendor_id}")
async def delete_device_vendor(device_vendor_id: int, db: Session = Depends(get_db)):
    db_device_vendor = db.query(DeviceVendor).filter(DeviceVendor.id == device_vendor_id).first()
    if not db_device_vendor:
        raise HTTPException(status_code=404, detail="设备厂商不存在")

    device_count = db.query(Device).filter(func.lower(Device.vendor) == db_device_vendor.name.lower()).count()
    if device_count > 0:
        raise HTTPException(status_code=400, detail=f"该厂商下有{device_count}个设备，无法删除")

    db.delete(db_device_vendor)
    db.commit()
    return {"message": "设备厂商已删除"}


@router.put("/device-types/{device_type_id}", response_model=DeviceTypeResponse)
async def update_device_type(
    device_type_id: int,
    device_type: DeviceTypeUpdate,
    db: Session = Depends(get_db)
):
    """更新设备类型"""
    db_device_type = db.query(DeviceType).filter(DeviceType.id == device_type_id).first()
    if not db_device_type:
        raise HTTPException(status_code=404, detail="设备类型不存在")
    
    update_data = device_type.model_dump(exclude_unset=True)
    if "name" in update_data:
        normalized_name = normalize_dictionary_name(update_data["name"])
        if normalized_name.lower() != db_device_type.name.lower():
            existing = db.query(DeviceType).filter(func.lower(DeviceType.name) == normalized_name.lower()).first()
            if existing and existing.id != db_device_type.id:
                raise HTTPException(status_code=400, detail="设备类型名称已存在")
            db.query(Device).filter(func.lower(Device.device_type) == db_device_type.name.lower()).update({Device.device_type: normalized_name})
        update_data["name"] = normalized_name
        if not update_data.get("display_name"):
            update_data["display_name"] = normalized_name
    for key, value in update_data.items():
        setattr(db_device_type, key, value)
    
    db.commit()
    db.refresh(db_device_type)
    return db_device_type


@router.delete("/device-types/{device_type_id}")
async def delete_device_type(device_type_id: int, db: Session = Depends(get_db)):
    """删除设备类型"""
    device_type = db.query(DeviceType).filter(DeviceType.id == device_type_id).first()
    if not device_type:
        raise HTTPException(status_code=404, detail="设备类型不存在")
    
    device_count = db.query(Device).filter(
        (Device.device_type_id == device_type_id) |
        (func.lower(Device.device_type) == device_type.name.lower())
    ).count()
    if device_count > 0:
        raise HTTPException(status_code=400, detail=f"该类型下有{device_count}个设备，无法删除")
    
    db.delete(device_type)
    db.commit()
    return {"message": "设备类型已删除"}


@router.get("/{device_id}/detail/connections", response_model=dict)
async def get_device_detail_connections(device_id: int, db: Session = Depends(get_db)):
    """设备详情：接口连接关系。"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    cached = _load_monitor_cache("interfaces", device.id)
    interfaces = cached.get("interfaces", []) if isinstance(cached, dict) else []
    source = "cache"
    if not interfaces:
        # 优先用后台快照；快照缺失时做一次限时 SNMP 轻量补采，避免详情页长期空白。
        # 大端口设备 SNMP walk 可能较慢，所以必须设置超时，超时后仍返回页面而不是一直转圈。
        if getattr(device, "snmp_version", None):
            try:
                interfaces = await asyncio.wait_for(
                    asyncio.to_thread(SNMPCollector().list_interfaces, device),
                    timeout=15,
                )
                source = "snmp_live"
            except Exception:
                logger.warning("设备详情接口实时补采失败 device_id=%s ip=%s", device.id, device.ip_address, exc_info=True)
                return {
                    "device": device.to_dict(),
                    "items": [],
                    "total": 0,
                    "source": "cache_miss",
                    "message": "后台接口快照暂未生成，实时补采超时或失败，请稍后刷新或触发设备采集",
                }
        else:
            return {
                "device": device.to_dict(),
                "items": [],
                "total": 0,
                "source": "cache_miss",
                "message": "设备未配置 SNMP/Exporter 接口采集参数",
            }

    cached_lldp = _load_monitor_cache("lldp_neighbors_v2", device.id) or _load_monitor_cache("protocol_neighbors", device.id)
    if isinstance(cached_lldp, dict) and isinstance(cached_lldp.get("neighbors"), dict):
        lldp_rows = cached_lldp.get("neighbors", {}).get("lldp") or []
    elif isinstance(cached_lldp, dict) and isinstance(cached_lldp.get("neighbors"), list):
        lldp_rows = cached_lldp.get("neighbors") or []
    else:
        lldp_rows = []

    lldp_by_index = {
        str(item.get("local_index") or item.get("local_port_num") or item.get("interface_index") or ""): item
        for item in lldp_rows
    }
    lldp_by_name: Dict[str, Dict[str, Any]] = {}
    for item in lldp_rows:
        for key in [item.get("local_port"), item.get("interface"), item.get("local_port_id")]:
            normalized = _normalize_interface_key(key)
            if normalized:
                lldp_by_name.setdefault(normalized, item)

    config_fields = _parse_interface_config_fields(_latest_device_config_text(db, device.id))

    items = []
    for iface in interfaces or []:
        index = str(iface.get("index") or iface.get("interface_index") or "")
        name = str(iface.get("name") or iface.get("interface_name") or iface.get("alias") or "")
        neighbor = lldp_by_index.get(index) or lldp_by_name.get(_normalize_interface_key(name)) or {}
        config_item = config_fields.get(_normalize_interface_key(name)) or {}
        alias = iface.get("alias")
        description = alias if not _is_default_interface_description(name, alias) else ""
        if not description:
            description = config_item.get("description") or ""
        ip_address = _best_interface_ip(iface.get("ip_address") or iface.get("interface_ip"), config_item.get("ip_address"))
        mtu = iface.get("mtu") or config_item.get("mtu")
        items.append({
            "index": index,
            "name": name,
            "logical_type": iface.get("logical_type") or iface.get("type") or iface.get("interface_type") or "",
            "description": description,
            "speed_bps": iface.get("speed_bps"),
            "mtu": mtu,
            "ip_address": ip_address,
            "oper_status": iface.get("oper_status") or "",
            "admin_status": iface.get("admin_status") or "",
            "remote_device": neighbor.get("remote_system") or neighbor.get("remote_device") or "",
            "remote_interface": neighbor.get("remote_port") or neighbor.get("remote_interface") or "",
            "remote_management_ip": (
                neighbor.get("remote_mgmt_addr")
                or neighbor.get("remote_management_address")
                or neighbor.get("remote_management_ip")
                or neighbor.get("management_address")
                or ""
            ),
        })

    def _sort_key(item: Dict[str, Any]):
        return (0 if str(item.get("oper_status")).lower() == "up" else 1, item.get("name") or "")

    items.sort(key=_sort_key)
    message = None
    if source == "cache" and items:
        missing_mtu_count = sum(1 for item in items if item.get("mtu") in (None, ""))
        missing_type_count = sum(1 for item in items if not item.get("logical_type"))
        if missing_mtu_count >= max(3, int(len(items) * 0.8)) or missing_type_count >= max(3, int(len(items) * 0.8)):
            message = "当前接口数据来自旧快照，MTU/逻辑类型等新增字段会在下一轮 SNMP 接口采集后自动补齐"
    return {"device": device.to_dict(), "items": items, "total": len(items), "source": source, "message": message}


@router.get("/{device_id}/detail/syslog", response_model=dict)
async def get_device_detail_syslog(
    device_id: int,
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
):
    """设备详情：Syslog日志。"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    query = db.query(SyslogEvent).filter(or_(SyslogEvent.device_id == device.id, SyslogEvent.source_ip == device.ip_address))
    if search:
        keyword = f"%{search.strip()}%"
        query = query.filter(or_(SyslogEvent.message.ilike(keyword), SyslogEvent.raw_message.ilike(keyword)))
    start_dt = _parse_time_filter(start_time)
    end_dt = _parse_time_filter(end_time)
    if start_dt:
        query = query.filter(SyslogEvent.created_at >= start_dt)
    if end_dt:
        query = query.filter(SyslogEvent.created_at <= end_dt)
    total = query.count()
    rows = query.order_by(SyslogEvent.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [{
            "id": item.id,
            "time": item.created_at.isoformat() if item.created_at else None,
            "severity": item.severity,
            "level": item.severity,
            "message": item.message,
            "raw_message": item.raw_message,
            "source_ip": item.source_ip,
            "source_host": item.source_host,
        } for item in rows],
    }


@router.get("/{device_id}/detail/config-backups", response_model=dict)
async def get_device_detail_config_backups(
    device_id: int,
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    """设备详情：本设备配置备份列表。"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    query = db.query(ConfigBackupResult).filter(ConfigBackupResult.device_id == device.id)
    total = query.count()
    rows = query.order_by(ConfigBackupResult.finished_at.desc().nullslast(), ConfigBackupResult.id.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [{
            "id": item.id,
            "job_id": item.job_id,
            "device_name": item.device_name,
            "device_ip": item.device_ip,
            "datacenter_name": item.datacenter_name,
            "vendor": item.vendor,
            "model": item.model,
            "status": item.status,
            "config_name": f"configuration_{item.device_ip}_{(item.finished_at or item.started_at or datetime.now()).strftime('%Y-%m-%d_%H%M%S')}.txt",
            "line_count": item.line_count,
            "finished_at": item.finished_at.isoformat() if item.finished_at else None,
        } for item in rows],
    }


@router.get("/{device_id}/detail/performance", response_model=dict)
async def get_device_detail_performance(
    device_id: int,
    range: str = Query("-24h"),
    interval: str = Query("5m"),
):
    """设备详情：CPU/内存/温度趋势。"""
    safe_range = range if re.fullmatch(r"-?\d+[smhdw]", range or "") else "-24h"
    safe_interval = interval if re.fullmatch(r"\d+[smhdw]", interval or "") else "5m"
    series_config = [
        ("cpu", "snmp_metrics", "usage", {"metric_type": "cpu"}),
        ("memory", "snmp_metrics", "usage_percent", {"metric_type": "memory"}),
        ("temperature", "snmp_temperature", "temperature", {}),
    ]
    series = []
    for name, measurement, field, tags in series_config:
        tag_filter = "".join([f'  |> filter(fn: (r) => r.{key} == "{_flux_escape(value)}")\n' for key, value in tags.items()])
        flux = f'''
from(bucket: "{influx_client.bucket}")
  |> range(start: {safe_range})
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> filter(fn: (r) => r.device_id == "{device_id}")
  |> filter(fn: (r) => r._field == "{field}")
{tag_filter}  |> aggregateWindow(every: {safe_interval}, fn: mean, createEmpty: false)
  |> yield(name: "result")
'''
        data = influx_client.query(flux)
        series.append({
            "name": name,
            "measurement": measurement,
            "field": field,
            "data": [{"time": row.get("time"), "value": row.get("value")} for row in data],
        })
    return {"device_id": device_id, "range": safe_range, "interval": safe_interval, "series": series}


@router.get("/{device_id}/detail/hardware", response_model=dict)
async def get_device_detail_hardware(device_id: int):
    """设备详情：硬件状态。"""
    flux = f'''
from(bucket: "{influx_client.bucket}")
  |> range(start: -7d)
  |> filter(fn: (r) => r._measurement == "snmp_hardware")
  |> filter(fn: (r) => r.device_id == "{device_id}")
  |> last()
  |> yield(name: "result")
'''
    rows = influx_client.query(flux)
    grouped: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        key = f"{row.get('component_type') or ''}:{row.get('component') or ''}"
        item = grouped.setdefault(key, {
            "component_type": row.get("component_type"),
            "component": row.get("component"),
            "time": row.get("time"),
        })
        item[str(row.get("field"))] = row.get("value")
    return {"device_id": device_id, "items": list(grouped.values()), "total": len(grouped)}


@router.get("/{device_id}/detail/tacacs", response_model=dict)
async def get_device_detail_tacacs(
    device_id: int,
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
    search: Optional[str] = None,
    start_time: Optional[str] = None,
    end_time: Optional[str] = None,
    username: Optional[str] = None,
    command: Optional[str] = None,
):
    """设备详情：Tacacs命令日志。"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    if not TACACS_LOG_FILE.exists():
        return {"total": 0, "items": []}
    keyword = (search or "").strip().lower()
    username_value = (username or "").strip().lower()
    command_value = (command or "").strip().lower()
    start_value = (start_time or "").strip()
    end_value = (end_time or "").strip()
    total = 0
    items: List[Dict[str, Any]] = []
    for line in reversed(TACACS_LOG_FILE.read_text(encoding="utf-8", errors="ignore").splitlines()):
        match = TACACS_LOG_PATTERN.search(line)
        if not match:
            continue
        if match.group(2) != device.ip_address:
            continue
        command_text = _extract_tacacs_command(match.group(6))
        parsed_time = _format_tacacs_time(match.group(1)) or match.group(1)
        item = {
            "time": parsed_time,
            "device_ip": match.group(2),
            "username": match.group(3),
            "tty": match.group(4),
            "client_ip": match.group(5),
            "login_time": parsed_time,
            "operation_type": _infer_tacacs_operation(command_text, line),
            "command": command_text,
            "raw": line,
        }
        if start_value and str(parsed_time) < start_value:
            continue
        if end_value and str(parsed_time) > end_value:
            continue
        if username_value and username_value not in item["username"].lower():
            continue
        if command_value and command_value not in item["command"].lower():
            continue
        if keyword and keyword not in " ".join(str(value).lower() for value in item.values()):
            continue
        total += 1
        if total <= skip:
            continue
        if len(items) < limit:
            items.append(item)
    return {"total": total, "items": items}


@router.get("/{device_id}", response_model=dict)
async def get_device(device_id: int, db: Session = Depends(get_db)):
    """获取设备详情"""
    device = db.query(Device).options(joinedload(Device.tags)).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    return device.to_dict()


@router.post("", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_device(device: DeviceCreate, db: Session = Depends(get_db)):
    """创建设备"""
    # 检查IP是否已存在
    existing = db.query(Device).filter(Device.ip_address == device.ip_address).first()
    if existing:
        raise HTTPException(status_code=400, detail="设备IP已存在")
    
    datacenter_id = device.datacenter_id
    if datacenter_id is None and device.datacenter:
        datacenter = db.query(Datacenter).filter(Datacenter.name == device.datacenter).first()
        if not datacenter:
            datacenter = Datacenter(name=device.datacenter, is_active=True)
            db.add(datacenter)
            db.flush()
        datacenter_id = datacenter.id

    device_type_id = device.device_type_id
    device_type_name = (device.device_type or "").strip() or None
    if device_type_id:
        db_device_type = db.query(DeviceType).filter(DeviceType.id == device_type_id).first()
        if not db_device_type:
            raise HTTPException(status_code=400, detail="设备类型不存在")
        device_type_name = db_device_type.name
    else:
        device_type_id, device_type_name = get_or_create_device_type(db, device_type_name)

    monitor_source = resolve_monitor_source_by_vendor(device.vendor, device.monitor_source)
    prometheus_url, prometheus_job, prometheus_instance, custom_fields = normalize_monitoring_config(
        monitor_source,
        device.ip_address,
        device.prometheus_url,
        device.prometheus_job,
        device.prometheus_instance,
        device.custom_fields,
    )
    device_role_name = ensure_device_role_catalog(db, device.device_role)
    inferred_vendor = infer_device_vendor(
        device.vendor or ("Asterfusion" if monitor_source == "asternos_exporter" else None),
        device.model,
        device.name,
        device.hostname,
    )
    vendor_name = ensure_device_vendor_catalog(db, inferred_vendor)

    # 创建设备
    db_device = Device(
        name=device.name,
        ip_address=device.ip_address,
        hostname=device.hostname,
        device_type=device_type_name or "unknown",
        device_role=device_role_name,
        vendor=vendor_name,
        model=device.model,
        serial_number=device.serial_number,
        location=device.location,
        latitude=device.latitude,
        longitude=device.longitude,
        rack=device.rack,
        # 机房信息
        datacenter_id=datacenter_id,
        # 设备类型
        device_type_id=device_type_id,
        # 责任人信息
        network_owner=device.network_owner,
        ops_owner=device.ops_owner,
        contact_phone=device.contact_phone,
        contact_email=device.contact_email,
        business_type=device.business_type,
        is_monitored=device.is_monitored,
        monitor_source=monitor_source,
        prometheus_url=prometheus_url,
        prometheus_job=prometheus_job,
        prometheus_instance=prometheus_instance,
        description=device.description,
        group_id=device.group_id,
        custom_fields=custom_fields,
        # 设备状态由台账录入指定，不主动探测
        status=normalize_inventory_status(device.status),
        last_seen=None,
        # SNMP配置
        snmp_version=device.snmp.version,
        snmp_port=device.snmp.port,
        snmp_community=device.snmp.community,
        snmp_username=device.snmp.username,
        snmp_auth_protocol=device.snmp.auth_protocol,
        snmp_auth_password=device.snmp.auth_password,
        snmp_priv_protocol=device.snmp.priv_protocol,
        snmp_priv_password=device.snmp.priv_password,
        snmp_security_level=device.snmp.security_level,
        # gNMI配置
        gnmi_enabled=1 if device.gnmi.enabled else 0,
        gnmi_port=device.gnmi.port,
        gnmi_username=device.gnmi.username,
        gnmi_password=device.gnmi.password,
        gnmi_tls_enabled=1 if device.gnmi.tls_enabled else 0,
        gnmi_tls_cert=device.gnmi.tls_cert,
        gnmi_skip_verify=1 if device.gnmi.skip_verify else 0,
        gnmi_subscriptions=device.gnmi.subscriptions,
        # SSH配置
        ssh_port=device.ssh.port,
        ssh_username=device.ssh.username,
        ssh_password=device.ssh.password,
        ssh_key=device.ssh.key,
    )
    
    # 处理标签
    if device.tags:
        for tag_name in device.tags:
            tag = db.query(Tag).filter(Tag.name == tag_name).first()
            if not tag:
                tag = Tag(name=tag_name)
                db.add(tag)
            db_device.tags.append(tag)
    
    db.add(db_device)
    db.commit()
    db.refresh(db_device)
    _trigger_monitor_refresh_for_device(db_device)
    
    logger.info("设备创建成功", device_id=db_device.id, name=db_device.name)
    return db_device.to_dict()


@router.put("/{device_id}", response_model=dict)
async def update_device(device_id: int, device: DeviceUpdate, db: Session = Depends(get_db)):
    """更新设备"""
    db_device = db.query(Device).filter(Device.id == device_id).first()
    if not db_device:
        raise HTTPException(status_code=404, detail="设备不存在")
    was_monitored = bool(db_device.is_monitored)
    
    # 更新基本字段
    update_data = device.model_dump(exclude_unset=True)
    
    # 处理SNMP配置
    if "snmp" in update_data and update_data["snmp"]:
        snmp = update_data.pop("snmp")
        db_device.snmp_version = snmp.get("version", db_device.snmp_version)
        db_device.snmp_port = snmp.get("port", db_device.snmp_port)
        db_device.snmp_community = snmp.get("community", db_device.snmp_community)
        db_device.snmp_username = snmp.get("username", db_device.snmp_username)
        db_device.snmp_auth_protocol = snmp.get("auth_protocol", db_device.snmp_auth_protocol)
        db_device.snmp_auth_password = snmp.get("auth_password", db_device.snmp_auth_password)
        db_device.snmp_priv_protocol = snmp.get("priv_protocol", db_device.snmp_priv_protocol)
        db_device.snmp_priv_password = snmp.get("priv_password", db_device.snmp_priv_password)
        db_device.snmp_security_level = snmp.get("security_level", db_device.snmp_security_level)
    
    # 处理gNMI配置
    if "gnmi" in update_data and update_data["gnmi"]:
        gnmi = update_data.pop("gnmi")
        db_device.gnmi_enabled = 1 if gnmi.get("enabled") else 0
        db_device.gnmi_port = gnmi.get("port", db_device.gnmi_port)
        db_device.gnmi_username = gnmi.get("username", db_device.gnmi_username)
        db_device.gnmi_password = gnmi.get("password", db_device.gnmi_password)
        db_device.gnmi_tls_enabled = 1 if gnmi.get("tls_enabled") else 0
        db_device.gnmi_tls_cert = gnmi.get("tls_cert", db_device.gnmi_tls_cert)
        db_device.gnmi_skip_verify = 1 if gnmi.get("skip_verify") else 0
        db_device.gnmi_subscriptions = gnmi.get("subscriptions", db_device.gnmi_subscriptions)

    # 处理SSH配置（配置备份使用）
    if "ssh" in update_data and update_data["ssh"]:
        ssh = update_data.pop("ssh")
        db_device.ssh_port = ssh.get("port", db_device.ssh_port)
        db_device.ssh_username = ssh.get("username", db_device.ssh_username)
        db_device.ssh_password = ssh.get("password", db_device.ssh_password)
        db_device.ssh_key = ssh.get("key", db_device.ssh_key)
    
    # 处理标签
    if "tags" in update_data and update_data["tags"] is not None:
        tag_names = update_data.pop("tags")
        db_device.tags = []
        for tag_name in tag_names:
            tag = db.query(Tag).filter(Tag.name == tag_name).first()
            if not tag:
                tag = Tag(name=tag_name)
                db.add(tag)
            db_device.tags.append(tag)

    if "status" in update_data and update_data["status"] is not None:
        update_data["status"] = normalize_inventory_status(update_data["status"])

    if "monitor_source" in update_data and not update_data.get("monitor_source"):
        update_data["monitor_source"] = "snmp"

    effective_vendor = infer_device_vendor(
        update_data.get("vendor", db_device.vendor),
        update_data.get("model", db_device.model),
        update_data.get("name", db_device.name),
        update_data.get("hostname", db_device.hostname),
    )
    effective_monitor_source = resolve_monitor_source_by_vendor(
        effective_vendor,
        update_data.get("monitor_source", db_device.monitor_source or "snmp"),
    )
    update_data["monitor_source"] = effective_monitor_source
    if effective_monitor_source == "asternos_exporter":
        next_url, next_job, next_instance, next_custom_fields = normalize_monitoring_config(
            effective_monitor_source,
            update_data.get("ip_address", db_device.ip_address),
            update_data.get("prometheus_url", db_device.prometheus_url),
            update_data.get("prometheus_job", db_device.prometheus_job),
            update_data.get("prometheus_instance", db_device.prometheus_instance),
            update_data.get("custom_fields", db_device.custom_fields),
        )
        update_data["monitor_source"] = effective_monitor_source
        update_data["prometheus_url"] = next_url
        update_data["prometheus_job"] = next_job
        update_data["prometheus_instance"] = next_instance
        update_data["custom_fields"] = next_custom_fields
    elif "monitor_source" in update_data or "vendor" in update_data:
        update_data["prometheus_url"] = None
        update_data["prometheus_job"] = None
        update_data["prometheus_instance"] = None

    if "device_type_id" in update_data or "device_type" in update_data:
        device_type_id = update_data.get("device_type_id", db_device.device_type_id)
        device_type_name = update_data.get("device_type", db_device.device_type)
        if device_type_id:
            db_device_type = db.query(DeviceType).filter(DeviceType.id == device_type_id).first()
            if not db_device_type:
                raise HTTPException(status_code=400, detail="设备类型不存在")
            update_data["device_type_id"] = db_device_type.id
            update_data["device_type"] = db_device_type.name
        else:
            next_type_id, next_type_name = get_or_create_device_type(db, device_type_name)
            update_data["device_type_id"] = next_type_id
            update_data["device_type"] = next_type_name or db_device.device_type

    if "device_role" in update_data:
        update_data["device_role"] = ensure_device_role_catalog(db, update_data["device_role"])

    if "vendor" in update_data or (effective_vendor and not db_device.vendor):
        update_data["vendor"] = ensure_device_vendor_catalog(db, effective_vendor)
    elif effective_monitor_source == "asternos_exporter" and not db_device.vendor:
        update_data["vendor"] = ensure_device_vendor_catalog(db, "Asterfusion")
    
    # 更新其他字段
    for key, value in update_data.items():
        if value is not None and hasattr(db_device, key):
            setattr(db_device, key, value)
    
    db_device.updated_at = datetime.now()
    resolved_alerts = 0
    if "is_monitored" in update_data and was_monitored and not bool(db_device.is_monitored):
        resolved_alerts = resolve_active_alerts_for_unmonitored_devices(db, [db_device.id])
    elif bool(db_device.is_monitored) and "custom_fields" in update_data:
        resolved_alerts = resolve_active_interface_alerts_outside_scope(db, db_device)
    db.commit()
    db.refresh(db_device)
    _trigger_monitor_refresh_for_device(db_device)
    
    logger.info("设备更新成功", device_id=device_id, auto_resolved_alerts=resolved_alerts)
    return db_device.to_dict()


@router.delete("/{device_id}")
async def delete_device(device_id: int, db: Session = Depends(get_db)):
    """删除设备"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    
    db.delete(device)
    db.commit()
    
    logger.info("设备删除成功", device_id=device_id)
    return {"message": "设备已删除"}


@router.post("/bulk/delete")
async def batch_delete_devices(payload: DeviceBatchDeleteRequest, db: Session = Depends(get_db)):
    """批量删除设备"""
    device_ids = list(dict.fromkeys(payload.device_ids))
    if not device_ids:
        raise HTTPException(status_code=400, detail="请选择需要删除的设备")

    devices = db.query(Device).filter(Device.id.in_(device_ids)).all()
    existing_ids = {device.id for device in devices}
    missing_ids = [device_id for device_id in device_ids if device_id not in existing_ids]

    for device in devices:
        db.delete(device)

    db.commit()

    logger.info("设备批量删除成功", deleted_count=len(devices), missing_count=len(missing_ids))
    return {
        "deleted": len(devices),
        "missing_ids": missing_ids,
    }


@router.post("/bulk/update")
async def batch_update_devices(payload: DeviceBatchUpdateRequest, db: Session = Depends(get_db)):
    """批量更新设备单个字段"""
    device_ids = list(dict.fromkeys(payload.device_ids))
    if not device_ids:
        raise HTTPException(status_code=400, detail="请选择需要修改的设备")

    supported_fields = {
        "status",
        "is_monitored",
        "datacenter_id",
        "device_type",
        "device_role",
        "vendor",
        "model",
        "serial_number",
    }
    if payload.field not in supported_fields:
        raise HTTPException(status_code=400, detail="不支持批量修改该字段")

    devices = db.query(Device).filter(Device.id.in_(device_ids)).all()
    existing_ids = {device.id for device in devices}
    missing_ids = [device_id for device_id in device_ids if device_id not in existing_ids]
    if not devices:
        raise HTTPException(status_code=404, detail="选中的设备不存在")

    update_kwargs: dict[str, object] = {}
    if payload.field == "status":
        update_kwargs["status"] = normalize_inventory_status(payload.value)
    elif payload.field == "is_monitored":
        normalized_value = (payload.value or "").strip().lower()
        if normalized_value not in {"true", "false"}:
            raise HTTPException(status_code=400, detail="请选择是否加入监控")
        update_kwargs["is_monitored"] = normalized_value == "true"
    elif payload.field == "datacenter_id":
        if payload.value_id is None:
            raise HTTPException(status_code=400, detail="请选择机房")
        datacenter = db.query(Datacenter).filter(Datacenter.id == payload.value_id).first()
        if not datacenter:
            raise HTTPException(status_code=400, detail="机房不存在")
        update_kwargs["datacenter_id"] = datacenter.id
    elif payload.field == "device_type":
        if payload.value_id is not None:
            db_device_type = db.query(DeviceType).filter(DeviceType.id == payload.value_id).first()
            if not db_device_type:
                raise HTTPException(status_code=400, detail="设备类型不存在")
            update_kwargs["device_type_id"] = db_device_type.id
            update_kwargs["device_type"] = db_device_type.name
        else:
            next_type_id, next_type_name = get_or_create_device_type(db, payload.value)
            update_kwargs["device_type_id"] = next_type_id
            update_kwargs["device_type"] = next_type_name or "unknown"
    elif payload.field == "device_role":
        update_kwargs["device_role"] = ensure_device_role_catalog(db, payload.value)
    elif payload.field == "vendor":
        update_kwargs["vendor"] = ensure_device_vendor_catalog(db, payload.value)
    elif payload.field in {"model", "serial_number"}:
        update_kwargs[payload.field] = (payload.value or "").strip() or None

    for device in devices:
        for key, value in update_kwargs.items():
            setattr(device, key, value)
        device.updated_at = datetime.now()

    resolved_alerts = 0
    if payload.field == "is_monitored" and update_kwargs.get("is_monitored") is False:
        resolved_alerts = resolve_active_alerts_for_unmonitored_devices(db, [device.id for device in devices])

    db.commit()

    logger.info(
        "设备批量更新成功",
        updated_count=len(devices),
        field=payload.field,
        missing_count=len(missing_ids),
        auto_resolved_alerts=resolved_alerts,
    )
    return {
        "updated": len(devices),
        "missing_ids": missing_ids,
        "resolved_alerts": resolved_alerts,
    }


@router.patch("/{device_id}/status")
async def update_device_status(
    device_id: int, 
    status_update: DeviceStatusUpdate, 
    db: Session = Depends(get_db)
):
    """更新设备状态"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    
    device.status = status_update.status
    if status_update.last_seen:
        device.last_seen = status_update.last_seen
    else:
        device.last_seen = datetime.now()
    
    db.commit()
    return device.to_dict()


@router.post("/{device_id}/probe", response_model=dict)
async def probe_device(device_id: int, db: Session = Depends(get_db)):
    """库存模式下不主动探测设备连通性"""
    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    return {
        "device_id": device_id,
        "ip_address": device.ip_address,
        "ping_success": None,
        "ping_latency": None,
        "old_status": device.status,
        "new_status": device.status,
        "error": "库存模式下不主动探测连通性"
    }


@router.post("/{device_id}/test-connection", response_model=dict)
async def test_device_connection(
    device_id: int,
    payload: DeviceConnectionTestRequest,
    db: Session = Depends(get_db)
):
    """测试设备连接"""
    import asyncio

    device = db.query(Device).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")

    test_type = payload.type.lower()

    if test_type == "ping":
        try:
            proc = await asyncio.wait_for(
                asyncio.create_subprocess_exec(
                    "ping", "-c", "1", "-W", "2", device.ip_address,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                ),
                timeout=5.0
            )
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=5.0)
            if proc.returncode == 0:
                latency = None
                output = stdout.decode()
                if "time=" in output:
                    time_part = output.split("time=")[1].split()[0]
                    latency = float(time_part.replace("ms", ""))
                return {
                    "success": True,
                    "message": "Ping 测试成功",
                    "latency": latency,
                    "details": {"ip_address": device.ip_address}
                }
            return {
                "success": False,
                "message": stderr.decode().strip() or "Ping 测试失败",
                "details": {"ip_address": device.ip_address}
            }
        except Exception as e:
            return {"success": False, "message": f"Ping 测试失败: {str(e)}"}

    if test_type == "snmp":
        collector = SNMPCollector()
        try:
            value = collector.snmp_get(device, "1.3.6.1.2.1.1.1.0")
            if value is not None:
                return {
                    "success": True,
                    "message": "SNMP 连接成功",
                    "details": {"sysDescr": str(value)}
                }
            return {"success": False, "message": "SNMP 无响应，请检查团体字或版本配置"}
        except Exception as e:
            return {"success": False, "message": f"SNMP 测试失败: {str(e)}"}

    if test_type in {"gnmi", "ssh"}:
        port = device.gnmi_port if test_type == "gnmi" else device.ssh_port
        try:
            reader, writer = await asyncio.wait_for(
                asyncio.open_connection(device.ip_address, port),
                timeout=5.0
            )
            writer.close()
            await writer.wait_closed()
            return {
                "success": True,
                "message": f"{test_type.upper()} 端口连接成功",
                "details": {"ip_address": device.ip_address, "port": port}
            }
        except Exception as e:
            return {
                "success": False,
                "message": f"{test_type.upper()} 连接失败: {str(e)}",
                "details": {"ip_address": device.ip_address, "port": port}
            }

    raise HTTPException(status_code=400, detail="不支持的测试类型")


# ========== 设备分组管理 ==========

@router.get("/groups/list", response_model=List[DeviceGroupResponse])
async def list_device_groups(db: Session = Depends(get_db)):
    """获取设备分组列表"""
    groups = db.query(DeviceGroup).all()
    result = []
    for group in groups:
        device_count = db.query(Device).filter(Device.group_id == group.id).count()
        group_dict = {
            "id": group.id,
            "name": group.name,
            "description": group.description,
            "parent_id": group.parent_id,
            "created_at": group.created_at,
            "updated_at": group.updated_at,
            "device_count": device_count
        }
        result.append(group_dict)
    return result


@router.post("/groups", response_model=DeviceGroupResponse, status_code=status.HTTP_201_CREATED)
async def create_device_group(group: DeviceGroupCreate, db: Session = Depends(get_db)):
    """创建设备分组"""
    existing = db.query(DeviceGroup).filter(DeviceGroup.name == group.name).first()
    if existing:
        raise HTTPException(status_code=400, detail="分组名称已存在")
    
    db_group = DeviceGroup(
        name=group.name,
        description=group.description,
        parent_id=group.parent_id
    )
    db.add(db_group)
    db.commit()
    db.refresh(db_group)
    
    return {
        "id": db_group.id,
        "name": db_group.name,
        "description": db_group.description,
        "parent_id": db_group.parent_id,
        "created_at": db_group.created_at,
        "updated_at": db_group.updated_at,
        "device_count": 0
    }


@router.put("/groups/{group_id}", response_model=DeviceGroupResponse)
async def update_device_group(
    group_id: int, 
    group: DeviceGroupUpdate, 
    db: Session = Depends(get_db)
):
    """更新设备分组"""
    db_group = db.query(DeviceGroup).filter(DeviceGroup.id == group_id).first()
    if not db_group:
        raise HTTPException(status_code=404, detail="分组不存在")
    
    if group.name:
        existing = db.query(DeviceGroup).filter(
            DeviceGroup.name == group.name,
            DeviceGroup.id != group_id
        ).first()
        if existing:
            raise HTTPException(status_code=400, detail="分组名称已存在")
        db_group.name = group.name
    
    if group.description is not None:
        db_group.description = group.description
    if group.parent_id is not None:
        db_group.parent_id = group.parent_id
    
    db.commit()
    db.refresh(db_group)
    
    device_count = db.query(Device).filter(Device.group_id == group_id).count()
    return {
        "id": db_group.id,
        "name": db_group.name,
        "description": db_group.description,
        "parent_id": db_group.parent_id,
        "created_at": db_group.created_at,
        "updated_at": db_group.updated_at,
        "device_count": device_count
    }


@router.delete("/groups/{group_id}")
async def delete_device_group(group_id: int, db: Session = Depends(get_db)):
    """删除设备分组"""
    group = db.query(DeviceGroup).filter(DeviceGroup.id == group_id).first()
    if not group:
        raise HTTPException(status_code=404, detail="分组不存在")
    
    # 检查是否有设备在使用该分组
    device_count = db.query(Device).filter(Device.group_id == group_id).count()
    if device_count > 0:
        raise HTTPException(status_code=400, detail=f"该分组下有{device_count}个设备，无法删除")
    
    db.delete(group)
    db.commit()
    return {"message": "分组已删除"}
