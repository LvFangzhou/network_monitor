"""
网络设备配置备份与检索。
"""
from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, or_
from sqlalchemy.orm import aliased, selectinload
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ConfigBackupJob, ConfigBackupResult, Datacenter, Device, User
from app.routers.auth import get_current_active_user
from app.tasks.config_backup_tasks import run_config_backup
from app.utils import notification_manager
from app.utils import redis_client
from app.utils.config_backup_settings import (
    detect_webhook_type,
    load_config_backup_settings,
    save_config_backup_settings,
)

router = APIRouter()
CONFIG_BACKUP_FILTERS_CACHE_KEY = "config_backups:filters:v1"
CONFIG_BACKUP_FILTERS_CACHE_SECONDS = 300


def _job_to_dict(job: ConfigBackupJob, include_results: bool = False) -> Dict[str, Any]:
    data = {
        "id": job.id,
        "status": job.status,
        "trigger_type": job.trigger_type,
        "total_devices": job.total_devices,
        "success_count": job.success_count,
        "failed_count": job.failed_count,
        "summary": job.summary,
        "error_message": job.error_message,
        "started_by": job.started_by,
        "started_at": job.started_at.isoformat() if job.started_at else None,
        "finished_at": job.finished_at.isoformat() if job.finished_at else None,
        "created_at": job.created_at.isoformat() if job.created_at else None,
    }
    if include_results:
        data["results"] = [_result_to_dict(item, include_content=False) for item in job.results]
    return data


def _result_to_dict(result: ConfigBackupResult, include_content: bool = False) -> Dict[str, Any]:
    data = {
        "id": result.id,
        "job_id": result.job_id,
        "device_id": result.device_id,
        "device_name": result.device_name,
        "device_ip": result.device_ip,
        "datacenter_name": result.datacenter_name,
        "device_type": result.device.device_type if result.device else None,
        "vendor": result.vendor,
        "model": result.model,
        "status": result.status,
        "command": result.command,
        "config_hash": result.config_hash,
        "line_count": result.line_count,
        "error_message": result.error_message,
        "started_at": result.started_at.isoformat() if result.started_at else None,
        "finished_at": result.finished_at.isoformat() if result.finished_at else None,
    }
    if include_content:
        data["config_content"] = result.config_content
    return data


@router.post("/run", response_model=dict)
async def trigger_config_backup(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """手动触发一次全量上线设备配置备份。"""
    running_job = (
        db.query(ConfigBackupJob)
        .filter(ConfigBackupJob.status.in_(["pending", "running"]))
        .order_by(ConfigBackupJob.started_at.desc())
        .first()
    )
    if running_job:
        return {
            "message": "已有配置备份任务正在执行",
            "job": _job_to_dict(running_job),
        }

    job = ConfigBackupJob(status="pending", trigger_type="manual", started_by=current_user.username)
    db.add(job)
    db.commit()
    db.refresh(job)
    task = run_config_backup.apply_async(kwargs={"job_id": job.id, "trigger_type": "manual", "actor": current_user.username})
    return {
        "message": "已提交配置备份任务",
        "task_id": task.id,
        "job": _job_to_dict(job),
    }


@router.post("/jobs/{job_id}/cancel", response_model=dict)
async def cancel_config_backup_job(
    job_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """取消正在执行/等待的配置备份任务。"""
    job = db.query(ConfigBackupJob).filter(ConfigBackupJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="配置备份任务不存在")
    if job.status not in {"pending", "running"}:
        return {"message": "任务已结束，无需取消", "job": _job_to_dict(job)}

    job.status = "cancelled"
    job.error_message = f"由 {current_user.username} 手动取消"
    job.finished_at = job.finished_at or datetime.now(timezone.utc)
    (
        db.query(ConfigBackupResult)
        .filter(
            ConfigBackupResult.job_id == job.id,
            ConfigBackupResult.status.in_(["pending", "running"]),
        )
        .update(
            {
                ConfigBackupResult.status: "failed",
                ConfigBackupResult.error_message: "任务已手动取消",
                ConfigBackupResult.finished_at: job.finished_at,
            },
            synchronize_session=False,
        )
    )
    db.commit()
    db.refresh(job)
    return {"message": "已发送取消指令", "job": _job_to_dict(job)}


@router.post("/jobs/cancel-running", response_model=dict)
async def cancel_running_config_backup_job(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """取消最新的运行中/等待中配置备份任务。"""
    job = (
        db.query(ConfigBackupJob)
        .filter(ConfigBackupJob.status.in_(["pending", "running"]))
        .order_by(ConfigBackupJob.started_at.desc(), ConfigBackupJob.id.desc())
        .first()
    )
    if not job:
        return {"message": "当前没有运行中的配置备份任务", "job": None}
    return await cancel_config_backup_job(job.id, current_user=current_user, db=db)


@router.get("/jobs", response_model=dict)
async def list_config_backup_jobs(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    query = db.query(ConfigBackupJob)
    total = query.count()
    jobs = query.order_by(ConfigBackupJob.started_at.desc()).offset(skip).limit(limit).all()
    return {"total": total, "items": [_job_to_dict(job) for job in jobs]}


@router.get("/jobs/latest", response_model=dict)
async def get_latest_config_backup_job(db: Session = Depends(get_db)):
    job = db.query(ConfigBackupJob).order_by(ConfigBackupJob.started_at.desc()).first()
    return {"job": _job_to_dict(job) if job else None}


@router.get("/jobs/{job_id}", response_model=dict)
async def get_config_backup_job(job_id: int, db: Session = Depends(get_db)):
    job = (
        db.query(ConfigBackupJob)
        .options(
            selectinload(ConfigBackupJob.results)
            .defer(ConfigBackupResult.config_content)
            .selectinload(ConfigBackupResult.device)
        )
        .filter(ConfigBackupJob.id == job_id)
        .first()
    )
    if not job:
        raise HTTPException(status_code=404, detail="配置备份任务不存在")
    return _job_to_dict(job, include_results=True)


@router.get("/results/{result_id}", response_model=dict)
async def get_config_backup_result(result_id: int, db: Session = Depends(get_db)):
    result = db.query(ConfigBackupResult).filter(ConfigBackupResult.id == result_id).first()
    if not result:
        raise HTTPException(status_code=404, detail="配置备份结果不存在")
    return _result_to_dict(result, include_content=True)


@router.get("/search", response_model=dict)
async def search_config_backups(
    db: Session = Depends(get_db),
    keyword: str = Query(..., min_length=1),
    datacenter: Optional[str] = None,
    device_id: Optional[int] = None,
    device_ip: Optional[str] = None,
    limit: int = Query(100, ge=1, le=500),
    context_lines: int = Query(1, ge=0, le=5),
):
    """在每台设备最新一次成功获取的配置中搜索关键字。"""
    latest_job = (
        db.query(ConfigBackupJob)
        .filter(ConfigBackupJob.status.in_(["success", "partial_failed"]))
        .order_by(ConfigBackupJob.finished_at.desc().nullslast(), ConfigBackupJob.started_at.desc())
        .first()
    )
    if not latest_job:
        return {"total": 0, "items": [], "job": None}

    ranked_latest_results = (
        db.query(
            ConfigBackupResult.id.label("id"),
            func.row_number().over(
                partition_by=ConfigBackupResult.device_id,
                order_by=(
                    ConfigBackupResult.finished_at.desc().nullslast(),
                    ConfigBackupResult.id.desc(),
                ),
            ).label("rank"),
        )
        .filter(
            ConfigBackupResult.status == "success",
            ConfigBackupResult.config_content.isnot(None),
        )
        .subquery()
    )
    latest_result = aliased(ConfigBackupResult)
    query = (
        db.query(latest_result)
        .join(ranked_latest_results, ranked_latest_results.c.id == latest_result.id)
        .filter(
            ranked_latest_results.c.rank == 1,
            latest_result.config_content.ilike(f"%{keyword}%"),
        )
    )
    if datacenter:
        query = query.filter(latest_result.datacenter_name == datacenter)
    if device_id:
        query = query.filter(latest_result.device_id == device_id)
    if device_ip:
        query = query.filter(latest_result.device_ip.ilike(f"%{device_ip.strip()}%"))

    results = query.order_by(latest_result.device_ip.asc()).limit(limit).all()
    pattern = re.compile(re.escape(keyword), re.IGNORECASE)
    items: List[Dict[str, Any]] = []
    for result in results:
        lines = (result.config_content or "").splitlines()
        for index, line in enumerate(lines, start=1):
            if not pattern.search(line):
                continue
            start = max(1, index - context_lines)
            end = min(len(lines), index + context_lines)
            items.append({
                "result_id": result.id,
                "job_id": result.job_id,
                "device_id": result.device_id,
                "device_name": result.device_name,
                "device_ip": result.device_ip,
                "datacenter_name": result.datacenter_name,
                "vendor": result.vendor,
                "model": result.model,
                "line_number": index,
                "line": line,
                "context": [
                    {"line_number": line_no, "text": lines[line_no - 1]}
                    for line_no in range(start, end + 1)
                ],
            })
            if len(items) >= limit:
                return {"total": len(items), "items": items, "job": _job_to_dict(latest_job)}
    return {"total": len(items), "items": items, "job": _job_to_dict(latest_job)}


@router.get("/filters", response_model=dict)
async def get_config_backup_filters(db: Session = Depends(get_db)):
    cached = redis_client.get(CONFIG_BACKUP_FILTERS_CACHE_KEY)
    if cached:
        try:
            return json.loads(cached)
        except Exception:
            redis_client.delete(CONFIG_BACKUP_FILTERS_CACHE_KEY)
    datacenters = [
        {"name": name}
        for (name,) in (
            db.query(ConfigBackupResult.datacenter_name)
            .filter(ConfigBackupResult.datacenter_name.isnot(None))
            .distinct()
            .order_by(ConfigBackupResult.datacenter_name.asc())
            .all()
        )
    ]
    payload = {"datacenters": datacenters}
    redis_client.setex(CONFIG_BACKUP_FILTERS_CACHE_KEY, CONFIG_BACKUP_FILTERS_CACHE_SECONDS, json.dumps(payload, ensure_ascii=False))
    return payload


@router.get("/settings", response_model=dict)
async def get_config_backup_page_settings(current_user: User = Depends(get_current_active_user)):
    """获取配置备份页面设置。"""
    return {"settings": load_config_backup_settings()}


@router.post("/settings", response_model=dict)
async def save_config_backup_page_settings(
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_active_user),
):
    """保存配置备份页面设置。"""
    raw_channels = payload.get("notification_channels") or []
    if not isinstance(raw_channels, list):
        raise HTTPException(status_code=400, detail="机器人通知配置格式不正确")

    channels = []
    for channel in raw_channels:
        webhook = str((channel or {}).get("webhook") or (channel or {}).get("url") or "").strip()
        if not webhook:
            continue
        channels.append({
            "type": detect_webhook_type(webhook),
            "webhook": webhook,
        })
    settings_payload = save_config_backup_settings({"notification_channels": channels})
    return {"message": "配置备份机器人通知已保存", "settings": settings_payload}


@router.post("/test-notification", response_model=dict)
async def test_config_backup_notification(
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_active_user),
):
    """测试配置备份机器人/Webhook 是否可用。"""
    webhook_url = str(payload.get("url") or payload.get("webhook") or "").strip()
    if not webhook_url:
        raise HTTPException(status_code=400, detail="请先填写机器人 Webhook 地址")
    channel_type = detect_webhook_type(webhook_url)
    config = {"url": webhook_url} if channel_type == "webhook" else {"webhook": webhook_url}
    success = await notification_manager.send_notification(
        channel_type,
        config,
        "配置备份机器人测试",
        "这是一条配置备份测试消息，用于验证机器人 webhook 是否配置正确。",
        {
            "severity": "P2",
            "notification_kind": "config_backup",
            "rows": [
                {"label": "模块", "value": "配置备份"},
                {"label": "测试人", "value": current_user.username},
            ],
        },
    )
    if not success:
        detail = notification_manager.last_error_message or "测试消息发送失败，请检查 webhook 地址或机器人配置"
        raise HTTPException(status_code=400, detail=detail)
    return {"success": True, "channel_type": channel_type, "message": "测试消息发送成功"}
