"""add structured H3C version baseline

Revision ID: 010
Revises: 009
"""
from alembic import op
import sqlalchemy as sa


revision = "010"
down_revision = "009"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("version_baselines", sa.Column("platform_version", sa.String(100)))
    op.add_column(
        "version_baselines",
        sa.Column("allowed_releases", sa.JSON(), nullable=False, server_default=sa.text("'[]'::json")),
    )


def downgrade():
    op.drop_column("version_baselines", "allowed_releases")
    op.drop_column("version_baselines", "platform_version")
