"""Runner settings. Everything the app needs from the outside world is passed in through here."""

from dataclasses import dataclass

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8765
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


@dataclass(frozen=True)
class Settings:
    """Settings for one runner process."""

    host: str = DEFAULT_HOST
    port: int = DEFAULT_PORT

    @property
    def is_loopback(self) -> bool:
        return self.host in LOOPBACK_HOSTS
