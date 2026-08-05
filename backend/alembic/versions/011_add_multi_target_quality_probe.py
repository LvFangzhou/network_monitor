"""add multi-target quality probe fields

Revision ID: 011_add_multi_target_quality_probe
Revises: 010_add_structured_h3c_version_baseline
"""
from alembic import op
import sqlalchemy as sa


revision = "011_add_multi_target_quality_probe"
down_revision = "010_add_structured_h3c_version_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("quality_probe_targets", sa.Column("target_addresses", sa.JSON(), nullable=True, server_default=sa.text("'[]'::json")))
    op.add_column("quality_probe_targets", sa.Column("target_statuses", sa.JSON(), nullable=True, server_default=sa.text("'{}'::json")))
    op.execute("UPDATE quality_probe_targets SET target_addresses = json_build_array(target)")
    op.execute("UPDATE quality_probe_targets SET mtr_interval_seconds = 3600 WHERE mtr_interval_seconds IS NULL OR mtr_interval_seconds < 3600")


def downgrade():
    op.drop_column("quality_probe_targets", "target_statuses")
    op.drop_column("quality_probe_targets", "target_addresses")
