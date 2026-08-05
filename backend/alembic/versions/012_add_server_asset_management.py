"""add server asset management

Revision ID: 012
Revises: 011
"""
from alembic import op
import sqlalchemy as sa

revision = "012"
down_revision = "011"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()
    from app.models.server import ServerAsset, ServerComponent, ServerNIC, ServerIPAddress, ServerNetworkConnection, ServerPortChange
    for table in (ServerAsset.__table__, ServerComponent.__table__, ServerNIC.__table__, ServerIPAddress.__table__, ServerNetworkConnection.__table__, ServerPortChange.__table__):
        table.create(bind, checkfirst=True)


def downgrade():
    for name in ("server_port_changes", "server_network_connections", "server_ip_addresses", "server_nics", "server_components", "server_assets"):
        op.drop_table(name)
