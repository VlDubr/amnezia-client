from datetime import date

from app.db.models import Config, Server, TrafficDaily, User
from tests.conftest import bearer


async def test_traffic_report(db, client, admin_token):
    s1 = Server(name="s1", host="h1", ssh_port=22, ssh_user="r", ssh_secret_enc="x")
    s2 = Server(name="s2", host="h2", ssh_port=22, ssh_user="r", ssh_secret_enc="x")
    u = User(display_name="u", max_configs=5)
    db.add_all([s1, s2, u])
    await db.flush()
    c1 = Config(user_id=u.id, server_id=s1.id, container="amnezia-awg2", name="a", client_id="1")
    c2 = Config(user_id=u.id, server_id=s2.id, container="amnezia-awg2", name="b", client_id="2")
    db.add_all([c1, c2])
    await db.flush()
    def row(c, day, rx, tx):
        return TrafficDaily(config_id=c.id, user_id=c.user_id, server_id=c.server_id, day=day, rx=rx, tx=tx)

    db.add_all([row(c1, date(2026, 9, 25), 10, 1), row(c1, date(2026, 9, 26), 20, 2), row(c2, date(2026, 9, 26), 5, 5)])
    await db.commit()

    r = await client.get(f"/api/admin/traffic?user_id={u.id}", headers=bearer(admin_token))
    body = r.json()
    assert body["total"] == {"rx": 35, "tx": 8}
    assert body["rows"] == [
        {"day": "2026-09-25", "server_id": s1.id, "rx": 10, "tx": 1},
        {"day": "2026-09-26", "server_id": s1.id, "rx": 20, "tx": 2},
        {"day": "2026-09-26", "server_id": s2.id, "rx": 5, "tx": 5},
    ]
    r = await client.get(f"/api/admin/traffic?server_id={s1.id}&from=2026-09-26", headers=bearer(admin_token))
    assert r.json()["total"] == {"rx": 20, "tx": 2}
