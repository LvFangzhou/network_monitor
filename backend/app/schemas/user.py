"""
用户相关 Pydantic Schemas
"""
from pydantic import BaseModel, Field, EmailStr
from typing import Optional, List
from datetime import datetime


class UserBase(BaseModel):
    """用户基础Schema"""
    username: str = Field(..., min_length=3, max_length=50)
    email: EmailStr
    full_name: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    department: Optional[str] = Field(None, max_length=100)
    is_active: bool = True
    read_only: bool = False
    allowed_menus: List[str] = Field(default_factory=list)


class UserCreate(UserBase):
    """创建用户Schema"""
    password: Optional[str] = Field(None, min_length=6, max_length=100)
    role_ids: List[int] = Field(default_factory=list)


class UserUpdate(BaseModel):
    """更新用户Schema"""
    username: Optional[str] = Field(None, min_length=3, max_length=50)
    email: Optional[EmailStr] = None
    full_name: Optional[str] = Field(None, max_length=100)
    phone: Optional[str] = Field(None, max_length=20)
    department: Optional[str] = Field(None, max_length=100)
    is_active: Optional[bool] = None
    read_only: Optional[bool] = None
    allowed_menus: Optional[List[str]] = None
    password: Optional[str] = Field(None, min_length=6, max_length=100)
    role_ids: Optional[List[int]] = None


class UserResponse(UserBase):
    """用户响应Schema"""
    id: int
    is_superuser: bool
    last_login: Optional[datetime] = None
    roles: List[str] = Field(default_factory=list)
    created_at: datetime
    updated_at: Optional[datetime] = None

    class Config:
        from_attributes = True


class RoleBase(BaseModel):
    """角色基础Schema"""
    name: str = Field(..., min_length=1, max_length=50)
    description: Optional[str] = None


class RoleCreate(RoleBase):
    permission_ids: List[int] = Field(default_factory=list)


class RoleUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=50)
    description: Optional[str] = None
    permission_ids: Optional[List[int]] = None


class PermissionBase(BaseModel):
    """权限基础Schema"""
    name: str
    code: str
    category: str


class PermissionResponse(PermissionBase):
    id: int
    description: Optional[str] = None

    class Config:
        from_attributes = True


class RoleResponse(RoleBase):
    """角色响应Schema"""
    id: int
    created_at: datetime
    permissions: List[PermissionResponse] = Field(default_factory=list)

    class Config:
        from_attributes = True


class Token(BaseModel):
    """Token响应"""
    access_token: str
    token_type: str = "bearer"
    expires_in: int


class TokenPayload(BaseModel):
    """Token载荷"""
    sub: Optional[int] = None
    exp: Optional[datetime] = None


class LoginRequest(BaseModel):
    """登录请求"""
    username: str
    password: str


class PasswordChange(BaseModel):
    """修改密码"""
    old_password: str
    new_password: str = Field(..., min_length=6, max_length=100)

