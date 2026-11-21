"""Connection-chain cleanup: cancellation and malformed keys must not leak jump connections."""

import asyncio
from unittest.mock import AsyncMock, patch

import pytest

from app.ssh.conn import RemoteError, SshTarget, open_remote


def target(host: str, jump: SshTarget | None = None, key: str | None = None) -> SshTarget:
    return SshTarget(host=host, port=22, user="root", password="pw", private_key=key, host_key=None, jump=jump)


class FakeConn:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True

    async def wait_closed(self):
        return None


async def test_cancelled_last_hop_closes_the_jump():
    jump_conn = FakeConn()
    calls = {"n": 0}

    async def fake_connect(host, port, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:  # the jump connects
            return jump_conn
        await asyncio.sleep(3600)  # the target hangs; the outer timeout cancels us
        raise AssertionError("unreachable")

    with patch("app.ssh.conn.asyncssh.connect", side_effect=fake_connect), pytest.raises(TimeoutError):
        async with asyncio.timeout(0.2):
            async with open_remote(target("de", jump=target("nl"))):
                pass
    assert jump_conn.closed, "the jump connection must be closed when the target hop is cancelled"


async def test_malformed_target_key_closes_the_jump():
    from app.ssh import conn

    jump_conn = FakeConn()

    async def fake_connect(host, port, **kwargs):
        return jump_conn

    # the jump connects; the target carries a malformed private key, so building its options raises
    with patch.object(conn.asyncssh, "connect", new=AsyncMock(side_effect=fake_connect)), pytest.raises(RemoteError):
        async with open_remote(target("de", jump=target("nl"), key="not-a-key")):
            pass
    assert jump_conn.closed


async def test_malformed_key_is_a_remote_error_not_a_500():
    with pytest.raises(RemoteError):
        async with open_remote(target("de", key="not-a-key")):
            pass


async def test_successful_chain_closes_in_reverse_order():
    order = []
    conns = {"nl": FakeConn(), "de": FakeConn()}
    for name, c in conns.items():
        c.close = lambda n=name: order.append(n)

    async def fake_connect(host, port, **kwargs):
        return conns["nl" if host == "nl" else "de"]

    with patch("app.ssh.conn.asyncssh.connect", side_effect=fake_connect):
        async with open_remote(target("de", jump=target("nl"))) as remote:
            assert remote.host == "de"
    assert order == ["de", "nl"]