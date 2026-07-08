"""add startup config content to backup results

Revision ID: 004
Revises: 003
Create Date: 2026-07-07 11:10:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "004"
down_revision = "003"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("config_backup_results", sa.Column("startup_config_content", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("config_backup_results", "startup_config_content")
