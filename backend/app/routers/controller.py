"""
控制器/分析器集成接口。
"""
from __future__ import annotations

from typing import Any, Dict, Optional

from fastapi import APIRouter, Depends, HTTPException, Query

from app.models import User
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
    page: int = Query(1, ge=1),
    page_size: int = Query(20, ge=1, le=200),
    search: Optional[str] = Query(None),
    device_ip: Optional[str] = Query(None),
    hours: int = Query(3, ge=1, le=168),
    controller_id: Optional[str] = Query(None),
):
    client = _client_from_saved(controller_id=controller_id)
    return await client.list_opticals(page=page, page_size=page_size, search=search, device_ip=device_ip, hours=hours)
