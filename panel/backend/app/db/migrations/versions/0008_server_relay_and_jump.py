"""Client-facing relay address and SSH jump host for a server (cascaded servers).

Revision ID: 0008
Revises: 0007
"""

import sqlalchemy as sa
from alembic import op

revision = "0008"
down_revision = "0007"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("servers", sa.Column("client_host", sa.String(length=255), nullable=True))
    op.add_column("servers", sa.Column("ssh_jump_enc", sa.Text(), nullable=True))


def downgrade() -> None:
    op.drop_column("servers", "ssh_jump_enc")
    op.drop_column("servers", "client_host")
