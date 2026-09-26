from datetime import datetime

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.audit import audit
from app.api.deps import AdminDep, ClockDep, Db, SecretBoxDep
from app.db.models import Config, Server, ServerContainer
from app.drivers.base import get_driver
from app.errors import ApiError
from app.jobs.queue import enqueue
from app.drivers.base import installable_containers
from app.services.servers import seal_ssh_secret
from app.services.sync import enqueue_server_sync
from app.ssh.conn import RemoteError

router = APIRouter(prefix="/api/admin/servers", tags=["admin"])


class ServerIn(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    host: str = Field(min_length=1, max_length=255)
    ssh_port: int = Field(default=22, ge=1, le=65535)
    ssh_user: str = Field(min_length=1, max_length=64)
    ssh_password: str | None = None
    ssh_private_key: str | None = None
    enabled_for_users: bool = True


class ServerPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=128)
    enabled_for_users: bool | None = None
    ssh_port: int | None = Field(default=None, ge=1, le=65535)
    ssh_user: str | None = None
    ssh_password: str | None = None
    ssh_private_key: str | None = None


class ContainerIn(BaseModel):
    container: str
    port: str | None = Field(default=None, pattern=r"^\d{1,5}$")
    force: bool = False  # reinstall over an existing container: new server keys, all its configs stop working


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


async def server_out(db: AsyncSession, server: Server) -> dict:
    containers = (await db.execute(
        select(ServerContainer).where(ServerContainer.server_id == server.id).order_by(ServerContainer.container)
    )).scalars().all()
    configs = (await db.execute(
        select(func.count()).select_from(Config).where(Config.server_id == server.id, Config.deleted_at.is_(None))
    )).scalar_one()
    return {
        "id": server.id, "name": server.name, "host": server.host, "ssh_port": server.ssh_port,
        "ssh_user": server.ssh_user, "enabled_for_users": server.enabled_for_users, "host_key": server.host_key,
        "imported_at": _iso(server.imported_at), "last_ok_at": _iso(server.last_ok_at),
        "last_error": server.last_error, "created_at": _iso(server.created_at), "configs_count": configs,
        "containers": [{"container": c.container, "title": get_driver(c.container).title,
                        "port": c.params_json.get("port")} for c in containers],
    }


async def _get(db: AsyncSession, server_id: int) -> Server:
    server = await db.get(Server, server_id)
    if server is None:
        raise ApiError(404, "not_found", "server not found")
    return server


async def _fetch_host_key(request: Request, host: str, port: int) -> str:
    try:
        return await request.app.state.fetch_host_key(host, port)
    except RemoteError as e:
        raise ApiError(502, "server_unreachable", str(e)) from e


def _accepted(body: dict) -> JSONResponse:
    return JSONResponse(status_code=202, content=body)


@router.post("", status_code=202)
async def add_server(body: ServerIn, request: Request, admin: AdminDep, db: Db, box: SecretBoxDep):
    if not body.ssh_password and not body.ssh_private_key:
        raise ApiError(422, "ssh_credentials_required", "give an SSH password or a private key")
    host_key = await _fetch_host_key(request, body.host, body.ssh_port)
    server = Server(name=body.name, host=body.host, ssh_port=body.ssh_port, ssh_user=body.ssh_user,
                    ssh_secret_enc=seal_ssh_secret(box, body.ssh_password, body.ssh_private_key), host_key=host_key,
                    enabled_for_users=body.enabled_for_users)
    db.add(server)
    await db.flush()
    job = await enqueue_server_sync(db, server.id, "server_import")
    audit(db, admin.actor, "server_add", f"server:{server.id}", host=body.host)
    await db.commit()
    return _accepted({"server": await server_out(db, server), "job_id": job.id})


@router.get("/installable")
async def installable(_: AdminDep) -> list[dict]:
    """Protocols the panel can install on a server."""
    return [{"container": c, "title": get_driver(c).title} for c in sorted(installable_containers())]


@router.get("")
async def list_servers(_: AdminDep, db: Db) -> list[dict]:
    servers = (await db.execute(select(Server).order_by(Server.id))).scalars().all()
    return [await server_out(db, s) for s in servers]


@router.get("/{server_id}")
async def get_server(server_id: int, _: AdminDep, db: Db) -> dict:
    return await server_out(db, await _get(db, server_id))


@router.patch("/{server_id}")
async def patch_server(server_id: int, body: ServerPatch, admin: AdminDep, db: Db, box: SecretBoxDep) -> dict:
    server = await _get(db, server_id)
    changes = body.model_dump(exclude_unset=True)
    for field in ("name", "enabled_for_users", "ssh_port", "ssh_user"):
        if changes.get(field) is not None:
            setattr(server, field, changes[field])
    if body.ssh_password or body.ssh_private_key:
        server.ssh_secret_enc = seal_ssh_secret(box, body.ssh_password, body.ssh_private_key)
    audit(db, admin.actor, "server_update", f"server:{server.id}",
          fields=sorted(k for k in changes if not k.startswith("ssh_password") and k != "ssh_private_key"))
    await db.commit()
    return await server_out(db, server)


@router.delete("/{server_id}", status_code=204)
async def delete_server(server_id: int, admin: AdminDep, db: Db) -> Response:
    server = await _get(db, server_id)
    await db.delete(server)
    audit(db, admin.actor, "server_delete", f"server:{server_id}", host=server.host)
    await db.commit()
    return Response(status_code=204)


@router.post("/{server_id}/sync", status_code=202)
async def sync_server(server_id: int, admin: AdminDep, db: Db):
    await _get(db, server_id)
    job = await enqueue_server_sync(db, server_id)
    await db.commit()
    return _accepted({"job_id": job.id})


@router.post("/{server_id}/host-key/accept", status_code=202)
async def accept_host_key(server_id: int, request: Request, admin: AdminDep, db: Db, clock: ClockDep):
    server = await _get(db, server_id)
    server.host_key = await _fetch_host_key(request, server.host, server.ssh_port)
    server.last_error = None
    job = await enqueue_server_sync(db, server_id)
    audit(db, admin.actor, "server_host_key_accept", f"server:{server_id}", host_key=server.host_key)
    await db.commit()
    return _accepted({"host_key": server.host_key, "job_id": job.id})


@router.post("/{server_id}/containers", status_code=202)
async def install(server_id: int, body: ContainerIn, admin: AdminDep, db: Db):
    await _get(db, server_id)
    if body.container not in installable_containers():
        raise ApiError(422, "unsupported_container", f"supported: {', '.join(sorted(installable_containers()))}")
    if not body.force and await db.get(ServerContainer, (server_id, body.container)) is not None:
        raise ApiError(409, "already_installed", "the protocol is already installed; reinstalling breaks its configs")
    payload = {"container": body.container, "port": body.port} if body.port else {"container": body.container}
    job = await enqueue(db, "install_container", payload, server_id=server_id,
                        dedupe_key=f"install:{server_id}:{body.container}")
    audit(db, admin.actor, "container_install", f"server:{server_id}", container=body.container, force=body.force)
    await db.commit()
    return _accepted({"job_id": job.id})
