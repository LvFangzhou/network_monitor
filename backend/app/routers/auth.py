"""
认证授权路由
"""
from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from datetime import datetime, timedelta, timezone
from jose import JWTError, jwt
import bcrypt
from typing import Optional

from app.database import get_db
from app.models import AuditLog, CustomerAudit, CircuitAudit, User, Role, Permission, DEFAULT_PERMISSIONS, DEFAULT_MENU_PERMISSIONS
from app.schemas import (
    UserCreate, UserUpdate, UserResponse, Token,
)
from app.config import settings
from app.core import get_logger
from app.utils.redis_client import redis_client

logger = get_logger(__name__)
router = APIRouter()


def verify_password(plain_password: str, hashed_password: str) -> bool:
    """验证密码"""
    password_bytes = plain_password.encode('utf-8')
    hash_bytes = hashed_password.encode('utf-8')
    return bcrypt.checkpw(password_bytes, hash_bytes)


def get_password_hash(password: str) -> str:
    """获取密码哈希"""
    password_bytes = password.encode('utf-8')
    # bcrypt 自动处理 salt
    hash_bytes = bcrypt.hashpw(password_bytes, bcrypt.gensalt())
    return hash_bytes.decode('utf-8')

# OAuth2 scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl=f"{settings.API_PREFIX}/auth/login")

# JWT配置
SECRET_KEY = settings.SECRET_KEY
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 7 * 24 * 60
ONLINE_USER_TTL_SECONDS = 5 * 60

MENU_OPTIONS = [
    {"label": "仪表盘", "value": "/dashboard"},
    {"label": "字典管理", "value": "/device-dictionaries"},
    {"label": "网络设备", "value": "/devices"},
    {"label": "公网管理", "value": "/public-circuits"},
    {"label": "专线管理", "value": "/private-circuits"},
    {"label": "IPDB", "value": "/ipdb"},
    {"label": "配置备份", "value": "/config-backups"},
    {"label": "机房管理（字典管理内）", "value": "/datacenters"},
    {"label": "供应商管理（字典管理内）", "value": "/vendors"},
    {"label": "客户管理", "value": "/customers"},
    {"label": "告警规则", "value": "/alerts/rules"},
    {"label": "告警历史", "value": "/alerts/history"},
    {"label": "告警日志", "value": "/alerts/audit"},
    {"label": "告警屏蔽", "value": "/alerts/silences"},
    {"label": "端口查询", "value": "/port-query"},
    {"label": "IP查询", "value": "/ip-flow-query"},
    {"label": "质量查询", "value": "/quality-query"},
    {"label": "流量查询", "value": "/traffic-query"},
    {"label": "设备总览", "value": "/device-overview"},
    {"label": "模块信息查询", "value": "/module-info-query"},
    {"label": "无损信息查询", "value": "/lossless-info-query"},
    {"label": "系统设置", "value": "/settings"},
    {"label": "Tacacs管理", "value": "/tacacs"},
]

def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    """创建访问令牌"""
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)
    return encoded_jwt


def normalize_allowed_menus(allowed_menus: Optional[list[str]], is_superuser: bool = False) -> list[str]:
    if is_superuser:
        return DEFAULT_MENU_PERMISSIONS.copy()
    normalized = list(allowed_menus or ["/dashboard", "/devices", "/port-query", "/ip-flow-query", "/quality-query", "/traffic-query", "/device-overview"])
    has_monitor_center_access = any(path in normalized for path in [
        "/metrics",
        "/device-overview",
        "/port-query",
        "/ip-flow-query",
        "/module-info-query",
        "/lossless-info-query",
    ])
    if "/metrics" in normalized and "/port-query" not in normalized:
        normalized.append("/port-query")
    if "/metrics" in normalized and "/device-overview" not in normalized:
        normalized.append("/device-overview")
    if has_monitor_center_access and "/quality-query" not in normalized:
        normalized.append("/quality-query")
    if has_monitor_center_access and "/traffic-query" not in normalized:
        normalized.append("/traffic-query")
    if "/metrics" in normalized and "/config-backups" not in normalized:
        normalized.append("/config-backups")
    if "/metrics" in normalized and "/module-info-query" not in normalized:
        normalized.append("/module-info-query")
    if "/metrics" in normalized and "/lossless-info-query" not in normalized:
        normalized.append("/lossless-info-query")
    return [path for path in normalized if path != "/metrics"]


def _online_key(user_id: int) -> str:
    return f"auth:online:user:{user_id}"


def _last_seen_key(user_id: int) -> str:
    return f"auth:online:last_seen:user:{user_id}"


def _touch_online_user(user: User) -> None:
    try:
        redis_client.setex(_online_key(user.id), ONLINE_USER_TTL_SECONDS, "1")
        redis_client.set(_last_seen_key(user.id), str(datetime.now(timezone.utc).timestamp()))
    except Exception as exc:
        logger.warning("更新用户在线状态失败", username=user.username, error=str(exc))


def _is_online_user(user_id: int) -> bool:
    try:
        return bool(redis_client.exists(_online_key(user_id)))
    except Exception as exc:
        logger.warning("读取用户在线状态失败", user_id=user_id, error=str(exc))
        return False


def _last_offline_at(user_id: int, online: bool) -> Optional[str]:
    if online:
        return None
    try:
        value = redis_client.get(_last_seen_key(user_id))
        if not value:
            return None
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="ignore")
        offline_at = datetime.fromtimestamp(float(value) + ONLINE_USER_TTL_SECONDS, timezone.utc)
        return offline_at.isoformat()
    except Exception as exc:
        logger.warning("读取用户最后离线时间失败", user_id=user_id, error=str(exc))
        return None


def create_user_record(db: Session, user_data: UserCreate) -> User:
    existing_user = db.query(User).filter(User.username == user_data.username).first()
    if existing_user:
        raise HTTPException(status_code=400, detail="用户名已存在")

    existing_email = db.query(User).filter(User.email == user_data.email).first()
    if existing_email:
        raise HTTPException(status_code=400, detail="邮箱已被注册")

    if not user_data.password:
        raise HTTPException(status_code=400, detail="账号必须设置密码")

    db_user = User(
        username=user_data.username,
        email=user_data.email,
        hashed_password=get_password_hash(user_data.password),
        full_name=user_data.full_name,
        phone=user_data.phone,
        department=user_data.department,
        is_active=user_data.is_active,
        read_only=user_data.read_only,
        allowed_menus=normalize_allowed_menus(user_data.allowed_menus),
    )

    if user_data.role_ids:
        roles = db.query(Role).filter(Role.id.in_(user_data.role_ids)).all()
        db_user.roles = roles

    db.add(db_user)
    db.commit()
    db.refresh(db_user)
    return db_user


async def get_current_user(
    token: str = Depends(oauth2_scheme),
    db: Session = Depends(get_db)
) -> User:
    """获取当前用户"""
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="无效的认证凭据",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        user_id: str = payload.get("sub")
        if user_id is None:
            raise credentials_exception
    except JWTError:
        raise credentials_exception
    
    user = db.query(User).filter(User.id == int(user_id)).first()
    if user is None:
        raise credentials_exception
    return user


async def get_current_active_user(current_user: User = Depends(get_current_user)) -> User:
    """获取当前活跃用户"""
    if not current_user.is_active:
        raise HTTPException(status_code=400, detail="用户已被禁用")
    return current_user


def check_permission(user: User, permission_code: str):
    """检查用户权限"""
    if not user.has_permission(permission_code):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="权限不足"
        )


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: Session = Depends(get_db)
):
    """用户登录"""
    user = db.query(User).filter(User.username == form_data.username).first()

    authenticated_user: Optional[User] = None
    login_error_detail = "用户名或密码错误"

    if user:
        if not user.is_active:
            raise HTTPException(status_code=400, detail="账号已停用，请联系管理员")

        if verify_password(form_data.password, user.hashed_password):
            authenticated_user = user
        else:
            login_error_detail = "密码错误"
    else:
        login_error_detail = "用户不存在"

    if not authenticated_user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=login_error_detail,
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # 更新登录信息
    authenticated_user.last_login = datetime.now()
    authenticated_user.login_count += 1
    db.commit()
    _touch_online_user(authenticated_user)
    
    access_token_expires = timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    access_token = create_access_token(
        data={"sub": str(authenticated_user.id)}, expires_delta=access_token_expires
    )
    
    logger.info("用户登录成功", username=authenticated_user.username)
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60
    }


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(
    user_data: UserCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """用户注册"""
    if not current_user.is_superuser:
        check_permission(current_user, "user:create")
    db_user = create_user_record(db, user_data)
    logger.info("用户注册成功", username=user_data.username)
    return db_user.to_dict()


@router.get("/me", response_model=UserResponse)
async def get_me(current_user: User = Depends(get_current_active_user)):
    """获取当前用户信息"""
    _touch_online_user(current_user)
    user_data = current_user.to_dict()
    user_data["allowed_menus"] = normalize_allowed_menus(user_data.get("allowed_menus"), current_user.is_superuser)
    return user_data


@router.put("/me", response_model=dict)
async def update_me(
    user_data: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
):
    """更新当前登录账号的个人信息。"""
    update_data = user_data.model_dump(exclude_unset=True)
    for protected_key in ["username", "is_active", "is_superuser", "read_only", "allowed_menus", "role_ids"]:
        update_data.pop(protected_key, None)
    if "password" in update_data and update_data["password"]:
        current_user.hashed_password = get_password_hash(update_data.pop("password"))
    elif "password" in update_data:
        update_data.pop("password", None)
    if "email" in update_data and update_data["email"] and update_data["email"] != current_user.email:
        existing = db.query(User).filter(User.email == update_data["email"]).first()
        if existing:
            raise HTTPException(status_code=400, detail="邮箱已被注册")
    for key, value in update_data.items():
        setattr(current_user, key, value)
    db.commit()
    db.refresh(current_user)
    _touch_online_user(current_user)
    return {
        **current_user.to_dict(),
        "allowed_menus": normalize_allowed_menus(current_user.allowed_menus, current_user.is_superuser),
        "online": True,
    }


@router.post("/change-password")
async def change_password(
    old_password: str,
    new_password: str,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    """修改密码"""
    if not verify_password(old_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="原密码错误")
    
    current_user.hashed_password = get_password_hash(new_password)
    db.commit()
    
    logger.info("密码修改成功", username=current_user.username)
    return {"message": "密码修改成功"}


@router.post("/init")
async def init_auth(db: Session = Depends(get_db)):
    """初始化认证数据（创建默认权限、角色和管理员）"""
    # 创建权限
    for perm_data in DEFAULT_PERMISSIONS:
        existing = db.query(Permission).filter(Permission.code == perm_data["code"]).first()
        if not existing:
            perm = Permission(**perm_data)
            db.add(perm)
    
    db.commit()
    
    # 创建管理员角色
    admin_role = db.query(Role).filter(Role.name == "管理员").first()
    if not admin_role:
        admin_role = Role(name="管理员", description="系统管理员，拥有所有权限")
        all_perms = db.query(Permission).all()
        admin_role.permissions = all_perms
        db.add(admin_role)
        db.commit()
    
    # 创建默认管理员
    admin_user = db.query(User).filter(User.username == "admin").first()
    if not admin_user:
        admin_user = User(
            username="admin",
            email="admin@example.com",
            hashed_password=get_password_hash("admin123"),
            full_name="系统管理员",
            is_active=True,
            is_superuser=True,
            allowed_menus=DEFAULT_MENU_PERMISSIONS.copy(),
        )
        admin_user.roles = [admin_role]
        db.add(admin_user)
        db.commit()
        
        logger.info("默认管理员创建成功", username="admin")
        return {
            "message": "初始化完成",
            "admin_user": {
                "username": "admin",
                "password": "admin123"
            }
        }
    
    return {"message": "认证数据已初始化"}


@router.get("/menu-options")
async def get_menu_options(current_user: User = Depends(get_current_active_user)):
    if not current_user.is_superuser:
        check_permission(current_user, "user:view")
    return {"items": MENU_OPTIONS}


@router.get("/audit-logs", response_model=dict)
async def list_audit_logs(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    username: Optional[str] = None,
    menu: Optional[str] = None,
    action: Optional[str] = None,
    method: Optional[str] = None,
    path: Optional[str] = None,
    success: Optional[bool] = None,
):
    if not current_user.is_superuser:
        check_permission(current_user, "system:logs")
    query = db.query(AuditLog)
    if username:
        query = query.filter(AuditLog.username.ilike(f"%{username}%"))
    if menu:
        query = query.filter(AuditLog.menu.ilike(f"%{menu}%"))
    if action:
        query = query.filter(AuditLog.action == action)
    if method:
        query = query.filter(AuditLog.method == method.upper())
    if path:
        query = query.filter(AuditLog.path.ilike(f"%{path}%"))
    if success is not None:
        query = query.filter(AuditLog.success == success)

    total = query.count()
    logs = query.order_by(AuditLog.created_at.desc()).offset(skip).limit(limit).all()
    return {
        "total": total,
        "items": [
            {
                "id": item.id,
                "request_id": item.request_id,
                "user_id": item.user_id,
                "username": item.username,
                "action": item.action,
                "menu": item.menu,
                "method": item.method,
                "path": item.path,
                "query_params": item.query_params or {},
                "resource_type": item.resource_type,
                "resource_id": item.resource_id,
                "request_body": item.request_body,
                "response_status": item.response_status,
                "success": item.success,
                "client_ip": item.client_ip,
                "user_agent": item.user_agent,
                "error_message": item.error_message,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            }
            for item in logs
        ],
    }


@router.post("/audit/menu-visit", response_model=dict)
async def record_menu_visit(
    payload: dict,
    current_user: User = Depends(get_current_active_user),
):
    return {
        "success": True,
        "path": payload.get("path"),
        "username": current_user.username,
    }


@router.get("/users", response_model=dict)
async def list_users(
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    if not current_user.is_superuser:
        check_permission(current_user, "user:view")
    _touch_online_user(current_user)
    users = db.query(User).order_by(User.id.asc()).all()
    user_online_states = {user.id: _is_online_user(user.id) for user in users}
    return {
        "total": len(users),
        "items": [
            {
                **user.to_dict(),
                "allowed_menus": normalize_allowed_menus(user.allowed_menus, user.is_superuser),
                "online": user_online_states.get(user.id, False),
                "last_offline_at": _last_offline_at(user.id, user_online_states.get(user.id, False)),
            }
            for user in users
        ],
    }


@router.post("/users", response_model=dict, status_code=status.HTTP_201_CREATED)
async def create_user(
    user_data: UserCreate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    if not current_user.is_superuser:
        check_permission(current_user, "user:create")
    db_user = create_user_record(db, user_data)
    return db_user.to_dict()


@router.put("/users/{user_id}", response_model=dict)
async def update_user(
    user_id: int,
    user_data: UserUpdate,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    if not current_user.is_superuser:
        check_permission(current_user, "user:update")
    db_user = db.query(User).filter(User.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")

    if user_data.username and user_data.username != db_user.username:
        existing = db.query(User).filter(User.username == user_data.username).first()
        if existing:
            raise HTTPException(status_code=400, detail="用户名已存在")
    if user_data.email and user_data.email != db_user.email:
        existing = db.query(User).filter(User.email == user_data.email).first()
        if existing:
            raise HTTPException(status_code=400, detail="邮箱已被注册")

    update_data = user_data.model_dump(exclude_unset=True)
    if "password" in update_data and update_data["password"]:
        db_user.hashed_password = get_password_hash(update_data.pop("password"))
    if "allowed_menus" in update_data:
        update_data["allowed_menus"] = normalize_allowed_menus(update_data["allowed_menus"], db_user.is_superuser)
    if "role_ids" in update_data and update_data["role_ids"] is not None:
        role_ids = update_data.pop("role_ids")
        db_user.roles = db.query(Role).filter(Role.id.in_(role_ids)).all() if role_ids else []
    for key, value in update_data.items():
        setattr(db_user, key, value)
    db.commit()
    db.refresh(db_user)
    return {
        **db_user.to_dict(),
        "allowed_menus": normalize_allowed_menus(db_user.allowed_menus, db_user.is_superuser),
    }


@router.delete("/users/{user_id}")
async def delete_user(
    user_id: int,
    current_user: User = Depends(get_current_active_user),
    db: Session = Depends(get_db)
):
    if not current_user.is_superuser:
        check_permission(current_user, "user:delete")
    if current_user.id == user_id:
        raise HTTPException(status_code=400, detail="不能删除当前登录用户")
    db_user = db.query(User).filter(User.id == user_id).first()
    if not db_user:
        raise HTTPException(status_code=404, detail="用户不存在")
    db.query(AuditLog).filter(AuditLog.user_id == user_id).update(
        {AuditLog.user_id: None},
        synchronize_session=False,
    )
    db.query(CustomerAudit).filter(CustomerAudit.actor_user_id == user_id).update(
        {CustomerAudit.actor_user_id: None},
        synchronize_session=False,
    )
    db.query(CircuitAudit).filter(CircuitAudit.actor_user_id == user_id).update(
        {CircuitAudit.actor_user_id: None},
        synchronize_session=False,
    )
    db.delete(db_user)
    db.commit()
    return {"message": "用户已删除"}
