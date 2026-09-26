"""Background job handlers. They read collaborators from app.state at run time so tests can swap them."""

from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Job, Server
from app.jobs.queue import PermanentJobError
from app.jobs.worker import Handler
from app.jobs.periodic import cleanup
from app.services.expiry import expire_users
from app.services.install import install_container
from app.services.reconcile import reconcile_server
from app.services.servers import discover_containers
from app.services.traffic import collect_traffic
from app.ssh.conn import HostKeyMismatch


def build_handlers(state: Any) -> dict[str, Handler]:
    async def _server(db: AsyncSession, job: Job) -> Server:
        server = await db.get(Server, job.server_id)
        if server is None:
            raise PermanentJobError("server no longer exists")
        return server

    async def sync(db: AsyncSession, job: Job) -> None:
        server = await _server(db, job)
        try:
            async with state.remote_factory(server) as remote:
                await discover_containers(db, server, remote, state.clock)
            await db.commit()
            await reconcile_server(db, server.id, state.remote_factory, state.clock, state.secretbox)
        except HostKeyMismatch as e:
            raise PermanentJobError(f"host_key_mismatch: {e}") from e

    async def install(db: AsyncSession, job: Job) -> None:
        server = await _server(db, job)
        settings = state.settings
        try:
            async with state.remote_factory(server) as remote:
                await install_container(remote, job.payload_json["container"], server.host,
                                        (settings.dns1, settings.dns2), port=job.payload_json.get("port"))
        except HostKeyMismatch as e:
            raise PermanentJobError(f"host_key_mismatch: {e}") from e
        await sync(db, job)

    async def traffic(db: AsyncSession, job: Job) -> None:
        server = await _server(db, job)
        try:
            await collect_traffic(db, server.id, state.remote_factory, state.clock, state.settings.tz)
        except HostKeyMismatch as e:
            raise PermanentJobError(f"host_key_mismatch: {e}") from e

    async def expire(db: AsyncSession, job: Job) -> None:
        await expire_users(db, state.clock)

    async def clean(db: AsyncSession, job: Job) -> None:
        await cleanup(db, state.clock)

    return {"server_import": sync, "reconcile": sync, "install_container": install, "traffic": traffic,
            "expire": expire, "cleanup": clean}
