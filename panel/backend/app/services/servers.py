"""Server credentials and container discovery."""

import json
from collections.abc import Callable
from contextlib import AbstractAsyncContextManager

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Server, ServerContainer
from app.domain.clock import Clock
from app.drivers.base import get_driver, supported_containers
from app.security.secretbox import SecretBox
from app.ssh.conn import Remote, RemoteError, SshTarget, open_remote

RemoteFactory = Callable[[Server], AbstractAsyncContextManager[Remote]]


def seal_ssh_secret(box: SecretBox, password: str | None, private_key: str | None) -> str:
    return box.encrypt(json.dumps({"password": password, "private_key": private_key}))


def ssh_target(server: Server, box: SecretBox) -> SshTarget:
    secret = json.loads(box.decrypt(server.ssh_secret_enc))
    return SshTarget(host=server.host, port=server.ssh_port, user=server.ssh_user, password=secret.get("password"),
                     private_key=secret.get("private_key"), host_key=server.host_key)


def make_remote_factory(box: SecretBox) -> RemoteFactory:
    def factory(server: Server) -> AbstractAsyncContextManager[Remote]:
        return open_remote(ssh_target(server, box))

    return factory


async def discover_containers(db: AsyncSession, server: Server, remote: Remote, clock: Clock) -> list[str]:
    """Records the supported Amnezia containers running on the server together with their parameters."""
    running = set(await remote.list_containers())
    found = sorted(running & supported_containers())
    await db.execute(delete(ServerContainer).where(ServerContainer.server_id == server.id,
                                                   ServerContainer.container.not_in(found)))
    existing = {sc.container: sc for sc in (await db.execute(
        select(ServerContainer).where(ServerContainer.server_id == server.id))).scalars()}
    for container in found:
        try:
            params = await get_driver(container).read_params(remote)
        except (RemoteError, ValueError, KeyError):
            # An unreadable container keeps its previous parameters; it is still recorded so that
            # reconcile tries it and reports the error on the server.
            if container not in existing:
                db.add(ServerContainer(server_id=server.id, container=container, params_json={},
                                       refreshed_at=clock.now()))
            continue
        row = existing.get(container)
        if row is None:
            db.add(ServerContainer(server_id=server.id, container=container, params_json=params,
                                   refreshed_at=clock.now()))
        else:
            row.params_json = params
            row.refreshed_at = clock.now()
    return found
