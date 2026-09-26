"""IKEv2 driver against a real amnezia-ipsec container: certificates are issued, and blocking lands in the CRL."""

import os

import pytest

from app.drivers.base import get_driver
from app.services.install import install_container
from app.ssh.conn import RemoteError, open_remote
from tests.integration.conftest import docker

pytestmark = pytest.mark.integration
IK = "amnezia-ipsec"


def crl_serials() -> str:
    return docker("exec", IK, "sh", "-c", 'crlutil -L -d sql:/etc/ipsec.d -n "IKEv2 VPN CA" 2>&1 || true',
                  check=False)


async def test_ikev2_certificates(sshhost):
    docker("rm", "-f", IK, check=False)
    try:
        async with open_remote(sshhost) as remote:
            try:
                await install_container(remote, IK, "127.0.0.1", ("1.1.1.1", "1.0.0.1"), host_setup=False,
                                        only_installable=False)
            except RemoteError as e:
                pytest.skip(f"IPsec container cannot run here: {e}")
            driver = get_driver(IK)
            params = await driver.read_params(remote)
            assert params["ca"], "CA certificate missing"
            m = await driver.create_material(remote, params, set())
            assert m.data["serial"] and m.data["private_key"]
            assert m.client_id in await driver.list_clients(remote)

            await driver.apply(remote, [], {m.client_id})
            assert m.data["serial"] in crl_serials() or hex(int(m.data["serial"]))[2:] in crl_serials().lower()

            await driver.apply(remote, [m], {m.client_id})
            listing = crl_serials()
            assert m.data["serial"] not in listing

            await driver.apply(remote, [], {m.client_id}, revoked={m.client_id})
            assert m.client_id not in await driver.list_clients(remote)
            out = driver.render(m, params, "203.0.113.10", ("1.1.1.1", "1.0.0.1"), "it")
            assert "<plist" in out.native
    finally:
        if not os.environ.get("PANEL_KEEP_CONTAINERS"):
            docker("rm", "-f", IK, check=False)
