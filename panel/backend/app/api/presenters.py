"""JSON shapes shared by the admin and user APIs."""

from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Config, Server, TrafficDaily, User
from app.domain.rules import UserState, expires_on, is_config_active, user_status
from app.drivers.base import get_driver
from app.services.materials import has_private_part


def iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def state_of(user: User | None) -> UserState | None:
    return None if user is None else UserState(user.blocked_by, user.deleting_at, user.expires_at)


def _traffic(rx: int | None, tx: int | None) -> dict:
    return {"rx": int(rx or 0), "tx": int(tx or 0)}


async def traffic_by_config(db: AsyncSession, config_ids: list[int]) -> dict[int, dict]:
    if not config_ids:
        return {}
    rows = await db.execute(
        select(TrafficDaily.config_id, func.sum(TrafficDaily.rx), func.sum(TrafficDaily.tx))
        .where(TrafficDaily.config_id.in_(config_ids)).group_by(TrafficDaily.config_id))
    return {cid: _traffic(rx, tx) for cid, rx, tx in rows}


async def traffic_by_user(db: AsyncSession, user_ids: list[int]) -> dict[int, dict]:
    if not user_ids:
        return {}
    rows = await db.execute(
        select(Config.user_id, func.sum(TrafficDaily.rx), func.sum(TrafficDaily.tx))
        .join(Config, Config.id == TrafficDaily.config_id)
        .where(Config.user_id.in_(user_ids)).group_by(Config.user_id))
    return {uid: _traffic(rx, tx) for uid, rx, tx in rows}


async def config_counts(db: AsyncSession, user_ids: list[int]) -> dict[int, int]:
    if not user_ids:
        return {}
    rows = await db.execute(
        select(Config.user_id, func.count()).where(Config.user_id.in_(user_ids), Config.deleted_at.is_(None))
        .group_by(Config.user_id))
    return dict(rows.all())


def user_out(user: User, now: datetime, tz: str, configs_count: int, traffic: dict | None) -> dict:
    return {
        "id": user.id,
        "display_name": user.display_name,
        "note": user.note,
        "login": user.login,
        "registered": user.login is not None,
        "status": user_status(state_of(user), now),
        "blocked_by": user.blocked_by,
        "expires_on": expires_on(user.expires_at, tz),
        "max_configs": user.max_configs,
        "configs_count": configs_count,
        "traffic_total": traffic or _traffic(0, 0),
        "created_at": iso(user.created_at),
    }


def config_status(cfg: Config, user: User | None, now: datetime) -> str:
    if cfg.deleted_at is not None:
        return "deleting"
    if cfg.blocked_by is not None:
        return "blocked"
    return "active" if is_config_active(None, None, state_of(user), now) else "inactive"


def config_out(cfg: Config, server: Server, user: User | None, now: datetime, traffic: dict | None,
               material: dict | None = None) -> dict:
    return {
        "id": cfg.id,
        "name": cfg.name,
        "user_id": cfg.user_id,
        "server_id": server.id,
        "server_name": server.name,
        "container": cfg.container,
        "protocol": get_driver(cfg.container).title,
        "client_id": cfg.client_id,
        "status": config_status(cfg, user, now),
        "blocked_by": cfg.blocked_by,
        "can_render": has_private_part(material) if material is not None else None,
        "traffic": traffic or _traffic(0, 0),
        "created_at": iso(cfg.created_at),
    }
