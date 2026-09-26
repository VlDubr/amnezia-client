"""OpenVPN driver. Mirrors OpenVpnConfigurator / UsersController::revokeOpenVpn in the Qt client.

Clients are certificates signed by the container's easy-rsa CA. Blocking is reversible (a `disable` file in the
client-config-dir plus a management `kill`); deletion revokes the certificate into the CRL.
"""

import json
import re
import secrets
import shlex
from typing import Any

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID

from app.drivers.base import ApplyResult, ClientInfo, ClientMaterial, Counter, Rendered, register
from app.drivers.scripts import replace_vars, script
from app.render.vpnkey import encode_vpn_key
from app.ssh.conn import Remote

DATA_DIR = "/opt/amnezia/openvpn"
SERVER_CONF = f"{DATA_DIR}/server.conf"
CCD_DIR = f"{DATA_DIR}/ccd"
MANAGEMENT = ("127.0.0.1", 7505)
SERVER_CN = "AmneziaReq"
PREPARE_LINES = (f"client-config-dir {CCD_DIR}", f"management {MANAGEMENT[0]} {MANAGEMENT[1]}")
_CN = re.compile(r"CN=([^/]+)")


def _conf_value(conf: str, key: str) -> str | None:
    for line in conf.splitlines():
        parts = line.split()
        if parts and parts[0] == key:
            return " ".join(parts[1:])
    return None


def _make_request(client_id: str) -> tuple[str, str]:
    """RSA-2048 key and CSR with the same subject as OpenVpnConfigurator::createCertRequest."""
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    subject = x509.Name([x509.NameAttribute(NameOID.COUNTRY_NAME, "OR"),
                         x509.NameAttribute(NameOID.COMMON_NAME, client_id)])
    csr = x509.CertificateSigningRequestBuilder().subject_name(subject).sign(key, hashes.SHA256())
    key_pem = key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                serialization.NoEncryption()).decode()
    return key_pem, csr.public_bytes(serialization.Encoding.PEM).decode()


class OpenVpnDriver:
    container = "amnezia-openvpn"
    title = "OpenVPN"
    installable = True
    script_folder = "openvpn"
    default_port = "1194"

    def install_vars(self, port: str | None) -> dict[str, str]:
        return {
            "OPENVPN_PORT": port or self.default_port, "OPENVPN_TRANSPORT_PROTO": "udp",
            "OPENVPN_SUBNET_IP": "10.8.0.0", "OPENVPN_SUBNET_MASK": "255.255.255.0", "OPENVPN_SUBNET_CIDR": "24",
            "OPENVPN_NCP_DISABLE": "", "OPENVPN_CIPHER": "AES-256-GCM", "OPENVPN_HASH": "SHA512",
            "OPENVPN_TLS_AUTH": f"tls-auth {DATA_DIR}/ta.key 0", "OPENVPN_ADDITIONAL_SERVER_CONFIG": "",
        }

    async def _exec(self, remote: Remote, command: str) -> str:
        return await remote.container_exec(self.container, f"cd {DATA_DIR} && {command}")

    async def read_params(self, remote: Remote) -> dict[str, Any]:
        conf = await remote.read_container_file(self.container, SERVER_CONF)
        tls_auth = _conf_value(conf, "tls-auth") is not None
        return {
            "port": _conf_value(conf, "port") or self.default_port,
            "proto": (_conf_value(conf, "proto") or "udp").replace("-server", ""),
            "cipher": _conf_value(conf, "cipher") or "AES-256-GCM",
            "auth": _conf_value(conf, "auth") or "SHA512",
            "ncp_disable": _conf_value(conf, "ncp-disable") is not None,
            "ca": (await remote.read_container_file(self.container, f"{DATA_DIR}/ca.crt")).strip(),
            "ta": (await remote.read_container_file(self.container, f"{DATA_DIR}/ta.key")).strip() if tls_auth else "",
        }

    async def _index(self, remote: Remote) -> dict[str, str]:
        """CN -> status ("V" valid, "R" revoked) from easy-rsa's database."""
        text = await remote.read_container_file(self.container, f"{DATA_DIR}/pki/index.txt")
        out: dict[str, str] = {}
        for line in text.splitlines():
            fields = line.split("\t")
            m = _CN.search(fields[-1]) if fields else None
            if m and m.group(1) != SERVER_CN:
                if out.get(m.group(1)) != "V":  # a re-issued CN stays valid
                    out[m.group(1)] = fields[0]
        return out

    async def list_clients(self, remote: Remote) -> dict[str, ClientInfo]:
        return {cn: ClientInfo(cn, None, {}) for cn, status in (await self._index(remote)).items() if status == "V"}

    async def create_material(self, remote: Remote, params: dict[str, Any], taken: set[str]) -> ClientMaterial:
        client_id = secrets.token_hex(16)
        key_pem, req_pem = _make_request(client_id)
        await remote.write_container_file(self.container, f"{DATA_DIR}/clients/{client_id}.req", req_pem)
        await self._exec(remote, f"easyrsa import-req {DATA_DIR}/clients/{client_id}.req {client_id}")
        await self._exec(remote, f"export EASYRSA_BATCH=1; easyrsa sign-req client {client_id}")
        cert = await remote.read_container_file(self.container, f"{DATA_DIR}/pki/issued/{client_id}.crt")
        return ClientMaterial(client_id, {"private_key": key_pem, "cert": cert.strip()})

    def reserved(self, material: ClientMaterial) -> str | None:
        return None

    async def _prepare(self, remote: Remote) -> None:
        conf = await remote.read_container_file(self.container, SERVER_CONF)
        missing = [line for line in PREPARE_LINES if line not in conf]
        if missing:
            await self._exec(remote, f"mkdir -p {CCD_DIR} && chmod 755 {CCD_DIR}")
            await remote.write_container_file(self.container, SERVER_CONF,
                                              conf.rstrip("\n") + "\n" + "\n".join(missing) + "\n")
            await remote.run(f"sudo docker restart {self.container}")

    async def _disabled(self, remote: Remote) -> set[str]:
        out = await remote.container_exec(self.container, f"grep -l '^disable' {CCD_DIR}/* 2>/dev/null || true")
        return {line.rsplit("/", 1)[1] for line in out.split() if "/" in line}

    async def _kill(self, remote: Remote, cn: str) -> None:
        cmd = f"(printf 'kill {cn}\\nquit\\n'; sleep 1) | nc {MANAGEMENT[0]} {MANAGEMENT[1]} >/dev/null 2>&1 || true"
        await remote.container_exec(self.container, cmd)

    async def apply(self, remote: Remote, desired: list[ClientMaterial], known_ids: set[str],
                    revoked: frozenset[str] | set[str] = frozenset()) -> ApplyResult:
        await self._prepare(remote)
        index = await self._index(remote)
        disabled = await self._disabled(remote)
        wanted = {m.client_id for m in desired}
        result = ApplyResult()
        for cn, status in index.items():
            if status == "R" and cn in revoked and cn in known_ids:
                # A revoke whose CRL step failed earlier: regenerate the CRL so the certificate is really refused.
                await self._exec(remote, f"export EASYRSA_BATCH=1; easyrsa gen-crl && cp pki/crl.pem {DATA_DIR}/crl.pem "
                                         f"&& chmod 644 {DATA_DIR}/crl.pem")
                result.removed.add(cn)
                continue
            if status != "V" or cn not in known_ids:
                continue
            q = shlex.quote(cn)
            if cn in wanted:
                if cn in disabled:
                    await remote.container_exec(self.container, f"rm -f {CCD_DIR}/{q}")
                    result.added.add(cn)
            elif cn in revoked:
                await self._exec(remote, f"export EASYRSA_BATCH=1; easyrsa revoke {q} && easyrsa gen-crl && "
                                         f"cp pki/crl.pem {DATA_DIR}/crl.pem && chmod 644 {DATA_DIR}/crl.pem && "
                                         f"rm -f {CCD_DIR}/{q}")
                await self._kill(remote, cn)
                result.removed.add(cn)
            elif cn not in disabled:
                await remote.write_container_file(self.container, f"{CCD_DIR}/{cn}", "disable\n")
                await self._kill(remote, cn)
                result.removed.add(cn)
        return result

    async def read_traffic(self, remote: Remote) -> dict[str, Counter]:
        out = await remote.container_exec(
            self.container, f"cat /openvpn-status.log 2>/dev/null || cat {DATA_DIR}/openvpn-status.log 2>/dev/null "
                            "|| true")
        counters: dict[str, Counter] = {}
        sessions: dict[str, list[str]] = {}
        in_list = False
        for line in out.splitlines():
            if line.startswith("Common Name,Real Address"):
                in_list = True
                continue
            if line.startswith("ROUTING TABLE"):
                break
            if in_list:
                fields = line.split(",")
                if len(fields) >= 5 and fields[2].isdigit() and fields[3].isdigit():
                    # duplicate-cn: several devices may share one config; sum them, and key the session on
                    # the set of connections so that row order does not matter.
                    sessions.setdefault(fields[0], []).append(f"{fields[1]}@{fields[4]}")
                    c = counters.setdefault(fields[0], Counter(0, 0, None))
                    c.rx += int(fields[2])
                    c.tx += int(fields[3])
        for cn, parts in sessions.items():
            counters[cn].session = "|".join(sorted(parts))
        return counters

    def render(self, material: ClientMaterial, params: dict[str, Any], host: str, dns: tuple[str, str],
               description: str) -> Rendered:
        variables = {
            "OPENVPN_TRANSPORT_PROTO": params["proto"], "OPENVPN_NCP_DISABLE": "ncp-disable" if params["ncp_disable"]
            else "", "OPENVPN_CIPHER": params["cipher"], "OPENVPN_HASH": params["auth"],
            "PRIMARY_DNS": dns[0], "SECONDARY_DNS": dns[1], "REMOTE_HOST": host, "OPENVPN_PORT": params["port"],
            "OPENVPN_ADDITIONAL_CLIENT_CONFIG": "", "OPENVPN_CA_CERT": params["ca"],
            "OPENVPN_CLIENT_CERT": material.data["cert"], "OPENVPN_PRIV_KEY": material.data["private_key"],
            "OPENVPN_TA_KEY": params["ta"],
        }
        ovpn = replace_vars(script(self.script_folder, "template.ovpn"), variables, keep_unknown=False)
        if not params["ta"]:
            ovpn = ovpn.replace("<tls-auth>\n\n</tls-auth>", "").replace("key-direction 1\n", "")
        ovpn = ovpn.replace("block-outside-dns\n", "")  # Windows-only option; the Qt client drops it too
        client = {"config": ovpn, "clientId": material.client_id, "block_outside_dns": False}
        doc = {
            "containers": [{"container": self.container, "openvpn": {
                "port": params["port"], "transport_proto": params["proto"],
                "last_config": json.dumps(client, separators=(",", ":")),
            }}],
            "defaultContainer": self.container,
            "description": description,
            "dns1": dns[0],
            "dns2": dns[1],
            "hostName": host,
        }
        safe = re.sub(r"[^\w.-]+", "_", description).strip("_") or "amnezia"
        return Rendered(encode_vpn_key(doc), ovpn, f"{safe}.ovpn")


register(OpenVpnDriver())


