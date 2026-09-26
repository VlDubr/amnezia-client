async def test_health(client):
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_unknown_route_uses_error_format(client):
    r = await client.get("/api/nope")
    assert r.status_code == 404
    assert r.json()["code"] == "not_found"
