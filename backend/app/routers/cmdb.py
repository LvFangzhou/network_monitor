"""
CMDB 资产管理路由
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified
from typing import List, Optional
import csv
import io
import re
from copy import deepcopy
from datetime import datetime
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_db
from app.models import AlertHistory, Device, DeviceGroup, Tag, Datacenter, DeviceType, DeviceRole, DeviceVendor
from app.schemas import DeviceCreate
from app.core import get_logger
from app.utils.interface_scope import alert_target_interface_is_monitored

logger = get_logger(__name__)
router = APIRouter()


REQUIRED_IMPORT_HEADERS = {'name', 'ip_address'}
ACTIVE_ALERT_STATUSES = ("firing", "acknowledged", "ignored", "snoozed")
CSV_HEADER_ALIASES = {
    '设备名称': 'name',
    '名称': 'name',
    '管理地址': 'ip_address',
    'IP地址': 'ip_address',
    'ip': 'ip_address',
    '运行状态': 'status',
    '设备角色': 'device_role',
    '设备类型': 'device_type',
    '厂商': 'vendor',
    '型号': 'model',
    '序列号': 'serial_number',
    '机房名称': 'datacenter_name',
    '机房': 'datacenter_name',
    '机房编号': 'datacenter_code',
    '是否加入监控': 'is_monitored',
    '是否监控': 'is_monitored',
    '端口监控模式': 'interface_scope_mode',
    '端口监控范围': 'interface_scope_mode',
    '监控端口模式': 'interface_scope_mode',
    '只监控端口': 'interface_scope_include',
    '只监控指定端口': 'interface_scope_include',
    '监控指定端口': 'interface_scope_include',
    '指定监控端口': 'interface_scope_include',
    '排除端口': 'interface_scope_exclude',
    '排除指定端口': 'interface_scope_exclude',
    '不监控端口': 'interface_scope_exclude',
    '不监控这些端口': 'interface_scope_exclude',
    'SSH端口': 'ssh_port',
    'SSH用户名': 'ssh_username',
    'SSH账号': 'ssh_username',
    'SSH密码': 'ssh_password',
    'SSH私钥': 'ssh_key',
}
DEVICE_CSV_HEADERS = [
    'name', 'status', 'ip_address', 'device_role', 'device_type', 'vendor', 'model', 'serial_number',
    'datacenter_name', 'datacenter_code', 'is_monitored',
    'interface_scope_mode', 'interface_scope_include', 'interface_scope_exclude',
    'ssh_port', 'ssh_username', 'ssh_password', 'ssh_key'
]
DEVICE_EXPORT_CSV_HEADERS = [
    'name', 'status', 'ip_address', 'device_role', 'device_type', 'vendor', 'model', 'serial_number',
    'datacenter_name', 'datacenter_code', 'is_monitored',
    'interface_scope_mode', 'interface_scope_include', 'interface_scope_exclude'
]
DEVICE_EXPORT_FIELD_DEFINITIONS = {
    'name': ('name', lambda device, scope: device.name),
    'status': ('status', lambda device, scope: normalize_inventory_status(device.status)),
    'ip_address': ('ip_address', lambda device, scope: device.ip_address),
    'device_role': ('device_role', lambda device, scope: device.device_role),
    'device_type': ('device_type', lambda device, scope: device.device_type),
    'vendor': ('vendor', lambda device, scope: device.vendor),
    'model': ('model', lambda device, scope: device.model),
    'serial_number': ('serial_number', lambda device, scope: device.serial_number),
    'datacenter_name': ('datacenter_name', lambda device, scope: device.datacenter_ref.name if device.datacenter_ref else None),
    'datacenter_code': ('datacenter_code', lambda device, scope: device.datacenter_ref.code if device.datacenter_ref else None),
    'is_monitored': ('is_monitored', lambda device, scope: '是' if device.is_monitored else '否'),
    'interface_scope_mode': ('interface_scope_mode', lambda device, scope: scope["mode"]),
    'interface_scope_include': ('interface_scope_include', lambda device, scope: scope["include"]),
    'interface_scope_exclude': ('interface_scope_exclude', lambda device, scope: scope["exclude"]),
}


def decode_csv_content(content: bytes) -> str:
    """兼容常见 CSV 编码，避免导入时直接抛 500。"""
    for encoding in ('utf-8-sig', 'utf-8', 'gbk', 'gb2312'):
        try:
            return content.decode(encoding)
        except UnicodeDecodeError:
            continue
    raise HTTPException(status_code=400, detail="CSV 文件编码无法识别，请使用 UTF-8 或 GBK 编码后重试")


def normalize_csv_row(row: dict[str, Optional[str]]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in row.items():
        normalized_key = (key or '').strip().lstrip('\ufeff')
        if not normalized_key:
            continue
        normalized_key = CSV_HEADER_ALIASES.get(normalized_key, normalized_key)
        normalized[normalized_key] = (value or '').strip()
    return normalized


def normalize_inventory_status(raw_status: Optional[str]) -> str:
    value = (raw_status or 'in_stock').strip().lower()
    status_aliases = {
        '启用': 'active',
        '停用': 'inactive',
        '库存': 'in_stock',
        '上架': 'deployed',
        '在线': 'active',
        '离线': 'inactive',
        'active': 'active',
        'inactive': 'inactive',
        'in_stock': 'in_stock',
        'deployed': 'deployed',
        'online': 'active',
        'offline': 'inactive',
    }
    return status_aliases.get(value, 'in_stock')


def normalize_is_monitored(raw_value: Optional[str]) -> bool:
    value = (raw_value or '').strip().lower()
    if value in {'1', 'true', 'yes', 'y', '是', '加入', '已监控'}:
        return True
    if value in {'', '0', 'false', 'no', 'n', '否', '不加入', '未监控'}:
        return False
    raise ValueError("is_monitored 仅支持 是/否、true/false 或 1/0")


def csv_value(row: dict[str, str], key: str) -> Optional[str]:
    value = row.get(key)
    if value is None:
        return None
    value = value.strip()
    return value if value else None


def normalize_ssh_port(raw_value: Optional[str]) -> int:
    value = (raw_value or "").strip()
    if not value:
        return 22
    try:
        port = int(value)
    except ValueError as exc:
        raise ValueError("ssh_port 必须是 1-65535 的整数") from exc
    if port < 1 or port > 65535:
        raise ValueError("ssh_port 必须是 1-65535 的整数")
    return port


def normalize_interface_scope_mode(raw_value: Optional[str]) -> str:
    value = (raw_value or "all").strip().lower()
    aliases = {
        "": "all",
        "all": "all",
        "全部监控": "all",
        "全部": "all",
        "全部端口": "all",
        "监控全部端口": "all",
        "include": "include",
        "only": "include",
        "只监控": "include",
        "只监控端口": "include",
        "只监控指定端口": "include",
        "监控指定端口": "include",
        "指定监控端口": "include",
        "指定端口": "include",
        "exclude": "exclude",
        "except": "exclude",
        "排除": "exclude",
        "排除指定端口": "exclude",
        "排除端口": "exclude",
        "不监控端口": "exclude",
        "不监控这些端口": "exclude",
    }
    if value not in aliases:
        raise ValueError("interface_scope_mode 仅支持 all/include/exclude")
    return aliases[value]


def apply_interface_scope_from_csv(device: Device, row: dict[str, str]) -> None:
    if not any(key in row for key in ("interface_scope_mode", "interface_scope_include", "interface_scope_exclude")):
        return

    raw_mode = row.get("interface_scope_mode")
    include = csv_value(row, "interface_scope_include") or ""
    exclude = csv_value(row, "interface_scope_exclude") or ""

    # 运维导入时经常只填写“只监控端口”或“排除端口”其中一列；
    # 未显式填写模式时，根据有内容的列自动推断，避免静默导成“全部端口”。
    if raw_mode in {None, ""}:
        raw_mode = "include" if include else "exclude" if exclude else "all"

    mode = normalize_interface_scope_mode(raw_mode)
    if mode == "include" and not include:
        raise ValueError("interface_scope_mode 为 include 时，interface_scope_include 不能为空")
    if mode == "exclude" and not exclude:
        raise ValueError("interface_scope_mode 为 exclude 时，interface_scope_exclude 不能为空")

    custom_fields = deepcopy(device.custom_fields or {})
    monitoring = custom_fields.get("monitoring")
    if not isinstance(monitoring, dict):
        monitoring = {}
    monitoring["interface_scope"] = {
        "mode": mode,
        "include": include if mode == "include" else "",
        "exclude": exclude if mode == "exclude" else "",
    }
    custom_fields["monitoring"] = monitoring
    device.custom_fields = custom_fields
    flag_modified(device, "custom_fields")


def resolve_active_interface_alerts_outside_scope(db: Session, device: Device) -> int:
    """导入时端口监控范围变化后，自动恢复范围外仍在触发中的接口类告警。"""
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
        alert.resolution_note = "导入端口监控范围后，范围外接口系统自动恢复活动告警"
        alert.updated_at = now
        resolved_count += 1

    return resolved_count


def get_device_interface_scope(device: Device) -> dict[str, str]:
    custom_fields = device.custom_fields or {}
    monitoring = custom_fields.get("monitoring") if isinstance(custom_fields, dict) else {}
    scope = monitoring.get("interface_scope") if isinstance(monitoring, dict) else {}
    if not isinstance(scope, dict):
        scope = {}
    return {
        "mode": str(scope.get("mode") or "all"),
        "include": str(scope.get("include") or scope.get("include_patterns") or ""),
        "exclude": str(scope.get("exclude") or scope.get("exclude_patterns") or ""),
    }


def apply_device_import_row(device: Device, row: dict[str, str], db: Session, group_id: Optional[int] = None) -> None:
    """将 CSV 行应用到新建或已有设备；空字段不覆盖已有非空值。"""
    if csv_value(row, 'name'):
        device.name = csv_value(row, 'name')
    if csv_value(row, 'ip_address'):
        device.ip_address = csv_value(row, 'ip_address')
    if csv_value(row, 'hostname'):
        device.hostname = csv_value(row, 'hostname')
    if csv_value(row, 'status'):
        device.status = normalize_inventory_status(row.get('status'))
    if csv_value(row, 'model'):
        device.model = csv_value(row, 'model')
    if csv_value(row, 'serial_number'):
        device.serial_number = csv_value(row, 'serial_number')

    if csv_value(row, 'device_type'):
        device_type_id, device_type_name = find_or_create_device_type(db, row.get('device_type'))
        device.device_type_id = device_type_id
        device.device_type = device_type_name or device.device_type or 'unknown'
    elif not device.device_type:
        device.device_type = 'unknown'

    if csv_value(row, 'device_role'):
        device.device_role = ensure_device_role_catalog(db, row.get('device_role'))
    if csv_value(row, 'vendor'):
        device.vendor = ensure_device_vendor_catalog(db, row.get('vendor'))

    if row.get('datacenter_name') or row.get('datacenter_code'):
        datacenter_id = find_existing_datacenter_id(db, row.get('datacenter_name'), row.get('datacenter_code'))
        if datacenter_id is None:
            datacenter_hint = row.get('datacenter_name') or row.get('datacenter_code')
            raise ValueError(f"机房 {datacenter_hint} 不存在，请先在机房管理中创建")
        device.datacenter_id = datacenter_id

    if row.get('is_monitored') not in {None, ''}:
        device.is_monitored = normalize_is_monitored(row.get('is_monitored'))

    if group_id or csv_value(row, 'group_id'):
        device.group_id = group_id or row.get('group_id') or None

    if row.get('ssh_port') not in {None, ''}:
        device.ssh_port = normalize_ssh_port(row.get('ssh_port'))
    elif not device.ssh_port:
        device.ssh_port = 22
    if 'ssh_username' in row and csv_value(row, 'ssh_username') is not None:
        device.ssh_username = csv_value(row, 'ssh_username')
    if 'ssh_password' in row and csv_value(row, 'ssh_password') is not None:
        device.ssh_password = csv_value(row, 'ssh_password')
    if 'ssh_key' in row and csv_value(row, 'ssh_key') is not None:
        device.ssh_key = csv_value(row, 'ssh_key')

    apply_interface_scope_from_csv(device, row)


def find_or_create_datacenter(
    db: Session,
    datacenter_name: Optional[str],
    datacenter_code: Optional[str],
) -> Optional[int]:
    name = (datacenter_name or "").strip()
    code = (datacenter_code or "").strip()
    if not name and not code:
        return None

    datacenter = None
    if code:
        datacenter = db.query(Datacenter).filter(Datacenter.code == code).first()
    if not datacenter and name:
        datacenter = db.query(Datacenter).filter(Datacenter.name == name).first()
    if not datacenter:
        datacenter = Datacenter(
            code=code or None,
            name=name or code,
            is_active=True,
        )
        db.add(datacenter)
        db.flush()
    elif code and not datacenter.code:
        datacenter.code = code
        db.flush()
    return datacenter.id


def find_existing_datacenter_id(
    db: Session,
    datacenter_name: Optional[str],
    datacenter_code: Optional[str],
) -> Optional[int]:
    """导入场景下只校验机房是否存在，不自动创建。"""
    name = (datacenter_name or "").strip()
    code = (datacenter_code or "").strip()
    if not name and not code:
        return None

    datacenter = None
    if code:
        datacenter = db.query(Datacenter).filter(Datacenter.code == code).first()
    if not datacenter and name:
        datacenter = db.query(Datacenter).filter(Datacenter.name == name).first()
    return datacenter.id if datacenter else None


def find_or_create_device_type(
    db: Session,
    raw_name: Optional[str],
) -> tuple[Optional[int], Optional[str]]:
    name = (raw_name or "").strip()
    if not name:
        return None, None

    device_type = (
        db.query(DeviceType)
        .filter(
            (func.lower(DeviceType.name) == name.lower()) |
            (func.lower(DeviceType.display_name) == name.lower())
        )
        .first()
    )
    if not device_type:
        device_type = DeviceType(name=name, display_name=name, is_active=True)
        db.add(device_type)
        db.flush()
    return device_type.id, device_type.name


def ensure_device_role_catalog(db: Session, raw_name: Optional[str]) -> Optional[str]:
    name = (raw_name or "").strip()
    if not name:
        return None

    device_role = db.query(DeviceRole).filter(func.lower(DeviceRole.name) == name.lower()).first()
    if not device_role:
        device_role = DeviceRole(name=name, display_name=name, is_active=True)
        db.add(device_role)
        db.flush()
    return device_role.name


def ensure_device_vendor_catalog(db: Session, raw_name: Optional[str]) -> Optional[str]:
    name = (raw_name or "").strip()
    if not name:
        return None

    device_vendor = db.query(DeviceVendor).filter(func.lower(DeviceVendor.name) == name.lower()).first()
    if not device_vendor:
        device_vendor = DeviceVendor(name=name, display_name=name, is_active=True)
        db.add(device_vendor)
        db.flush()
    return device_vendor.name


@router.post("/devices/import")
async def import_devices(
    file: UploadFile = File(...),
    group_id: Optional[int] = None,
    db: Session = Depends(get_db)
):
    """批量导入设备（CSV格式）"""
    filename = (file.filename or '').lower()
    if not filename.endswith('.csv'):
        raise HTTPException(status_code=400, detail="只支持CSV文件")
    
    content = await file.read()
    csv_text = decode_csv_content(content)
    csv_file = io.StringIO(csv_text)
    reader = csv.DictReader(csv_file)

    if not reader.fieldnames:
        raise HTTPException(status_code=400, detail="CSV 文件缺少表头")

    normalized_headers = {(header or '').strip().lstrip('\ufeff') for header in reader.fieldnames if header}
    missing_headers = REQUIRED_IMPORT_HEADERS - normalized_headers
    if missing_headers:
        raise HTTPException(
            status_code=400,
            detail=f"CSV 表头缺少必填字段: {', '.join(sorted(missing_headers))}"
        )
    
    imported = 0
    failed = 0
    errors = []
    seen_ips: set[str] = set()
    seen_names: set[str] = set()
    
    for row_num, row in enumerate(reader, start=2):
        try:
            row = normalize_csv_row(row)

            # 检查必填字段
            if not row.get('name') or not row.get('ip_address'):
                failed += 1
                errors.append(f"第{row_num}行: 缺少必填字段(name或ip_address)")
                continue

            if row['ip_address'] in seen_ips:
                failed += 1
                errors.append(f"第{row_num}行: IP地址 {row['ip_address']} 在导入文件中重复")
                continue

            if row['name'] in seen_names:
                failed += 1
                errors.append(f"第{row_num}行: 设备名称 {row['name']} 在导入文件中重复")
                continue
            
            existing = db.query(Device).filter(Device.ip_address == row['ip_address']).first()
            existing_name = db.query(Device).filter(Device.name == row['name']).first()
            if existing_name and (not existing or existing_name.id != existing.id):
                failed += 1
                errors.append(f"第{row_num}行: 设备名称 {row['name']} 已被其他设备使用")
                continue

            with db.begin_nested():
                device = existing or Device(
                    name=row['name'],
                    ip_address=row['ip_address'],
                    device_type='unknown',
                    status='in_stock',
                    ssh_port=22,
                )
                apply_device_import_row(device, row, db, group_id)
                db.flush()
                resolve_active_interface_alerts_outside_scope(db, device)

                # 处理标签
                if row.get('tags'):
                    device.tags = []
                    tag_names = [t.strip() for t in row['tags'].split(',') if t.strip()]
                    for tag_name in tag_names:
                        tag = db.query(Tag).filter(Tag.name == tag_name).first()
                        if not tag:
                            tag = Tag(name=tag_name)
                            db.add(tag)
                            db.flush()
                        device.tags.append(tag)

                if not existing:
                    db.add(device)
                db.flush()

            imported += 1
            seen_ips.add(row['ip_address'])
            seen_names.add(row['name'])

        except HTTPException:
            raise
        except SQLAlchemyError as e:
            db.expire_all()
            failed += 1
            errors.append(f"第{row_num}行: 数据库写入失败 - {str(e)}")
        except Exception as e:
            failed += 1
            errors.append(f"第{row_num}行: {str(e)}")
    
    try:
        db.commit()
    except SQLAlchemyError as e:
        db.rollback()
        logger.exception("设备导入提交失败")
        raise HTTPException(status_code=500, detail=f"导入提交失败: {str(e)}") from e
    
    logger.info("设备导入完成", imported=imported, failed=failed)
    return {
        "imported": imported,
        "failed": failed,
        "errors": errors[:10]  # 只返回前10个错误
    }


def normalize_export_fields(fields: Optional[List[str]]) -> list[str]:
    if not fields:
        return DEVICE_EXPORT_CSV_HEADERS

    normalized: list[str] = []
    invalid_fields: list[str] = []
    for raw_field in fields:
        for field in str(raw_field or "").split(","):
            field = field.strip()
            if not field:
                continue
            if field not in DEVICE_EXPORT_FIELD_DEFINITIONS:
                invalid_fields.append(field)
                continue
            if field not in normalized:
                normalized.append(field)

    if invalid_fields:
        raise HTTPException(status_code=400, detail=f"不支持导出字段: {', '.join(invalid_fields)}")
    if not normalized:
        raise HTTPException(status_code=400, detail="至少选择一个导出字段")
    return normalized


def apply_device_export_filters(
    query,
    *,
    group_id: Optional[int] = None,
    status: Optional[str] = None,
    device_type: Optional[str] = None,
    device_type_id: Optional[int] = None,
    device_role: Optional[str] = None,
    vendor: Optional[str] = None,
    datacenter_id: Optional[int] = None,
    is_monitored: Optional[bool] = None,
    search: Optional[str] = None,
    search_mode: str = "fuzzy",
    name_text: Optional[str] = None,
    ip_address_text: Optional[str] = None,
    status_text: Optional[str] = None,
    monitored_text: Optional[str] = None,
    datacenter_text: Optional[str] = None,
    model_text: Optional[str] = None,
    device_type_text: Optional[str] = None,
    serial_number_text: Optional[str] = None,
):
    """导出接口复用网络设备列表的主要筛选语义，确保“所见即所导”。"""
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
                Device.name.ilike(f"%{search}%") |
                Device.ip_address.ilike(f"%{search}%") |
                Device.hostname.ilike(f"%{search}%") |
                Device.serial_number.ilike(f"%{search}%") |
                Device.model.ilike(f"%{search}%") |
                Device.vendor.ilike(f"%{search}%") |
                Device.device_role.ilike(f"%{search}%") |
                Device.device_type.ilike(f"%{search}%") |
                Device.description.ilike(f"%{search}%") |
                Device.location.ilike(f"%{search}%") |
                Device.datacenter_ref.has(
                    Datacenter.name.ilike(f"%{search}%") |
                    Datacenter.code.ilike(f"%{search}%") |
                    Datacenter.location.ilike(f"%{search}%")
                )
            )

    return query


def build_device_csv(devices: list[Device], fields: Optional[List[str]] = None) -> bytes:
    export_fields = normalize_export_fields(fields)
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow([DEVICE_EXPORT_FIELD_DEFINITIONS[field][0] for field in export_fields])
    for device in devices:
        interface_scope = get_device_interface_scope(device)
        writer.writerow([
            DEVICE_EXPORT_FIELD_DEFINITIONS[field][1](device, interface_scope)
            for field in export_fields
        ])
    return output.getvalue().encode('utf-8-sig')


@router.get("/devices/template")
async def export_device_template():
    """下载设备导入模板；包含 SSH 字段，便于批量补充配置备份账号。"""
    from fastapi.responses import StreamingResponse
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(DEVICE_CSV_HEADERS)
    writer.writerow([
        '示例交换机', 'in_stock', '192.0.2.10', '接入交换机', 'switch', 'H3C', '', '', '', '', '是',
        'include', '400G1/0/1-400G1/0/64', '',
        '22', 'backup', '', ''
    ])
    return StreamingResponse(
        io.BytesIO(content.getvalue().encode('utf-8-sig')),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=device_import_template.csv"},
    )


@router.get("/devices/export")
async def export_devices(
    db: Session = Depends(get_db),
    group_id: Optional[int] = None,
    status: Optional[str] = None,
    device_type: Optional[str] = None,
    device_type_id: Optional[int] = None,
    device_role: Optional[str] = None,
    vendor: Optional[str] = None,
    datacenter_id: Optional[int] = None,
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
    fields: Optional[List[str]] = Query(None),
):
    """导出设备（CSV格式）"""
    query = db.query(Device)

    query = apply_device_export_filters(
        query,
        group_id=group_id,
        status=status,
        device_type=device_type,
        device_type_id=device_type_id,
        device_role=device_role,
        vendor=vendor,
        datacenter_id=datacenter_id,
        is_monitored=is_monitored,
        search=search,
        search_mode=search_mode,
        name_text=name_text,
        ip_address_text=ip_address_text,
        status_text=status_text,
        monitored_text=monitored_text,
        datacenter_text=datacenter_text,
        model_text=model_text,
        device_type_text=device_type_text,
        serial_number_text=serial_number_text,
    )
    
    devices = query.all()
    
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        io.BytesIO(build_device_csv(devices, fields)),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=devices.csv"}
    )


@router.get("/device-types")
async def get_device_types():
    """获取支持的设备类型列表"""
    return {
        "types": [
            {"value": "router", "label": "路由器"},
            {"value": "switch", "label": "交换机"},
            {"value": "firewall", "label": "防火墙"},
            {"value": "server", "label": "服务器"},
            {"value": "load_balancer", "label": "负载均衡"},
            {"value": "wireless_ap", "label": "无线AP"},
            {"value": "storage", "label": "存储设备"},
            {"value": "unknown", "label": "未知"}
        ]
    }


@router.get("/vendors")
async def get_vendors(db: Session = Depends(get_db)):
    """获取设备厂商列表"""
    vendors = db.query(Device.vendor).distinct().all()
    return {
        "vendors": [v[0] for v in vendors if v[0]]
    }


@router.get("/locations")
async def get_locations(db: Session = Depends(get_db)):
    """获取设备位置列表"""
    locations = db.query(Device.location).distinct().all()
    return {
        "locations": [l[0] for l in locations if l[0]]
    }


@router.post("/devices/discover")
async def discover_devices(
    ip_range: str = Query(..., description="IP范围，如: 192.168.1.1-192.168.1.254"),
    snmp_community: Optional[str] = None,
    snmp_version: str = "v2c"
):
    """设备发现（通过SNMP扫描）"""
    # TODO: 实现SNMP扫描发现设备
    return {
        "message": "设备发现功能待实现",
        "ip_range": ip_range,
        "discovered": []
    }


@router.get("/summary")
async def get_cmdb_summary(db: Session = Depends(get_db)):
    """获取CMDB概览统计"""
    total_devices = db.query(Device).count()
    
    # 按类型统计
    type_stats = {}
    for device_type in db.query(Device.device_type).distinct():
        count = db.query(Device).filter(Device.device_type == device_type[0]).count()
        type_stats[device_type[0]] = count
    
    # 按状态统计
    status_stats = {
        "online": db.query(Device).filter(Device.status == "online").count(),
        "offline": db.query(Device).filter(Device.status == "offline").count(),
        "unknown": db.query(Device).filter(Device.status == "unknown").count()
    }
    
    # 按分组统计
    group_stats = []
    groups = db.query(DeviceGroup).all()
    for group in groups:
        count = db.query(Device).filter(Device.group_id == group.id).count()
        group_stats.append({
            "id": group.id,
            "name": group.name,
            "device_count": count
        })
    
    return {
        "total_devices": total_devices,
        "by_type": type_stats,
        "by_status": status_stats,
        "by_group": group_stats
    }
