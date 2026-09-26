"""Telemt (Telegram MTProto proxy) driver. Every client is a `name = "secret"` line in the [access.users]
section of /data/config.toml, which telemt/configure_container.sh writes; links follow the same rules
("ee" + secret + domain hex in fake-TLS mode, "dd" + secret otherwise)."""

import re
import secrets
import urllib.parse
from typing import Any

from app.drivers.base import ApplyResult, ClientInfo, ClientMaterial, Counter, Rendered, register
from app.ssh.conn import Remote

CONFIG = "/data/config.toml"
_USER = re.compile(r'^\s*([A-Za-z0-9_]+)\s*=\s*"([0-9a-fA-F]{32})"\s*$')


def _section(lines: list[str], name: str) -> tuple[int, int] | None:
    """Line range [start, end) of a TOML section body."""
    start = next((i + 1 for i, line in enumerate(lines) if line.strip() == f"[{name}]"), None)
    if start is None:
        return None
    end = next((i for i in range(start, len(lines)) if lines[i].strip().startswith("[")), len(lines))
    return start, end


def _value(lines: list[str], section: str, key: str) -> str | None:
    span = _section(lines, section)
    if not span:
        return None
    for line in lines[span[0]:span[1]]:
        k, _, v = line.partition("=")
        if k.strip() == key:
            return v.strip().strip('"')
    return None


class TelemtDriver:
    container = "amnezia-telemt"
    title = "Telemt (Telegram)"
    installable = True
    script_folder = "telemt"
    default_port = "443"

    def install_vars(self, port: str | None) -> dict[str, str]:
        return {
            "TELEMT_PORT": port or self.default_port, "TELEMT_SECRET": "", "TELEMT_REGENERATE_SECRET": "1",
            "TELEMT_TAG": "", "TELEMT_TLS_DOMAIN": "googletagmanager.com", "TELEMT_PUBLIC_HOST": "",
            "TELEMT_USER_NAME": "amnezia", "TELEMT_USE_MIDDLE_PROXY": "false", "TELEMT_TOML_SECURE": "false",
            "TELEMT_TOML_TLS": "true", "TELEMT_MASK": "true", "TELEMT_TLS_EMULATION": "false",
            "TELEMT_ADDITIONAL_SECRETS": "", "TELEMT_MIDDLE_PROXY_NAT_IP": "",
        }

    async def _lines(self, remote: Remote) -> list[str]:
        return (await remote.read_container_file(self.container, CONFIG)).splitlines()

    async def read_params(self, remote: Remote) -> dict[str, Any]:
        lines = await self._lines(remote)
        return {
            "port": _value(lines, "server", "port") or self.default_port,
            "tls": (_value(lines, "general.modes", "tls") or "false") == "true",
            "tls_domain": _value(lines, "censorship", "tls_domain") or "",
            "public_host": _value(lines, "general.links", "public_host") or "",
        }

    @staticmethod
    def _users(lines: list[str]) -> dict[str, str]:
        span = _section(lines, "access.users")
        users: dict[str, str] = {}
        if span:
            for line in lines[span[0]:span[1]]:
                m = _USER.match(line)
                if m:
                    users[m.group(1)] = m.group(2).lower()
        return users

    async def list_clients(self, remote: Remote) -> dict[str, ClientInfo]:
        return {name: ClientInfo(name, None, {"secret": secret})
                for name, secret in self._users(await self._lines(remote)).items()}

    async def create_material(self, remote: Remote, params: dict[str, Any], taken: set[str]) -> ClientMaterial:
        return ClientMaterial(f"p{secrets.token_hex(5)}", {"secret": secrets.token_hex(16)})

    def reserved(self, material: ClientMaterial) -> str | None:
        return None

    async def apply(self, remote: Remote, desired: list[ClientMaterial], known_ids: set[str],
                    revoked: frozenset[str] | set[str] = frozenset()) -> ApplyResult:
        lines = await self._lines(remote)
        current = self._users(lines)
        wanted = {m.client_id: m for m in desired}
        result = ApplyResult()
        users: dict[str, str] = {}
        for name, secret in current.items():
            if name in wanted or name not in known_ids:
                users[name] = secret
            else:
                result.removed.add(name)
        for name, m in wanted.items():
            if name not in users and m.data.get("secret"):
                users[name] = m.data["secret"]
                result.added.add(name)
        if users != current:
            span = _section(lines, "access.users")
            body = [f'{name} = "{secret}"' for name, secret in users.items()]
            if span is None:
                lines += ["", "[access.users]", *body]
            else:
                lines[span[0]:span[1]] = body + [""]
            await remote.write_container_file(self.container, CONFIG, "\n".join(lines) + "\n")
            await remote.run(f"sudo docker restart {self.container}")
        return result

    async def read_traffic(self, remote: Remote) -> dict[str, Counter]:
        return {}  # Telemt exposes no per-user byte counters the panel can rely on

    def render(self, material: ClientMaterial, params: dict[str, Any], host: str, dns: tuple[str, str],
               description: str) -> Rendered:
        secret = material.data["secret"]
        full = f"ee{secret}{params['tls_domain'].encode().hex()}" if params["tls"] else f"dd{secret}"
        server = params["public_host"] or host
        query = urllib.parse.urlencode({"server": server, "port": params["port"], "secret": full})
        return Rendered("", f"tg://proxy?{query}", "telegram-proxy.txt")


register(TelemtDriver())
