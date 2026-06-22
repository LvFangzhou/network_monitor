"""
CMDB 资产管理路由
"""
from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlalchemy import func
from sqlalchemy.orm import Session
from typing import List, Optional
import csv
import io
from datetime import datetime
from sqlalchemy.exc import SQLAlchemyError

from app.database import get_db
from app.models import Device, DeviceGroup, Tag, Datacenter, DeviceType, DeviceRole, DeviceVendor
from app.schemas import DeviceCreate
from app.core import get_logger

logger = get_logger(__name__)
router = APIRouter()


REQUIRED_IMPORT_HEADERS = {'name', 'ip_address'}
DEVICE_CSV_HEADERS = [
    'name', 'status', 'ip_address', 'device_role', 'device_type', 'vendor', 'model', 'serial_number',
    'datacenter_name', 'datacenter_code', 'is_monitored'
]


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
            
            # 检查IP是否已存在
            existing = db.query(Device).filter(
                Device.ip_address == row['ip_address']
            ).first()
            if existing:
                failed += 1
                errors.append(f"第{row_num}行: IP地址 {row['ip_address']} 已存在")
                continue

            existing_name = db.query(Device).filter(Device.name == row['name']).first()
            if existing_name:
                failed += 1
                errors.append(f"第{row_num}行: 设备名称 {row['name']} 已存在")
                continue

            datacenter_id = find_existing_datacenter_id(
                db,
                row.get('datacenter_name'),
                row.get('datacenter_code'),
            )
            if (row.get('datacenter_name') or row.get('datacenter_code')) and datacenter_id is None:
                failed += 1
                datacenter_hint = row.get('datacenter_name') or row.get('datacenter_code')
                errors.append(f"第{row_num}行: 机房 {datacenter_hint} 不存在，请先在机房管理中创建")
                continue
            
            device_type_id, device_type_name = find_or_create_device_type(db, row.get('device_type', 'unknown'))
            device_role_name = ensure_device_role_catalog(db, row.get('device_role'))
            vendor_name = ensure_device_vendor_catalog(db, row.get('vendor'))

            with db.begin_nested():
                # 创建设备
                device = Device(
                    name=row['name'],
                    ip_address=row['ip_address'],
                    hostname=row.get('hostname'),
                    device_type=device_type_name or 'unknown',
                    device_type_id=device_type_id,
                    device_role=device_role_name,
                    vendor=vendor_name,
                    model=row.get('model'),
                    serial_number=row.get('serial_number'),
                    status=normalize_inventory_status(row.get('status')),
                    datacenter_id=datacenter_id,
                    group_id=group_id or row.get('group_id') or None,
                    is_monitored=normalize_is_monitored(row.get('is_monitored')),
                )

                # 处理标签
                if row.get('tags'):
                    tag_names = [t.strip() for t in row['tags'].split(',') if t.strip()]
                    for tag_name in tag_names:
                        tag = db.query(Tag).filter(Tag.name == tag_name).first()
                        if not tag:
                            tag = Tag(name=tag_name)
                            db.add(tag)
                            db.flush()
                        device.tags.append(tag)

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


def build_device_csv(devices: list[Device]) -> bytes:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(DEVICE_CSV_HEADERS)
    for device in devices:
        writer.writerow([
            device.name,
            normalize_inventory_status(device.status),
            device.ip_address,
            device.device_role,
            device.device_type,
            device.vendor,
            device.model,
            device.serial_number,
            device.datacenter_ref.name if device.datacenter_ref else None,
            device.datacenter_ref.code if device.datacenter_ref else None,
            '是' if device.is_monitored else '否',
        ])
    return output.getvalue().encode('utf-8-sig')


@router.get("/devices/template")
async def export_device_template():
    """下载设备导入模板；SNMP 团体字由系统后台默认配置，不出现在模板中。"""
    from fastapi.responses import StreamingResponse
    content = io.StringIO()
    writer = csv.writer(content)
    writer.writerow(DEVICE_CSV_HEADERS)
    writer.writerow(['示例交换机', 'in_stock', '192.0.2.10', '接入交换机', 'switch', 'H3C', '', '', '', '', '是'])
    return StreamingResponse(
        io.BytesIO(content.getvalue().encode('utf-8-sig')),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": "attachment; filename=device_import_template.csv"},
    )


@router.get("/devices/export")
async def export_devices(
    db: Session = Depends(get_db),
    group_id: Optional[int] = None,
    device_type: Optional[str] = None
):
    """导出设备（CSV格式）"""
    query = db.query(Device)

    if group_id:
        query = query.filter(Device.group_id == group_id)
    if device_type:
        query = query.filter(Device.device_type == device_type)
    
    devices = query.all()
    
    from fastapi.responses import StreamingResponse
    return StreamingResponse(
        io.BytesIO(build_device_csv(devices)),
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
