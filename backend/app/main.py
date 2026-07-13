"""
FastAPI 应用主入口
"""
import asyncio
import json
from urllib.parse import parse_qs
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from jose import jwt, JWTError
from sqlalchemy import text

from app.config import settings
from app.database import init_db, SessionLocal
from app.routers import api_router
from app.core import setup_logging, get_logger
from app.websocket import ws_manager
from app.collectors import gnmi_manager
from app.models import AuditLog, User
from app.services.syslog_listener import syslog_listener
from app.services.flow_listener import flow_listener
from app.services.snmp_trap_listener import snmp_trap_listener
from app.utils import (
    build_idempotency_key,
    build_rate_limit_key,
    build_request_id,
    check_rate_limit,
    get_client_ip,
    load_idempotent_response,
    should_store_idempotent_response,
    store_idempotent_response,
)

# 设置日志
setup_logging()
logger = get_logger(__name__)

SENSITIVE_AUDIT_KEYS = {
    "password",
    "old_password",
    "new_password",
    "hashed_password",
    "token",
    "access_token",
    "authorization",
    "community",
    "webhook",
    "bind_password",
    "secret",
    "key",
    "priv_password",
    "auth_password",
}

AUDIT_MENU_PREFIXES = [
    ("/api/v1/devices", "网络设备"),
    ("/api/v1/cmdb", "CMDB"),
    ("/api/v1/resources/customers", "客户管理"),
    ("/api/v1/resources/vendors", "供应商管理"),
    ("/api/v1/resources/circuits", "线路管理"),
    ("/api/v1/resources/ipdb", "IPDB"),
    ("/api/v1/alerts/rules", "告警规则"),
    ("/api/v1/alerts/history", "告警历史"),
    ("/api/v1/alerts/silences", "告警屏蔽"),
    ("/api/v1/alerts/syslog", "Syslog"),
    ("/api/v1/metrics", "监控中心"),
    ("/api/v1/config-backups", "配置备份"),
    ("/api/v1/tacacs", "Tacacs管理"),
    ("/api/v1/auth/users", "用户管理"),
    ("/api/v1/auth/login", "登录"),
    ("/api/v1/auth/change-password", "修改密码"),
    ("/api/v1/auth", "认证授权"),
]


def _redact_audit_value(value):
    if isinstance(value, dict):
        redacted = {}
        for key, item in value.items():
            if any(marker in str(key).lower() for marker in SENSITIVE_AUDIT_KEYS):
                redacted[key] = "***"
            else:
                redacted[key] = _redact_audit_value(item)
        return redacted
    if isinstance(value, list):
        return [_redact_audit_value(item) for item in value]
    return value


def _parse_audit_body(content_type: str, body: bytes):
    if not body:
        return None
    if len(body) > 128 * 1024:
        return {"_truncated": True, "size": len(body)}
    content_type = (content_type or "").lower()
    try:
        if "application/json" in content_type:
            return _redact_audit_value(json.loads(body.decode("utf-8")))
        if "application/x-www-form-urlencoded" in content_type or "multipart/form-data" in content_type:
            parsed = parse_qs(body.decode("utf-8"), keep_blank_values=True)
            normalized = {
                key: values[0] if len(values) == 1 else values
                for key, values in parsed.items()
            }
            return _redact_audit_value(normalized)
        return {"_content_type": content_type, "_size": len(body)}
    except Exception as exc:
        return {"_parse_error": str(exc), "_size": len(body)}


def _audit_menu_for_path(path: str) -> str:
    for prefix, menu in AUDIT_MENU_PREFIXES:
        if path.startswith(prefix):
            return menu
    return "系统接口"


def _audit_action_for_method(method: str, path: str) -> str:
    if path.endswith("/login") and method == "POST":
        return "login"
    return {
        "GET": "view",
        "POST": "create",
        "PUT": "update",
        "PATCH": "update",
        "DELETE": "delete",
    }.get(method, method.lower())


def _audit_resource_from_path(path: str) -> tuple[str | None, str | None]:
    parts = [part for part in path.split("/") if part]
    if len(parts) < 3:
        return None, None
    resource_type = parts[2]
    resource_id = None
    for part in reversed(parts[3:]):
        if part.isdigit():
            resource_id = part
            break
    return resource_type, resource_id


def _audit_user_from_request(request: Request) -> tuple[int | None, str | None]:
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None, None
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("sub")
        if not user_id:
            return None, None
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == int(user_id)).first()
            if user:
                return user.id, user.username
        finally:
            db.close()
    except Exception:
        return None, None
    return None, None


async def _rebuild_request_body(request: Request, body: bytes) -> None:
    async def receive():
        return {"type": "http.request", "body": body, "more_body": False}

    request._receive = receive


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期管理"""
    # 启动时执行
    logger.info("=" * 50)
    logger.info(f"{settings.APP_NAME} v{settings.APP_VERSION} 启动中...")
    logger.info("=" * 50)
    
    # 初始化数据库
    init_db()
    
    # 启动gNMI管理器
    await gnmi_manager.start()
    await syslog_listener.start()
    await flow_listener.start()
    await snmp_trap_listener.start()
    
    logger.info("应用启动完成")
    
    yield
    
    # 关闭时执行
    logger.info("应用关闭中...")
    
    # 停止gNMI管理器
    await gnmi_manager.stop()
    await syslog_listener.stop()
    await flow_listener.stop()
    await snmp_trap_listener.stop()
    
    logger.info("应用已关闭")


# 创建FastAPI应用
app = FastAPI(
    title=settings.APP_NAME,
    version=settings.APP_VERSION,
    description="企业级网络运营与监控系统",
    docs_url=settings.DOCS_URL if settings.ENABLE_API_DOCS else None,
    redoc_url=settings.REDOC_URL if settings.ENABLE_API_DOCS else None,
    openapi_url=f"{settings.API_PREFIX}/openapi.json" if settings.ENABLE_API_DOCS else None,
    lifespan=lifespan
)

# CORS配置
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.CORS_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _is_public_api_path(path: str) -> bool:
    public_paths = {
        f"{settings.API_PREFIX}/auth/login",
        f"{settings.API_PREFIX}/auth/init",
    }
    return path in public_paths


def _is_internal_request(request: Request) -> bool:
    token = (settings.INTERNAL_API_TOKEN or "").strip()
    if not token:
        return False
    return request.headers.get("X-Internal-Token") == token


def _authenticate_api_request(request: Request) -> tuple[int | None, str | None] | None:
    if _is_internal_request(request):
        return None, "internal-service"
    authorization = request.headers.get("Authorization", "")
    if not authorization.startswith("Bearer "):
        return None
    token = authorization.split(" ", 1)[1]
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
        user_id = payload.get("sub")
        if not user_id:
            return None
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == int(user_id)).first()
            if not user or not user.is_active:
                return None
            return user.id, user.username
        finally:
            db.close()
    except Exception:
        return None


@app.middleware("http")
async def enforce_api_authentication(request: Request, call_next):
    if request.method == "OPTIONS" or not request.url.path.startswith(settings.API_PREFIX):
        return await call_next(request)
    if _is_public_api_path(request.url.path):
        return await call_next(request)
    identity = _authenticate_api_request(request)
    if identity is None:
        return JSONResponse(
            status_code=401,
            content={"detail": "请先登录后再访问"},
            headers={"WWW-Authenticate": "Bearer"},
        )
    request.state.auth_user_id = identity[0]
    request.state.auth_username = identity[1]
    request.state.internal_request = identity[1] == "internal-service"
    return await call_next(request)


@app.middleware("http")
async def request_governance(request: Request, call_next):
    request_id = request.headers.get(settings.REQUEST_ID_HEADER) or build_request_id()
    request.state.request_id = request_id
    client_ip = get_client_ip(request)

    if request.url.path.startswith(settings.API_PREFIX):
        if request.url.path == f"{settings.API_PREFIX}/auth/login":
            allowed, retry_after = check_rate_limit(
                build_rate_limit_key(request, "login"),
                settings.RATE_LIMIT_LOGIN_PER_MINUTE,
            )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "登录请求过于频繁，请稍后再试"},
                    headers={"Retry-After": str(retry_after), settings.REQUEST_ID_HEADER: request_id},
                )
        elif request.method in {"POST", "PUT", "PATCH", "DELETE"}:
            allowed, retry_after = check_rate_limit(
                build_rate_limit_key(request, "write"),
                settings.RATE_LIMIT_WRITE_PER_MINUTE,
            )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "写操作过于频繁，请稍后再试"},
                    headers={"Retry-After": str(retry_after), settings.REQUEST_ID_HEADER: request_id},
                )
        else:
            allowed, retry_after = check_rate_limit(
                build_rate_limit_key(request, "read"),
                settings.RATE_LIMIT_READ_PER_MINUTE,
            )
            if not allowed:
                return JSONResponse(
                    status_code=429,
                    content={"detail": "请求过于频繁，请稍后再试"},
                    headers={"Retry-After": str(retry_after), settings.REQUEST_ID_HEADER: request_id},
                )

    idempotency_token = request.headers.get("Idempotency-Key")
    authorization = request.headers.get("Authorization", "")
    identity_token = authorization.split(" ", 1)[1] if authorization.startswith("Bearer ") else None
    idempotency_cache_key = None
    if (
        idempotency_token
        and request.method in {"POST", "PUT", "PATCH", "DELETE"}
        and request.url.path.startswith(settings.API_PREFIX)
    ):
        idempotency_cache_key = build_idempotency_key(request, idempotency_token, identity_token)
        cached_response = load_idempotent_response(idempotency_cache_key)
        if cached_response:
            cached_response.headers[settings.REQUEST_ID_HEADER] = request_id
            return cached_response

    logger.info(
        "请求开始",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        client_ip=client_ip,
    )

    response = await call_next(request)

    if idempotency_cache_key and should_store_idempotent_response(response):
        body_bytes = b""
        async for chunk in response.body_iterator:
            body_bytes += chunk
        response = Response(
            content=body_bytes,
            status_code=response.status_code,
            headers=dict(response.headers),
            media_type=response.media_type,
        )
        try:
            body_payload = json.loads(body_bytes.decode("utf-8")) if body_bytes else None
            store_idempotent_response(idempotency_cache_key, response, body_payload)
        except Exception:
            pass

    response.headers[settings.REQUEST_ID_HEADER] = request_id
    logger.info(
        "请求完成",
        request_id=request_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        client_ip=client_ip,
    )
    return response


@app.middleware("http")
async def enforce_read_only_accounts(request: Request, call_next):
    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        allowed_paths = {
            f"{settings.API_PREFIX}/auth/login",
            f"{settings.API_PREFIX}/auth/init",
            f"{settings.API_PREFIX}/auth/change-password",
        }
        if request.url.path not in allowed_paths:
            authorization = request.headers.get("Authorization", "")
            if authorization.startswith("Bearer "):
                token = authorization.split(" ", 1)[1]
                try:
                    payload = jwt.decode(token, settings.SECRET_KEY, algorithms=["HS256"])
                    user_id = payload.get("sub")
                    if user_id:
                        db = SessionLocal()
                        try:
                            user = db.query(User).filter(User.id == int(user_id)).first()
                            if user and user.read_only:
                                return JSONResponse(
                                    status_code=403,
                                    content={"detail": "权限不足"},
                                    headers={settings.REQUEST_ID_HEADER: getattr(request.state, "request_id", "")},
                                )
                        finally:
                            db.close()
                except JWTError:
                    pass
    return await call_next(request)


@app.middleware("http")
async def audit_requests(request: Request, call_next):
    if not request.url.path.startswith(settings.API_PREFIX):
        return await call_next(request)

    request_id = getattr(request.state, "request_id", None) or request.headers.get(settings.REQUEST_ID_HEADER) or build_request_id()
    request.state.request_id = request_id
    client_ip = get_client_ip(request)
    user_id, username = _audit_user_from_request(request)
    content_length = int(request.headers.get("content-length") or 0)
    content_type = request.headers.get("content-type", "")
    body_bytes = b""
    audit_body = None

    if request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if content_length and content_length > 128 * 1024:
            audit_body = {"_truncated": True, "size": content_length}
        else:
            body_bytes = await request.body()
            audit_body = _parse_audit_body(content_type, body_bytes)
            await _rebuild_request_body(request, body_bytes)
            if request.url.path == f"{settings.API_PREFIX}/auth/login" and isinstance(audit_body, dict):
                username = username or audit_body.get("username")

    status_code = 500
    error_message = None
    try:
        response = await call_next(request)
        status_code = response.status_code
        return response
    except Exception as exc:
        error_message = str(exc)
        raise
    finally:
        try:
            resource_type, resource_id = _audit_resource_from_path(request.url.path)
            db = SessionLocal()
            try:
                db.add(
                    AuditLog(
                        request_id=request_id,
                        user_id=user_id,
                        username=username or "anonymous",
                        action=_audit_action_for_method(request.method, request.url.path),
                        menu=_audit_menu_for_path(request.url.path),
                        method=request.method,
                        path=request.url.path,
                        query_params=dict(request.query_params),
                        resource_type=resource_type,
                        resource_id=resource_id,
                        request_body=audit_body,
                        response_status=status_code,
                        success=200 <= status_code < 400,
                        client_ip=client_ip,
                        user_agent=request.headers.get("user-agent"),
                        error_message=error_message,
                    )
                )
                db.commit()
            finally:
                db.close()
        except Exception as audit_exc:
            logger.error("写入审计日志失败", error=str(audit_exc), request_id=request_id)

# 注册API路由
app.include_router(api_router, prefix=settings.API_PREFIX)


@app.get("/")
async def root():
    """根路径"""
    return {
        "name": settings.APP_NAME,
        "version": settings.APP_VERSION,
        "docs": settings.DOCS_URL
    }


@app.get("/health")
async def health_check():
    """健康检查"""
    return {
        "status": "healthy",
        "version": settings.APP_VERSION,
        "websocket_connections": ws_manager.get_connection_count(),
        "gnmi_connected": gnmi_manager.get_connected_count(),
        "syslog_enabled": settings.SYSLOG_ENABLED,
        "syslog_port": settings.SYSLOG_LISTEN_PORT,
        "snmp_trap_enabled": settings.SNMP_TRAP_ENABLED,
        "snmp_trap_port": settings.SNMP_TRAP_LISTEN_PORT,
    }


@app.get("/health/dependencies")
async def health_dependencies():
    db_ok = False
    redis_ok = False
    try:
        db = SessionLocal()
        try:
            db.execute(text("SELECT 1"))
            db_ok = True
        finally:
            db.close()
    except Exception:
        db_ok = False

    try:
        from app.utils.redis_client import redis_client
        redis_ok = bool(redis_client.ping())
        influx_health_raw = redis_client.get("system:health:influxdb:state")
    except Exception:
        redis_ok = False
        influx_health_raw = None

    status_value = "healthy" if db_ok and redis_ok else "degraded"
    influx_health = None
    if influx_health_raw:
        try:
            influx_health = json.loads(influx_health_raw)
        except Exception:
            influx_health = None
    return {
        "status": status_value,
        "database": db_ok,
        "redis": redis_ok,
        "influxdb_url": settings.INFLUXDB_URL,
        "influxdb_storage": influx_health,
        "rabbitmq_host": settings.RABBITMQ_HOST,
    }


# WebSocket端点
@app.websocket("/ws")
async def websocket_endpoint(websocket: WebSocket):
    """WebSocket连接端点"""
    await ws_manager.connect(websocket)
    try:
        while True:
            # 接收客户端消息
            data = await websocket.receive_json()
            
            # 处理消息
            msg_type = data.get("type")
            
            if msg_type == "subscribe":
                topic = data.get("topic", "")
                await ws_manager.subscribe(websocket, topic)
                await ws_manager.send_personal_message(
                    {"type": "subscribed", "topic": topic},
                    websocket
                )
                
            elif msg_type == "unsubscribe":
                topic = data.get("topic", "")
                await ws_manager.unsubscribe(websocket, topic)
                
            elif msg_type == "ping":
                await ws_manager.send_personal_message(
                    {"type": "pong", "timestamp": data.get("timestamp")},
                    websocket
                )
                
    except WebSocketDisconnect:
        await ws_manager.disconnect(websocket)
    except Exception as e:
        logger.error("WebSocket异常", error=str(e))
        await ws_manager.disconnect(websocket)


# 全局异常处理
@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    """全局异常处理"""
    logger.error("未处理的异常", error=str(exc))
    return JSONResponse(
        status_code=500,
        content={"detail": "服务器内部错误"}
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "app.main:app",
        host="0.0.0.0",
        port=8000,
        reload=settings.DEBUG,
        log_level="info"
    )
