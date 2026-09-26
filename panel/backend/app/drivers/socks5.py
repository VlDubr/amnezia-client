"""SOCKS5 proxy (3proxy) driver. Users are `users login:CL:password` lines in 3proxy.cfg, as the Qt client
writes them (genSocks5ProxyVars); the panel keeps one line per user and always requires authentication."""

import json
import secrets
import urllib.parse
from typing import Any

from app.drivers.base import ApplyResult, ClientInfo, ClientMaterial, Counter, Rendered, register
from app.ssh.conn import Remote

CONFIG = "/usr/local/3proxy/conf/3proxy.cfg"
LOG = "/usr/local/3proxy/logs/3proxy.log"


def _users(cfg: str) -> dict[str, str]:
    users: dict[str, str] = {}
    for line in cfg.splitlines():
        parts = line.split()
        if parts and parts[0] == "users":
            for entry in parts[1:]:
                login, _, rest = entry.partition(":")
                kind, _, password = rest.partition(":")
                if login and kind == "CL":
                    users[login] = password
    return users


def _rewrite(cfg: str, users: dict[str, str]) -> str:
    """Replaces the users lines and forces `auth strong`; users go before the first auth/socks line."""
    kept = [line for line in cfg.splitlines() if not line.split()[:1] == ["users"]]
    kept = ["auth strong" if line.split()[:1] == ["auth"] else line for line in kept]
    if not any(line.split()[:1] == ["auth"] for line in kept):
        at = next((i for i, line in enumerate(kept) if line.split()[:1] == ["socks"]), len(kept))
        kept.insert(at, "auth strong")
    at = next((i for i, line in enumerate(kept) if line.split()[:1] in (["log"], ["auth"], ["socks"])), len(kept))
    kept[at:at] = [f"users {login}:CL:{password}" for login, password in users.items()]
    return "\n".join(kept) + "\n"


class Socks5Driver:
    container = "amnezia-socks5proxy"
    title = "SOCKS5"
    installable = True
    script_folder = "socks5_proxy"
    default_port = "38080"
    shell = "sh"  # the 3proxy image has no bash

    def install_vars(self, port: str | None) -> dict[str, str]:
        # No initial user and strong auth: the proxy stays closed until the panel adds users.
        return {"SOCKS5_PROXY_PORT": port or self.default_port, "SOCKS5_USER": "", "SOCKS5_AUTH_TYPE": "strong"}

    async def _cfg(self, remote: Remote) -> str:
        return await remote.read_container_file(self.container, CONFIG)

    async def read_params(self, remote: Remote) -> dict[str, Any]:
        port = self.default_port
        for line in (await self._cfg(remote)).splitlines():
            parts = line.split()
            if parts and parts[0] == "socks":
                port = next((p[2:] for p in parts[1:] if p.startswith("-p")), port)
        return {"port": port}

    async def list_clients(self, remote: Remote) -> dict[str, ClientInfo]:
        return {login: ClientInfo(login, None, {"login": login, "secret": password})
                for login, password in _users(await self._cfg(remote)).items()}

    async def create_material(self, remote: Remote, params: dict[str, Any], taken: set[str]) -> ClientMaterial:
        login = f"u{secrets.token_hex(4)}"
        return ClientMaterial(login, {"login": login, "secret": secrets.token_urlsafe(18)})

    def reserved(self, material: ClientMaterial) -> str | None:
        return None

    async def apply(self, remote: Remote, desired: list[ClientMaterial], known_ids: set[str],
                    revoked: frozenset[str] | set[str] = frozenset()) -> ApplyResult:
        cfg = await self._cfg(remote)
        current = _users(cfg)
        wanted = {m.client_id: m for m in desired}
        result = ApplyResult()
        users: dict[str, str] = {}
        for login, password in current.items():
            if login in wanted or login not in known_ids:
                users[login] = password
            else:
                result.removed.add(login)
        for login, m in wanted.items():
            if login not in users and m.data.get("secret"):
                users[login] = m.data["secret"]
                result.added.add(login)
        new_cfg = _rewrite(cfg, users)
        if new_cfg != cfg:
            await remote.write_container_file(self.container, CONFIG, new_cfg)
            await remote.run(f"sudo docker restart {self.container}")
        return result

    async def read_traffic(self, remote: Remote) -> dict[str, Counter]:
        out = await remote.container_exec(self.container, f"cat {LOG} 2>/dev/null || true", shell=self.shell)
        counters: dict[str, Counter] = {}
        for line in out.splitlines():
            start = line.find("{")
            if start < 0:
                continue
            try:
                entry = json.loads(line[start:])
                user = entry["auth"]["user"]
                sent, received = int(entry["bytes"]["sent"]), int(entry["bytes"]["received"])
            except (ValueError, KeyError, TypeError):
                continue
            if not user or user == "-":
                continue
            c = counters.setdefault(user, Counter(0, 0, None))
            # 3proxy counts from the target's side: "sent" went out to the target (the client's upload),
            # "received" came back from it (the client's download).
            c.rx += sent
            c.tx += received
        return counters

    def render(self, material: ClientMaterial, params: dict[str, Any], host: str, dns: tuple[str, str],
               description: str) -> Rendered:
        q = urllib.parse.quote
        endpoint = f"[{host}]" if ":" in host and not host.startswith("[") else host
        link = f"socks5://{q(material.data['login'], safe='')}:{q(material.data['secret'], safe='')}@" \
               f"{endpoint}:{params['port']}"
        return Rendered("", link, "socks5.txt")


register(Socks5Driver())
