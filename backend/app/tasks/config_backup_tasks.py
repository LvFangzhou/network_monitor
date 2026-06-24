import asyncio
import os
import hashlib
import io
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import defaultdict
from datetime import datetime, timezone
from types import SimpleNamespace
from typing import Any, Dict, List, Optional, Tuple

from app.core import get_logger
from app.database import SessionLocal
from app.models import ConfigBackupJob, ConfigBackupResult, Device
from app.tasks import celery_app
from app.tasks.system_tasks import _notification_channels
from app.utils import notification_manager
from app.utils.config_backup_settings import config_backup_notification_channels

logger = get_logger(__name__)
CONFIG_BACKUP_CONCURRENCY = max(1, int(os.getenv("CONFIG_BACKUP_CONCURRENCY", "16")))


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _backup_command(device: Device) -> str:
    vendor = f"{device.vendor or ''} {device.model or ''}".lower()
    if any(marker in vendor for marker in ["h3c", "华三"]):
        return "dis current-configuration"
    if any(marker in vendor for marker in ["asteros", "asternos", "asterfusion", "星融元", "ruijie", "锐捷"]):
        return "show running-config"
    if any(marker in vendor for marker in ["hillstone", "山石"]):
        return "show configuration"
    if any(marker in vendor for marker in ["cisco", "nexus", "ios", "nx-os"]):
        return "show running-config"
    return "display current-configuration"


def _screen_disable_commands(device: Device) -> List[str]:
    vendor = f"{device.vendor or ''} {device.model or ''}".lower()
    commands = ["screen-length 0 temporary", "terminal length 0"]
    if any(marker in vendor for marker in ["asteros", "asternos", "asterfusion", "星融元", "ruijie", "锐捷", "hillstone", "山石", "cisco", "nexus", "ios", "nx-os"]):
        return ["terminal length 0", "screen-length 0 temporary"]
    return commands


def _netmiko_device_type(device: Device) -> str:
    vendor = f"{device.vendor or ''} {device.model or ''}".lower()
    if any(marker in vendor for marker in ["h3c", "华三"]):
        return "hp_comware"
    if any(marker in vendor for marker in ["ruijie", "锐捷"]):
        return "cisco_ios"
    if any(marker in vendor for marker in ["asteros", "asternos", "asterfusion", "星融元"]):
        return "cisco_ios"
    if any(marker in vendor for marker in ["hillstone", "山石"]):
        return "autodetect"
    if any(marker in vendor for marker in ["huawei", "华为"]):
        return "huawei"
    if any(marker in vendor for marker in ["cisco", "nexus", "ios", "nx-os"]):
        return "cisco_ios"
    return "autodetect"


def _collect_config_netmiko(device: Device) -> Tuple[str, str]:
    try:
        from netmiko import ConnectHandler
        from netmiko.ssh_autodetect import SSHDetect
    except ImportError as exc:
        raise RuntimeError("缺少 netmiko 依赖") from exc

    username = (device.ssh_username or "").strip()
    password = device.ssh_password or None
    key_text = (device.ssh_key or "").strip()
    if not username:
        raise RuntimeError("设备未配置 SSH 用户名")
    if not password and not key_text:
        raise RuntimeError("设备未配置 SSH 密码或密钥")
    if key_text and not password:
        raise RuntimeError("Netmiko 不使用内联 SSH Key，交由 Paramiko 回退处理")

    command = _backup_command(device)
    device_type = _netmiko_device_type(device)
    base_params: Dict[str, Any] = {
        "device_type": device_type,
        "host": device.ip_address,
        "port": int(device.ssh_port or 22),
        "username": username,
        "timeout": 20,
        "conn_timeout": 15,
        "banner_timeout": 15,
        "auth_timeout": 15,
        "fast_cli": False,
    }
    if password:
        base_params["password"] = password

    if device_type == "autodetect":
        try:
            guesser = SSHDetect(**base_params)
            detected = guesser.autodetect()
            if detected:
                base_params["device_type"] = detected
        except Exception as exc:
            logger.info("Netmiko 自动识别失败，使用 cisco_ios 通用方式重试", device_id=device.id, ip=device.ip_address, error=str(exc))
            base_params["device_type"] = "cisco_ios"

    connection = ConnectHandler(**base_params)
    try:
        output = connection.send_command(
            command,
            expect_string=None,
            read_timeout=120,
            strip_prompt=True,
            strip_command=True,
        )
        config = output.strip()
        if not config:
            raise RuntimeError("Netmiko 备份命令未返回配置内容")
        return command, config
    finally:
        connection.disconnect()


def _read_shell_output(shell: Any, idle_seconds: float = 0.8, timeout_seconds: float = 45.0) -> str:
    started = time.monotonic()
    last_data = time.monotonic()
    chunks: List[str] = []
    while time.monotonic() - started < timeout_seconds:
        if shell.recv_ready():
            data = shell.recv(65535).decode("utf-8", errors="ignore")
            chunks.append(data)
            last_data = time.monotonic()
            continue
        if chunks and time.monotonic() - last_data >= idle_seconds:
            break
        time.sleep(0.1)
    return "".join(chunks)


def _clean_config_output(output: str, command: str) -> str:
    text = output.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"\x1b\\[[0-9;?]*[A-Za-z]", "", text)
    text = text.replace("--More--", "")
    lines = [line.rstrip() for line in text.splitlines()]
    cleaned: List[str] = []
    command_seen = False
    for line in lines:
        stripped = line.strip()
        if not command_seen:
            if command in stripped:
                command_seen = True
            continue
        if stripped.lower() in {"return", "end"}:
            cleaned.append(stripped)
            continue
        cleaned.append(line)
    if not cleaned:
        cleaned = lines
    while cleaned and not cleaned[0].strip():
        cleaned.pop(0)
    while cleaned and not cleaned[-1].strip():
        cleaned.pop()
    return "\n".join(cleaned).strip()


def _collect_config(device: Device) -> Tuple[str, str]:
    netmiko_error: Optional[Exception] = None
    try:
        return _collect_config_netmiko(device)
    except Exception as exc:
        netmiko_error = exc
        logger.info("Netmiko配置备份失败，回退Paramiko", device_id=device.id, ip=device.ip_address, error=str(exc))

    try:
        import paramiko
    except ImportError as exc:
        if netmiko_error:
            raise RuntimeError(f"Netmiko失败：{netmiko_error}；且缺少 paramiko 依赖，无法回退") from exc
        raise RuntimeError("缺少 paramiko 依赖，无法执行 SSH 配置备份") from exc

    username = (device.ssh_username or "").strip()
    password = device.ssh_password or None
    key_text = (device.ssh_key or "").strip()
    if not username:
        raise RuntimeError("设备未配置 SSH 用户名")
    if not password and not key_text:
        raise RuntimeError("设备未配置 SSH 密码或密钥")

    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    connect_kwargs: Dict[str, Any] = {
        "hostname": device.ip_address,
        "port": int(device.ssh_port or 22),
        "username": username,
        "timeout": 12,
        "banner_timeout": 12,
        "auth_timeout": 12,
        "look_for_keys": False,
        "allow_agent": False,
    }
    if key_text:
        key_file = io.StringIO(key_text)
        key = None
        key_errors = []
        for key_cls in (paramiko.RSAKey, paramiko.ECDSAKey, paramiko.Ed25519Key):
            key_file.seek(0)
            try:
                key = key_cls.from_private_key(key_file, password=password)
                break
            except Exception as exc:
                key_errors.append(str(exc))
        if not key:
            raise RuntimeError(f"SSH 密钥解析失败：{'; '.join(key_errors[:2])}")
        connect_kwargs["pkey"] = key
    else:
        connect_kwargs["password"] = password

    command = _backup_command(device)
    try:
        client.connect(**connect_kwargs)
        shell = client.invoke_shell(width=240, height=1000)
        _read_shell_output(shell, idle_seconds=0.3, timeout_seconds=3)
        for disable_command in _screen_disable_commands(device):
            shell.send(disable_command + "\n")
            _read_shell_output(shell, idle_seconds=0.3, timeout_seconds=3)
        shell.send(command + "\n")
        output = _read_shell_output(shell, idle_seconds=1.2, timeout_seconds=90)
        config = _clean_config_output(output, command)
        if not config:
            raise RuntimeError("备份命令未返回配置内容")
        return command, config
    finally:
        client.close()


def _build_notification_content(job: ConfigBackupJob, datacenter_stats: Dict[str, Dict[str, int]]) -> Tuple[str, List[Dict[str, str]]]:
    lines = [
        f"配置备份完成：成功 {job.success_count} 台，失败 {job.failed_count} 台，共 {job.total_devices} 台。",
        "",
        "按机房统计：",
    ]
    rows: List[Dict[str, str]] = [
        {"label": "任务ID", "value": str(job.id)},
        {"label": "触发方式", "value": "定时" if job.trigger_type == "scheduled" else "手动"},
        {"label": "总设备", "value": str(job.total_devices)},
        {"label": "成功", "value": str(job.success_count)},
        {"label": "失败", "value": str(job.failed_count)},
    ]
    for datacenter, stats in sorted(datacenter_stats.items(), key=lambda item: item[0]):
        line = f"- {datacenter}：成功 {stats.get('success', 0)} 台，失败 {stats.get('failed', 0)} 台"
        lines.append(line)
        rows.append({"label": datacenter, "value": f"成功 {stats.get('success', 0)} / 失败 {stats.get('failed', 0)}"})
    return "\n".join(lines), rows


def _send_backup_notification(job: ConfigBackupJob, datacenter_stats: Dict[str, Dict[str, int]]) -> None:
    channels = config_backup_notification_channels() or _notification_channels()
    if not channels:
        logger.warning("配置备份完成但没有配置机器人通知", job_id=job.id)
        return

    content, rows = _build_notification_content(job, datacenter_stats)
    title = "网络设备配置备份完成" if job.failed_count == 0 else "网络设备配置备份完成（存在失败）"
    card_data = {
        "severity": "P2" if job.failed_count == 0 else "P1",
        "rows": rows,
        "notification_kind": "config_backup",
    }

    async def _send_all() -> None:
        for channel in channels:
            await notification_manager.send_notification(
                channel["type"],
                channel["config"],
                title,
                content,
                card_data,
            )

    asyncio.run(_send_all())


def _device_payload(device: Device) -> Dict[str, Any]:
    return {
        "id": device.id,
        "name": device.name,
        "ip_address": device.ip_address,
        "datacenter_name": device.datacenter_ref.name if device.datacenter_ref else "未设置机房",
        "vendor": device.vendor,
        "model": device.model,
        "ssh_port": device.ssh_port,
        "ssh_username": device.ssh_username,
        "ssh_password": device.ssh_password,
        "ssh_key": device.ssh_key,
    }


def _backup_device_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    started_at = _utc_now()
    device = SimpleNamespace(**payload)
    try:
        command, config = _collect_config(device)
        return {
            **payload,
            "status": "success",
            "command": command,
            "config_content": config,
            "config_hash": hashlib.sha256(config.encode("utf-8", errors="ignore")).hexdigest(),
            "line_count": len(config.splitlines()),
            "error_message": None,
            "started_at": started_at,
            "finished_at": _utc_now(),
        }
    except Exception as exc:
        logger.warning("设备配置备份失败", device_id=payload.get("id"), ip=payload.get("ip_address"), error=str(exc))
        return {
            **payload,
            "status": "failed",
            "command": None,
            "config_content": None,
            "config_hash": None,
            "line_count": 0,
            "error_message": str(exc)[:2000],
            "started_at": started_at,
            "finished_at": _utc_now(),
        }


@celery_app.task(bind=True, name="app.tasks.config_backup_tasks.run_config_backup", time_limit=7200, soft_time_limit=6900)
def run_config_backup(self, job_id: Optional[int] = None, trigger_type: str = "manual", actor: Optional[str] = None) -> Dict[str, Any]:
    db = SessionLocal()
    datacenter_stats: Dict[str, Dict[str, int]] = defaultdict(lambda: {"success": 0, "failed": 0})
    try:
        if job_id:
            job = db.query(ConfigBackupJob).filter(ConfigBackupJob.id == int(job_id)).first()
        else:
            job = None
        if not job:
            job = ConfigBackupJob(status="running", trigger_type=trigger_type, started_by=actor, started_at=_utc_now())
            db.add(job)
            db.commit()
            db.refresh(job)
        else:
            job.status = "running"
            job.started_at = _utc_now()
            job.error_message = None
            db.commit()

        devices = (
            db.query(Device)
            .filter(Device.status.in_(["active", "online"]))
            .order_by(Device.ip_address.asc())
            .all()
        )
        job.total_devices = len(devices)
        job.success_count = 0
        job.failed_count = 0
        db.commit()

        payloads = [_device_payload(device) for device in devices]
        result_ids: Dict[int, int] = {}
        for payload in payloads:
            result = ConfigBackupResult(
                job_id=job.id,
                device_id=payload["id"],
                device_name=payload["name"],
                device_ip=payload["ip_address"],
                datacenter_name=payload["datacenter_name"],
                vendor=payload.get("vendor"),
                model=payload.get("model"),
                status="pending",
                started_at=_utc_now(),
            )
            db.add(result)
            db.flush()
            result_ids[payload["id"]] = result.id
        db.commit()

        max_workers = min(CONFIG_BACKUP_CONCURRENCY, max(1, len(payloads)))
        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_map = {executor.submit(_backup_device_payload, payload): payload for payload in payloads}
            for future in as_completed(future_map):
                backup_result = future.result()
                result_id = result_ids.get(backup_result["id"])
                if not result_id:
                    continue

                result = db.query(ConfigBackupResult).filter(ConfigBackupResult.id == result_id).first()
                if not result:
                    continue

                result.status = backup_result["status"]
                result.command = backup_result.get("command")
                result.config_content = backup_result.get("config_content")
                result.config_hash = backup_result.get("config_hash")
                result.line_count = backup_result.get("line_count") or 0
                result.error_message = backup_result.get("error_message")
                result.started_at = backup_result.get("started_at")
                result.finished_at = backup_result.get("finished_at")

                datacenter = backup_result.get("datacenter_name") or "未设置机房"
                if result.status == "success":
                    job.success_count += 1
                    datacenter_stats[datacenter]["success"] += 1
                else:
                    job.failed_count += 1
                    datacenter_stats[datacenter]["failed"] += 1
                db.commit()

                if (job.success_count + job.failed_count) % 10 == 0:
                    logger.info(
                        "配置备份进度",
                        job_id=job.id,
                        success=job.success_count,
                        failed=job.failed_count,
                        total=job.total_devices,
                    )

        job.status = "success" if job.failed_count == 0 else ("partial_failed" if job.success_count else "failed")
        job.finished_at = _utc_now()
        content, _rows = _build_notification_content(job, datacenter_stats)
        job.summary = content
        db.commit()
        _send_backup_notification(job, datacenter_stats)
        return {
            "job_id": job.id,
            "status": job.status,
            "total_devices": job.total_devices,
            "success_count": job.success_count,
            "failed_count": job.failed_count,
        }
    except Exception as exc:
        db.rollback()
        if job_id:
            job = db.query(ConfigBackupJob).filter(ConfigBackupJob.id == int(job_id)).first()
            if job:
                job.status = "failed"
                job.error_message = str(exc)
                job.finished_at = _utc_now()
                db.commit()
        logger.error("配置备份任务失败", job_id=job_id, error=str(exc))
        raise
    finally:
        db.close()


@celery_app.task(name="app.tasks.config_backup_tasks.run_scheduled_config_backup")
def run_scheduled_config_backup() -> Dict[str, Any]:
    return run_config_backup(trigger_type="scheduled", actor="system")
