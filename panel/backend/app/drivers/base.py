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

    async def read_params(self, remote: Remote) -> dict[str, Any]: ...

    async def list_clients(self, remote: Remote) -> dict[str, ClientInfo]: ...

    async def create_material(self, remote: Remote, params: dict[str, Any], taken: set[str]) -> ClientMaterial:
        """`taken` holds resources reserved by configs the panel knows (for WireGuard: client IPs)."""
        ...

    async def apply(self, remote: Remote, desired: list[ClientMaterial], known_ids: set[str]) -> ApplyResult:
        """Makes the server client set equal to desired + (clients unknown to the panel).

        Clients whose id is in known_ids but not in desired are removed; unknown clients are never touched.
        """
        ...

    async def read_traffic(self, remote: Remote) -> dict[str, Counter]: ...

    def render(self, material: ClientMaterial, params: dict[str, Any], host: str, dns: tuple[str, str],
               description: str) -> Rendered: ...

    def reserved(self, material: ClientMaterial) -> str | None:
        """The resource this client occupies (see create_material's `taken`)."""
        ...


_REGISTRY: dict[str, Driver] = {}


def register(driver: Driver) -> Driver:
    _REGISTRY[driver.container] = driver
    return driver


def get_driver(container: str) -> Driver:
    from app.drivers import wg  # noqa: F401  (registers the WireGuard family)

    try:
        return _REGISTRY[container]
    except KeyError:
        raise KeyError(f"unsupported container: {container}") from None


def supported_containers() -> set[str]:
    from app.drivers import wg  # noqa: F401

    return set(_REGISTRY)
