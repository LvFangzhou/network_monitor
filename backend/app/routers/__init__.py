from fastapi import APIRouter
from app.routers import devices, alerts, auth, metrics, cmdb, resources, tacacs, config_backups

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["认证"])
api_router.include_router(devices.router, prefix="/devices", tags=["设备管理"])
api_router.include_router(cmdb.router, prefix="/cmdb", tags=["CMDB"])
api_router.include_router(resources.router, prefix="/resources", tags=["资源管理"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["告警管理"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["指标查询"])
api_router.include_router(tacacs.router, prefix="/tacacs", tags=["Tacacs管理"])
api_router.include_router(config_backups.router, prefix="/config-backups", tags=["配置备份"])

__all__ = ["api_router"]
