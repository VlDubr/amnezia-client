"""Installs Amnezia protocol containers with the Qt client's scripts (InstallController::setupContainer)."""

import shlex

from app.drivers.base import get_driver, installable_containers
from app.drivers.scripts import replace_vars, script
from app.ssh.conn import Remote, RemoteError


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
                            port: str | None = None, host_setup: bool = True, only_installable: bool = True) -> None:
    if only_installable and container not in installable_containers():
        raise ValueError(f"cannot install {container}")
    driver = get_driver(container)
    folder = driver.script_folder
    variables = {**base_vars(container, host, dns), **driver.install_vars(port)}

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
    await remote.container_exec(container, replace_vars(script(folder, "configure_container.sh"), variables),
                                shell=getattr(driver, "shell", "bash"))
    await remote.write_container_file(container, "/opt/amnezia/start.sh",
                                      replace_vars(script(folder, "start.sh"), variables))
    before_start = getattr(driver, "before_start", None)
    if before_start is not None:  # e.g. Xray: the Qt client writes server.json itself before starting
        await before_start(remote, variables)
    await remote.run(f"sudo docker exec -d {container} sh -c "
                     f"\"chmod a+x /opt/amnezia/start.sh && /opt/amnezia/start.sh\"")
