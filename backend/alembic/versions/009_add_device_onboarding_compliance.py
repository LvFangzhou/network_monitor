"""add device onboarding compliance

Revision ID: 009
Revises: 008
"""
from alembic import op
import sqlalchemy as sa


revision = "009"
down_revision = "008"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "device_model_profiles",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("vendor", sa.String(50), nullable=False),
        sa.Column("model_pattern", sa.String(120), nullable=False),
        sa.Column("network_type", sa.String(50), nullable=False, server_default="general"),
        sa.Column("device_type", sa.String(50)),
        sa.Column("default_role", sa.String(50)),
        sa.Column("capabilities", sa.JSON(), nullable=False),
        sa.Column("required_checks", sa.JSON(), nullable=False),
        sa.Column("description", sa.Text()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
        sa.UniqueConstraint("vendor", "model_pattern", "network_type", name="uq_device_model_profile_scope"),
    )
    op.create_index("ix_device_model_profiles_vendor", "device_model_profiles", ["vendor"])
    op.create_index("ix_device_model_profiles_model_pattern", "device_model_profiles", ["model_pattern"])
    op.create_index("ix_device_model_profiles_network_type", "device_model_profiles", ["network_type"])

    op.create_table(
        "version_baselines",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("name", sa.String(150), nullable=False),
        sa.Column("model_profile_id", sa.Integer(), sa.ForeignKey("device_model_profiles.id", ondelete="SET NULL")),
        sa.Column("vendor", sa.String(50)),
        sa.Column("model_pattern", sa.String(120)),
        sa.Column("device_role", sa.String(50)),
        sa.Column("allowed_versions", sa.JSON(), nullable=False),
        sa.Column("minimum_version", sa.String(100)),
        sa.Column("required_patches", sa.JSON(), nullable=False),
        sa.Column("forbidden_versions", sa.JSON(), nullable=False),
        sa.Column("recommendation", sa.Text()),
        sa.Column("priority", sa.Integer(), nullable=False, server_default="100"),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_version_baselines_model_profile_id", "version_baselines", ["model_profile_id"])
    op.create_index("ix_version_baselines_vendor", "version_baselines", ["vendor"])
    op.create_index("ix_version_baselines_model_pattern", "version_baselines", ["model_pattern"])
    op.create_index("ix_version_baselines_device_role", "version_baselines", ["device_role"])

    op.create_table(
        "device_compliance_snapshots",
        sa.Column("id", sa.Integer(), primary_key=True),
        sa.Column("device_id", sa.Integer(), sa.ForeignKey("devices.id", ondelete="CASCADE"), nullable=False, unique=True),
        sa.Column("model_profile_id", sa.Integer(), sa.ForeignKey("device_model_profiles.id", ondelete="SET NULL")),
        sa.Column("version_baseline_id", sa.Integer(), sa.ForeignKey("version_baselines.id", ondelete="SET NULL")),
        sa.Column("overall_status", sa.String(30), nullable=False, server_default="pending"),
        sa.Column("score", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("observed_vendor", sa.String(50)),
        sa.Column("observed_model", sa.String(120)),
        sa.Column("observed_version", sa.String(255)),
        sa.Column("observed_patches", sa.JSON(), nullable=False),
        sa.Column("checks", sa.JSON(), nullable=False),
        sa.Column("blockers", sa.JSON(), nullable=False),
        sa.Column("evaluated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True)),
    )
    op.create_index("ix_device_compliance_snapshots_device_id", "device_compliance_snapshots", ["device_id"])
    op.create_index("ix_device_compliance_snapshots_overall_status", "device_compliance_snapshots", ["overall_status"])
    op.create_index("ix_device_compliance_snapshots_evaluated_at", "device_compliance_snapshots", ["evaluated_at"])


def downgrade():
    op.drop_table("device_compliance_snapshots")
    op.drop_table("version_baselines")
    op.drop_table("device_model_profiles")
