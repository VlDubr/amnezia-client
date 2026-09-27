from datetime import date, datetime
from typing import Any

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import Base

TS = DateTime(timezone=True)


def _created() -> Mapped[datetime]:
    return mapped_column(TS, server_default=func.now(), nullable=False)


ROLE_ADMIN = "admin"
ROLE_USER = "user"


class User(Base):
    """Every account. Administrators have role 'admin' and none of the user limits or configs."""

    __tablename__ = "users"
    __table_args__ = (
        CheckConstraint("blocked_by IN ('admin', 'expiry')", name="blocked_by"),
        CheckConstraint("role IN ('admin', 'user')", name="role"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    role: Mapped[str] = mapped_column(String(8), default=ROLE_USER, server_default=ROLE_USER)
    display_name: Mapped[str] = mapped_column(String(128))
    note: Mapped[str] = mapped_column(Text, default="", server_default="")
    login: Mapped[str | None] = mapped_column(String(64), unique=True)
    password_hash: Mapped[str | None] = mapped_column(Text)
    blocked_by: Mapped[str | None] = mapped_column(String(16))
    expires_at: Mapped[datetime | None] = mapped_column(TS)
    max_configs: Mapped[int] = mapped_column(Integer)
    deleting_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = _created()


class InviteKey(Base):
    __tablename__ = "invite_keys"
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    key_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created()
    used_at: Mapped[datetime | None] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class Server(Base):
    __tablename__ = "servers"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(128))
    host: Mapped[str] = mapped_column(String(255))
    ssh_port: Mapped[int] = mapped_column(Integer, default=22)
    ssh_user: Mapped[str] = mapped_column(String(64))
    ssh_secret_enc: Mapped[str] = mapped_column(Text)
    host_key: Mapped[str | None] = mapped_column(Text)
    enabled_for_users: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    prepared_at: Mapped[datetime | None] = mapped_column(TS)
    imported_at: Mapped[datetime | None] = mapped_column(TS)
    last_ok_at: Mapped[datetime | None] = mapped_column(TS)
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = _created()


class ServerContainer(Base):
    __tablename__ = "server_containers"
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True)
    container: Mapped[str] = mapped_column(String(64), primary_key=True)
    params_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    refreshed_at: Mapped[datetime] = _created()


class Config(Base):
    __tablename__ = "configs"
    __table_args__ = (
        UniqueConstraint("server_id", "container", "client_id"),
        CheckConstraint("blocked_by IN ('user', 'admin')", name="blocked_by"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    container: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(128))
    client_id: Mapped[str] = mapped_column(String(255))
    material_enc: Mapped[str | None] = mapped_column(Text)
    blocked_by: Mapped[str | None] = mapped_column(String(16))
    deleted_at: Mapped[datetime | None] = mapped_column(TS)
    last_rx: Mapped[int | None] = mapped_column(BigInteger)
    last_tx: Mapped[int | None] = mapped_column(BigInteger)
    counter_session: Mapped[str | None] = mapped_column(String(128))
    # Whether the panel last left this client on the server; tells "removed outside the panel" apart
    # from "removed by the panel because it is blocked".
    applied: Mapped[bool] = mapped_column(Boolean, default=True, server_default=text("true"))
    created_at: Mapped[datetime] = _created()


class RevokedClient(Base):
    """Clients the panel removed for good: if one shows up on the server again it is removed, not imported."""
    __tablename__ = "revoked_clients"
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), primary_key=True)
    container: Mapped[str] = mapped_column(String(64), primary_key=True)
    client_id: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = _created()


class TrafficDaily(Base):
    """Daily traffic; rows outlive their config so user and server totals never shrink."""
    __tablename__ = "traffic_daily"
    __table_args__ = (UniqueConstraint("config_id", "day"),)
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    config_id: Mapped[int | None] = mapped_column(ForeignKey("configs.id", ondelete="SET NULL"))
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), index=True)
    server_id: Mapped[int] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"), index=True)
    day: Mapped[date] = mapped_column(Date)
    rx: Mapped[int] = mapped_column(BigInteger, default=0)
    tx: Mapped[int] = mapped_column(BigInteger, default=0)


class Session(Base):
    __tablename__ = "sessions"
    id: Mapped[int] = mapped_column(primary_key=True)
    # No role here: it is read from the account on every request.
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    created_at: Mapped[datetime] = _created()
    last_used_at: Mapped[datetime | None] = mapped_column(TS)
    expires_at: Mapped[datetime] = mapped_column(TS)
    revoked_at: Mapped[datetime | None] = mapped_column(TS)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (
        CheckConstraint("status IN ('queued', 'running', 'done', 'failed')", name="status"),
        Index("uq_jobs_queued_dedupe", "dedupe_key", unique=True, postgresql_where=text("status = 'queued'")),
        Index("ix_jobs_claim", "status", "run_after"),
    )
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(32))
    server_id: Mapped[int | None] = mapped_column(ForeignKey("servers.id", ondelete="CASCADE"))
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    dedupe_key: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(8), default="queued", server_default="queued")
    run_after: Mapped[datetime] = mapped_column(TS, server_default=func.now())
    attempts: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    last_error: Mapped[str | None] = mapped_column(Text)
    locked_at: Mapped[datetime | None] = mapped_column(TS)
    created_at: Mapped[datetime] = _created()
    finished_at: Mapped[datetime | None] = mapped_column(TS)


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(primary_key=True)
    actor: Mapped[str] = mapped_column(String(32))
    action: Mapped[str] = mapped_column(String(64))
    target: Mapped[str] = mapped_column(String(64))
    details_json: Mapped[dict[str, Any]] = mapped_column(JSONB, default=dict)
    ts: Mapped[datetime] = mapped_column(TS, server_default=func.now(), index=True)
