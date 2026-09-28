"""WireGuard family driver: AmneziaWG (amnezia-awg2), legacy AmneziaWG (amnezia-awg) and WireGuard.

Mirrors WireguardConfigurator / AwgInstaller / UsersController in the Qt client.
"""

import base64
import ipaddress
import json
import random
import re
from typing import Any

from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat, PublicFormat

from app.drivers import wgconf
from app.drivers.base import ApplyResult, ClientInfo, ClientMaterial, Counter, Rendered, register
from app.drivers.scripts import drop_empty_value_lines, replace_vars, script
from app.render.vpnkey import encode_vpn_key
from app.ssh.conn import Remote

MTU = "1280"
ALLOWED_IPS = ["0.0.0.0/0", "::/0"]

# AWG server config keys -> variables used by client/server_scripts/*/template.conf
AWG_TEMPLATE_VARS = {
    "Jc": "JUNK_PACKET_COUNT", "Jmin": "JUNK_PACKET_MIN_SIZE", "Jmax": "JUNK_PACKET_MAX_SIZE",
    "S1": "INIT_PACKET_JUNK_SIZE", "S2": "RESPONSE_PACKET_JUNK_SIZE", "S3": "COOKIE_REPLY_PACKET_JUNK_SIZE",
    "S4": "TRANSPORT_PACKET_JUNK_SIZE", "H1": "INIT_PACKET_MAGIC_HEADER", "H2": "RESPONSE_PACKET_MAGIC_HEADER",
    "H3": "UNDERLOAD_PACKET_MAGIC_HEADER", "H4": "TRANSPORT_PACKET_MAGIC_HEADER",
    "I1": "SPECIAL_JUNK_1", "I2": "SPECIAL_JUNK_2", "I3": "SPECIAL_JUNK_3", "I4": "SPECIAL_JUNK_4",
    "I5": "SPECIAL_JUNK_5", "HeaderProtectionKey": "HEADER_PROTECTION_KEY",
    "ContentPaddingAddition": "CONTENT_PADDING_ADDITION", "RekeyAfterTime": "REKEY_AFTER_TIME",
    "RekeyTimeout": "REKEY_TIMEOUT", "RejectAfterTime": "REJECT_AFTER_TIME", "KeepaliveTimeout": "KEEPALIVE_TIMEOUT",
    "MaxHandshakeAttempts": "MAX_HANDSHAKE_ATTEMPTS", "RandomTrailers": "RANDOM_TRAILERS",
    "DisableCookies": "DISABLE_COOKIES",
}
_AWG3_KEYS = ("HeaderProtectionKey", "ContentPaddingAddition", "RekeyAfterTime", "RekeyTimeout", "RejectAfterTime",
              "KeepaliveTimeout", "MaxHandshakeAttempts")


def awg_protocol_version(awg: dict[str, str]) -> str:
    """Port of awgVersionOf() in awgProtocolConfig.cpp."""
    if any(awg.get(k) for k in _AWG3_KEYS) or "on" in (awg.get("RandomTrailers"), awg.get("DisableCookies")):
        return "3.1"
    if awg.get("S3") or awg.get("S4") or any("-" in awg.get(h, "") for h in ("H1", "H2", "H3", "H4")):
        return "2"
    if any(awg.get(f"I{i}") for i in range(1, 6)):
        return "1.5"
    return ""


def generate_keypair() -> tuple[str, str]:
    key = X25519PrivateKey.generate()
    private = key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption())
    public = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return base64.b64encode(private).decode(), base64.b64encode(public).decode()


def _safe_filename(name: str) -> str:
    return re.sub(r"[^\w.-]+", "_", name, flags=re.UNICODE).strip("_") or "amnezia"


SUBNET = "10.8.1.0"
SUBNET_CIDR = "24"
SPECIAL_JUNK_1 = "<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>"


class WgFamilyDriver:
    traffic_counters = True
    def __init__(self, container: str, title: str, bin_: str, iface: str, data_dir: str, conf_name: str,
                 script_folder: str, proto_key: str, is_awg: bool, default_port: str, installable: bool):
        self.container = container
        self.title = title
        self.bin = bin_
        self.iface = iface
        self.data_dir = data_dir
        self.conf_path = f"{data_dir}/{conf_name}"
        self.script_folder = script_folder
        self.proto_key = proto_key
        self.is_awg = is_awg
        self.default_port = default_port
        self.installable = installable

    def install_vars(self, port: str | None) -> dict[str, str]:
        """As AwgInstaller::generateAwgParameters / WireguardInstaller in the Qt client."""
        port = port or self.default_port
        if not self.is_awg:
            return {"WIREGUARD_SUBNET_IP": SUBNET, "WIREGUARD_SUBNET_CIDR": SUBNET_CIDR, "WIREGUARD_SERVER_PORT": port}
        header_key, _ = generate_keypair()
        return {
            "AWG_SUBNET_IP": SUBNET, "WIREGUARD_SUBNET_CIDR": SUBNET_CIDR, "AWG_SERVER_PORT": port,
            "JUNK_PACKET_COUNT": str(random.randint(4, 6)), "JUNK_PACKET_MIN_SIZE": "10", "JUNK_PACKET_MAX_SIZE": "50",
            "INIT_PACKET_JUNK_SIZE": "12", "RESPONSE_PACKET_JUNK_SIZE": "12", "COOKIE_REPLY_PACKET_JUNK_SIZE": "12",
            "TRANSPORT_PACKET_JUNK_SIZE": "12",
            "INIT_PACKET_MAGIC_HEADER": "1", "RESPONSE_PACKET_MAGIC_HEADER": "2", "UNDERLOAD_PACKET_MAGIC_HEADER": "3",
            "TRANSPORT_PACKET_MAGIC_HEADER": "4",
            "SPECIAL_JUNK_1": SPECIAL_JUNK_1, "SPECIAL_JUNK_2": "", "SPECIAL_JUNK_3": "", "SPECIAL_JUNK_4": "",
            "SPECIAL_JUNK_5": "",
            "HEADER_PROTECTION_KEY": header_key, "CONTENT_PADDING_ADDITION": "",
            "REKEY_AFTER_TIME": "100-120", "REKEY_TIMEOUT": "3-7", "REJECT_AFTER_TIME": "150-180",
            "KEEPALIVE_TIMEOUT": "5-15", "MAX_HANDSHAKE_ATTEMPTS": "15-20",
            "RANDOM_TRAILERS": "on", "DISABLE_COOKIES": "on", "PERSISTENT_KEEPALIVE": "25-35",
        }

    # --- server state -----------------------------------------------------

    async def _read_conf(self, remote: Remote) -> wgconf.WgConf:
        return wgconf.parse(await remote.read_container_file(self.container, self.conf_path))

    async def _read_key(self, remote: Remote, name: str) -> str:
        return (await remote.read_container_file(self.container, f"{self.data_dir}/{name}")).strip()

    async def read_params(self, remote: Remote) -> dict[str, Any]:
        conf = await self._read_conf(remote)
        values = conf.interface_values()
        address, _, cidr = values.get("Address", "10.8.1.0/24").partition("/")
        params: dict[str, Any] = {
            "port": values.get("ListenPort") or self.default_port,
            "subnet_address": address,
            "subnet_cidr": cidr or "24",
            "server_public_key": await self._read_key(remote, "wireguard_server_public_key.key"),
            "psk": await self._read_key(remote, "wireguard_psk.key"),
        }
        if self.is_awg:
            awg = {k: values[k] for k in AWG_TEMPLATE_VARS if values.get(k)}
            params["awg"] = awg
            params["protocol_version"] = awg_protocol_version(awg)
        return params

    async def _client_names(self, remote: Remote) -> dict[str, str]:
        raw = await remote.container_exec(self.container, f"cat {self.data_dir}/clientsTable 2>/dev/null || true")
        try:
            table = json.loads(raw) if raw.strip() else []
        except ValueError:
            return {}
        names: dict[str, str] = {}
        if isinstance(table, list):
            for item in table:
                if isinstance(item, dict) and item.get("clientId"):
                    name = (item.get("userData") or {}).get("clientName")
                    if name:
                        names[item["clientId"]] = name
        return names

    async def list_clients(self, remote: Remote) -> dict[str, ClientInfo]:
        conf = await self._read_conf(remote)
        names = await self._client_names(remote)
        return {
            p.public_key: ClientInfo(p.public_key, names.get(p.public_key), {"ip": p.ip, "psk": p.psk})
            for p in conf.peers if p.public_key
        }

    async def create_material(self, remote: Remote, params: dict[str, Any], taken: set[str]) -> ClientMaterial:
        conf = await self._read_conf(remote)
        used = set(taken) | {p.ip for p in conf.peers if p.ip} | {params["subnet_address"]}
        network = ipaddress.ip_network(f"{params['subnet_address']}/{params['subnet_cidr']}", strict=False)
        ip = next((str(h) for h in network.hosts() if str(h) not in used), None)
        if ip is None:
            raise RuntimeError(f"no free addresses left in {network}")
        private, public = generate_keypair()
        return ClientMaterial(public, {"private_key": private, "public_key": public, "psk": params["psk"], "ip": ip})

    def reserved(self, material: ClientMaterial) -> str | None:
        return material.data.get("ip")

    async def apply(self, remote: Remote, desired: list[ClientMaterial], known_ids: set[str],
                    revoked: frozenset[str] | set[str] = frozenset()) -> ApplyResult:
        # Removing a peer is reversible and final at the same time for WireGuard: revoked needs nothing extra.
        conf = await self._read_conf(remote)
        wanted = {m.client_id: m for m in desired}
        result = ApplyResult()
        kept: list[wgconf.Peer] = []
        present: set[str] = set()
        for peer in conf.peers:
            key = peer.public_key
            if key in wanted or key not in known_ids:
                kept.append(peer)
                present.add(key)
            else:
                result.removed.add(key)
        for key, material in wanted.items():
            if key not in present and material.data.get("ip"):
                kept.append(wgconf.Peer.new(key, material.data.get("psk"), material.data["ip"]))
                result.added.add(key)
        if result.added or result.removed:
            conf.peers = kept
            await remote.write_container_file(self.container, self.conf_path, conf.dump())
            await remote.container_exec(
                self.container, f"{self.bin} syncconf {self.iface} <({self.bin}-quick strip {self.conf_path})")
        return result

    async def read_traffic(self, remote: Remote) -> dict[str, Counter]:
        out = await remote.container_exec(self.container, f"{self.bin} show {self.iface} dump")
        counters: dict[str, Counter] = {}
        for line in out.splitlines()[1:]:
            fields = line.split("\t")
            if len(fields) >= 7 and fields[5].isdigit() and fields[6].isdigit():
                counters[fields[0]] = Counter(int(fields[5]), int(fields[6]), None)
        return counters

    # --- client config ------------------------------------------------------

    def _keepalive(self, params: dict[str, Any]) -> str:
        return "25-35" if self.is_awg and params.get("protocol_version") == "3.1" else "25"

    def render(self, material: ClientMaterial, params: dict[str, Any], host: str, dns: tuple[str, str],
               description: str) -> Rendered:
        d = material.data
        awg = params.get("awg", {}) if self.is_awg else {}
        variables = {
            "WIREGUARD_CLIENT_IP": d["ip"],
            "PRIMARY_DNS": dns[0],
            "SECONDARY_DNS": dns[1],
            "WIREGUARD_CLIENT_PRIVATE_KEY": d["private_key"],
            "WIREGUARD_SERVER_PUBLIC_KEY": params["server_public_key"],
            "WIREGUARD_PSK": d.get("psk") or params["psk"],
            "SERVER_IP_ADDRESS": host,
            "AWG_SERVER_PORT": params["port"],
            "WIREGUARD_SERVER_PORT": params["port"],
            "PERSISTENT_KEEPALIVE": self._keepalive(params),
        }
        variables.update({var: awg.get(key, "") for key, var in AWG_TEMPLATE_VARS.items()})
        native = drop_empty_value_lines(
            replace_vars(script(self.script_folder, "template.conf"), variables, keep_unknown=False))

        client = {
            "config": native,
            "hostName": host,
            "port": int(params["port"]),
            "client_ip": d["ip"],
            "client_priv_key": d["private_key"],
            "client_pub_key": d["public_key"],
            "server_pub_key": params["server_public_key"],
            "psk_key": d.get("psk") or params["psk"],
            "clientId": material.client_id,
            "allowed_ips": ALLOWED_IPS,
            "persistent_keep_alive": self._keepalive(params),
            "mtu": MTU,
            **awg,
        }
        proto: dict[str, Any] = {
            "port": params["port"],
            "transport_proto": "udp",
            "subnet_address": params["subnet_address"],
            "subnet_cidr": params["subnet_cidr"],
        }
        if self.is_awg:
            if params.get("protocol_version"):
                proto["protocol_version"] = params["protocol_version"]
            proto.update(awg)
        proto["last_config"] = json.dumps(client, ensure_ascii=False, separators=(",", ":"))
        doc = {
            "containers": [{"container": self.container, self.proto_key: proto}],
            "defaultContainer": self.container,
            "description": description,
            "dns1": dns[0],
            "dns2": dns[1],
            "hostName": host,
        }
        return Rendered(encode_vpn_key(doc), native, f"{_safe_filename(description)}.conf")


register(WgFamilyDriver("amnezia-awg2", "AmneziaWG", "awg", "awg0", "/opt/amnezia/awg", "awg0.conf", "awg", "awg",
                        True, "55424", installable=True))
register(WgFamilyDriver("amnezia-awg", "AmneziaWG (legacy)", "wg", "wg0", "/opt/amnezia/awg", "wg0.conf",
                        "awg_legacy", "awg", True, "55424", installable=False))
register(WgFamilyDriver("amnezia-wireguard", "WireGuard", "wg", "wg0", "/opt/amnezia/wireguard", "wg0.conf",
                        "wireguard", "wireguard", False, "51820", installable=True))
