"""link quality probe target to internet circuit

Revision ID: 007
Revises: 006
Create Date: 2026-07-21 15:30:00.000000
"""
from alembic import op
import sqlalchemy as sa


revision = "007"
down_revision = "006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("quality_probe_targets")}
    if "circuit_id" in columns:
        return
    op.add_column("quality_probe_targets", sa.Column("circuit_id", sa.Integer(), nullable=True))
    op.create_foreign_key(
        "fk_quality_probe_targets_circuit_id",
        "quality_probe_targets",
        "circuits",
        ["circuit_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index("idx_quality_probe_targets_circuit", "quality_probe_targets", ["circuit_id"])


def downgrade() -> None:
    columns = {column["name"] for column in sa.inspect(op.get_bind()).get_columns("quality_probe_targets")}
    if "circuit_id" not in columns:
        return
    op.drop_index("idx_quality_probe_targets_circuit", table_name="quality_probe_targets")
    op.drop_constraint("fk_quality_probe_targets_circuit_id", "quality_probe_targets", type_="foreignkey")
    op.drop_column("quality_probe_targets", "circuit_id")
