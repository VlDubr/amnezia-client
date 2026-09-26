"""In-memory stand-ins for a server reached over SSH."""

import re

from app.ssh.conn import RemoteError, RunResult


class FakeRemote:
    """Emulates the subset of Remote used by drivers: container files, `wg show dump`, `syncconf`."""

    def __init__(self, containers: list[str] | None = None):
        self.files: dict[tuple[str, str], str] = {}
        self.commands: list[tuple[str, str]] = []
        self.containers = containers or []
        self.dumps: dict[str, str] = {}  # container -> `wg show <iface> dump` output
        self.fail_with: str | None = None
        self.fail_on: str | None = None  # fail container commands that contain this text

    def _check(self):
        if self.fail_with:
            raise RemoteError(self.fail_with)

    async def run(self, cmd: str, input: str | None = None, check: bool = True, timeout: float = 120) -> RunResult:
        self._check()
        self.commands.append(("host", cmd))
        return RunResult("", "", 0)

    async def read_container_file(self, container: str, path: str) -> str:
        self._check()
        try:
            return self.files[(container, path)]
        except KeyError:
            raise RemoteError(f"cat: {path}: No such file") from None

    async def write_container_file(self, container: str, path: str, content: str) -> None:
        self._check()
        self.commands.append((container, f"write {path}"))
        self.files[(container, path)] = content

    async def container_exec(self, container: str, script: str, shell: str = "bash", check: bool = True) -> str:
        self._check()
        self.commands.append((container, script))
        if self.fail_on and self.fail_on in script:
            raise RemoteError(f"command failed: {self.fail_on}")
        if " show " in script and script.rstrip().endswith("dump"):
            return self.dumps.get(container, "")
        m = re.match(r"cat (\S+) 2>/dev/null \|\| true", script.strip())
        if m:
            return self.files.get((container, m.group(1)), "")
        return ""

    async def list_containers(self) -> list[str]:
        self._check()
        return list(self.containers)


AWG = "amnezia-awg2"
AWG_CONF = "/opt/amnezia/awg/awg0.conf"
AWG_SERVER_CONF = """[Interface]
PrivateKey = c2VydmVycHJpdg==
Address = 10.8.1.0/24
ListenPort = 55424
Jc = 5
H1 = 1

[Peer]
PublicKey = pubA=
PresharedKey = srvpsk=
AllowedIPs = 10.8.1.1/32

[Peer]
PublicKey = pubB=
PresharedKey = srvpsk=
AllowedIPs = 10.8.1.2/32
"""


def awg_server() -> FakeRemote:
    """A server with an AWG container holding two peers created outside the panel."""
    r = FakeRemote([AWG, "amnezia-dns"])
    r.files[(AWG, AWG_CONF)] = AWG_SERVER_CONF
    r.files[(AWG, "/opt/amnezia/awg/wireguard_server_public_key.key")] = "srvpub=\n"
    r.files[(AWG, "/opt/amnezia/awg/wireguard_psk.key")] = "srvpsk=\n"
    r.files[(AWG, "/opt/amnezia/awg/clientsTable")] = (
        '[{"clientId": "pubA=", "userData": {"clientName": "Old phone"}}]')
    return r


def peers_on(remote: FakeRemote, container: str = AWG, path: str = AWG_CONF) -> set[str]:
    from app.drivers.wgconf import parse

    return {p.public_key for p in parse(remote.files[(container, path)]).peers}


def remote_factory_for(remote: FakeRemote):
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory(server):
        remote._check()
        yield remote

    return factory
