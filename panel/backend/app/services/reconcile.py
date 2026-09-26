"""Makes a server match the desired state stored in the database (spec §4)."""

from sqlalchemy import delete, exists, select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import Config, RevokedClient, Server, ServerContainer, User
from app.domain.clock import Clock
from app.domain.rules import UserState, is_config_active
from app.drivers.base import ClientMaterial, get_driver, supported_containers
from app.security.secretbox import SecretBox
from app.services.materials import has_private_part, seal, unseal
from app.services.servers import RemoteFactory


def _user_state(user: User | None) -> UserState | None:
    return None if user is None else UserState(user.blocked_by, user.deleting_at, user.expires_at)


async def revoke(db: AsyncSession, server_id: int, container: str, client_ids: list[str]) -> None:
    """Remembers clients the panel removed for good so they are removed again if they ever reappear."""
    if client_ids:
        await db.execute(insert(RevokedClient).values(
            [{"server_id": server_id, "container": container, "client_id": c} for c in client_ids]
        ).on_conflict_do_nothing())


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
            revoked = set((await db.execute(select(RevokedClient.client_id).where(
                RevokedClient.server_id == server_id, RevokedClient.container == container))).scalars())
            rows = (await db.execute(
                select(Config, User).outerjoin(User, Config.user_id == User.id)
                .where(Config.server_id == server_id, Config.container == container)
            )).all()
            known = {cfg.client_id: (cfg, user) for cfg, user in rows}

            for client_id, info in actual.items():
                if client_id not in known and client_id not in revoked:
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
                material = unseal(box, cfg.material_enc)
                if cfg.deleted_at is None and cfg.applied and client_id not in actual and \
                        not has_private_part(material):
                    # An imported client the panel cannot re-issue was removed outside the panel
                    # (for example revoked in the Qt app): keep it removed.
                    cfg.deleted_at = now
                if is_config_active(cfg.blocked_by, cfg.deleted_at, _user_state(user), now):
                    desired.append(ClientMaterial(client_id, material))
            result = await driver.apply(remote, desired, set(known) | revoked)

            desired_ids = {m.client_id for m in desired}
            for client_id, (cfg, _) in known.items():
                cfg.applied = client_id in desired_ids
                if client_id in result.added:
                    cfg.last_rx = cfg.last_tx = None
                    cfg.counter_session = None
            gone = [cfg for cfg, _ in known.values() if cfg.deleted_at is not None]
            await revoke(db, server_id, container, [cfg.client_id for cfg in gone])
            for cfg in gone:
                await db.delete(cfg)

    if not first_import:
        # Deleted configs of containers that are not running have nothing to remove now; the revocation
        # record makes sure they are removed if the container comes back.
        stale = (await db.execute(select(Config).where(
            Config.server_id == server_id, Config.deleted_at.is_not(None),
            Config.container.not_in(containers)))).scalars().all()
        for cfg in stale:
            await revoke(db, server_id, cfg.container, [cfg.client_id])
            await db.delete(cfg)
    await db.flush()
    await db.execute(delete(User).where(User.deleting_at.is_not(None),
                                        ~exists().where(Config.user_id == User.id)))
    server.imported_at = server.imported_at or now
    server.last_ok_at = now
    server.last_error = None
    await db.commit()
