"""Runner settings. Everything the app needs from the outside world is passed in through here."""

from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


def _default_web_dist() -> Path:
    """The built UI in a source checkout: <repo>/web/dist."""
    return Path(__file__).resolve().parents[2] / "web" / "dist"


@dataclass(frozen=True)
class Settings:
    """Settings for one runner process."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    web_dist: Path = field(default_factory=_default_web_dist)

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS

    @property
    def allowed_hosts(self) -> list[str]:
        """Host headers the server answers. Blocks DNS-rebinding pages from reaching the API."""
        hosts = {"127.0.0.1", "localhost", "[::1]", self.host}
        return sorted(hosts)
