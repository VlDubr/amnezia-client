"""Installs Amnezia protocol containers with the Qt client's scripts (InstallController::setupContainer)."""

import random
import shlex

from app.drivers.scripts import replace_vars, script
from app.drivers.wg import generate_keypair
from app.ssh.conn import Remote, RemoteError

INSTALLABLE = {
    "amnezia-awg2": {"folder": "awg", "default_port": "55424"},
    "amnezia-wireguard": {"folder": "wireguard", "default_port": "51820"},
}
SUBNET = "10.8.1.0"
SUBNET_CIDR = "24"


def default_install_vars(container: str, port: str | None = None) -> dict[str, str]:
    """Protocol variables for a fresh install, as AwgInstaller::generateAwgParameters / WireguardInstaller."""
    port = port or INSTALLABLE[container]["default_port"]
    if container == "amnezia-wireguard":
        return {"WIREGUARD_SUBNET_IP": SUBNET, "WIREGUARD_SUBNET_CIDR": SUBNET_CIDR, "WIREGUARD_SERVER_PORT": port}
    header_key, _ = generate_keypair()
    return {
        "AWG_SUBNET_IP": SUBNET, "WIREGUARD_SUBNET_CIDR": SUBNET_CIDR, "AWG_SERVER_PORT": port,
        "JUNK_PACKET_COUNT": str(random.randint(4, 6)), "JUNK_PACKET_MIN_SIZE": "10", "JUNK_PACKET_MAX_SIZE": "50",
        "INIT_PACKET_JUNK_SIZE": "12", "RESPONSE_PACKET_JUNK_SIZE": "12", "COOKIE_REPLY_PACKET_JUNK_SIZE": "12",
        "TRANSPORT_PACKET_JUNK_SIZE": "12",
        "INIT_PACKET_MAGIC_HEADER": "1", "RESPONSE_PACKET_MAGIC_HEADER": "2", "UNDERLOAD_PACKET_MAGIC_HEADER": "3",
        "TRANSPORT_PACKET_MAGIC_HEADER": "4",
        "SPECIAL_JUNK_1": "<r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737>",
        "SPECIAL_JUNK_2": "", "SPECIAL_JUNK_3": "", "SPECIAL_JUNK_4": "", "SPECIAL_JUNK_5": "",
        "HEADER_PROTECTION_KEY": header_key, "CONTENT_PADDING_ADDITION": "",
        "REKEY_AFTER_TIME": "100-120", "REKEY_TIMEOUT": "3-7", "REJECT_AFTER_TIME": "150-180",
        "KEEPALIVE_TIMEOUT": "5-15", "MAX_HANDSHAKE_ATTEMPTS": "15-20",
        "RANDOM_TRAILERS": "on", "DISABLE_COOKIES": "on", "PERSISTENT_KEEPALIVE": "25-35",
    }


def base_vars(container: str, host: str, dns: tuple[str, str]) -> dict[str, str]:
    return {
        "REMOTE_HOST": host,
        "CONTAINER_NAME": container,
        "DOCKERFILE_FOLDER": f"/opt/amnezia/{container}",
        "SERVER_IP_ADDRESS": host,
        "PRIMARY_SERVER_DNS": dns[0],
        "SECONDARY_SERVER_DNS": dns[1],
    }


async def install_container(remote: Remote, container: str, host: str, dns: tuple[str, str],
                            port: str | None = None, host_setup: bool = True) -> None:
    if container not in INSTALLABLE:
        raise ValueError(f"cannot install {container}")
    folder = INSTALLABLE[container]["folder"]
    variables = {**base_vars(container, host, dns), **default_install_vars(container, port)}

    def shared(name: str) -> str:
        return replace_vars(script(None, name), variables)

    if host_setup:
        await remote.run(shared("install_docker.sh"), timeout=900)
    await remote.run(shared("prepare_host.sh"))
    if host_setup:
        await remote.run(shared("setup_host_firewall.sh"), check=False)
    await remote.run(shared("remove_container.sh"), check=False)

    dockerfile_path = f"/opt/amnezia/{container}/Dockerfile"
    await remote.run(f"sudo tee {shlex.quote(dockerfile_path)} > /dev/null", input=script(folder, "Dockerfile"))
    build = await remote.run(shared("build_container.sh"), check=False, timeout=1800)
    if build.exit_status != 0:
        raise RemoteError(f"docker build failed: {(build.stdout + build.stderr)[-800:]}")
    await remote.run(replace_vars(script(folder, "run_container.sh"), variables))
    await remote.container_exec(container, replace_vars(script(folder, "configure_container.sh"), variables))
    await remote.write_container_file(container, "/opt/amnezia/start.sh",
                                      replace_vars(script(folder, "start.sh"), variables))
    await remote.run(f"sudo docker exec -d {container} sh -c "
                     f"\"chmod a+x /opt/amnezia/start.sh && /opt/amnezia/start.sh\"")
