"""add SNMP NQA quality probe source

Revision ID: 008
Revises: 007_link_quality_probe_to_circuit
"""
from alembic import op
import sqlalchemy as sa


revision = "008"
down_revision = "007"
branch_labels = None
depends_on = None


def upgrade():
    inspector = sa.inspect(op.get_bind())
    columns = {column["name"] for column in inspector.get_columns("quality_probe_targets")}
    additions = (
        ("device_id", sa.Column("device_id", sa.Integer(), nullable=True)),
        ("probe_source", sa.Column("probe_source", sa.String(30), nullable=False, server_default="server_icmp")),
        ("nqa_admin_name", sa.Column("nqa_admin_name", sa.String(32), nullable=True)),
        ("nqa_operation_tag", sa.Column("nqa_operation_tag", sa.String(32), nullable=True)),
    )
    for name, column in additions:
        if name not in columns:
            op.add_column("quality_probe_targets", column)
    foreign_keys = inspector.get_foreign_keys("quality_probe_targets")
    has_device_foreign_key = any(
        constraint.get("constrained_columns") == ["device_id"]
        and constraint.get("referred_table") == "devices"
        for constraint in foreign_keys
    )
    if not has_device_foreign_key:
        op.create_foreign_key(
            "fk_quality_probe_targets_device_id", "quality_probe_targets", "devices", ["device_id"], ["id"], ondelete="SET NULL"
        )
    op.execute("CREATE INDEX IF NOT EXISTS idx_quality_probe_targets_device ON quality_probe_targets (device_id)")


def downgrade():
    op.drop_index("idx_quality_probe_targets_device", table_name="quality_probe_targets")
    op.drop_constraint("fk_quality_probe_targets_device_id", "quality_probe_targets", type_="foreignkey")
    op.drop_column("quality_probe_targets", "nqa_operation_tag")
    op.drop_column("quality_probe_targets", "nqa_admin_name")
    op.drop_column("quality_probe_targets", "probe_source")
    op.drop_column("quality_probe_targets", "device_id")
