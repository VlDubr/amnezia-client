"""MTProxy (official mtproto-proxy) driver. Panel clients are the additional secrets that
mtproxy/start.sh passes as `-S`; the list is baked into /opt/amnezia/start.sh by the Qt client's variable
substitution and mirrored in /data/mtproxy-meta. The main secret (/data/secret) belongs to the admin's own
Qt configuration and is left alone."""

import re
import secrets
import urllib.parse
from typing import Any

from app.drivers.base import ApplyResult, ClientInfo, ClientMaterial, Counter, Rendered, register
from app.ssh.conn import Remote, RemoteError

START = "/opt/amnezia/start.sh"
META = "/data/mtproxy-meta"
MULTI_CONF = "/data/proxy-multi.conf"
# The additional secrets appear twice, in the `if` right before the `for` loop; other `if [ -n "" ]` checks
# (the tag, the domain) must not be touched.
_BLOCK = re.compile(r'(if \[ -n ")([0-9a-fA-F,]*)(" \]; then\s*\n\s*for S in \$\(echo ")([0-9a-fA-F,]*)(")')
_HEX32 = re.compile(r"^[0-9a-fA-F]{32}$")


def _meta(text: str) -> dict[str, str]:
    return dict(line.split("=", 1) for line in text.splitlines() if "=" in line)


class MtProxyDriver:
    container = "amnezia-mtproxy"
    title = "MTProxy (Telegram)"
    installable = True
    script_folder = "mtproxy"
    default_port = "443"
    shell = "sh"

    def install_vars(self, port: str | None) -> dict[str, str]:
        return {
            "MTPROXY_PORT": port or self.default_port, "MTPROXY_SECRET": "", "MTPROXY_REGENERATE_SECRET": "1",
            "MTPROXY_TAG": "", "MTPROXY_TRANSPORT_MODE": "faketls", "MTPROXY_TLS_DOMAIN": "googletagmanager.com",
            "MTPROXY_PUBLIC_HOST": "", "MTPROXY_ADDITIONAL_SECRETS": "", "MTPROXY_WORKERS_MODE": "auto",
            "MTPROXY_WORKERS": "2", "MTPROXY_NAT_ENABLED": "0", "MTPROXY_NAT_INTERNAL_IP": "",
            "MTPROXY_NAT_EXTERNAL_IP": "",
        }

    async def _start(self, remote: Remote) -> str:
        return await remote.read_container_file(self.container, START)

    @staticmethod
    def _secrets(start: str) -> list[str]:
        m = _BLOCK.search(start)
        if not m:
            raise RemoteError("start.sh has no additional secrets list")
        return [s.lower() for s in m.group(4).split(",") if _HEX32.match(s)]

    async def read_params(self, remote: Remote) -> dict[str, Any]:
        start = await self._start(remote)
        meta = _meta(await remote.read_container_file(self.container, META))
        port = re.search(r"^LISTEN_PORT=(\d+)", start, re.M)
        return {
            "port": port.group(1) if port else self.default_port,
            "tls": meta.get("mode") == "faketls" and bool(meta.get("domain")),
            "tls_domain": meta.get("domain", ""),
            "public_host": meta.get("public_host", ""),
        }

    async def list_clients(self, remote: Remote) -> dict[str, ClientInfo]:
        return {s: ClientInfo(s, None, {"secret": s}) for s in self._secrets(await self._start(remote))}

    async def create_material(self, remote: Remote, params: dict[str, Any], taken: set[str]) -> ClientMaterial:
        secret = secrets.token_hex(16)
        return ClientMaterial(secret, {"secret": secret})

    def reserved(self, material: ClientMaterial) -> str | None:
        return None

    async def apply(self, remote: Remote, desired: list[ClientMaterial], known_ids: set[str],
                    revoked: frozenset[str] | set[str] = frozenset()) -> ApplyResult:
        start = await self._start(remote)
        current = self._secrets(start)
        wanted = {m.client_id for m in desired}
        result = ApplyResult()
        keep = []
        for s in current:
            if s in wanted or s not in known_ids:
                keep.append(s)
            else:
                result.removed.add(s)
        for s in wanted:
            if s not in keep and _HEX32.match(s):
                keep.append(s)
                result.added.add(s)
        if keep != current:
            joined = ",".join(keep)
            start = _BLOCK.sub(lambda m: m.group(1) + joined + m.group(3) + joined + m.group(5), start, count=1)
            meta = await remote.read_container_file(self.container, META)
            meta = re.sub(r"^additional=.*$", f"additional={joined}", meta, flags=re.M)
            await remote.write_container_file(self.container, START, start)
            await remote.write_container_file(self.container, META, meta)
            # mtproto-proxy exits without Telegram's config (its download at install time can fail), and the
            # container would then restart forever.
            await remote.container_exec(self.container, f"[ -s {MULTI_CONF} ] || curl -s --max-time 20 "
                                        f"https://core.telegram.org/getProxyConfig -o {MULTI_CONF}", shell=self.shell)
            await remote.run(f"sudo docker restart {self.container}")
        return result

    async def read_traffic(self, remote: Remote) -> dict[str, Counter]:
        return {}  # mtproto-proxy has no per-secret statistics

    def render(self, material: ClientMaterial, params: dict[str, Any], host: str, dns: tuple[str, str],
               description: str) -> Rendered:
        secret = material.data["secret"]
        full = f"ee{secret}{params['tls_domain'].encode().hex()}" if params["tls"] else f"dd{secret}"
        query = urllib.parse.urlencode({"server": params["public_host"] or host, "port": params["port"],
                                        "secret": full})
        return Rendered("", f"tg://proxy?{query}", "telegram-proxy.txt")


register(MtProxyDriver())
