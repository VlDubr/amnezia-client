"""Makes a server match the desired state stored in the database (spec §4)."""

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Config, Server, ServerContainer, User
from app.domain.clock import Clock
from app.domain.rules import UserState, is_config_active
from app.drivers.base import ClientMaterial, get_driver, supported_containers
from app.security.secretbox import SecretBox
from app.services.materials import seal, unseal
from app.services.servers import RemoteFactory


def _user_state(user: User | None) -> UserState | None:
    return None if user is None else UserState(user.blocked_by, user.deleting_at, user.expires_at)


async def reconcile_server(db: AsyncSession, server_id: int, remote_factory: RemoteFactory, clock: Clock,
                           box: SecretBox) -> None:
    """Imports clients unknown to the panel, then applies the desired client set of every container.

    On the first run (server.imported_at is None) nothing is changed on the server: existing clients are
    only recorded as configs without an owner.
    """
    server = await db.get(Server, server_id)
    first_import = server.imported_at is None
    now = clock.now()
    containers = (await db.execute(
        select(ServerContainer.container).where(ServerContainer.server_id == server_id,
                                                ServerContainer.container.in_(supported_containers()))
    )).scalars().all()

    async with remote_factory(server) as remote:
        for container in containers:
            driver = get_driver(container)
            actual = await driver.list_clients(remote)
            rows = (await db.execute(
                select(Config, User).outerjoin(User, Config.user_id == User.id)
                .where(Config.server_id == server_id, Config.container == container)
            )).all()
            known = {cfg.client_id: (cfg, user) for cfg, user in rows}

            for client_id, info in actual.items():
                if client_id not in known:
                    cfg = Config(user_id=None, server_id=server_id, container=container,
                                 name=(info.name or f"Imported {client_id[:8]}")[:128], client_id=client_id,
                                 material_enc=seal(box, info.data))
                    db.add(cfg)
                    known[client_id] = (cfg, None)
            await db.flush()
            if first_import:
                continue

            desired: list[ClientMaterial] = []
            for client_id, (cfg, user) in known.items():
                if is_config_active(cfg.blocked_by, cfg.deleted_at, _user_state(user), now):
                    desired.append(ClientMaterial(client_id, unseal(box, cfg.material_enc)))
            result = await driver.apply(remote, desired, set(known))

            for client_id in result.added:
                cfg = known[client_id][0]
                cfg.last_rx = cfg.last_tx = None
                cfg.counter_session = None
            for cfg, _ in known.values():
                if cfg.deleted_at is not None:
                    await db.delete(cfg)

    if not first_import:
        # Deleted configs of containers that no longer exist on the server have nothing left to remove.
        await db.execute(delete(Config).where(Config.server_id == server_id, Config.deleted_at.is_not(None),
                                              Config.container.not_in(containers)))
    await db.flush()
    await db.execute(delete(User).where(User.deleting_at.is_not(None),
                                        ~exists().where(Config.user_id == User.id)))
    server.imported_at = server.imported_at or now
    server.last_ok_at = now
    server.last_error = None
    await db.commit()
