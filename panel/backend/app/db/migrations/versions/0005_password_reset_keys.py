"""Password reset keys: one-time keys get a purpose and an optional expiry.

Revision ID: 0005
Revises: 0004
"""

import sqlalchemy as sa
from alembic import op

revision = "0005"
down_revision = "0004"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("invite_keys", sa.Column("purpose", sa.String(length=8), server_default="invite", nullable=False))
    op.create_check_constraint(op.f("ck_invite_keys_purpose"), "invite_keys", "purpose IN ('invite', 'reset')")
    op.add_column("invite_keys", sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True))


def downgrade() -> None:
    op.execute("DELETE FROM invite_keys WHERE purpose = 'reset'")
    op.drop_column("invite_keys", "expires_at")
    op.drop_constraint(op.f("ck_invite_keys_purpose"), "invite_keys", type_="check")
    op.drop_column("invite_keys", "purpose")
