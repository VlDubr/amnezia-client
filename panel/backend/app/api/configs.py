from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import audit
from app.api.deps import AdminDep, ClockDep, Db, UserDep
from app.api.presenters import config_out, traffic_by_config
from app.db.models import Config, Server, ServerContainer, User
from app.domain.rules import user_can_unblock
from app.drivers.base import get_driver, supported_containers
from app.errors import ApiError
from app.services.configs import create_config, ensure_below_limit, ensure_user_active, export_config, lock_user
from app.services.materials import unseal
from app.services.sync import enqueue_server_sync

admin_router = APIRouter(prefix="/api/admin", tags=["admin"])
me_router = APIRouter(prefix="/api/me", tags=["me"])


class ConfigIn(BaseModel):
    server_id: int
    container: str
    name: str | None = Field(default=None, max_length=128)


class AssignIn(BaseModel):
    user_id: int


async def _load(db: AsyncSession, config_id: int, owner_id: int | None = None) -> tuple[Config, Server, User | None]:
    row = (await db.execute(
        select(Config, Server, User).join(Server, Server.id == Config.server_id)
        .outerjoin(User, User.id == Config.user_id)
        .where(Config.id == config_id, Config.deleted_at.is_(None)))).first()
    if row is None or (owner_id is not None and row[0].user_id != owner_id):
        raise ApiError(404, "not_found", "config not found")
    return row[0], row[1], row[2]


async def _out(db: AsyncSession, request: Request, cfg: Config, server: Server, user: User | None,
               with_export: bool = False) -> dict:
    state = request.app.state
    traffic = await traffic_by_config(db, [cfg.id])
    material = unseal(state.secretbox, cfg.material_enc)
    out = config_out(cfg, server, user, state.clock.now(), traffic.get(cfg.id), material)
    if with_export:
        out["export"] = await export_config(db, state, cfg)
    return out


async def _list(db: AsyncSession, request: Request, query) -> list[dict]:
    rows = (await db.execute(query)).all()
    state = request.app.state
    traffic = await traffic_by_config(db, [c.id for c, _, _ in rows])
    now = state.clock.now()
    return [config_out(c, s, u, now, traffic.get(c.id), unseal(state.secretbox, c.material_enc))
            for c, s, u in rows]


def _base_query():
    return (select(Config, Server, User).join(Server, Server.id == Config.server_id)
            .outerjoin(User, User.id == Config.user_id).where(Config.deleted_at.is_(None)).order_by(Config.id))


async def _set_block(db: AsyncSession, cfg: Config, blocked_by: str | None) -> None:
    cfg.blocked_by = blocked_by
    await enqueue_server_sync(db, cfg.server_id)


async def _mark_deleted(db: AsyncSession, cfg: Config, now) -> None:
    cfg.deleted_at = now
    await enqueue_server_sync(db, cfg.server_id)


# --- admin ------------------------------------------------------------------


@admin_router.post("/users/{user_id}/configs", status_code=201)
async def admin_create(user_id: int, body: ConfigIn, request: Request, admin: AdminDep, db: Db) -> dict:
    cfg = await create_config(db, request.app.state, user_id, body.server_id, body.container, body.name,
                              for_user=False)
    audit(db, admin.actor, "config_create", f"config:{cfg.id}", user_id=user_id, server_id=body.server_id)
    await db.commit()
    return await _out(db, request, *(await _load(db, cfg.id)))


@admin_router.get("/configs")
async def admin_list(request: Request, _: AdminDep, db: Db, orphan: bool = False, server_id: int | None = None,
                     user_id: int | None = None) -> list[dict]:
    query = _base_query()
    if orphan:
        query = query.where(Config.user_id.is_(None))
    if server_id is not None:
        query = query.where(Config.server_id == server_id)
    if user_id is not None:
        query = query.where(Config.user_id == user_id)
    return await _list(db, request, query)


@admin_router.get("/configs/{config_id}")
async def admin_get(config_id: int, request: Request, _: AdminDep, db: Db) -> dict:
    return await _out(db, request, *(await _load(db, config_id)), with_export=True)


@admin_router.post("/configs/{config_id}/block")
async def admin_block(config_id: int, request: Request, admin: AdminDep, db: Db) -> dict:
    cfg, server, user = await _load(db, config_id)
    await _set_block(db, cfg, "admin")
    audit(db, admin.actor, "config_block", f"config:{cfg.id}")
    await db.commit()
    return await _out(db, request, cfg, server, user)


@admin_router.post("/configs/{config_id}/unblock")
async def admin_unblock(config_id: int, request: Request, admin: AdminDep, db: Db) -> dict:
    cfg, server, user = await _load(db, config_id)
    await _set_block(db, cfg, None)
    audit(db, admin.actor, "config_unblock", f"config:{cfg.id}")
    await db.commit()
    return await _out(db, request, cfg, server, user)


@admin_router.delete("/configs/{config_id}", status_code=202)
async def admin_delete(config_id: int, admin: AdminDep, db: Db, clock: ClockDep) -> JSONResponse:
    cfg, _, _ = await _load(db, config_id)
    await _mark_deleted(db, cfg, clock.now())
    audit(db, admin.actor, "config_delete", f"config:{cfg.id}")
    await db.commit()
    return JSONResponse(status_code=202, content={"id": config_id})


@admin_router.post("/configs/{config_id}/assign")
async def admin_assign(config_id: int, body: AssignIn, request: Request, admin: AdminDep, db: Db) -> dict:
    user = await lock_user(db, body.user_id)
    cfg, server, _ = await _load(db, config_id)
    if cfg.user_id != user.id:
        await ensure_below_limit(db, user)
        cfg.user_id = user.id
        await enqueue_server_sync(db, cfg.server_id)
    audit(db, admin.actor, "config_assign", f"config:{cfg.id}", user_id=user.id)
    await db.commit()
    return await _out(db, request, cfg, server, user)


# --- user -------------------------------------------------------------------


@me_router.get("/servers")
async def my_servers(_: UserDep, db: Db) -> list[dict]:
    rows = (await db.execute(
        select(Server, ServerContainer.container).join(ServerContainer, ServerContainer.server_id == Server.id)
        .where(Server.enabled_for_users.is_(True), ServerContainer.container.in_(supported_containers()))
        .order_by(Server.name, Server.id, ServerContainer.container))).all()
    servers: dict[int, dict] = {}
    for server, container in rows:
        entry = servers.setdefault(server.id, {"id": server.id, "name": server.name, "containers": []})
        entry["containers"].append({"container": container, "title": get_driver(container).title})
    return list(servers.values())


@me_router.get("/configs")
async def my_configs(request: Request, principal: UserDep, db: Db) -> list[dict]:
    return await _list(db, request, _base_query().where(Config.user_id == principal.subject_id))


@me_router.post("/configs", status_code=201)
async def my_create(body: ConfigIn, request: Request, principal: UserDep, db: Db) -> dict:
    cfg = await create_config(db, request.app.state, principal.subject_id, body.server_id, body.container,
                              body.name, for_user=True)
    audit(db, principal.actor, "config_create", f"config:{cfg.id}", server_id=body.server_id)
    await db.commit()
    return await _out(db, request, *(await _load(db, cfg.id)))


@me_router.get("/configs/{config_id}")
async def my_get(config_id: int, request: Request, principal: UserDep, db: Db, clock: ClockDep) -> dict:
    cfg, server, user = await _load(db, config_id, owner_id=principal.subject_id)
    ensure_user_active(user, clock.now())
    return await _out(db, request, cfg, server, user, with_export=True)


@me_router.post("/configs/{config_id}/block")
async def my_block(config_id: int, request: Request, principal: UserDep, db: Db) -> dict:
    cfg, server, user = await _load(db, config_id, owner_id=principal.subject_id)
    if cfg.blocked_by is None:
        await _set_block(db, cfg, "user")
        audit(db, principal.actor, "config_block", f"config:{cfg.id}")
        await db.commit()
    return await _out(db, request, cfg, server, user)


@me_router.post("/configs/{config_id}/unblock")
async def my_unblock(config_id: int, request: Request, principal: UserDep, db: Db) -> dict:
    cfg, server, user = await _load(db, config_id, owner_id=principal.subject_id)
    if cfg.blocked_by is not None:
        if not user_can_unblock(cfg.blocked_by):
            raise ApiError(403, "blocked_by_admin", "this config was blocked by the administrator")
        await _set_block(db, cfg, None)
        audit(db, principal.actor, "config_unblock", f"config:{cfg.id}")
        await db.commit()
    return await _out(db, request, cfg, server, user)


@me_router.delete("/configs/{config_id}", status_code=202)
async def my_delete(config_id: int, principal: UserDep, db: Db, clock: ClockDep) -> JSONResponse:
    cfg, _, _ = await _load(db, config_id, owner_id=principal.subject_id)
    await _mark_deleted(db, cfg, clock.now())
    audit(db, principal.actor, "config_delete", f"config:{cfg.id}")
    await db.commit()
    return JSONResponse(status_code=202, content={"id": config_id})

