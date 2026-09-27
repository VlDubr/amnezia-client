from datetime import date

from fastapi import APIRouter
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import audit
from app.api.deps import AdminDep, ClockDep, Db, SettingsDep
from app.api.presenters import (
    config_counts,
    config_out,
    state_of,
    traffic_by_config,
    traffic_by_user,
    user_out,
)
from app.db.models import ROLE_USER, Config, InviteKey, Server, TrafficDaily, User
from app.domain.rules import after_expiry_change, expiry_instant, user_status
from app.errors import ApiError
from app.security.tokens import new_invite_key, normalize_invite_key, sha256_hex
from app.services.sync import enqueue_user_sync

router = APIRouter(prefix="/api/admin/users", tags=["admin"])

MAX_CONFIGS_LIMIT = 1000


class UserIn(BaseModel):
    display_name: str = Field(min_length=1, max_length=128)
    note: str = Field(default="", max_length=2000)
    expires_on: date | None = None
    max_configs: int = Field(ge=0, le=MAX_CONFIGS_LIMIT)


class UserPatch(BaseModel):
    display_name: str | None = Field(default=None, min_length=1, max_length=128)
    note: str | None = Field(default=None, max_length=2000)
    expires_on: date | None = None
    max_configs: int | None = Field(default=None, ge=0, le=MAX_CONFIGS_LIMIT)


async def _get(db: AsyncSession, user_id: int, lock: bool = False) -> User:
    # Administrator accounts live in the same table but are never managed here.
    query = select(User).where(User.id == user_id, User.role == ROLE_USER)
    if lock:
        query = query.with_for_update()
    user = (await db.execute(query)).scalar_one_or_none()
    if user is None:
        raise ApiError(404, "not_found", "user not found")
    return user


async def _out(db: AsyncSession, user: User, clock, settings) -> dict:
    counts = await config_counts(db, [user.id])
    traffic = await traffic_by_user(db, [user.id])
    return user_out(user, clock.now(), settings.tz, counts.get(user.id, 0), traffic.get(user.id))


async def _issue_invite(db: AsyncSession, user: User, now) -> str:
    await db.execute(update(InviteKey).where(InviteKey.user_id == user.id, InviteKey.used_at.is_(None),
                                             InviteKey.revoked_at.is_(None)).values(revoked_at=now))
    key = new_invite_key()
    db.add(InviteKey(user_id=user.id, key_hash=sha256_hex(normalize_invite_key(key))))
    return key


@router.post("", status_code=201)
async def create_user(body: UserIn, admin: AdminDep, db: Db, clock: ClockDep, settings: SettingsDep) -> dict:
    now = clock.now()
    expires_at = expiry_instant(body.expires_on, settings.tz) if body.expires_on else None
    user = User(display_name=body.display_name, note=body.note, max_configs=body.max_configs, expires_at=expires_at,
                blocked_by=after_expiry_change(None, expires_at, now))
    db.add(user)
    await db.flush()
    key = await _issue_invite(db, user, now)
    audit(db, admin.actor, "user_create", f"user:{user.id}", display_name=user.display_name)
    await db.commit()
    return {"user": await _out(db, user, clock, settings), "invite_key": key}


@router.get("")
async def list_users(_: AdminDep, db: Db, clock: ClockDep, settings: SettingsDep, status: str | None = None,
                     q: str | None = None) -> list[dict]:
    query = select(User).where(User.role == ROLE_USER).order_by(User.display_name, User.id)
    if q:
        pattern = f"%{q}%"
        query = query.where(or_(User.display_name.ilike(pattern), User.login.ilike(pattern),
                                User.note.ilike(pattern)))
    users = (await db.execute(query)).scalars().all()
    ids = [u.id for u in users]
    counts, traffic = await config_counts(db, ids), await traffic_by_user(db, ids)
    now = clock.now()
    out = [user_out(u, now, settings.tz, counts.get(u.id, 0), traffic.get(u.id)) for u in users]
    return [u for u in out if status is None or u["status"] == status]


@router.get("/{user_id}")
async def get_user(user_id: int, _: AdminDep, db: Db, clock: ClockDep, settings: SettingsDep) -> dict:
    user = await _get(db, user_id)
    out = await _out(db, user, clock, settings)
    rows = (await db.execute(
        select(Config, Server).join(Server, Server.id == Config.server_id)
        .where(Config.user_id == user.id).order_by(Config.id))).all()
    traffic = await traffic_by_config(db, [c.id for c, _ in rows])
    now = clock.now()
    out["configs"] = [config_out(c, s, user, now, traffic.get(c.id)) for c, s in rows]
    per_server = await db.execute(
        select(Server.id, Server.name, func.sum(TrafficDaily.rx), func.sum(TrafficDaily.tx))
        .join(TrafficDaily, TrafficDaily.server_id == Server.id)
        .where(TrafficDaily.user_id == user.id).group_by(Server.id, Server.name).order_by(Server.id))
    out["traffic_by_server"] = [{"server_id": sid, "server_name": name, "rx": int(rx), "tx": int(tx)}
                                for sid, name, rx, tx in per_server]
    return out


@router.patch("/{user_id}")
async def patch_user(user_id: int, body: UserPatch, admin: AdminDep, db: Db, clock: ClockDep,
                     settings: SettingsDep) -> dict:
    user = await _get(db, user_id, lock=True)
    changes = body.model_dump(exclude_unset=True)
    before = user_status(state_of(user), clock.now())
    for field in ("display_name", "note", "max_configs"):
        if changes.get(field) is not None:
            setattr(user, field, changes[field])
    if "expires_on" in changes:
        user.expires_at = expiry_instant(body.expires_on, settings.tz) if body.expires_on else None
        user.blocked_by = after_expiry_change(user.blocked_by, user.expires_at, clock.now())
    if user_status(state_of(user), clock.now()) != before:
        await enqueue_user_sync(db, user.id)
    audit(db, admin.actor, "user_update", f"user:{user.id}",
          **{k: (str(v) if v is not None else None) for k, v in changes.items()})
    await db.commit()
    return await _out(db, user, clock, settings)


@router.post("/{user_id}/block")
async def block_user(user_id: int, admin: AdminDep, db: Db, clock: ClockDep, settings: SettingsDep) -> dict:
    user = await _get(db, user_id, lock=True)
    user.blocked_by = "admin"
    await enqueue_user_sync(db, user.id)
    audit(db, admin.actor, "user_block", f"user:{user.id}")
    await db.commit()
    return await _out(db, user, clock, settings)


@router.post("/{user_id}/unblock")
async def unblock_user(user_id: int, admin: AdminDep, db: Db, clock: ClockDep, settings: SettingsDep) -> dict:
    user = await _get(db, user_id, lock=True)
    user.blocked_by = after_expiry_change(None, user.expires_at, clock.now())
    await enqueue_user_sync(db, user.id)
    audit(db, admin.actor, "user_unblock", f"user:{user.id}")
    await db.commit()
    return await _out(db, user, clock, settings)


@router.post("/{user_id}/invite")
async def reissue_invite(user_id: int, admin: AdminDep, db: Db, clock: ClockDep) -> dict:
    user = await _get(db, user_id, lock=True)
    if user.login is not None:
        raise ApiError(409, "already_registered", "the user has already registered")
    key = await _issue_invite(db, user, clock.now())
    audit(db, admin.actor, "user_invite_reissue", f"user:{user.id}")
    await db.commit()
    return {"invite_key": key}


@router.delete("/{user_id}", status_code=202)
async def delete_user(user_id: int, admin: AdminDep, db: Db, clock: ClockDep) -> JSONResponse:
    user = await _get(db, user_id, lock=True)
    now = clock.now()
    audit(db, admin.actor, "user_delete", f"user:{user.id}", display_name=user.display_name)
    has_configs = (await db.execute(select(Config.id).where(Config.user_id == user.id).limit(1))).first()
    if has_configs:
        user.deleting_at = now
        await db.execute(update(Config).where(Config.user_id == user.id, Config.deleted_at.is_(None))
                         .values(deleted_at=now))
        await enqueue_user_sync(db, user.id)
    else:
        await db.delete(user)
    await db.commit()
    return JSONResponse(status_code=202, content={"deleting": bool(has_configs)})
