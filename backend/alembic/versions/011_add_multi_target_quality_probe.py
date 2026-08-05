"""add multi-target quality probe fields

Revision ID: 011
Revises: 010
"""
from alembic import op
import sqlalchemy as sa


revision = "011"
down_revision = "010"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    inspector = sa.inspect(bind)
    columns = {column["name"] for column in inspector.get_columns("quality_probe_targets")}
    additions = (
        ("probe_interface_name", sa.Column("probe_interface_name", sa.String(128))),
        ("target_addresses", sa.Column("target_addresses", sa.JSON(), server_default=sa.text("'[]'::json"))),
        ("target_statuses", sa.Column("target_statuses", sa.JSON(), server_default=sa.text("'{}'::json"))),
        ("mtr_enabled", sa.Column("mtr_enabled", sa.Boolean(), server_default=sa.text("false"))),
        ("mtr_interval_seconds", sa.Column("mtr_interval_seconds", sa.Integer(), server_default="3600")),
        ("last_mtr_at", sa.Column("last_mtr_at", sa.DateTime(timezone=True))),
        ("last_mtr_path_hash", sa.Column("last_mtr_path_hash", sa.String(64))),
        ("last_mtr_final_latency_ms", sa.Column("last_mtr_final_latency_ms", sa.Float())),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("quality_probe_targets", column)

    op.execute(
        "UPDATE quality_probe_targets SET target_addresses = json_build_array(target) "
        "WHERE target_addresses IS NULL OR json_array_length(target_addresses) = 0"
    )
    op.execute("UPDATE quality_probe_targets SET mtr_interval_seconds = 3600 WHERE mtr_interval_seconds IS NULL OR mtr_interval_seconds < 3600")

    metadata = sa.MetaData()
    target = sa.Table("quality_probe_targets", metadata, autoload_with=bind)
    snapshots = sa.Table(
        "quality_mtr_snapshots", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey(target.c.id, ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("target", sa.String(255), nullable=False),
        sa.Column("path_hash", sa.String(64), nullable=False, index=True),
        sa.Column("hop_count", sa.Integer(), default=0),
        sa.Column("final_hop_ip", sa.String(64)),
        sa.Column("final_avg_latency_ms", sa.Float()),
        sa.Column("final_loss_percent", sa.Float()),
        sa.Column("max_avg_latency_ms", sa.Float()),
        sa.Column("command", sa.String(255)),
        sa.Column("tool", sa.String(30)),
        sa.Column("raw_output", sa.Text()),
        sa.Column("hops", sa.JSON()),
        sa.Column("success", sa.Boolean(), default=True),
        sa.Column("error", sa.Text()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )
    snapshots.create(bind, checkfirst=True)
    events = sa.Table(
        "quality_mtr_events", metadata,
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("target_id", sa.Integer(), sa.ForeignKey(target.c.id, ondelete="CASCADE"), nullable=False, index=True),
        sa.Column("event_type", sa.String(50), nullable=False, index=True),
        sa.Column("title", sa.String(200), nullable=False),
        sa.Column("previous_snapshot_id", sa.Integer(), sa.ForeignKey(snapshots.c.id, ondelete="SET NULL")),
        sa.Column("current_snapshot_id", sa.Integer(), sa.ForeignKey(snapshots.c.id, ondelete="SET NULL")),
        sa.Column("previous_path_hash", sa.String(64)),
        sa.Column("current_path_hash", sa.String(64)),
        sa.Column("previous_final_latency_ms", sa.Float()),
        sa.Column("current_final_latency_ms", sa.Float()),
        sa.Column("latency_delta_ms", sa.Float()),
        sa.Column("detail", sa.JSON()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), index=True),
    )
    events.create(bind, checkfirst=True)

    op.execute("CREATE INDEX IF NOT EXISTS idx_quality_probe_targets_mtr_due ON quality_probe_targets (mtr_enabled, is_active, last_mtr_at)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_quality_mtr_snapshots_target_time ON quality_mtr_snapshots (target_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_quality_mtr_snapshots_path_hash ON quality_mtr_snapshots (path_hash)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_quality_mtr_events_target_time ON quality_mtr_events (target_id, created_at DESC)")
    op.execute("CREATE INDEX IF NOT EXISTS idx_quality_mtr_events_type_time ON quality_mtr_events (event_type, created_at DESC)")


def downgrade():
    op.drop_table("quality_mtr_events")
    op.drop_table("quality_mtr_snapshots")
    for name in (
        "last_mtr_final_latency_ms", "last_mtr_path_hash", "last_mtr_at",
        "mtr_interval_seconds", "mtr_enabled", "target_statuses",
        "target_addresses", "probe_interface_name",
    ):
        op.drop_column("quality_probe_targets", name)
