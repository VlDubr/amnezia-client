import dataclasses

import pytest

from app.ssh.conn import HostKeyMismatch, RemoteError, fetch_host_key, open_remote

pytestmark = pytest.mark.integration


async def test_fetch_host_key_and_run(sshhost):
    key = await fetch_host_key(sshhost.host, sshhost.port)
    assert key.split()[0].startswith(("ssh-", "ecdsa-"))
    async with open_remote(dataclasses.replace(sshhost, host_key=key)) as remote:
        r = await remote.run("echo hi")
        assert r.stdout == "hi\n" and r.exit_status == 0


async def test_wrong_host_key_is_rejected(sshhost):
    other = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIDqL5r5Yq3v2yZ3b8vB6yJ0pZ8o0Z7l4Q1q2w3e4r5t6"
    with pytest.raises(HostKeyMismatch):
        async with open_remote(dataclasses.replace(sshhost, host_key=other)):
            pass


async def test_run_failure_raises(sshhost):
    async with open_remote(sshhost) as remote:
        with pytest.raises(RemoteError):
            await remote.run("exit 3")
        r = await remote.run("exit 3", check=False)
        assert r.exit_status == 3


async def test_container_files_and_exec(sshhost, scratch_container):
    async with open_remote(sshhost) as remote:
        assert scratch_container in await remote.list_containers()
        await remote.write_container_file(scratch_container, "/opt/amnezia/test/a.conf", "line1\n$VAR 'q'\n")
        assert await remote.read_container_file(scratch_container, "/opt/amnezia/test/a.conf") == "line1\n$VAR 'q'\n"
        out = await remote.container_exec(scratch_container, "cat /opt/amnezia/test/a.conf | wc -l", shell="sh")
        assert out.strip() == "2"
