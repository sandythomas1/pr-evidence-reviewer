"""Shared safety checks for CLI providers: what environment they get, and which executable is trusted.

A CLI provider is handed only the variables it needs to start, find its sign-in, and reach the
network. Everything else is left out: API keys (which would bill a key instead of the person's
sign-in), GitHub tokens, other providers' keys, and settings that redirect where a CLI sends data.
"""

import os
from collections.abc import Iterable, Mapping
from pathlib import Path

from pr_evidence_reviewer.errors import ProviderUnavailableError

# Enough for a CLI to start, find the person's home and sign-in files, and use a proxy if one is set.
BASE_ENV_NAMES = frozenset(
    {
        "PATH",
        "PATHEXT",
        "SYSTEMROOT",
        "SYSTEMDRIVE",
        "WINDIR",
        "COMSPEC",
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "APPDATA",
        "LOCALAPPDATA",
        "PROGRAMDATA",
        "PROGRAMFILES",
        "PROGRAMFILES(X86)",
        "PROGRAMW6432",
        "TEMP",
        "TMP",
        "TMPDIR",
        "USER",
        "USERNAME",
        "LOGNAME",
        "LANG",
        "LC_ALL",
        "LC_CTYPE",
        "TZ",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_STATE_HOME",
        "XDG_CACHE_HOME",
        "XDG_RUNTIME_DIR",
        "HTTP_PROXY",
        "HTTPS_PROXY",
        "ALL_PROXY",
        "NO_PROXY",
        "SSL_CERT_FILE",
        "SSL_CERT_DIR",
    }
)


def allowlisted_environment(
    base: Mapping[str, str], extra_names: Iterable[str] = ()
) -> dict[str, str]:
    """Keep only the allowed variables from `base`. Names match regardless of case, as on Windows."""
    allowed = BASE_ENV_NAMES | {name.upper() for name in extra_names}
    return {name: value for name, value in base.items() if name.upper() in allowed}


def refuse_if_in_start_folder(executable: str, provider_name: str) -> None:
    """Refuse a program found in the folder the reviewer was started from.

    On Windows, looking up a command by name checks the current folder before PATH, so a file
    planted in a checkout (say a `codex.exe` in a cloned repository) would run instead of the real
    one. A real install never lives there.
    """
    try:
        found_in = Path(executable).resolve().parent
        start_folder = Path(os.getcwd()).resolve()
    except OSError:
        return
    if os.path.normcase(found_in) == os.path.normcase(start_folder):
        raise ProviderUnavailableError(
            f"The {provider_name} program found is in the folder the reviewer was started from, "
            "so it isn't trusted. Start the reviewer from another folder, then try again."
        )
