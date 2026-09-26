"""Xray (VLESS) driver. Mirrors XrayConfigurator / UsersController::revokeXray in the Qt client.

The server keeps its clients in /opt/amnezia/xray/server.json (inbounds[0].settings.clients). On the first apply
the driver also enables the stats API so traffic can be counted per client (clients get `email` = their id).
"""

import copy
import json
import urllib.parse
import uuid
from typing import Any

from app.drivers.base import ApplyResult, ClientInfo, ClientMaterial, Counter, Rendered, register
from app.render.vpnkey import encode_vpn_key
from app.ssh.conn import Remote, RemoteError

DATA_DIR = "/opt/amnezia/xray"
SERVER_CONFIG = f"{DATA_DIR}/server.json"
API_LISTEN = "127.0.0.1:10085"
DEFAULT_SITE = "www.googletagmanager.com"
DEFAULT_FLOW = "xtls-rprx-vision"
LOCAL_PROXY_PORT = 10808


def _prepare(conf: dict[str, Any]) -> None:
    """Enables per-client traffic statistics (idempotent)."""
    conf["api"] = {"tag": "api", "listen": API_LISTEN, "services": ["StatsService"]}
    conf.setdefault("stats", {})
    levels = conf.setdefault("policy", {}).setdefault("levels", {})
    level0 = levels.setdefault("0", {})
    level0["statsUserUplink"] = True
    level0["statsUserDownlink"] = True


def _client_entry(client_id: str, flow: str | None) -> dict[str, Any]:
    entry: dict[str, Any] = {"id": client_id, "email": client_id, "level": 0}
    if flow:
        entry["flow"] = flow
    return entry


class XrayDriver:
    container = "amnezia-xray"
    title = "XRay"
    installable = True
    script_folder = "xray"
    default_port = "443"

    def install_vars(self, port: str | None) -> dict[str, str]:
        return {"XRAY_SERVER_PORT": port or self.default_port}

    async def _read_key(self, remote: Remote, name: str) -> str:
        return (await remote.read_container_file(self.container, f"{DATA_DIR}/{name}")).strip()

    async def before_start(self, remote: Remote, variables: dict[str, str]) -> None:
        """Writes the initial server.json the way XrayConfigurator::writeServerConfigForSetup does
        (VLESS + REALITY over TCP with the vision flow)."""
        client_id = await self._read_key(remote, "xray_uuid.key")
        conf = {
            "log": {"loglevel": "error"},
            "inbounds": [{
                "port": int(variables["XRAY_SERVER_PORT"]),
                "protocol": "vless",
                "settings": {"clients": [{"id": client_id, "flow": DEFAULT_FLOW}], "decryption": "none"},
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "dest": f"{DEFAULT_SITE}:443",
                        "fingerprint": "chrome",
                        "privateKey": await self._read_key(remote, "xray_private.key"),
                        "serverNames": [DEFAULT_SITE],
                        "shortIds": [await self._read_key(remote, "xray_short_id.key")],
                    },
                },
            }],
            "outbounds": [{"protocol": "freedom"}],
        }
        await remote.write_container_file(self.container, SERVER_CONFIG, json.dumps(conf, indent=4))

    async def _read_conf(self, remote: Remote) -> dict[str, Any]:
        conf = json.loads(await remote.read_container_file(self.container, SERVER_CONFIG))
        if not conf.get("inbounds"):
            raise RemoteError("xray server.json has no inbounds")
        return conf

    @staticmethod
    def _clients(conf: dict[str, Any]) -> list[dict[str, Any]]:
        return conf["inbounds"][0].setdefault("settings", {}).setdefault("clients", [])

    async def read_params(self, remote: Remote) -> dict[str, Any]:
        conf = await self._read_conf(remote)
        inbound = conf["inbounds"][0]
        stream = inbound.get("streamSettings", {})
        flows = [c.get("flow") for c in self._clients(conf) if c.get("flow")]
        params: dict[str, Any] = {
            "port": str(inbound.get("port", self.default_port)),
            "stream": stream,
            "flow": flows[0] if flows else "",
        }
        if stream.get("security") == "reality":
            params["public_key"] = await self._read_key(remote, "xray_public.key")
            params["short_id"] = await self._read_key(remote, "xray_short_id.key")
        return params

    async def list_clients(self, remote: Remote) -> dict[str, ClientInfo]:
        conf = await self._read_conf(remote)
        out = {}
        for c in self._clients(conf):
            if c.get("id"):
                out[c["id"]] = ClientInfo(c["id"], None, {"secret": c["id"], "flow": c.get("flow", "")})
        return out

    async def create_material(self, remote: Remote, params: dict[str, Any], taken: set[str]) -> ClientMaterial:
        client_id = str(uuid.uuid4())
        return ClientMaterial(client_id, {"secret": client_id, "flow": params.get("flow", "")})

    def reserved(self, material: ClientMaterial) -> str | None:
        return None

    async def apply(self, remote: Remote, desired: list[ClientMaterial], known_ids: set[str],
                    revoked: frozenset[str] | set[str] = frozenset()) -> ApplyResult:
        conf = await self._read_conf(remote)
        before = copy.deepcopy(conf)
        wanted = {m.client_id: m for m in desired}
        flow_default = next((c.get("flow") for c in self._clients(conf) if c.get("flow")), "")
        result = ApplyResult()
        kept: list[dict[str, Any]] = []
        present = set()
        for c in self._clients(conf):
            cid = c.get("id")
            if cid in wanted or cid not in known_ids:
                c["email"] = cid
                c.setdefault("level", 0)
                kept.append(c)
                present.add(cid)
            else:
                result.removed.add(cid)
        for cid, m in wanted.items():
            if cid not in present:
                kept.append(_client_entry(cid, m.data.get("flow", flow_default)))
                result.added.add(cid)
        conf["inbounds"][0]["settings"]["clients"] = kept
        _prepare(conf)
        if conf != before:
            await remote.write_container_file(self.container, SERVER_CONFIG, json.dumps(conf, indent=4))
            await remote.run(f"sudo docker restart {self.container}")
        return result

    async def read_traffic(self, remote: Remote) -> dict[str, Counter]:
        try:
            out = await remote.container_exec(
                self.container, f"xray api statsquery --server={API_LISTEN} -pattern 'user>>>'")
            stats = json.loads(out or "{}").get("stat", [])
        except (RemoteError, ValueError):
            return {}  # stats API not enabled yet (before the first apply) or xray restarting
        counters: dict[str, Counter] = {}
        for item in stats:
            parts = item.get("name", "").split(">>>")
            if len(parts) != 4 or parts[0] != "user":
                continue
            c = counters.setdefault(parts[1], Counter(0, 0, None))
            value = int(item.get("value", 0) or 0)
            if parts[3] == "uplink":
                c.rx = value
            elif parts[3] == "downlink":
                c.tx = value
        return counters

    def _client_stream(self, params: dict[str, Any]) -> dict[str, Any]:
        stream = copy.deepcopy(params.get("stream", {}))
        if stream.get("security") == "reality":
            server = stream.get("realitySettings", {})
            names = server.get("serverNames") or [DEFAULT_SITE]
            stream["realitySettings"] = {
                "fingerprint": server.get("fingerprint", "chrome"),
                "serverName": names[0],
                "publicKey": params.get("public_key", ""),
                "shortId": params.get("short_id", ""),
                "spiderX": "",
            }
        return stream

    def _vless_link(self, client_id: str, flow: str, params: dict[str, Any], host: str, name: str) -> str:
        stream = params.get("stream", {})
        security = stream.get("security", "none")
        query: dict[str, str] = {"type": stream.get("network", "tcp"), "security": security,
                                 "encryption": "none"}
        if security == "reality":
            rs = self._client_stream(params)["realitySettings"]
            query.update({"pbk": rs["publicKey"], "sid": rs["shortId"], "sni": rs["serverName"],
                          "fp": rs["fingerprint"], "spx": "/"})
        elif security == "tls":
            tls = stream.get("tlsSettings", {})
            query.update({k: v for k, v in (("sni", tls.get("serverName")), ("fp", tls.get("fingerprint")),
                                            ("alpn", ",".join(tls.get("alpn", [])))) if v})
        if stream.get("network") == "xhttp":
            x = stream.get("xhttpSettings", {})
            query.update({k: str(v) for k, v in (("host", x.get("host")), ("path", x.get("path")),
                                                 ("mode", x.get("mode"))) if v})
        if flow:
            query["flow"] = flow
        endpoint = f"[{host}]" if ":" in host and not host.startswith("[") else host
        return (f"vless://{client_id}@{endpoint}:{params['port']}?{urllib.parse.urlencode(query)}"
                f"#{urllib.parse.quote(name)}")

    def render(self, material: ClientMaterial, params: dict[str, Any], host: str, dns: tuple[str, str],
               description: str) -> Rendered:
        client_id = material.client_id
        flow = material.data.get("flow", "")
        user: dict[str, Any] = {"id": client_id, "encryption": "none"}
        if flow:
            user["flow"] = flow
        client = {
            "log": {"loglevel": "error"},
            "inbounds": [{"listen": "127.0.0.1", "port": LOCAL_PROXY_PORT, "protocol": "socks",
                          "settings": {"udp": True}}],
            "outbounds": [{
                "protocol": "vless",
                "settings": {"vnext": [{"address": host, "port": int(params["port"]), "users": [user]}]},
                "streamSettings": self._client_stream(params),
            }],
        }
        doc = {
            "containers": [{"container": self.container, "xray": {
                "port": params["port"],
                "transport_proto": "tcp",
                "last_config": json.dumps(client, separators=(",", ":")),
            }}],
            "defaultContainer": self.container,
            "description": description,
            "dns1": dns[0],
            "dns2": dns[1],
            "hostName": host,
        }
        return Rendered(encode_vpn_key(doc), self._vless_link(client_id, flow, params, host, description),
                        "vless.txt")


register(XrayDriver())
