"""Integration fixtures: an SSH "server" container that drives the local Docker daemon.

Containers started through it (Amnezia protocol containers) run on the same Docker daemon as the tests.
"""

import subprocess
import time
import uuid
from pathlib import Path

import pytest

from app.ssh.conn import SshTarget

DOCKER_DIR = Path(__file__).resolve().parents[1] / "docker"
SSHHOST_IMAGE = "amnezia-panel-test-sshhost"
SSH_PASSWORD = "panel-test-password"


def docker(*args: str, check: bool = True) -> str:
    r = subprocess.run(["docker", *args], check=False, capture_output=True, text=True, encoding="utf-8",
                       errors="replace")
    if check and r.returncode != 0:
        raise RuntimeError(f"docker {' '.join(args)} failed: {r.stderr.strip()[-1500:]}")
    return r.stdout.strip()


@pytest.fixture(scope="session")
def sshhost():
    docker("build", "-q", "-t", SSHHOST_IMAGE, str(DOCKER_DIR / "sshhost"))
    name = f"panel-sshhost-{uuid.uuid4().hex[:8]}"
    docker("run", "-d", "--rm", "--name", name, "-p", "127.0.0.1::22",
           "-v", "/var/run/docker.sock:/var/run/docker.sock", SSHHOST_IMAGE)
    try:
        port = int(docker("port", name, "22").splitlines()[0].rsplit(":", 1)[1])
        deadline = time.time() + 30
        while "Server listening" not in subprocess.run(["docker", "logs", name], capture_output=True,
                                                          text=True).stderr:
            if time.time() > deadline:
                raise RuntimeError("sshd did not start")
            time.sleep(0.3)
        yield SshTarget(host="127.0.0.1", port=port, user="panel", password=SSH_PASSWORD, private_key=None,
                        host_key=None)
    finally:
        docker("rm", "-f", name, check=False)


@pytest.fixture
def scratch_container():
    name = f"panel-scratch-{uuid.uuid4().hex[:8]}"
    docker("run", "-d", "--rm", "--name", name, "alpine:3.20", "sleep", "600")
    yield name
    docker("rm", "-f", name, check=False)
