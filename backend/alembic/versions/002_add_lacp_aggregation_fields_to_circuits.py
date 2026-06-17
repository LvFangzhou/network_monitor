"""add lacp aggregation fields to circuits

Revision ID: 002
Revises: 001
Create Date: 2026-06-17

"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


# revision identifiers, used by Alembic.
revision: str = "002"
down_revision: Union[str, None] = "001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("circuits", sa.Column("aggregation_monitor_device_id", sa.Integer(), nullable=True))
    op.add_column("circuits", sa.Column("aggregation_interface_name", sa.String(length=100), nullable=True))
    op.create_foreign_key(
        "fk_circuits_aggregation_monitor_device_id",
        "circuits",
        "devices",
        ["aggregation_monitor_device_id"],
        ["id"],
    )


def downgrade() -> None:
    op.drop_constraint("fk_circuits_aggregation_monitor_device_id", "circuits", type_="foreignkey")
    op.drop_column("circuits", "aggregation_interface_name")
    op.drop_column("circuits", "aggregation_monitor_device_id")
