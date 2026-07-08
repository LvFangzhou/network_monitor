"""add config backup startup sync fields

Revision ID: 003
Revises: 002
Create Date: 2026-07-07 00:00:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "003"
down_revision = "002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("config_backup_jobs", sa.Column("config_changed_count", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("config_backup_jobs", sa.Column("config_saved_count", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("config_backup_jobs", sa.Column("config_save_failed_count", sa.Integer(), nullable=True, server_default="0"))

    op.add_column("config_backup_results", sa.Column("startup_command", sa.String(length=255), nullable=True))
    op.add_column("config_backup_results", sa.Column("startup_config_hash", sa.String(length=64), nullable=True))
    op.add_column("config_backup_results", sa.Column("startup_line_count", sa.Integer(), nullable=True, server_default="0"))
    op.add_column("config_backup_results", sa.Column("config_sync_status", sa.String(length=30), nullable=True))
    op.add_column("config_backup_results", sa.Column("config_sync_diff", sa.Text(), nullable=True))
    op.add_column("config_backup_results", sa.Column("config_save_command", sa.String(length=255), nullable=True))
    op.add_column("config_backup_results", sa.Column("config_save_status", sa.String(length=30), nullable=True))
    op.add_column("config_backup_results", sa.Column("config_save_message", sa.Text(), nullable=True))

    op.create_index("ix_config_backup_results_startup_config_hash", "config_backup_results", ["startup_config_hash"])
    op.create_index("ix_config_backup_results_config_sync_status", "config_backup_results", ["config_sync_status"])
    op.create_index("ix_config_backup_results_config_save_status", "config_backup_results", ["config_save_status"])


def downgrade() -> None:
    op.drop_index("ix_config_backup_results_config_save_status", table_name="config_backup_results")
    op.drop_index("ix_config_backup_results_config_sync_status", table_name="config_backup_results")
    op.drop_index("ix_config_backup_results_startup_config_hash", table_name="config_backup_results")

    op.drop_column("config_backup_results", "config_save_message")
    op.drop_column("config_backup_results", "config_save_status")
    op.drop_column("config_backup_results", "config_save_command")
    op.drop_column("config_backup_results", "config_sync_diff")
    op.drop_column("config_backup_results", "config_sync_status")
    op.drop_column("config_backup_results", "startup_line_count")
    op.drop_column("config_backup_results", "startup_config_hash")
    op.drop_column("config_backup_results", "startup_command")

    op.drop_column("config_backup_jobs", "config_save_failed_count")
    op.drop_column("config_backup_jobs", "config_saved_count")
    op.drop_column("config_backup_jobs", "config_changed_count")
