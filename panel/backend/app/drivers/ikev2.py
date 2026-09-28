"""IKEv2 (libreswan) driver. Mirrors Ikev2Configurator: client certificates are issued by the container's
"IKEv2 VPN CA" in the NSS database and exported as /opt/amnezia/ikev2/clients/<id>.p12.

Blocking puts the certificate's serial into a CRL signed by the same CA (rebuilt from the panel's list in
/opt/amnezia/ikev2/panel-revoked); unblocking rebuilds the CRL without it. Deleting keeps the serial in the CRL
and removes the certificate.
"""

import json
import re
import secrets
import shlex
import string
import uuid
from datetime import UTC, datetime
from typing import Any

from app.drivers.base import ApplyResult, ClientInfo, ClientMaterial, Counter, Rendered, register
from app.drivers.scripts import script
from app.render.vpnkey import encode_vpn_key
from app.ssh.conn import Remote, RemoteError

NSS = "sql:/etc/ipsec.d"
CA = "IKEv2 VPN CA"
CLIENTS = "/opt/amnezia/ikev2/clients"
REVOKED = "/opt/amnezia/ikev2/panel-revoked"
_SERIAL = re.compile(r"Serial Number:\s*(\d+)")
_ALPHABET = string.ascii_lowercase + string.digits


class Ikev2Driver:
    container = "amnezia-ipsec"
    title = "IKEv2"
    installable = False  # fixed ports 500/4500 and kernel IPsec support: installed from the Qt app
    traffic_counters = False
    script_folder = "ipsec"
    default_port = "500"

    def install_vars(self, port: str | None) -> dict[str, str]:
        # Constants from amnezia::genBaseVars in the Qt client.
        return {
            "IPSEC_VPN_L2TP_NET": "192.168.42.0/24", "IPSEC_VPN_L2TP_POOL": "192.168.42.10-192.168.42.250",
            "IPSEC_VPN_L2TP_LOCAL": "192.168.42.1", "IPSEC_VPN_XAUTH_NET": "192.168.43.0/24",
            "IPSEC_VPN_XAUTH_POOL": "192.168.43.10-192.168.43.250", "IPSEC_VPN_SHA2_TRUNCBUG": "yes",
            "IPSEC_VPN_VPN_ANDROID_MTU_FIX": "yes", "IPSEC_VPN_DISABLE_IKEV2": "no", "IPSEC_VPN_DISABLE_L2TP": "no",
            "IPSEC_VPN_DISABLE_XAUTH": "no", "IPSEC_VPN_C2C_TRAFFIC": "no",
        }

    async def read_params(self, remote: Remote) -> dict[str, Any]:
        ca = await remote.container_exec(self.container, "tr -d '\\n' < /etc/ipsec.d/ca_cert_base64.p12")
        return {"port": self.default_port, "ca": ca.strip()}

    async def list_clients(self, remote: Remote) -> dict[str, ClientInfo]:
        out = await remote.container_exec(self.container, f"ls {CLIENTS} 2>/dev/null || true")
        names = [line[:-4] for line in out.splitlines() if line.endswith(".p12")]
        return {n: ClientInfo(n, None, {}) for n in names}

    async def _serial(self, remote: Remote, client_id: str) -> str | None:
        out = await remote.container_exec(
            self.container, f'certutil -L -d {NSS} -n {shlex.quote(client_id)} | grep "Serial Number" || true')
        m = _SERIAL.search(out)
        return m.group(1) if m else None

    async def create_material(self, remote: Remote, params: dict[str, Any], taken: set[str]) -> ClientMaterial:
        client_id = "".join(secrets.choice(_ALPHABET) for _ in range(16))
        await remote.container_exec(
            self.container,
            f'certutil -z <(head -c 1024 /dev/urandom) -S -c "{CA}" -n "{client_id}" '
            f'-s "O=IKEv2 VPN,CN={client_id}" -k rsa -g 3072 -v 120 -d {NSS} -t ",," '
            f'--keyUsage digitalSignature,keyEncipherment --extKeyUsage serverAuth,clientAuth -8 "{client_id}"')
        p12 = f"{CLIENTS}/{client_id}.p12"
        await remote.container_exec(self.container, f'mkdir -p {CLIENTS} && pk12util -W "" -d {NSS} '
                                                    f'-n "{client_id}" -o "{p12}"')
        p12_b64 = (await remote.container_exec(self.container, f"base64 -w0 {p12}")).strip()
        return ClientMaterial(client_id, {"private_key": p12_b64, "serial": await self._serial(remote, client_id)})

    def reserved(self, material: ClientMaterial) -> str | None:
        return None

    async def _revoked(self, remote: Remote) -> dict[str, str]:
        try:
            text = await remote.read_container_file(self.container, REVOKED)
        except RemoteError:
            return {}
        return dict(line.split() for line in text.splitlines() if len(line.split()) == 2)

    async def _rebuild_crl(self, remote: Remote, revoked: dict[str, str]) -> None:
        now = datetime.now(UTC).strftime("%Y%m%d%H%M%SZ")
        body = "\\n".join([f"update={now}"] + [f"addcert {serial} {now}" for serial in revoked.values()])
        await remote.container_exec(
            self.container,
            f'crlutil -D -d {NSS} -n "{CA}" >/dev/null 2>&1; printf "{body}\\n" > /tmp/panel-crl && '
            f'crlutil -G -d {NSS} -n "{CA}" -c /tmp/panel-crl >/dev/null && rm -f /tmp/panel-crl; '
            "ipsec whack --rereadcrls >/dev/null 2>&1 || ipsec crls >/dev/null 2>&1 || true")

    async def apply(self, remote: Remote, desired: list[ClientMaterial], known_ids: set[str],
                    revoked: frozenset[str] | set[str] = frozenset()) -> ApplyResult:
        present = await self.list_clients(remote)
        crl = await self._revoked(remote)
        before = dict(crl)
        wanted = {m.client_id: m for m in desired}
        result = ApplyResult()
        for client_id in present:
            if client_id not in known_ids:
                continue
            if client_id in wanted:
                if crl.pop(client_id, None) is not None:
                    result.added.add(client_id)
                continue
            if client_id not in crl:
                serial = await self._serial(remote, client_id)
                if serial:
                    crl[client_id] = serial
                result.removed.add(client_id)
            if client_id in revoked:
                q = shlex.quote(client_id)
                await remote.container_exec(self.container, f"certutil -D -d {NSS} -n {q} || true")
                await remote.container_exec(self.container, f"rm -f {shlex.quote(f'{CLIENTS}/{client_id}.p12')}")
                result.removed.add(client_id)
        if crl != before:
            await remote.write_container_file(self.container, REVOKED,
                                              "".join(f"{k} {v}\n" for k, v in crl.items()))
            await self._rebuild_crl(remote, crl)
        return result

    async def read_traffic(self, remote: Remote) -> dict[str, Counter]:
        return {}  # libreswan traffic is per SA, not per certificate name in a stable form

    def render(self, material: ClientMaterial, params: dict[str, Any], host: str, dns: tuple[str, str],
               description: str) -> Rendered:
        p12 = material.data["private_key"]
        client = {"hostName": host, "userName": material.client_id, "cert": p12, "password": ""}
        doc = {
            "containers": [{"container": self.container, "ikev2": {
                "last_config": json.dumps(client, separators=(",", ":")),
            }}],
            "defaultContainer": self.container,
            "description": description,
            "dns1": dns[0],
            "dns2": dns[1],
            "hostName": host,
        }
        mobile = script(self.script_folder, "mobileconfig.plist")
        mobile = (mobile.replace("$CLIENT_NAME", material.client_id).replace("$UUID1", str(uuid.uuid4()))
                  .replace("$SERVER_ADDR", host).replace("$P12_BASE64", p12).replace("$CA_BASE64", params["ca"]))
        while "$(UUID_GEN)" in mobile:
            mobile = mobile.replace("$(UUID_GEN)", str(uuid.uuid4()), 1)
        safe = re.sub(r"[^\w.-]+", "_", description).strip("_") or "amnezia"
        return Rendered(encode_vpn_key(doc), mobile, f"{safe}.mobileconfig")


register(Ikev2Driver())
