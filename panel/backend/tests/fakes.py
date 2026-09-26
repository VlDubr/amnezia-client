"""In-memory stand-ins for a server reached over SSH."""

import re

from app.ssh.conn import RemoteError, RunResult


class FakeRemote:
    """Emulates the subset of Remote used by drivers: container files, `wg show dump`, `syncconf`."""

    def __init__(self, containers: list[str] | None = None):
        self.files: dict[tuple[str, str], str] = {}
        self.commands: list[tuple[str, str]] = []
        self.containers = containers or []
        self.dumps: dict[str, str] = {}  # container -> `wg show <iface> dump` output
        self.fail_with: str | None = None

    def _check(self):
        if self.fail_with:
            raise RemoteError(self.fail_with)

    async def run(self, cmd: str, input: str | None = None, check: bool = True, timeout: float = 120) -> RunResult:
        self._check()
        self.commands.append(("host", cmd))
        return RunResult("", "", 0)

    async def read_container_file(self, container: str, path: str) -> str:
        self._check()
        try:
            return self.files[(container, path)]
        except KeyError:
            raise RemoteError(f"cat: {path}: No such file") from None

    async def write_container_file(self, container: str, path: str, content: str) -> None:
        self._check()
        self.commands.append((container, f"write {path}"))
        self.files[(container, path)] = content

    async def container_exec(self, container: str, script: str, shell: str = "bash", check: bool = True) -> str:
        self._check()
        self.commands.append((container, script))
        if " show " in script and script.rstrip().endswith("dump"):
            return self.dumps.get(container, "")
        m = re.match(r"cat (\S+) 2>/dev/null \|\| true", script.strip())
        if m:
            return self.files.get((container, m.group(1)), "")
        return ""

    async def list_containers(self) -> list[str]:
        self._check()
        return list(self.containers)
