"""One accounts table: administrators move into users with role 'admin'; sessions point at users.

A session no longer stores a role: the role is read from the account on every request, so it cannot be
forged and a changed role applies at once. Administrator sessions are dropped (they pointed at the old table),
so administrators sign in again once.

Revision ID: 0003
Revises: 0002
"""

import sqlalchemy as sa
from alembic import op

revision = "0003"
down_revision = "0002"
branch_labels = None
depends_on = None


def upgrade() -> None:
    conn = op.get_bind()
    clashes = conn.execute(sa.text(
        "SELECT a.login FROM admins a JOIN users u ON u.login = a.login ORDER BY a.login")).scalars().all()
    if clashes:
        raise RuntimeError(
            "these logins belong to both an administrator and a user: " + ", ".join(clashes)
            + ". Rename one of each pair, then run the migration again.")

    op.add_column("users", sa.Column("role", sa.String(length=8), server_default="user", nullable=False))
    op.create_check_constraint(op.f("ck_users_role"), "users", "role IN ('admin', 'user')")

    conn.execute(sa.text(
        "INSERT INTO users (display_name, note, login, password_hash, max_configs, role, created_at) "
        "SELECT login, '', login, password_hash, 0, 'admin', created_at FROM admins ORDER BY id"))
    # Audit entries name accounts as "<role>:<id>": point the administrator ones at the new ids.
    for column in ("actor", "target"):
        conn.execute(sa.text(
            f"UPDATE audit_log SET {column} = 'admin:' || u.id FROM admins a JOIN users u ON u.login = a.login "
            f"WHERE audit_log.{column} = 'admin:' || a.id"))

    conn.execute(sa.text("DELETE FROM sessions WHERE subject <> 'user' "
                         "OR subject_id NOT IN (SELECT id FROM users WHERE role = 'user')"))
    op.drop_index("ix_sessions_subject", table_name="sessions")
    op.drop_constraint(op.f("ck_sessions_subject"), "sessions", type_="check")
    op.drop_column("sessions", "subject")
    op.alter_column("sessions", "subject_id", new_column_name="user_id")
    op.create_foreign_key(op.f("fk_sessions_user_id_users"), "sessions", "users", ["user_id"], ["id"],
                          ondelete="CASCADE")
    op.create_index(op.f("ix_sessions_user_id"), "sessions", ["user_id"])

    op.drop_table("admins")


def downgrade() -> None:
    op.create_table(
        "admins",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("login", sa.String(length=64), nullable=False),
        sa.Column("password_hash", sa.Text(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_admins")),
        sa.UniqueConstraint("login", name=op.f("uq_admins_login")),
    )
    conn = op.get_bind()
    conn.execute(sa.text("INSERT INTO admins (login, password_hash, created_at) "
                         "SELECT login, password_hash, created_at FROM users WHERE role = 'admin' ORDER BY id"))

    op.drop_index(op.f("ix_sessions_user_id"), table_name="sessions")
    op.drop_constraint(op.f("fk_sessions_user_id_users"), "sessions", type_="foreignkey")
    op.alter_column("sessions", "user_id", new_column_name="subject_id")
    conn.execute(sa.text("DELETE FROM sessions WHERE subject_id IN (SELECT id FROM users WHERE role = 'admin')"))
    op.add_column("sessions", sa.Column("subject", sa.String(length=8), server_default="user", nullable=False))
    op.alter_column("sessions", "subject", server_default=None)
    op.create_check_constraint(op.f("ck_sessions_subject"), "sessions", "subject IN ('admin', 'user')")
    op.create_index("ix_sessions_subject", "sessions", ["subject", "subject_id"])

    conn.execute(sa.text("DELETE FROM users WHERE role = 'admin'"))
    op.drop_constraint(op.f("ck_users_role"), "users", type_="check")
    op.drop_column("users", "role")
