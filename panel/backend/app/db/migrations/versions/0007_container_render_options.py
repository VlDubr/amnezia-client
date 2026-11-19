"""Per-container admin overrides for rendering client configs (Xray fingerprint, fragment, ...).

Revision ID: 0007
Revises: 0006
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0007"
down_revision = "0006"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("server_containers", sa.Column("render_options_json", postgresql.JSONB(astext_type=sa.Text()),
                                                 nullable=False, server_default=sa.text("'{}'::jsonb")))


def downgrade() -> None:
    op.drop_column("server_containers", "render_options_json")
