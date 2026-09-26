"""Creating and exporting configs (spec §5, §7)."""

import asyncio
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Config, Server, ServerContainer, User
from app.domain.rules import UserState, is_config_active, user_status
from app.drivers.base import ClientMaterial, get_driver
from app.errors import ApiError
from app.jobs.locks import server_lock
from app.render.qr import qr_svg
from app.services.materials import has_private_part, seal, unseal
from app.ssh.conn import RemoteError

CREATE_TIMEOUT_S = 30


def ensure_user_active(user: User, now: datetime) -> None:
    status = user_status(UserState(user.blocked_by, user.deleting_at, user.expires_at), now)
    if status == "blocked":
        raise ApiError(403, "user_blocked", "access is blocked")
    if status == "expired":
        raise ApiError(403, "user_expired", "access period has ended")
    if status == "deleting":
        raise ApiError(403, "user_blocked", "the account is being removed")


async def lock_user(db: AsyncSession, user_id: int) -> User:
    user = (await db.execute(select(User).where(User.id == user_id).with_for_update())).scalar_one_or_none()
    if user is None:
        raise ApiError(404, "not_found", "user not found")
    return user


async def ensure_below_limit(db: AsyncSession, user: User) -> None:
    count = (await db.execute(select(func.count()).select_from(Config).where(
        Config.user_id == user.id, Config.deleted_at.is_(None)))).scalar_one()
    if count >= user.max_configs:
        raise ApiError(409, "config_limit", f"the limit of {user.max_configs} configs is reached")


async def desired_clients(db: AsyncSession, box, server_id: int, container: str,
                          now: datetime) -> tuple[list[ClientMaterial], set[str], list[dict[str, Any]]]:
    """Active client materials, all known client ids, and the material of every known config."""
    rows = (await db.execute(
        select(Config, User).outerjoin(User, Config.user_id == User.id)
        .where(Config.server_id == server_id, Config.container == container))).all()
    desired, known, materials = [], set(), []
    for cfg, user in rows:
        material = unseal(box, cfg.material_enc)
        known.add(cfg.client_id)
        materials.append(material)
        state = None if user is None else UserState(user.blocked_by, user.deleting_at, user.expires_at)
        if is_config_active(cfg.blocked_by, cfg.deleted_at, state, now):
            desired.append(ClientMaterial(cfg.client_id, material))
    return desired, known, materials


async def create_config(db: AsyncSession, state: Any, user_id: int, server_id: int, container: str,
                        name: str | None, for_user: bool) -> Config:
    """Creates the client on the server and stores the config; nothing is stored if the server fails."""
    now = state.clock.now()
    user = await lock_user(db, user_id)
    ensure_user_active(user, now)
    await ensure_below_limit(db, user)

    server = await db.get(Server, server_id)
    if server is None or (for_user and not server.enabled_for_users):
        raise ApiError(404, "not_found", "server not found")
    sc = await db.get(ServerContainer, (server_id, container))
    if sc is None:
        raise ApiError(422, "unsupported_container", "this protocol is not installed on the server")
    driver = get_driver(container)
    desired, known, materials = await desired_clients(db, state.secretbox, server_id, container, now)
    taken = {r for r in (driver.reserved(ClientMaterial("", m)) for m in materials) if r}

    try:
        async with asyncio.timeout(CREATE_TIMEOUT_S):
            async with server_lock(state.sessionmaker, server_id):
                async with state.remote_factory(server) as remote:
                    params = await driver.read_params(remote)
                    material = await driver.create_material(remote, params, taken)
                    await driver.apply(remote, [*desired, material], known | {material.client_id})
    except (RemoteError, TimeoutError, OSError) as e:
        await db.rollback()
        raise ApiError(503, "server_unavailable", f"the server did not respond: {e}") from e

    sc.params_json = params
    cfg = Config(user_id=user.id, server_id=server_id, container=container,
                 name=(name or f"{server.name} {driver.title}")[:128], client_id=material.client_id,
                 material_enc=seal(state.secretbox, material.data))
    db.add(cfg)
    await db.commit()
    return cfg


async def export_config(db: AsyncSession, state: Any, cfg: Config) -> dict[str, str] | None:
    material = unseal(state.secretbox, cfg.material_enc)
    if not has_private_part(material):
        return None
    server = await db.get(Server, cfg.server_id)
    sc = await db.get(ServerContainer, (cfg.server_id, cfg.container))
    if sc is None:
        return None
    settings = state.settings
    rendered = get_driver(cfg.container).render(ClientMaterial(cfg.client_id, material), sc.params_json, server.host,
                                                (settings.dns1, settings.dns2), server.name)
    return {"vpn_key": rendered.vpn_key, "native": rendered.native, "native_filename": rendered.native_filename,
            "qr_svg": qr_svg(rendered.vpn_key)}
