"""Line-preserving parser for WireGuard/AmneziaWG server configs (wg0.conf / awg0.conf)."""

from dataclasses import dataclass, field


def _kv(line: str) -> tuple[str, str] | None:
    if "=" not in line:
        return None
    key, _, value = line.partition("=")
    return key.strip(), value.strip()


@dataclass
class Peer:
    lines: list[str] = field(default_factory=list)

    @classmethod
    def new(cls, public_key: str, psk: str | None, ip: str) -> "Peer":
        lines = [f"PublicKey = {public_key}"]
        if psk:
            lines.append(f"PresharedKey = {psk}")
        lines.append(f"AllowedIPs = {ip}/32")
        return cls(lines)

    def _get(self, key: str) -> str | None:
        for line in self.lines:
            kv = _kv(line)
            if kv and kv[0] == key:
                return kv[1]
        return None

    @property
    def public_key(self) -> str | None:
        return self._get("PublicKey")

    @property
    def psk(self) -> str | None:
        return self._get("PresharedKey")

    @property
    def ip(self) -> str | None:
        allowed = self._get("AllowedIPs")
        if not allowed:
            return None
        return allowed.split(",")[0].strip().split("/")[0]


@dataclass
class WgConf:
    interface_lines: list[str] = field(default_factory=list)
    peers: list[Peer] = field(default_factory=list)

    def interface_values(self) -> dict[str, str]:
        """Interface settings; `# I1 = ...` comments count too, as in AwgInstaller::extractConfigFromContainer."""
        values: dict[str, str] = {}
        for line in self.interface_lines:
            text = line.strip()
            if text.startswith("#"):
                text = text.lstrip("#").strip()
            kv = _kv(text)
            if kv and kv[1]:
                values[kv[0]] = kv[1]
        return values

    def dump(self) -> str:
        out = ["[Interface]", *self.interface_lines, ""]
        for peer in self.peers:
            out += ["[Peer]", *peer.lines, ""]
        return "\n".join(out)


def parse(text: str) -> WgConf:
    conf = WgConf()
    section: list[str] | None = None
    for raw in text.splitlines():
        line = raw.rstrip()
        header = line.strip().lower()
        if header == "[interface]":
            section = conf.interface_lines
            continue
        if header == "[peer]":
            peer = Peer()
            conf.peers.append(peer)
            section = peer.lines
            continue
        if section is None or not line.strip():
            continue
        section.append(line)
    return conf
