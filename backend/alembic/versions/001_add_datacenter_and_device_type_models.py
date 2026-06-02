"""add datacenter and device type models

Revision ID: 001
Revises: 
Create Date: 2026-04-17

"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = '001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # 创建机房表
    op.create_table(
        'datacenters',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=100), nullable=False),
        sa.Column('location', sa.String(length=255), nullable=True),
        sa.Column('address', sa.String(length=500), nullable=True),
        sa.Column('contact_person', sa.String(length=100), nullable=True),
        sa.Column('contact_phone', sa.String(length=20), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_datacenters_id'), 'datacenters', ['id'], unique=False)

    # 创建设备类型表
    op.create_table(
        'device_types',
        sa.Column('id', sa.Integer(), nullable=False),
        sa.Column('name', sa.String(length=50), nullable=False),
        sa.Column('display_name', sa.String(length=100), nullable=True),
        sa.Column('icon', sa.String(length=50), nullable=True),
        sa.Column('description', sa.Text(), nullable=True),
        sa.Column('is_active', sa.Boolean(), nullable=True, default=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column('updated_at', sa.DateTime(timezone=True), onupdate=sa.func.now()),
        sa.PrimaryKeyConstraint('id'),
        sa.UniqueConstraint('name')
    )
    op.create_index(op.f('ix_device_types_id'), 'device_types', ['id'], unique=False)

    # 修改devices表 - 添加新字段
    op.add_column('devices', sa.Column('datacenter_id', sa.Integer(), nullable=True))
    op.add_column('devices', sa.Column('device_type_id', sa.Integer(), nullable=True))
    
    # 添加外键约束
    op.create_foreign_key('fk_devices_datacenter_id', 'devices', 'datacenters', ['datacenter_id'], ['id'])
    op.create_foreign_key('fk_devices_device_type_id', 'devices', 'device_types', ['device_type_id'], ['id'])
    
    # 插入默认设备类型
    op.bulk_insert(
        sa.table('device_types',
            sa.column('id', sa.Integer),
            sa.column('name', sa.String),
            sa.column('display_name', sa.String),
            sa.column('icon', sa.String),
            sa.column('description', sa.Text),
            sa.column('is_active', sa.Boolean)
        ),
        [
            {'id': 1, 'name': 'Firewall', 'display_name': '防火墙', 'icon': 'security-scan', 'description': '防火墙设备', 'is_active': True},
            {'id': 2, 'name': 'Switch', 'display_name': '交换机', 'icon': 'apartment', 'description': '网络交换机', 'is_active': True},
            {'id': 3, 'name': 'Router', 'display_name': '路由器', 'icon': 'cloud-server', 'description': '网络路由器', 'is_active': True},
            {'id': 4, 'name': 'Console', 'display_name': '控制台', 'icon': 'desktop', 'description': '控制台服务器', 'is_active': True},
        ]
    )


def downgrade() -> None:
    # 删除外键约束
    op.drop_constraint('fk_devices_device_type_id', 'devices', type_='foreignkey')
    op.drop_constraint('fk_devices_datacenter_id', 'devices', type_='foreignkey')
    
    # 删除新字段
    op.drop_column('devices', 'device_type_id')
    op.drop_column('devices', 'datacenter_id')
    
    # 删除表
    op.drop_index(op.f('ix_device_types_id'), table_name='device_types')
    op.drop_table('device_types')
    op.drop_index(op.f('ix_datacenters_id'), table_name='datacenters')
    op.drop_table('datacenters')
