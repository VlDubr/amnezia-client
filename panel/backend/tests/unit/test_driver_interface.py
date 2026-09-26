from sqlalchemy import select

from app.db.models import Config, Server
from app.drivers.base import get_driver, installable_containers
from app.security.secretbox import SecretBox
from app.services.materials import has_private_part
from app.services.reconcile import reconcile_server
from app.services.servers import discover_containers
from tests.conftest import MASTER_KEY
from tests.fakes import AWG, awg_server, remote_factory_for

BOX = SecretBox(MASTER_KEY)


def test_secret_counts_as_private_part():
    assert has_private_part({"secret": "uuid"})
    assert has_private_part({"private_key": "k"})
    assert not has_private_part({"ip": "10.8.1.2", "psk": "x"})


def test_installable_containers_come_from_the_registry():
    assert {"amnezia-awg2", "amnezia-wireguard"} <= installable_containers()
    assert "amnezia-awg" not in installable_containers()
    assert get_driver("amnezia-awg2").install_vars("5555")["AWG_SERVER_PORT"] == "5555"


async def test_reconcile_passes_deleted_configs_as_revoked(db, clock, monkeypatch):
    remote = awg_server()
    server = Server(name="s", host="h", ssh_port=22, ssh_user="r", ssh_secret_enc="x", imported_at=clock.now())
    db.add(server)
    await db.commit()
    await discover_containers(db, server, remote, clock)
    await db.commit()
    await reconcile_server(db, server.id, remote_factory_for(remote), clock, BOX)
    cfg = (await db.execute(select(Config).where(Config.client_id == "pubA="))).scalar_one()
    cfg.deleted_at = clock.now()
    blocked = (await db.execute(select(Config).where(Config.client_id == "pubB="))).scalar_one()
    blocked.blocked_by = "admin"
    await db.commit()

    driver = get_driver(AWG)
    seen = {}
    real_apply = driver.apply

    async def spy(remote_, desired, known_ids, revoked=frozenset()):
        seen["revoked"] = set(revoked)
        return await real_apply(remote_, desired, known_ids, revoked)

    monkeypatch.setattr(driver, "apply", spy)
    await reconcile_server(db, server.id, remote_factory_for(remote), clock, BOX)
    assert seen["revoked"] == {"pubA="}
