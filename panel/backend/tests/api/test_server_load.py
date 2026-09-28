"""Load in the server APIs (load spec §6): users see only a level and "recommended", admins see everything."""

from datetime import timedelta

from app.db.models import Server, ServerContainer, ServerSample
from tests.conftest import bearer, registered_user

AWG = "amnezia-awg2"


async def _server(db, name, **kw) -> Server:
    s = Server(name=name, host=f"{name}.example", ssh_port=22, ssh_user="root", ssh_secret_enc="x", **kw)
    db.add(s)
    await db.flush()
    db.add(ServerContainer(server_id=s.id, container=AWG, params_json={}))
    await db.commit()
    return s


async def _load(db, server, now, cpu, mem=20.0, points=7, rx=None, tx=None):
    for m in range(1, points + 1):
        db.add(ServerSample(server_id=server.id, ts=now - timedelta(minutes=m), boot_id="b", uptime_s=1, cpu_busy=1,
                            cpu_total=2, cpu_pct=cpu, mem_pct=mem, rx_mbps=rx, tx_mbps=tx, disk_pct=40.0))
    await db.commit()


async def test_users_see_levels_in_order_and_one_recommended(db, client, clock):
    now = clock.now()
    high = await _server(db, "a-high")
    unknown = await _server(db, "b-unknown")
    medium = await _server(db, "c-medium")
    low = await _server(db, "d-low")
    await _load(db, high, now, 95.0)
    await _load(db, medium, now, 60.0)
    await _load(db, low, now, 10.0)
    _, token = await registered_user(db, client)
    body = (await client.get("/api/me/servers", headers=bearer(token))).json()
    assert [(s["name"], s["load"], s["recommended"]) for s in body] == [
        ("d-low", "low", True), ("c-medium", "medium", False), ("a-high", "high", False),
        ("b-unknown", "unknown", False)]
    assert all(set(s) == {"id", "name", "containers", "load", "recommended"} for s in body)
    assert unknown.id in [s["id"] for s in body]


async def test_nobody_is_recommended_when_the_first_server_is_not_eligible(db, client, clock):
    now = clock.now()
    busy = await _server(db, "busy")
    await _load(db, busy, now, 90.0)
    _, token = await registered_user(db, client)
    assert [s["recommended"] for s in (await client.get("/api/me/servers", headers=bearer(token))).json()] == [False]

    # The first server by level lacks channel data although its width is set; the second one qualifies.
    blind = await _server(db, "blind", bandwidth_mbps=1000)
    fine = await _server(db, "fine")
    await _load(db, blind, now, 5.0)
    await _load(db, fine, now, 20.0)
    body = (await client.get("/api/me/servers", headers=bearer(token))).json()
    assert body[0]["name"] == "blind" and not any(s["recommended"] for s in body)


async def test_admin_list_has_levels_and_capacities(db, client, admin_token, clock):
    s = await _server(db, "nl", bandwidth_mbps=1000, expected_clients=50)
    await _load(db, s, clock.now(), 30.0, rx=700.0, tx=100.0)
    body = (await client.get("/api/admin/servers", headers=bearer(admin_token))).json()[0]
    assert (body["load"], round(body["load_pct"])) == ("medium", 70)
    assert (body["bandwidth_mbps"], body["expected_clients"], body["metrics_iface"]) == (1000, 50, None)


async def test_capacities_are_validated_and_null_clears_them(db, client, admin_token):
    s = await _server(db, "nl", bandwidth_mbps=1000, expected_clients=50)
    url, h = f"/api/admin/servers/{s.id}", bearer(admin_token)
    for bad in ({"bandwidth_mbps": 0}, {"bandwidth_mbps": 1_000_001}, {"expected_clients": 0},
                {"metrics_iface": "eth0; rm -rf /"}):
        assert (await client.patch(url, json=bad, headers=h)).status_code == 422, bad
    r = await client.patch(url, json={"metrics_iface": "ens3", "expected_clients": None}, headers=h)
    assert r.status_code == 200
    assert (r.json()["bandwidth_mbps"], r.json()["expected_clients"], r.json()["metrics_iface"]) == (1000, None, "ens3")


async def test_admin_load_page(db, client, admin_token, clock):
    s = await _server(db, "nl", specs_json={"cores": 2, "iface": "eth0", "link_mbps": 1000})
    db.add(ServerContainer(server_id=s.id, container="amnezia-ipsec", params_json={}))
    await db.commit()
    await _load(db, s, clock.now(), 30.0, rx=100.0, tx=50.0)
    for range_ in ("24h", "7d"):
        r = await client.get(f"/api/admin/servers/{s.id}/load?range={range_}", headers=bearer(admin_token))
        assert r.status_code == 200
        body = r.json()
        assert body["level"] == "low" and body["specs"]["cores"] == 2
        assert body["hints"] == {"link_mbps": 1000, "peak_mbps_7d": 100.0}
        assert body["current"]["cpu"] == 30.0 and body["window"]["cpu"]["points"] == 7
        assert body["untracked_protocols"] == ["IKEv2"]
        assert {"bandwidth_unset", "clients_unset", "untracked_protocols"} <= {r["code"] for r in
                                                                             body["recommendations"]}
        assert body["series"] and "peaks" in body
    assert (await client.get(f"/api/admin/servers/{s.id}/load?range=1y",
                             headers=bearer(admin_token))).status_code == 422


async def test_users_cannot_read_the_admin_load_page(db, client):
    s = await _server(db, "nl")
    _, token = await registered_user(db, client)
    assert (await client.get(f"/api/admin/servers/{s.id}/load", headers=bearer(token))).status_code == 403
