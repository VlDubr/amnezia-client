"""Scrub Xray server private keys that older drivers persisted in server_containers.params_json:
the REALITY privateKey and inline TLS certificate keys.

Revision ID: 0006
Revises: 0005
"""

from alembic import op

revision = "0006"
down_revision = "0005"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("UPDATE server_containers "
               "SET params_json = (params_json #- '{stream,realitySettings,privateKey}') "
               "#- '{stream,tlsSettings,certificates}' "
               "WHERE container = 'amnezia-xray'")


def downgrade() -> None:
    pass  # the removed keys cannot be restored; the driver re-reads them from the server on the next sync
