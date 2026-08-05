"""add quality probe targets

Revision ID: 006
Revises: 005
Create Date: 2026-07-09 16:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "006"
down_revision = "005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    metadata = sa.MetaData()
    targets = sa.Table(
        "quality_probe_targets",
        metadata,
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("target", sa.String(length=255), nullable=False),
        sa.Column("datacenter_id", sa.Integer(), nullable=True),
        sa.Column("operator_name", sa.String(length=50), nullable=True),
        sa.Column("interval_seconds", sa.Integer(), nullable=True, server_default="60"),
        sa.Column("packet_count", sa.Integer(), nullable=True, server_default="5"),
        sa.Column("timeout_ms", sa.Integer(), nullable=True, server_default="1000"),
        sa.Column("latency_threshold_ms", sa.Integer(), nullable=True, server_default="100"),
        sa.Column("loss_threshold_percent", sa.Integer(), nullable=True, server_default="1"),
        sa.Column("jitter_threshold_ms", sa.Integer(), nullable=True, server_default="30"),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=True, server_default=sa.text("true")),
        sa.Column("last_probe_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success", sa.Boolean(), nullable=True),
        sa.Column("last_avg_latency_ms", sa.Float(), nullable=True),
        sa.Column("last_packet_loss_percent", sa.Float(), nullable=True),
        sa.Column("last_jitter_ms", sa.Float(), nullable=True),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(["datacenter_id"], ["datacenters.id"]),
        sa.PrimaryKeyConstraint("id"),
    )
    targets.create(bind, checkfirst=True)
    op.execute("CREATE INDEX IF NOT EXISTS ix_quality_probe_targets_id ON quality_probe_targets (id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_quality_probe_targets_active ON quality_probe_targets (is_active, id)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_quality_probe_targets_datacenter ON quality_probe_targets (datacenter_id)")


def downgrade() -> None:
    op.drop_index("idx_quality_probe_targets_datacenter", table_name="quality_probe_targets")
    op.drop_index("idx_quality_probe_targets_active", table_name="quality_probe_targets")
    op.drop_index("ix_quality_probe_targets_id", table_name="quality_probe_targets")
    op.drop_table("quality_probe_targets")
