"""
网络设备配置备份与检索。
"""
from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import ConfigBackupJob, ConfigBackupResult, Datacenter, Device, User
from app.routers.auth import get_current_active_user
from app.tasks.config_backup_tasks import run_config_backup

router = APIRouter()


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
    job = db.query(ConfigBackupJob).filter(ConfigBackupJob.id == job_id).first()
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
    """在最近一次成功/部分成功任务的配置中搜索关键字。"""
    latest_job = (
        db.query(ConfigBackupJob)
        .filter(ConfigBackupJob.status.in_(["success", "partial_failed"]))
        .order_by(ConfigBackupJob.finished_at.desc().nullslast(), ConfigBackupJob.started_at.desc())
        .first()
    )
    if not latest_job:
        return {"total": 0, "items": [], "job": None}

    query = (
        db.query(ConfigBackupResult)
        .filter(
            ConfigBackupResult.job_id == latest_job.id,
            ConfigBackupResult.status == "success",
            ConfigBackupResult.config_content.isnot(None),
            ConfigBackupResult.config_content.ilike(f"%{keyword}%"),
        )
    )
    if datacenter:
        query = query.filter(ConfigBackupResult.datacenter_name == datacenter)
    if device_id:
        query = query.filter(ConfigBackupResult.device_id == device_id)
    if device_ip:
        query = query.filter(ConfigBackupResult.device_ip.ilike(f"%{device_ip.strip()}%"))

    results = query.order_by(ConfigBackupResult.device_ip.asc()).limit(limit).all()
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
    return {"datacenters": datacenters}
