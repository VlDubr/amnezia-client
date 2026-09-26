"""SSH access to VPN servers; commands mirror the Qt client's SshSession (sudo docker exec ...)."""

import asyncio
import shlex
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass

import asyncssh

CONNECT_TIMEOUT = 20


@dataclass(frozen=True)
class SshTarget:
    host: str
    port: int
    user: str
    password: str | None
    private_key: str | None
    host_key: str | None  # OpenSSH public key line pinned on first contact (TOFU); None accepts any key


@dataclass(frozen=True)
class RunResult:
    stdout: str
    stderr: str
    exit_status: int


class RemoteError(Exception):
    pass


class HostKeyMismatch(RemoteError):
    pass


async def fetch_host_key(host: str, port: int) -> str:
    try:
        key = await asyncio.wait_for(asyncssh.get_server_host_key(host, port), CONNECT_TIMEOUT)
    except (OSError, asyncssh.Error, TimeoutError) as e:
        raise RemoteError(f"cannot reach {host}:{port}: {e}") from e
    if key is None:
        raise RemoteError(f"no host key received from {host}:{port}")
    return key.export_public_key("openssh").decode().strip()


class Remote:
    def __init__(self, conn: asyncssh.SSHClientConnection):
        self._conn = conn

    async def run(self, cmd: str, input: str | None = None, check: bool = True, timeout: float = 120) -> RunResult:
        try:
            r = await self._conn.run(cmd, input=input, check=False, timeout=timeout)
        except (OSError, asyncssh.Error, TimeoutError) as e:
            raise RemoteError(f"command failed to run: {e}") from e
        result = RunResult(str(r.stdout or ""), str(r.stderr or ""), r.exit_status if r.exit_status is not None else -1)
        if check and result.exit_status != 0:
            raise RemoteError(f"`{cmd[:120]}` exited with {result.exit_status}: {result.stderr.strip()[:500]}")
        return result

    async def container_exec(self, container: str, script: str, shell: str = "bash", check: bool = True) -> str:
        cmd = f"sudo docker exec -i {shlex.quote(container)} {shell} -s"
        return (await self.run(cmd, input=script, check=check)).stdout

    async def read_container_file(self, container: str, path: str) -> str:
        return (await self.run(f"sudo docker exec -i {shlex.quote(container)} cat {shlex.quote(path)}")).stdout

    async def write_container_file(self, container: str, path: str, content: str) -> None:
        p = shlex.quote(path)
        tmp = shlex.quote(path + ".panel-tmp")
        inner = f'mkdir -p "$(dirname {p})" && cat > {tmp} && mv {tmp} {p}'
        await self.run(f"sudo docker exec -i {shlex.quote(container)} sh -c {shlex.quote(inner)}", input=content)

    async def list_containers(self) -> list[str]:
        out = (await self.run("sudo docker ps --format '{{.Names}}'")).stdout
        return [line.strip() for line in out.splitlines() if line.strip()]


@asynccontextmanager
async def open_remote(target: SshTarget) -> AsyncIterator[Remote]:
    options: dict = {"username": target.user, "connect_timeout": CONNECT_TIMEOUT}
    if target.password:
        options["password"] = target.password
    if target.private_key:
        options["client_keys"] = [asyncssh.import_private_key(target.private_key)]
    else:
        options["client_keys"] = None
    if target.host_key:
        pinned = asyncssh.import_public_key(target.host_key)
        options["known_hosts"] = ([pinned], [], [])
        options["server_host_key_algs"] = [alg.decode() for alg in pinned.sig_algorithms]
    else:
        options["known_hosts"] = None
    try:
        conn = await asyncssh.connect(target.host, target.port, **options)
    except asyncssh.HostKeyNotVerifiable as e:
        raise HostKeyMismatch(f"host key of {target.host} changed") from e
    except (OSError, asyncssh.Error, TimeoutError) as e:
        raise RemoteError(f"cannot connect to {target.host}:{target.port}: {e}") from e
    try:
        yield Remote(conn)
    finally:
        conn.close()
        await conn.wait_closed()
