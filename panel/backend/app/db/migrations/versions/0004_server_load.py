"""Server load: minute samples, hardware facts, admin capacities, traffic freshness.

Revision ID: 0004
Revises: 0003
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0004"
down_revision = "0003"
branch_labels = None
depends_on = None

TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.add_column("servers", sa.Column("bandwidth_mbps", sa.Integer(), nullable=True))
    op.add_column("servers", sa.Column("expected_clients", sa.Integer(), nullable=True))
    op.add_column("servers", sa.Column("metrics_iface", sa.String(length=15), nullable=True))
    op.add_column("servers", sa.Column("specs_json", postgresql.JSONB(), nullable=True))
    op.add_column("servers", sa.Column("specs_at", TS, nullable=True))
    op.add_column("servers", sa.Column("metrics_error", sa.Text(), nullable=True))
    op.add_column("servers", sa.Column("metrics_error_at", TS, nullable=True))
    op.add_column("server_containers", sa.Column("traffic_read_at", TS, nullable=True))
    op.add_column("configs", sa.Column("counter_read_at", TS, nullable=True))
    op.add_column("configs", sa.Column("last_active_at", TS, nullable=True))

    op.create_table(
        "server_samples",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("server_id", sa.Integer(), nullable=False),
        sa.Column("ts", TS, nullable=False),
        sa.Column("boot_id", sa.String(length=36), nullable=False),
        sa.Column("uptime_s", sa.Float(), nullable=False),
        sa.Column("iface", sa.String(length=15), nullable=True),
        sa.Column("cpu_busy", sa.BigInteger(), nullable=False),
        sa.Column("cpu_total", sa.BigInteger(), nullable=False),
        sa.Column("net_rx", sa.BigInteger(), nullable=True),
        sa.Column("net_tx", sa.BigInteger(), nullable=True),
        sa.Column("cpu_pct", sa.Float(), nullable=True),
        sa.Column("rx_mbps", sa.Float(), nullable=True),
        sa.Column("tx_mbps", sa.Float(), nullable=True),
        sa.Column("mem_pct", sa.Float(), nullable=True),
        sa.Column("disk_pct", sa.Float(), nullable=True),
        sa.Column("load1", sa.Float(), nullable=True),
        sa.Column("active_clients", sa.Integer(), nullable=True),
        sa.ForeignKeyConstraint(["server_id"], ["servers.id"], name=op.f("fk_server_samples_server_id_servers"),
                                ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_server_samples")),
    )
    op.create_index("ix_server_samples_server_ts", "server_samples", ["server_id", "ts"])


def downgrade() -> None:
    op.drop_index("ix_server_samples_server_ts", table_name="server_samples")
    op.drop_table("server_samples")
    for table, column in (("configs", "last_active_at"), ("configs", "counter_read_at"),
                          ("server_containers", "traffic_read_at"), ("servers", "metrics_error_at"),
                          ("servers", "metrics_error"), ("servers", "specs_at"), ("servers", "specs_json"),
                          ("servers", "metrics_iface"), ("servers", "expected_clients"),
                          ("servers", "bandwidth_mbps")):
        op.drop_column(table, column)
