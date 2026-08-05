"""add datacenter network owner contact fields

Revision ID: 005
Revises: 004
Create Date: 2026-07-08 15:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "005"
down_revision = "004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("datacenters")}
    additions = (
        ("network_owner", sa.Column("network_owner", sa.String(length=100), nullable=True)),
        ("network_owner_email", sa.Column("network_owner_email", sa.String(length=255), nullable=True)),
        ("robot_mention", sa.Column("robot_mention", sa.String(length=255), nullable=True)),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("datacenters", column)


def downgrade() -> None:
    op.drop_column("datacenters", "robot_mention")
    op.drop_column("datacenters", "network_owner_email")
    op.drop_column("datacenters", "network_owner")
