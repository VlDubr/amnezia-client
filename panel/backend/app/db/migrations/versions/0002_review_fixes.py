"""Revoked clients, applied flag, traffic history kept after config deletion, dedupe on queued jobs only.

Revision ID: 0002
Revises: 0001
"""

import sqlalchemy as sa
from alembic import op

revision = "0002"
down_revision = "0001"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("configs", sa.Column("applied", sa.Boolean(), server_default=sa.text("true"), nullable=False))

    op.create_table(
        "revoked_clients",
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("container", sa.String(length=64), nullable=False),
        sa.Column("client_id", sa.String(length=255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], name=op.f("fk_revoked_clients_server_id_servers"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("server_id", "container", "client_id", name=op.f("pk_revoked_clients")),
    )

    op.drop_index("uq_jobs_active_dedupe", table_name="jobs",
                  postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.create_index("uq_jobs_queued_dedupe", "jobs", ["dedupe_key"], unique=True,
                    postgresql_where=sa.text("status = 'queued'"))

    op.rename_table("traffic_daily", "traffic_daily_old")
    op.execute("ALTER TABLE traffic_daily_old RENAME CONSTRAINT pk_traffic_daily TO pk_traffic_daily_old")
    op.create_table(
        "traffic_daily",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("config_id", sa.Integer(), nullable=True),
        sa.Column("user_id", sa.Integer(), nullable=True),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("rx", sa.BigInteger(), nullable=False),
        sa.Column("tx", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["configs.id"], name=op.f("fk_traffic_daily_config_id_configs"),
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], name=op.f("fk_traffic_daily_user_id_users"),
                                ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], name=op.f("fk_traffic_daily_server_id_servers"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_traffic_daily")),
        sa.UniqueConstraint("config_id", "day", name=op.f("uq_traffic_daily_config_id_day")),
    )
    op.create_index(op.f("ix_traffic_daily_user_id"), "traffic_daily", ["user_id"])
    op.create_index(op.f("ix_traffic_daily_server_id"), "traffic_daily", ["server_id"])
    op.execute(
        "INSERT INTO traffic_daily (config_id, user_id, server_id, day, rx, tx) "
        "SELECT t.config_id, c.user_id, c.server_id, t.day, t.rx, t.tx "
        "FROM traffic_daily_old t JOIN configs c ON c.id = t.config_id"
    )
    op.drop_table("traffic_daily_old")


def downgrade() -> None:
    op.create_table(
        "traffic_daily_old",
        sa.Column("config_id", sa.Integer(), nullable=False),
        sa.Column("day", sa.Date(), nullable=False),
        sa.Column("rx", sa.BigInteger(), nullable=False),
        sa.Column("tx", sa.BigInteger(), nullable=False),
        sa.ForeignKeyConstraint(["config_id"], ["configs.id"], name="fk_traffic_daily_config_id_configs_old",
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("config_id", "day", name="pk_traffic_daily_old"),
    )
    op.execute("INSERT INTO traffic_daily_old (config_id, day, rx, tx) "
               "SELECT config_id, day, rx, tx FROM traffic_daily WHERE config_id IS NOT NULL")
    op.drop_table("traffic_daily")
    op.rename_table("traffic_daily_old", "traffic_daily")
    op.execute("ALTER TABLE traffic_daily RENAME CONSTRAINT pk_traffic_daily_old TO pk_traffic_daily")
    op.execute("ALTER TABLE traffic_daily RENAME CONSTRAINT fk_traffic_daily_config_id_configs_old "
               "TO fk_traffic_daily_config_id_configs")
    op.drop_index("uq_jobs_queued_dedupe", table_name="jobs", postgresql_where=sa.text("status = 'queued'"))
    op.create_index("uq_jobs_active_dedupe", "jobs", ["dedupe_key"], unique=True,
                    postgresql_where=sa.text("status IN ('queued', 'running')"))
    op.drop_table("revoked_clients")
    op.drop_column("configs", "applied")
