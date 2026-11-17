"""Runner settings. Everything the app needs from the outside world is passed in through here."""

from dataclasses import dataclass, field
from datetime import timedelta
from pathlib import Path

import platformdirs

APP_NAME = "pr-evidence-reviewer"
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})
DEFAULT_CHECKOUT_CACHE_MAX_BYTES = 2 * 1024**3
# How long a saved repository copy or evidence packet is kept unused (review S9: one setting).
DEFAULT_RETENTION_TTL = timedelta(days=7)


def _default_web_dist() -> Path:
    """The built UI in a source checkout: <repo>/web/dist."""
    return Path(__file__).resolve().parents[2] / "web" / "dist"


def _default_cache_dir() -> Path:
    """Evictable per-user state: repository checkouts."""
    return Path(platformdirs.user_cache_dir(APP_NAME, appauthor=False))


def _default_data_dir() -> Path:
    """Durable per-user state: stored evidence packets."""
    return Path(platformdirs.user_data_dir(APP_NAME, appauthor=False))


@dataclass(frozen=True)
class Settings:
    """Settings for one runner process. Directories are only named here, never created."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT
    web_dist: Path = field(default_factory=_default_web_dist)
    cache_dir: Path = field(default_factory=_default_cache_dir)
    data_dir: Path = field(default_factory=_default_data_dir)
    checkout_cache_max_bytes: int = DEFAULT_CHECKOUT_CACHE_MAX_BYTES
    retention_ttl: timedelta = DEFAULT_RETENTION_TTL

    @property
    def packets_dir(self) -> Path:
        """Stored evidence packets (`prev cache clear --packets` deletes them)."""
        return self.data_dir / "packets"

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS

    @property
    def allowed_hosts(self) -> list[str]:
        """Host headers the server answers. Blocks DNS-rebinding pages from reaching the API."""
        hosts = {"127.0.0.1", "localhost", "[::1]", self.host}
        return sorted(hosts)
