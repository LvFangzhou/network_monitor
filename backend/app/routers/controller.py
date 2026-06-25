"""
控制器/分析器集成接口。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Device, User
from app.routers.auth import get_current_active_user
from app.utils.controller_client import ControllerClient
from app.utils.controller_settings import (
    find_controller_settings,
    load_controller_settings,
    merge_runtime_settings,
    save_controller_settings,
)

router = APIRouter()


def _require_admin(user: User) -> None:
    if not user.is_superuser:
        raise HTTPException(status_code=403, detail="仅管理员可修改控制器集成配置")


def _client_from_saved(controller_id: Optional[str] = None) -> ControllerClient:
    try:
        settings = find_controller_settings(controller_id=controller_id, require_enabled=True)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return ControllerClient(settings)


@router.get("/settings", response_model=dict)
async def get_controller_settings(current_user: User = Depends(get_current_active_user)):
    _require_admin(current_user)
    return load_controller_settings(mask_secret=True)


@router.get("/options", response_model=dict)
async def get_controller_options(current_user: User = Depends(get_current_active_user)):
    settings = load_controller_settings(mask_secret=True)
    controllers = [
        {
            "id": item.get("id"),
            "name": item.get("name") or item.get("base_url"),
            "base_url": item.get("base_url"),
            "enabled": bool(item.get("enabled")),
        }
        for item in settings.get("controllers") or []
        if item.get("enabled")
    ]
    return {"items": controllers}


@router.put("/settings", response_model=dict)
async def update_controller_settings(
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    return save_controller_settings(payload)


@router.post("/test", response_model=dict)
async def test_controller_connectivity(
    payload: Optional[Dict[str, Any]] = None,
    current_user: User = Depends(get_current_active_user),
):
    _require_admin(current_user)
    try:
        settings = merge_runtime_settings(payload or {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    client = ControllerClient(settings)
    return await client.check()


@router.get("/assets", response_model=dict)
async def list_controller_assets(
    current_user: User = Depends(get_current_active_user),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None),
    controller_id: Optional[str] = Query(None),
):
    client = _client_from_saved(controller_id=controller_id)
    return await client.list_assets(page_num=page, page_size=page_size, filter_text=search)


@router.get("/opticals", response_model=dict)
async def list_controller_opticals(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None),
    device_ip: Optional[str] = Query(None),
    interface_name: Optional[str] = Query(None),
    vendor_name: Optional[str] = Query(None),
    level: int = Query(0, ge=0, le=4),
    hours: int = Query(3, ge=1, le=168),
    controller_id: Optional[str] = Query(None),
):
    client = _client_from_saved(controller_id=controller_id)
    result = await client.list_opticals(
        page=page,
        page_size=page_size,
        search=search,
        device_ip=device_ip,
        interface_name=interface_name,
        vendor_name=vendor_name,
        level=level,
        hours=hours,
    )
    _attach_device_datacenter(db, result.get("items") or [])
    return result


def _attach_device_datacenter(db: Session, items: list[Dict[str, Any]]) -> None:
    ips = {
        str(item.get("deviceIp") or item.get("ip") or "").strip()
        for item in items
        if str(item.get("deviceIp") or item.get("ip") or "").strip()
    }
    if not ips:
        return
    devices = db.query(Device).filter(Device.ip_address.in_(ips)).all()
    mapping = {item.ip_address: item for item in devices}
    for item in items:
        ip = str(item.get("deviceIp") or item.get("ip") or "").strip()
        device = mapping.get(ip)
        if not device:
            continue
        datacenter = device.datacenter_ref
        if not item.get("datacenterName") and datacenter:
            item["datacenterName"] = datacenter.name
        if datacenter:
            item["datacenterCode"] = datacenter.code
        item["cmdbDeviceId"] = device.id


@router.get("/lossless/overrun-devices", response_model=dict)
async def list_lossless_overrun_devices(
    current_user: User = Depends(get_current_active_user),
    controller_id: Optional[str] = Query(None),
    hours: int = Query(3, ge=1, le=168),
    tag: str = Query("3h"),
):
    client = _client_from_saved(controller_id=controller_id)
    return await client.list_lossless_overrun_devices(hours=hours, tag=tag)


@router.get("/lossless/buffer-details", response_model=dict)
async def list_lossless_buffer_details(
    current_user: User = Depends(get_current_active_user),
    controller_id: Optional[str] = Query(None),
    asset_id: str = Query(..., min_length=1),
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    hours: int = Query(3, ge=1, le=168),
    if_index: Optional[str] = Query(None),
    sort_column: str = Query("outDroppedPkts"),
    order_type: str = Query("desc"),
):
    client = _client_from_saved(controller_id=controller_id)
    return await client.list_lossless_buffer_details(
        asset_id=asset_id,
        page=page,
        page_size=page_size,
        hours=hours,
        if_index=if_index,
        sort_column=sort_column,
        order_type=order_type,
    )
