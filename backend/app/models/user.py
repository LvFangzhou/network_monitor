"""
用户权限数据模型 - RBAC
"""
from sqlalchemy import Column, Integer, String, DateTime, Text, ForeignKey, Table, Boolean, JSON
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base


# 用户角色关联表
user_roles = Table(
    'user_roles',
    Base.metadata,
    Column('user_id', Integer, ForeignKey('users.id'), primary_key=True),
    Column('role_id', Integer, ForeignKey('roles.id'), primary_key=True)
)

# 角色权限关联表
role_permissions = Table(
    'role_permissions',
    Base.metadata,
    Column('role_id', Integer, ForeignKey('roles.id'), primary_key=True),
    Column('permission_id', Integer, ForeignKey('permissions.id'), primary_key=True)
)


class User(Base):
    """用户模型"""
    __tablename__ = "users"
    
    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(50), nullable=False, unique=True)
    email = Column(String(100), nullable=False, unique=True)
    hashed_password = Column(String(255), nullable=False)
    
    # 用户信息
    full_name = Column(String(100))
    phone = Column(String(20))
    department = Column(String(100))
    
    # 状态
    is_active = Column(Boolean, default=True)
    is_superuser = Column(Boolean, default=False)
    read_only = Column(Boolean, default=False)
    allowed_menus = Column(JSON, default=list)
    
    # 登录信息
    last_login = Column(DateTime(timezone=True))
    login_count = Column(Integer, default=0)
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    
    # 关系
    roles = relationship("Role", secondary=user_roles, back_populates="users")
    
    def __repr__(self):
        return f"<User {self.username}>"
    
    def has_permission(self, permission_code: str) -> bool:
        """检查用户是否有指定权限"""
        if self.is_superuser:
            return True
        for role in self.roles:
            for perm in role.permissions:
                if perm.code == permission_code:
                    return True
        return False
    
    def to_dict(self):
        return {
            "id": self.id,
            "username": self.username,
            "email": self.email,
            "full_name": self.full_name,
            "phone": self.phone,
            "department": self.department,
            "is_active": self.is_active,
            "is_superuser": self.is_superuser,
            "read_only": self.read_only,
            "allowed_menus": self.allowed_menus or [],
            "last_login": self.last_login.isoformat() if self.last_login else None,
            "roles": [role.name for role in self.roles] if self.roles else [],
            "created_at": self.created_at.isoformat() if self.created_at else None,
        }


class Role(Base):
    """角色模型"""
    __tablename__ = "roles"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False, unique=True)
    description = Column(Text)
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    users = relationship("User", secondary=user_roles, back_populates="roles")
    permissions = relationship("Permission", secondary=role_permissions, back_populates="roles")
    
    def __repr__(self):
        return f"<Role {self.name}>"


class Permission(Base):
    """权限模型"""
    __tablename__ = "permissions"
    
    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(50), nullable=False)
    code = Column(String(100), nullable=False, unique=True)
    description = Column(Text)
    
    # 权限分组: device, alert, user, system
    category = Column(String(50))
    
    # 时间戳
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    
    # 关系
    roles = relationship("Role", secondary=role_permissions, back_populates="permissions")
    
    def __repr__(self):
        return f"<Permission {self.code}>"


# 预定义权限
DEFAULT_PERMISSIONS = [
    # 设备管理权限
    {"name": "查看设备", "code": "device:view", "category": "device"},
    {"name": "创建设备", "code": "device:create", "category": "device"},
    {"name": "编辑设备", "code": "device:update", "category": "device"},
    {"name": "删除设备", "code": "device:delete", "category": "device"},
    
    # 告警管理权限
    {"name": "查看告警", "code": "alert:view", "category": "alert"},
    {"name": "创建告警规则", "code": "alert:create", "category": "alert"},
    {"name": "编辑告警规则", "code": "alert:update", "category": "alert"},
    {"name": "删除告警规则", "code": "alert:delete", "category": "alert"},
    {"name": "处理告警", "code": "alert:handle", "category": "alert"},
    
    # 用户管理权限
    {"name": "查看用户", "code": "user:view", "category": "user"},
    {"name": "创建用户", "code": "user:create", "category": "user"},
    {"name": "编辑用户", "code": "user:update", "category": "user"},
    {"name": "删除用户", "code": "user:delete", "category": "user"},
    
    # 系统管理权限
    {"name": "系统设置", "code": "system:settings", "category": "system"},
    {"name": "查看日志", "code": "system:logs", "category": "system"},
    {"name": "查看监控", "code": "system:monitor", "category": "system"},
]


class AuditLog(Base):
    """全局操作审计日志"""
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    request_id = Column(String(64), index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, index=True)
    username = Column(String(100), index=True)
    action = Column(String(50), nullable=False, index=True)
    menu = Column(String(100), index=True)
    method = Column(String(10), nullable=False)
    path = Column(String(255), nullable=False, index=True)
    query_params = Column(JSON, default=dict)
    resource_type = Column(String(100), index=True)
    resource_id = Column(String(100), index=True)
    request_body = Column(JSON)
    response_status = Column(Integer, index=True)
    success = Column(Boolean, default=True, index=True)
    client_ip = Column(String(64), index=True)
    user_agent = Column(Text)
    error_message = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), index=True)

    user = relationship("User")
# 默认菜单
DEFAULT_MENU_PERMISSIONS = [
    "/dashboard",
    "/devices",
    "/device-dictionaries",
    "/customers",
    "/datacenters",
    "/vendors",
    "/public-circuits",
    "/private-circuits",
    "/ipdb",
    "/alerts/rules",
    "/alerts/history",
    "/alerts/audit",
    "/alerts/silences",
    "/grafana",
    "/ip-flow-query",
    "/quality-query",
    "/traffic-query",
    "/device-overview",
    "/module-info-query",
    "/lossless-info-query",
    "/config-backups",
    "/settings",
    "/tacacs",
]
