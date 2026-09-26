"""Protocol driver interface (spec §6). One driver per Amnezia container type."""

from dataclasses import dataclass, field
from typing import Any, Protocol

from app.ssh.conn import Remote


@dataclass
class ClientMaterial:
    """Everything needed to (re)create a client on the server and render its config."""
    client_id: str
    data: dict[str, Any]


@dataclass
class ClientInfo:
    """A client found on the server."""
    client_id: str
    name: str | None
    data: dict[str, Any]


@dataclass
class Counter:
    rx: int  # bytes received by the server from the client
    tx: int  # bytes sent by the server to the client
    session: str | None


@dataclass
class ApplyResult:
    added: set[str] = field(default_factory=set)
    removed: set[str] = field(default_factory=set)


@dataclass
class Rendered:
    vpn_key: str
    native: str
    native_filename: str


class Driver(Protocol):
    container: str
    title: str
    installable: bool  # the panel can install this container with client/server_scripts
    script_folder: str

    async def read_params(self, remote: Remote) -> dict[str, Any]: ...

    async def list_clients(self, remote: Remote) -> dict[str, ClientInfo]: ...

    async def create_material(self, remote: Remote, params: dict[str, Any], taken: set[str]) -> ClientMaterial:
        """`taken` holds resources reserved by configs the panel knows (for WireGuard: client IPs)."""
        ...

    async def apply(self, remote: Remote, desired: list[ClientMaterial], known_ids: set[str],
                    revoked: frozenset[str] | set[str] = frozenset()) -> ApplyResult:
        """Makes the server client set equal to desired + (clients unknown to the panel).

        Clients whose id is in known_ids but not in desired are removed; unknown clients are never touched.
        `revoked` (a subset of known_ids) are removed for good; the others may be removed reversibly (blocked).
        """
        ...

    def install_vars(self, port: str | None) -> dict[str, str]:
        """Protocol variables for a fresh install with the Qt client's scripts."""
        ...

    async def read_traffic(self, remote: Remote) -> dict[str, Counter]: ...

    def render(self, material: ClientMaterial, params: dict[str, Any], host: str, dns: tuple[str, str],
               description: str) -> Rendered: ...

    def reserved(self, material: ClientMaterial) -> str | None:
        """The resource this client occupies (see create_material's `taken`)."""
        ...


_REGISTRY: dict[str, Driver] = {}
_MODULES = ("wg", "xray", "openvpn")


def register(driver: Driver) -> Driver:
    _REGISTRY[driver.container] = driver
    return driver


def _load() -> None:
    import importlib

    for name in _MODULES:
        importlib.import_module(f"app.drivers.{name}")


def get_driver(container: str) -> Driver:
    _load()
    try:
        return _REGISTRY[container]
    except KeyError:
        raise KeyError(f"unsupported container: {container}") from None


def supported_containers() -> set[str]:
    _load()
    return set(_REGISTRY)


def installable_containers() -> set[str]:
    _load()
    return {name for name, driver in _REGISTRY.items() if driver.installable}
