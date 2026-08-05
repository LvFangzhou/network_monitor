from fastapi import APIRouter
from app.routers import devices, alerts, auth, metrics, cmdb, resources, servers, tacacs, config_backups, controller, bmp, compliance

api_router = APIRouter()

api_router.include_router(auth.router, prefix="/auth", tags=["认证"])
api_router.include_router(devices.router, prefix="/devices", tags=["设备管理"])
api_router.include_router(cmdb.router, prefix="/cmdb", tags=["CMDB"])
api_router.include_router(resources.router, prefix="/resources", tags=["资源管理"])
api_router.include_router(servers.router, prefix="/servers", tags=["服务器管理"])
api_router.include_router(alerts.router, prefix="/alerts", tags=["告警管理"])
api_router.include_router(metrics.router, prefix="/metrics", tags=["指标查询"])
api_router.include_router(tacacs.router, prefix="/tacacs", tags=["Tacacs管理"])
api_router.include_router(config_backups.router, prefix="/config-backups", tags=["配置备份"])
api_router.include_router(controller.router, prefix="/controller", tags=["控制器集成"])
api_router.include_router(bmp.router, prefix="/bmp", tags=["BMP"])
api_router.include_router(compliance.router, prefix="/compliance", tags=["设备上线合规"])

__all__ = ["api_router"]
