"""Xray (VLESS) driver. Mirrors XrayConfigurator / UsersController::revokeXray in the Qt client.

The server keeps its clients in /opt/amnezia/xray/server.json (inbounds[0].settings.clients). On the first apply
the driver also enables the stats API so traffic can be counted per client (clients get `email` = their id).

Client configs are rendered only from the live server.json facts that read_params collects (the sync job
refreshes them at least every 15 minutes). For tcp+REALITY three profiles are handed out: the mobile Amnezia
Xray JSON with the tlshello fragment, an importable Clash Verge / mihomo YAML without fragmentation, and the
vless:// link, which cannot carry the fragment. An invariant report ships with every config.
"""

import asyncio
import base64
import copy
import hashlib
import ipaddress
import json
import re
import socket
import threading
import unicodedata
import urllib.parse
import uuid
from typing import Any, Literal

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.domain.filenames import download_filename
from app.drivers.base import ApplyResult, ClientInfo, ClientMaterial, Counter, ExtraFile, Rendered, register
from app.render.vpnkey import encode_vpn_key
from app.ssh.conn import Remote, RemoteError

DATA_DIR = "/opt/amnezia/xray"
SERVER_CONFIG = f"{DATA_DIR}/server.json"
API_LISTEN = "127.0.0.1:10085"
DEFAULT_SITE = "www.googletagmanager.com"
DEFAULT_FLOW = "xtls-rprx-vision"
LOCAL_PROXY_PORT = 10808
# The core Amnezia mobile is pinned to: other versions may negotiate ML-KEM the mobile client cannot do.
EXPECTED_CORE_VERSION = "26.7.28"
FRAGMENT_TAG = "frag"
# chrome without fragmentation does not work on mobile networks; ios is the fallback if firefox fails.
MOBILE_FINGERPRINT = "firefox"
DEFAULT_RENDER_OPTIONS: dict[str, Any] = {
    "mobile_fingerprint": MOBILE_FINGERPRINT,
    "fragment": {"length": "100-200", "interval": "10-20"},  # packets stays tlshello
    "mihomo_fingerprint": "chrome",
    "spider_x": "",
}
# Xray reads uuid hex case-insensitively; imported clients may carry uppercase ids.
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
PUBLIC_KEY_RE = re.compile(r"[A-Za-z0-9_-]{43}")  # x25519 public key: base64url without padding
SHORT_ID_RE = re.compile(r"(?:[0-9a-fA-F]{2}){0,8}")
DEST_LOOP_TIMEOUT_S = 5
# A timed-out getaddrinfo keeps its thread running. Daemon threads cannot block interpreter shutdown,
# and two slots cap how many hung resolutions can pile up; with both slots busy the verdict is inconclusive.
_DNS_SLOTS = threading.Semaphore(2)
# mihomo built-in outbounds and our own group name: a proxy must not be called any of these.
MIHOMO_RESERVED_NAMES = {"DIRECT", "REJECT", "REJECT-DROP", "PASS", "COMPATIBLE", "GLOBAL", "PROXY"}


class FragmentOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")  # a typo must fail loudly, not look like a successful patch

    # [0-9], not \d: Xray parses ASCII digits only, \d would also accept e.g. fullwidth Unicode digits.
    length: str | None = Field(default=None, pattern=r"^[0-9]{1,5}-[0-9]{1,5}$")
    interval: str | None = Field(default=None, pattern=r"^[0-9]{1,5}-[0-9]{1,5}$")

    @field_validator("length", "interval")
    @classmethod
    def _ordered_range(cls, v: str | None, info) -> str | None:
        if v is None:
            return None
        lo, hi = (int(part) for part in v.split("-"))
        if lo > hi or hi > 65535 or (info.field_name == "length" and lo < 1):
            raise ValueError("range must be 'from-to' with from <= to <= 65535 (length starts at 1)")
        return v


class XrayRenderOptions(BaseModel):
    """Admin overrides for rendering client configs; an explicit null resets the key to its default."""
    model_config = ConfigDict(extra="forbid")

    mobile_fingerprint: Literal["firefox", "ios"] | None = None
    fragment: FragmentOptions | None = None
    mihomo_fingerprint: Literal["chrome", "firefox", "safari", "ios", "android", "edge", "random"] | None = None
    spider_x: str | None = Field(default=None, max_length=256)

    @field_validator("spider_x")
    @classmethod
    def _spider_path(cls, v: str | None) -> str | None:
        if v is None:
            return None
        if not v.startswith("/") or any(unicodedata.category(c) == "Cc" for c in v):
            raise ValueError("spider_x must be a path starting with '/' and without control characters")
        return v


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


def _id_hash(client_id: str) -> str:
    # params_json is stored in the clear, and a client id is the credential: keep the ids themselves out of it.
    return hashlib.sha256(client_id.encode()).hexdigest()


def _is_ip(host: str) -> bool:
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _dest_host(dest: str) -> str:
    return dest.rsplit(":", 1)[0].strip("[]")


def _same_ip(dest_host: str, server_host: str) -> bool:
    def ips(host: str) -> set[str]:
        return {info[4][0] for info in socket.getaddrinfo(host, None)}

    return bool(ips(dest_host) & ips(server_host))


def _settle(fut: asyncio.Future, result: bool | None, error: BaseException | None) -> None:
    if not fut.done():  # wait_for may have cancelled it on timeout
        if error is not None:
            fut.set_exception(error)
        else:
            fut.set_result(result)


async def _resolves_to_same_ip(dest_host: str, server_host: str) -> bool | None:
    if not _DNS_SLOTS.acquire(blocking=False):
        return None
    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()

    def resolve() -> None:
        try:
            try:
                result = _same_ip(dest_host, server_host)
            except OSError as e:
                loop.call_soon_threadsafe(_settle, fut, None, e)
            else:
                loop.call_soon_threadsafe(_settle, fut, result, None)
        except RuntimeError:
            pass  # the event loop closed while the resolution was in flight (shutdown)
        finally:
            _DNS_SLOTS.release()

    try:
        threading.Thread(target=resolve, name="xray-dns", daemon=True).start()
    except RuntimeError:  # thread startup refused (resource exhaustion): do not leak the slot
        _DNS_SLOTS.release()
        return None
    try:
        return await asyncio.wait_for(fut, DEST_LOOP_TIMEOUT_S)
    except (TimeoutError, OSError):
        return None


def _derive_public_key(private_key: str) -> str:
    """The X25519 public key of a base64url private key, as `xray x25519 -i` would print it. Done locally:
    the private key must not travel back to the server in a script or a process command line."""
    raw = base64.urlsafe_b64decode(private_key + "=" * (-len(private_key) % 4))
    public = X25519PrivateKey.from_private_bytes(raw).public_key().public_bytes(
        serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    return base64.urlsafe_b64encode(public).decode().rstrip("=")


def _invariants(params: dict[str, Any], host: str) -> list[dict[str, str]]:
    """The server invariants the client configs depend on. 'fail' entries block rendering, 'warn' ship in the
    report. Pure: works both with fresh params and with params cached before a fact was introduced."""
    checks: list[dict[str, str]] = []

    def add(status: str, text: str) -> None:
        checks.append({"status": status, "text": text})

    stream = params.get("stream", {})
    network = stream.get("network", "tcp")
    version = params.get("core_version", "")
    if version == EXPECTED_CORE_VERSION:
        add("ok", f"core version {version}")
    elif version:
        add("warn", f"core version {version} != {EXPECTED_CORE_VERSION}: possible ML-KEM incompatibility "
                    "with Amnezia mobile")
    else:
        add("warn", "core version unknown ('xray version' was not read)")

    if stream.get("security") == "reality":
        reality = stream.get("realitySettings", {})
        min_ver = reality.get("minClientVer", "")
        if min_ver == "0.0.0":
            add("ok", "minClientVer 0.0.0")
        else:
            add("warn", f"minClientVer {min_ver!r} != '0.0.0': mihomo may get REALITY authentication failed")
        max_ver, max_diff = reality.get("maxClientVer", ""), reality.get("maxTimeDiff", 0)
        if max_ver == "" and max_diff == 0:
            add("ok", 'maxClientVer "" and maxTimeDiff 0')
        else:
            add("warn", f"maxClientVer {max_ver!r} / maxTimeDiff {max_diff!r}: expected '' / 0")
        dest = reality.get("dest", "")
        loop = params.get("dest_loop")
        if loop is None and dest:  # no DNS verdict (legacy params or resolver unavailable): string check only
            loop = True if _dest_host(dest).lower() == host.lower() else None
        if not dest:
            add("fail", "realitySettings.dest is not set")
        elif loop:
            add("fail", f"dest {dest} points back at this server ({host}): REALITY dest loop, "
                        "the camouflaged site will not open")
        elif loop is None:
            add("warn", f"dest {dest}: could not verify it does not point back at this server")
        else:
            add("ok", f"dest {dest}")
        if reality.get("serverNames"):
            add("ok", f"serverNames {reality['serverNames']}")
        else:
            add("fail", "realitySettings.serverNames is empty")
        if reality.get("shortIds"):
            add("ok", f"shortIds {reality['shortIds']}")
        else:
            add("fail", "realitySettings.shortIds is empty")
        if params.get("public_key_source") == "file":
            add("warn", "publicKey taken from xray_public.key: the server.json private key could not be "
                        "parsed, the file may be stale")

    flows = params.get("client_flows")
    if flows is not None:
        if network == "xhttp":
            with_flow = sum(1 for f in flows.values() if f)
            if with_flow:
                add("warn", f"{with_flow} client(s) have a flow on xhttp: it must be empty")
            else:
                add("ok", "xhttp clients have no flow")
        elif network == "tcp":
            deviant = sum(1 for f in flows.values() if f != DEFAULT_FLOW)
            if deviant:
                add("warn", f"{deviant} client(s) do not have flow {DEFAULT_FLOW} on tcp")
            else:
                add("ok", f"all clients have flow {DEFAULT_FLOW}")
        else:
            add("warn", f"network {network}: the mobile fragment profile is defined for tcp, none is added")
    return checks


def _report_text(checks: list[dict[str, str]], params: dict[str, Any], host: str, description: str,
                 json_name: str, yaml_name: str, link_name: str, mobile: bool,
                 options: dict[str, Any] | None, opts: dict[str, Any]) -> str:
    lines = [f"Xray config report: {description}",
             f"Server: {host}:{params['port']} (container amnezia-xray)",
             "Source: live /opt/amnezia/xray/server.json, refreshed by the panel sync"]
    if options:
        lines.append(f"Render options (admin overrides): {json.dumps(options, sort_keys=True)}")
    lines += ["", "Invariants:"]
    lines += [f"  [{c['status'].upper():4}] {c['text']}" for c in checks]
    lines += ["", "Profiles:"]
    if mobile:
        fp = opts["mobile_fingerprint"]
        length, interval = opts["fragment"]["length"], opts["fragment"]["interval"]
        alt_fp = "ios" if fp == "firefox" else "firefox"
        alt_length = "50-100" if length == "100-200" else "100-200"
        lines += [
            f"  {json_name} - Amnezia mobile (Xray JSON): vless first, REALITY fingerprint {fp},",
            f'      tlshello fragment {length}/{interval} through the freedom outbound "frag" (sockopt.dialerProxy).',
            (f'      Import this file on the phone. If it still fails: fingerprint "{alt_fp}"'
             f' or fragment length "{alt_length}".'),
            (f"  {yaml_name} - Clash Verge / mihomo: {opts['mihomo_fingerprint']} fingerprint,"
             " no fragmentation (mihomo cannot tlshello)."),
        ]
    else:
        lines.append(f"  {json_name} - Xray client JSON for this server's transport (no mobile fragment profile).")
    lines += [
        f"  {link_name} - vless:// link: it does NOT carry fragment/dialerProxy, so it only fits clients",
        "      without fragmentation (Clash/desktop). For mobile use the JSON file.",
        "",
        "Drift: after any save/reinstall in the Amnezia UI the panel re-reads server.json on the next sync",
        "(at most 15 minutes); re-download and re-import the configs on all devices afterwards.",
    ]
    return "\n".join(lines) + "\n"


class XrayDriver:
    container = "amnezia-xray"
    title = "XRay"
    installable = True
    traffic_counters = True
    script_folder = "xray"
    default_port = "443"
    render_options_model = XrayRenderOptions

    @staticmethod
    def effective_render_options(options: dict[str, Any] | None) -> dict[str, Any]:
        """Defaults merged with the admin's stored overrides (only the overridden keys are stored)."""
        opts = copy.deepcopy(DEFAULT_RENDER_OPTIONS)
        for key, value in (options or {}).items():
            if key == "fragment":
                opts["fragment"].update(value or {})
            elif value is not None:
                opts[key] = value
        return opts

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

    async def _core_version(self, remote: Remote) -> str:
        out = await remote.container_exec(self.container, "xray version", check=False)
        m = re.search(r"Xray\s+(\d+\.\d+\.\d+)", out or "")
        return m.group(1) if m else ""

    async def _public_key(self, remote: Remote, private_key: str) -> tuple[str, str]:
        """Derives the public key from the live server.json private key locally: the key file can be stale
        after a reinstall, and the key itself must not go back to the server in a script or command line."""
        if private_key:
            try:
                return _derive_public_key(private_key), "derived"
            except ValueError:  # not base64url or not 32 bytes: fall back to the key file
                pass
        return await self._read_key(remote, "xray_public.key"), "file"

    async def _dest_loop(self, remote: Remote, dest: str) -> bool | None:
        """Whether dest points back at this server (a REALITY dest loop). None when it cannot be determined."""
        host = getattr(remote, "host", None)
        if not dest or not host:
            return None
        dest_host, server_host = _dest_host(dest), host.strip("[]")
        if dest_host.lower() == server_host.lower():
            return True
        if _is_ip(dest_host) and _is_ip(server_host):
            return ipaddress.ip_address(dest_host) == ipaddress.ip_address(server_host)
        return await _resolves_to_same_ip(dest_host, server_host)

    async def read_params(self, remote: Remote) -> dict[str, Any]:
        conf = await self._read_conf(remote)
        inbound = conf["inbounds"][0]
        stream = copy.deepcopy(inbound.get("streamSettings", {}))
        reality = stream.get("realitySettings", {})
        # Unconditionally: a server switched to tls may still carry a stale realitySettings.privateKey,
        # and no server private key (REALITY or inline TLS certificate keys) may enter params_json.
        private_key = reality.pop("privateKey", "")
        stream.get("tlsSettings", {}).pop("certificates", None)
        clients = self._clients(conf)
        flows = [c.get("flow") for c in clients if c.get("flow")]
        params: dict[str, Any] = {
            "port": str(inbound.get("port", self.default_port)),
            "stream": stream,
            "flow": flows[0] if flows else "",
            "client_flows": {_id_hash(c["id"]): c.get("flow", "") for c in clients if c.get("id")},
            "core_version": await self._core_version(remote),
        }
        if stream.get("security") == "reality":
            params["public_key"], params["public_key_source"] = await self._public_key(remote, private_key)
            short_ids = reality.get("shortIds") or []
            params["short_id"] = short_ids[0] if short_ids else await self._read_key(remote, "xray_short_id.key")
            params["dest_loop"] = await self._dest_loop(remote, reality.get("dest", ""))
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
        # Raises while the stats API is not enabled yet (before the first apply) or Xray restarts: the caller must
        # not mistake that for "no traffic".
        out = await remote.container_exec(
            self.container, f"xray api statsquery --server={API_LISTEN} -pattern 'user>>>'")
        stats = json.loads(out or "{}").get("stat", [])
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
        else:
            # Server-side leftovers have no business in a client config; params cached by older drivers
            # may still hold a privateKey in there.
            stream.pop("realitySettings", None)
        if "tlsSettings" in stream:
            # Only what a client needs, as XrayConfigurator::buildStreamSettings: never the server certificates.
            tls = stream["tlsSettings"]
            stream["tlsSettings"] = {k: tls[k] for k in ("serverName", "alpn", "fingerprint") if k in tls}
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

    @staticmethod
    def _validate(material: ClientMaterial, params: dict[str, Any]) -> None:
        """Blocking checks: a config that cannot work must not leave the generator."""
        client_id = material.client_id
        if not UUID_RE.fullmatch(client_id):
            raise ValueError(f"client id {client_id!r} is not a valid UUID")
        flows = params.get("client_flows")
        if flows is not None and _id_hash(client_id) not in flows:
            raise ValueError(f"client {client_id} is not present in the live server.json clients[]")
        if params.get("stream", {}).get("security") == "reality":
            if not PUBLIC_KEY_RE.fullmatch(params.get("public_key", "")):
                raise ValueError("publicKey must be 43 base64url characters (x25519)")
            if not SHORT_ID_RE.fullmatch(params.get("short_id", "")):
                raise ValueError("shortId must be hex of even length, at most 16 characters")

    def _live_flow(self, material: ClientMaterial, params: dict[str, Any]) -> str:
        flows = params.get("client_flows")
        if flows is not None:
            return flows.get(_id_hash(material.client_id), "")
        return material.data.get("flow", "")  # params cached before client_flows existed

    def _mobile_client(self, client_id: str, flow: str, params: dict[str, Any], host: str,
                       opts: dict[str, Any]) -> dict[str, Any]:
        """Profile A: the mobile Amnezia Xray JSON, exactly the working shape and nothing else (no dns/routing/
        mux/sniffing). vless comes first, the fragment outbound is reached through sockopt.dialerProxy."""
        reality = params["stream"]["realitySettings"]
        names = reality.get("serverNames") or [DEFAULT_SITE]
        user: dict[str, Any] = {"id": client_id, "encryption": "none"}
        if flow:
            user["flow"] = flow
        return {
            "log": {"loglevel": "error"},
            "inbounds": [{"listen": "127.0.0.1", "port": LOCAL_PROXY_PORT, "protocol": "socks",
                          "settings": {"udp": True}}],
            "outbounds": [
                {"protocol": "vless",
                 "settings": {"vnext": [{"address": host, "port": int(params["port"]), "users": [user]}]},
                 "streamSettings": {
                     "network": "tcp",
                     "security": "reality",
                     "tcpSettings": {"header": {"type": "none"}},
                     "realitySettings": {"fingerprint": opts["mobile_fingerprint"], "serverName": names[0],
                                         "publicKey": params["public_key"], "shortId": params["short_id"],
                                         "spiderX": opts["spider_x"]},
                     "sockopt": {"dialerProxy": FRAGMENT_TAG}}},
                {"tag": FRAGMENT_TAG, "protocol": "freedom",
                 "settings": {"fragment": {"packets": "tlshello", **opts["fragment"]}}},
            ],
        }

    def _plain_client(self, client_id: str, flow: str, params: dict[str, Any], host: str) -> dict[str, Any]:
        """Client JSON for servers the mobile fragment profile does not cover (tls, xhttp, other transports)."""
        user: dict[str, Any] = {"id": client_id, "encryption": "none"}
        if flow:
            user["flow"] = flow
        return {
            "log": {"loglevel": "error"},
            "inbounds": [{"listen": "127.0.0.1", "port": LOCAL_PROXY_PORT, "protocol": "socks",
                          "settings": {"udp": True}}],
            "outbounds": [{
                "protocol": "vless",
                "settings": {"vnext": [{"address": host, "port": int(params["port"]), "users": [user]}]},
                "streamSettings": self._client_stream(params),
            }],
        }

    def _mihomo_config(self, client_id: str, flow: str, params: dict[str, Any], host: str, name: str,
                       opts: dict[str, Any]) -> str:
        """Profile B: an importable Clash Verge / mihomo config. No fragmentation (mihomo cannot tlshello),
        no alpn/h2 and no xhttp next to tcp+vision."""
        reality = params["stream"]["realitySettings"]
        names = reality.get("serverNames") or [DEFAULT_SITE]
        # A config named PROXY or DIRECT would collide with the group or a built-in outbound.
        proxy_name = name if name.upper() not in MIHOMO_RESERVED_NAMES else f"{name}-xray"
        proxy: dict[str, Any] = {"name": proxy_name, "type": "vless", "server": host, "port": int(params["port"]),
                                 "uuid": client_id, "network": "tcp", "udp": True, "tls": True}
        if flow:
            proxy["flow"] = flow
        proxy.update({"servername": names[0], "client-fingerprint": opts["mihomo_fingerprint"],
                      "reality-opts": {"public-key": params["public_key"], "short-id": params["short_id"],
                                       "support-x25519mlkem768": False}})
        doc = {"mixed-port": 7890, "allow-lan": False, "mode": "rule", "log-level": "info",
               "proxies": [proxy],
               "proxy-groups": [{"name": "PROXY", "type": "select", "proxies": [proxy_name, "DIRECT"]}],
               "rules": ["MATCH,PROXY"]}
        return yaml.safe_dump(doc, sort_keys=False, allow_unicode=True)

    @staticmethod
    def _validate_artifacts(client: dict[str, Any], mihomo_yaml: str | None, mobile: bool) -> None:
        json.loads(json.dumps(client))  # the JSON must parse
        if mobile:
            outbounds = client["outbounds"]
            first = outbounds[0]
            if first["protocol"] != "vless":
                raise ValueError("profile A: the vless outbound must come first")
            stream = first["streamSettings"]
            if (FRAGMENT_TAG not in [o.get("tag") for o in outbounds]
                    or stream.get("sockopt", {}).get("dialerProxy") != FRAGMENT_TAG):
                raise ValueError("profile A: the fragment outbound and sockopt.dialerProxy must match")
            if stream["realitySettings"]["fingerprint"] not in ("firefox", "ios"):
                raise ValueError("profile A: the mobile fingerprint must be firefox or ios")
        if mihomo_yaml is not None:
            proxy = yaml.safe_load(mihomo_yaml)["proxies"][0]
            if "xhttp-opts" in proxy or "alpn" in proxy or proxy.get("network") == "xhttp":
                raise ValueError("profile B: xhttp/alpn fields are forbidden next to tcp+vision")

    def render(self, material: ClientMaterial, params: dict[str, Any], host: str, dns: tuple[str, str],
               description: str, options: dict[str, Any] | None = None) -> Rendered:
        self._validate(material, params)
        checks = _invariants(params, host)
        failed = [c["text"] for c in checks if c["status"] == "fail"]
        if failed:
            raise ValueError("Xray server invariants failed: " + "; ".join(failed))
        opts = self.effective_render_options(options)
        client_id = material.client_id
        stream = params.get("stream", {})
        network = stream.get("network", "tcp")
        mobile = network == "tcp" and stream.get("security") == "reality"
        flow = "" if network == "xhttp" else self._live_flow(material, params)
        client = (self._mobile_client(client_id, flow, params, host, opts) if mobile
                  else self._plain_client(client_id, flow, params, host))
        mihomo = self._mihomo_config(client_id, flow, params, host, description, opts) if mobile else None
        self._validate_artifacts(client, mihomo, mobile)
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
        json_name = download_filename(description, "json")
        # The JSON file is what the Qt client saves as "XRay native format" (ExportController::generateXrayConfig).
        files = [ExtraFile("xray_json", json_name, json.dumps(client, indent=4, ensure_ascii=False) + "\n")]
        yaml_name = download_filename(description, "yaml")
        if mihomo is not None:
            files.append(ExtraFile("mihomo_yaml", yaml_name, mihomo))
        link_name = download_filename(description, "txt")
        files.append(ExtraFile("invariants", download_filename(f"{description} invariants", "txt"),
                               _report_text(checks, params, host, description, json_name, yaml_name, link_name,
                                            mobile, options, opts)))
        return Rendered(encode_vpn_key(doc), self._vless_link(client_id, flow, params, host, description),
                        link_name, files)


register(XrayDriver())
