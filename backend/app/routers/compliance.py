"""型号、版本基线与设备上线合规接口。"""
from collections import Counter
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, or_
from sqlalchemy.orm import Session, joinedload

from app.database import get_db
from app.models import (
    Device, DeviceComplianceSnapshot, DeviceModelProfile, SyslogEvent, User, VersionBaseline,
)
from app.routers.auth import get_current_active_user
from app.schemas.compliance import (
    ModelProfilePayload, ModelProfileUpdate, VersionBaselinePayload, VersionBaselineUpdate,
)
from app.utils.device_compliance import (
    canonical_model_name, evaluate_device, load_tacacs_device_ips, persist_snapshot,
)


router = APIRouter()


def _require_write(user: User):
    if user.read_only:
        raise HTTPException(status_code=403, detail="只读用户不能修改上线合规配置")


def _apply_updates(instance, payload):
    values = payload.model_dump(exclude_unset=True)
    for key, value in values.items():
        setattr(instance, key, value)
    if isinstance(instance, DeviceModelProfile) and (
        "model_pattern" in values or "vendor" in values or "name" in values
    ):
        instance.name = canonical_model_name(instance.model_pattern, instance.vendor) or instance.name


@router.get("/model-profiles")
async def list_model_profiles(
    active_only: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    query = db.query(DeviceModelProfile)
    if active_only:
        query = query.filter(DeviceModelProfile.is_active.is_(True))
    items = query.order_by(DeviceModelProfile.priority, DeviceModelProfile.vendor, DeviceModelProfile.model_pattern).all()
    return {"total": len(items), "items": [item.to_dict() for item in items]}


@router.post("/model-profiles", status_code=status.HTTP_201_CREATED)
async def create_model_profile(
    payload: ModelProfilePayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require_write(user)
    duplicate = db.query(DeviceModelProfile).filter(
        func.lower(DeviceModelProfile.vendor) == payload.vendor.lower(),
        func.lower(DeviceModelProfile.model_pattern) == payload.model_pattern.lower(),
        func.lower(DeviceModelProfile.network_type) == payload.network_type.lower(),
    ).first()
    if duplicate:
        raise HTTPException(status_code=409, detail="相同厂商、型号匹配和网络类型的模板已存在")
    values = payload.model_dump()
    values["name"] = canonical_model_name(values["model_pattern"], values["vendor"]) or values["name"]
    item = DeviceModelProfile(**values)
    db.add(item)
    db.commit()
    db.refresh(item)
    return item.to_dict()


def _inferred_profile(device: Device):
    vendor = str(device.vendor or "").strip()
    model = str(device.model or "").strip()
    normalized_vendor = vendor.lower()
    normalized_model = model.lower()
    asternos = any(marker in normalized_vendor for marker in ("asternos", "asterfusion", "asteros", "星融元"))
    hillstone = any(marker in normalized_vendor for marker in ("hillstone", "山石"))
    is_s9867 = "s9867" in normalized_model
    capabilities = {
        "snmp": not asternos,
        "syslog": True,
        "tacacs": not asternos,
        "telemetry": "h3c" in normalized_vendor,
        "bmp": False,
        "nqa": "h3c" in normalized_vendor,
        "evpn_vxlan": False,
        "roce": is_s9867,
        "pfc": is_s9867,
        "ecn": is_s9867,
        "buffer": is_s9867,
        "config_backup": not asternos,
    }
    return {
        "name": canonical_model_name(model, vendor) or model,
        "vendor": vendor,
        "model_pattern": model,
        "network_type": "roce" if is_s9867 else ("firewall" if hillstone else "general"),
        "device_type": device.device_type,
        "default_role": device.device_role,
        "capabilities": capabilities,
        "required_checks": ["model_profile", "version", "snmp", "syslog", "tacacs"],
        "description": "根据现有CMDB设备自动生成，请核对网络类型、能力和必检项。",
        "priority": 100,
        "is_active": True,
    }


@router.post("/model-profiles/discover")
async def discover_model_profiles(
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    """按当前CMDB中的厂商/型号生成初始能力模板，不覆盖人工配置。"""
    _require_write(user)
    devices = db.query(Device).filter(
        Device.vendor.isnot(None),
        Device.model.isnot(None),
    ).order_by(Device.vendor, Device.model, Device.id).all()
    existing = {
        (str(item.vendor or "").strip().lower(), str(item.model_pattern or "").strip().lower())
        for item in db.query(DeviceModelProfile).all()
    }
    created = []
    for device in devices:
        key = (str(device.vendor or "").strip().lower(), str(device.model or "").strip().lower())
        if not all(key) or key in existing:
            continue
        item = DeviceModelProfile(**_inferred_profile(device))
        db.add(item)
        db.flush()
        created.append(item.to_dict())
        existing.add(key)
    db.commit()
    return {"created": len(created), "skipped": len(devices) - len(created), "items": created}


@router.put("/model-profiles/{profile_id}")
async def update_model_profile(
    profile_id: int,
    payload: ModelProfileUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require_write(user)
    item = db.query(DeviceModelProfile).filter(DeviceModelProfile.id == profile_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="型号模板不存在")
    _apply_updates(item, payload)
    db.commit()
    db.refresh(item)
    return item.to_dict()


@router.delete("/model-profiles/{profile_id}")
async def delete_model_profile(
    profile_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require_write(user)
    item = db.query(DeviceModelProfile).filter(DeviceModelProfile.id == profile_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="型号模板不存在")
    db.delete(item)
    db.commit()
    return {"success": True}


@router.get("/version-baselines")
async def list_version_baselines(
    active_only: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    query = db.query(VersionBaseline)
    if active_only:
        query = query.filter(VersionBaseline.is_active.is_(True))
    items = query.order_by(VersionBaseline.priority, VersionBaseline.vendor, VersionBaseline.model_pattern).all()
    return {"total": len(items), "items": [item.to_dict() for item in items]}


@router.post("/version-baselines", status_code=status.HTTP_201_CREATED)
async def create_version_baseline(
    payload: VersionBaselinePayload,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require_write(user)
    if payload.model_profile_id and not db.query(DeviceModelProfile).filter(
        DeviceModelProfile.id == payload.model_profile_id
    ).first():
        raise HTTPException(status_code=400, detail="关联的型号模板不存在")
    item = VersionBaseline(**payload.model_dump())
    db.add(item)
    db.commit()
    db.refresh(item)
    return item.to_dict()


@router.put("/version-baselines/{baseline_id}")
async def update_version_baseline(
    baseline_id: int,
    payload: VersionBaselineUpdate,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require_write(user)
    item = db.query(VersionBaseline).filter(VersionBaseline.id == baseline_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="版本基线不存在")
    _apply_updates(item, payload)
    db.commit()
    db.refresh(item)
    return item.to_dict()


@router.delete("/version-baselines/{baseline_id}")
async def delete_version_baseline(
    baseline_id: int,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require_write(user)
    item = db.query(VersionBaseline).filter(VersionBaseline.id == baseline_id).first()
    if not item:
        raise HTTPException(status_code=404, detail="版本基线不存在")
    db.delete(item)
    db.commit()
    return {"success": True}


def _evaluation_context(db: Session):
    profiles = db.query(DeviceModelProfile).filter(DeviceModelProfile.is_active.is_(True)).all()
    baselines = db.query(VersionBaseline).filter(VersionBaseline.is_active.is_(True)).all()
    latest_by_device = {
        device_id: latest_at
        for device_id, latest_at in db.query(
            SyslogEvent.device_id, func.max(SyslogEvent.created_at)
        ).filter(SyslogEvent.device_id.isnot(None)).group_by(SyslogEvent.device_id).all()
    }
    latest_by_ip = {
        source_ip: latest_at
        for source_ip, latest_at in db.query(
            SyslogEvent.source_ip, func.max(SyslogEvent.created_at)
        ).filter(SyslogEvent.source_ip.isnot(None)).group_by(SyslogEvent.source_ip).all()
    }
    return profiles, baselines, latest_by_device, latest_by_ip, load_tacacs_device_ips()


def _evaluate_and_save(db: Session, devices: list[Device]):
    profiles, baselines, latest_by_device, latest_by_ip, tacacs_ips = _evaluation_context(db)
    results = []
    for device in devices:
        latest_syslog = latest_by_device.get(device.id) or latest_by_ip.get(device.ip_address)
        result = evaluate_device(device, profiles, baselines, latest_syslog, tacacs_ips)
        persist_snapshot(db, result)
        results.append(result)
    db.commit()
    return results


@router.post("/evaluate")
async def evaluate_compliance(
    device_id: Optional[int] = None,
    db: Session = Depends(get_db),
    user: User = Depends(get_current_active_user),
):
    _require_write(user)
    query = db.query(Device).options(joinedload(Device.datacenter_ref))
    if device_id is not None:
        query = query.filter(Device.id == device_id)
    devices = query.order_by(Device.id).all()
    if device_id is not None and not devices:
        raise HTTPException(status_code=404, detail="设备不存在")
    results = _evaluate_and_save(db, devices)
    counts = Counter(item["overall_status"] for item in results)
    return {"total": len(results), "counts": dict(counts), "items": [_result_payload(item, devices[index]) for index, item in enumerate(results)]}


def _result_payload(result, device: Device):
    return {
        **{key: (value.isoformat() if key == "evaluated_at" else value) for key, value in result.items()},
        "device": {
            "id": device.id,
            "name": device.name,
            "ip_address": device.ip_address,
            "vendor": device.vendor,
            "model": device.model,
            "device_role": device.device_role,
            "is_monitored": bool(device.is_monitored),
            "datacenter": device.datacenter_ref.to_dict() if device.datacenter_ref else None,
        },
    }


@router.get("/devices")
async def list_device_compliance(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=500),
    overall_status: Optional[str] = None,
    vendor: Optional[str] = None,
    search: Optional[str] = None,
    refresh: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    if refresh or db.query(DeviceComplianceSnapshot).count() == 0:
        _evaluate_and_save(db, db.query(Device).options(joinedload(Device.datacenter_ref)).all())
    else:
        evaluated_ids = {
            device_id for (device_id,) in db.query(DeviceComplianceSnapshot.device_id).all()
        }
        missing_query = db.query(Device).options(joinedload(Device.datacenter_ref))
        if evaluated_ids:
            missing_query = missing_query.filter(Device.id.notin_(evaluated_ids))
        missing_devices = missing_query.all()
        if missing_devices:
            _evaluate_and_save(db, missing_devices)

    query = db.query(DeviceComplianceSnapshot, Device).join(
        Device, Device.id == DeviceComplianceSnapshot.device_id
    ).options(joinedload(Device.datacenter_ref))
    if overall_status:
        query = query.filter(DeviceComplianceSnapshot.overall_status == overall_status)
    if vendor:
        query = query.filter(Device.vendor.ilike(f"%{vendor.strip()}%"))
    if search:
        keyword = f"%{search.strip()}%"
        query = query.filter(or_(
            Device.name.ilike(keyword),
            Device.ip_address.ilike(keyword),
            Device.model.ilike(keyword),
        ))
    total = query.count()
    rows = query.order_by(DeviceComplianceSnapshot.score.asc(), Device.name.asc()).offset(skip).limit(limit).all()
    items = []
    for snapshot, device in rows:
        payload = snapshot.to_dict()
        payload["device"] = {
            "id": device.id,
            "name": device.name,
            "ip_address": device.ip_address,
            "vendor": device.vendor,
            "model": device.model,
            "device_role": device.device_role,
            "is_monitored": bool(device.is_monitored),
            "datacenter": device.datacenter_ref.to_dict() if device.datacenter_ref else None,
        }
        payload["profile"] = db.query(DeviceModelProfile).filter(DeviceModelProfile.id == snapshot.model_profile_id).first().to_dict() if snapshot.model_profile_id else None
        payload["baseline"] = db.query(VersionBaseline).filter(VersionBaseline.id == snapshot.version_baseline_id).first().to_dict() if snapshot.version_baseline_id else None
        items.append(payload)
    return {"total": total, "items": items}


@router.get("/devices/{device_id}")
async def get_device_compliance(
    device_id: int,
    refresh: bool = False,
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    device = db.query(Device).options(joinedload(Device.datacenter_ref)).filter(Device.id == device_id).first()
    if not device:
        raise HTTPException(status_code=404, detail="设备不存在")
    snapshot = db.query(DeviceComplianceSnapshot).filter(DeviceComplianceSnapshot.device_id == device_id).first()
    if refresh or not snapshot:
        result = _evaluate_and_save(db, [device])[0]
        return _result_payload(result, device)
    payload = snapshot.to_dict()
    payload["device"] = {
        "id": device.id, "name": device.name, "ip_address": device.ip_address,
        "vendor": device.vendor, "model": device.model, "device_role": device.device_role,
        "is_monitored": bool(device.is_monitored),
        "datacenter": device.datacenter_ref.to_dict() if device.datacenter_ref else None,
    }
    payload["profile"] = db.query(DeviceModelProfile).filter(DeviceModelProfile.id == snapshot.model_profile_id).first().to_dict() if snapshot.model_profile_id else None
    payload["baseline"] = db.query(VersionBaseline).filter(VersionBaseline.id == snapshot.version_baseline_id).first().to_dict() if snapshot.version_baseline_id else None
    return payload


@router.get("/summary")
async def compliance_summary(
    db: Session = Depends(get_db),
    _: User = Depends(get_current_active_user),
):
    total_devices = db.query(Device).count()
    rows = db.query(
        DeviceComplianceSnapshot.overall_status,
        func.count(DeviceComplianceSnapshot.id),
    ).group_by(DeviceComplianceSnapshot.overall_status).all()
    counts = {status_value: count for status_value, count in rows}
    evaluated = sum(counts.values())
    return {
        "total": total_devices,
        "evaluated": evaluated,
        "unevaluated": max(0, total_devices - evaluated),
        "counts": counts,
        "compliance_rate": round(counts.get("compliant", 0) * 100 / total_devices, 2) if total_devices else 0,
    }
