"""Durable store for evidence packets: `<root>/<repo key>/<head sha>/<content hash>.json`.

The store is content-addressed. A file's name is the sha256 of the packet it holds, so saving the
same packet twice writes nothing new, and no name on disk comes from the repository: the repository
key is a hash of `owner/repo` and the head sha is checked to be 40 hex digits first. Writes go to a
temporary file in the same directory and are renamed into place, so a reader never sees half a
packet. Every load is validated by pydantic and against its own hash.

Saving is best effort: a failure is logged (by error type only) and reported to the caller, never
raised, because a review is still useful without its saved packet. A packet older than
the retention period (7 days, shared with checkouts) is removed by the checkout retention pass, at
startup and then at most once an hour, never on save (review CR11); removal never follows a link,
and one file it can't remove doesn't stop the rest (review S10). Saving a packet whose stored copy
has expired writes it fresh. `prev cache clear --packets` deletes the store.
"""

import hashlib
import logging
import os
import re
import stat
import sys
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from pydantic import ValidationError

from pr_evidence_reviewer.config import DEFAULT_RETENTION_TTL
from pr_evidence_reviewer.domain.evidence import EvidencePacket, StoredPacket

logger = logging.getLogger(__name__)

REPO_KEY_CHARS = 16
# The retention pass runs on every checkout lease; a packet sweep reads the whole store, so it runs
# at most this often per process.
EXPIRY_INTERVAL = timedelta(hours=1)
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def repo_key(repo: str) -> str:
    """A fixed-length directory name for `owner/repo`; GitHub names are case-insensitive."""
    return hashlib.sha256(repo.lower().encode()).hexdigest()[:REPO_KEY_CHARS]


class PacketStore:
    def __init__(
        self,
        root: Path,
        *,
        ttl: timedelta = DEFAULT_RETENTION_TTL,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = root
        self._ttl_seconds = ttl.total_seconds()
        self._clock = clock
        self._expiry_lock = threading.Lock()
        self._last_expiry: float | None = None

    def path_for(self, repo: str, head_sha: str, content_hash: str) -> Path:
        if not _HEX40.fullmatch(head_sha) or not _HEX64.fullmatch(content_hash):
            raise ValueError("A packet is stored under a full head commit id and its hash.")
        return self._root / repo_key(repo) / head_sha / f"{content_hash}.json"

    def save(self, packet: EvidencePacket) -> bool:
        """Store the packet. True when it's on disk afterwards, False (logged) when it isn't."""
        try:
            content_hash = packet.content_hash()
            target = self.path_for(packet.snapshot.repo, packet.snapshot.head_sha, content_hash)
            if self._is_fresh(target):
                return True
            self._write(target, StoredPacket(content_hash=content_hash, packet=packet))
        except (OSError, ValueError) as error:
            logger.warning(
                "Couldn't save the evidence packet", extra={"error": type(error).__name__}
            )
            return False
        logger.info("Saved the evidence packet", extra={"packet_hash": content_hash})
        return True

    def remove_expired_if_due(self) -> int:
        """`remove_expired`, unless it already ran in the last hour (review CR11). The first call
        always runs. Never raises. Returns how many packets were removed."""
        with self._expiry_lock:
            now = self._clock()
            due = self._last_expiry is None or now - self._last_expiry >= _EXPIRY_SECONDS
            if due:
                self._last_expiry = now
        return self.remove_expired() if due else 0

    def remove_expired(self) -> int:
        """Delete packets written longer ago than the retention period, then emptied folders.
        Links are never followed or deleted through; a linked root is refused. A file or folder
        that can't be removed is counted and skipped (review S10). Never raises. Returns how many
        packets were removed."""
        try:
            sweep = self._remove_expired()
        except OSError as error:
            logger.warning("Couldn't remove expired packets", extra={"error": type(error).__name__})
            return 0
        if sweep.removed:
            logger.info(
                "Expired packets removed",
                extra={"event": "packets_expired", "count": sweep.removed},
            )
        if sweep.failed:
            logger.warning(
                "Some expired packets couldn't be removed",
                extra={"event": "packets_expire_failed", "count": sweep.failed},
            )
        return sweep.removed

    def _remove_expired(self) -> "_Sweep":
        sweep = _Sweep()
        if not os.path.lexists(self._root):
            return sweep
        if _is_link(self._root):
            raise OSError("the packet store is a link")
        if not self._root.is_dir():
            return sweep
        cutoff = self._clock() - self._ttl_seconds
        for repo_dir in _real_dirs(self._root):
            try:
                commit_dirs = _real_dirs(repo_dir)
            except OSError:
                sweep.failed += 1
                continue
            for commit_dir in commit_dirs:
                _expire_files(commit_dir, cutoff, sweep)
                _remove_if_empty(commit_dir, sweep)
            _remove_if_empty(repo_dir, sweep)
        return sweep

    def _is_fresh(self, target: Path) -> bool:
        """True when `target` is a regular file still inside the retention period. An expired or
        linked copy is written again, so a saved packet always gets a full retention period."""
        try:
            info = target.lstat()
        except FileNotFoundError:
            return False
        return stat.S_ISREG(info.st_mode) and info.st_mtime >= self._clock() - self._ttl_seconds

    def load(self, repo: str, head_sha: str, content_hash: str) -> StoredPacket | None:
        """The stored packet, or None when it's missing, unreadable, or doesn't match its hash."""
        try:
            data = self.path_for(repo, head_sha, content_hash).read_bytes()
            stored = StoredPacket.model_validate_json(data)
        except FileNotFoundError:
            return None
        except (OSError, ValueError, ValidationError) as error:
            logger.warning("Couldn't read a saved packet", extra={"error": type(error).__name__})
            return None
        packet = stored.packet
        if (
            stored.content_hash != content_hash
            or packet.content_hash() != content_hash
            or packet.snapshot.head_sha != head_sha
            or repo_key(packet.snapshot.repo) != repo_key(repo)
        ):
            logger.warning("A saved packet doesn't match its name", extra={"error": "mismatch"})
            return None
        return stored

    def _write(self, target: Path, stored: StoredPacket) -> None:
        for directory in (self._root, target.parent.parent, target.parent):
            _owner_only_dir(directory, parents=directory == self._root)
        data = stored.model_dump_json().encode()
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
            now = self._clock()
            os.utime(target, (now, now))
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise


_EXPIRY_SECONDS = EXPIRY_INTERVAL.total_seconds()


@dataclass
class _Sweep:
    removed: int = 0
    failed: int = 0


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def _real_dirs(parent: Path) -> list[Path]:
    """Sub-folders of `parent` that are real folders, not links."""
    return [child for child in parent.iterdir() if not _is_link(child) and child.is_dir()]


def _expire_files(commit_dir: Path, cutoff: float, sweep: _Sweep) -> None:
    """Unlink the expired regular files in one commit folder; a failure is counted, not raised."""
    try:
        files = list(commit_dir.iterdir())
    except OSError:
        sweep.failed += 1
        return
    for file in files:
        try:
            if _is_link(file) or not file.is_file() or file.lstat().st_mtime >= cutoff:
                continue
            file.unlink()
        except FileNotFoundError:
            continue
        except OSError:
            sweep.failed += 1
            continue
        sweep.removed += not file.name.startswith(".tmp-")


def _remove_if_empty(directory: Path, sweep: _Sweep) -> None:
    try:
        if not any(directory.iterdir()):
            directory.rmdir()
    except FileNotFoundError:
        return
    except OSError:
        sweep.failed += 1


def _owner_only_dir(directory: Path, *, parents: bool) -> None:
    """Create `directory` owner-only (0700 on POSIX), or refuse it when it is a link: a symlink or
    junction at any level could send private excerpts somewhere else."""
    if os.path.lexists(directory) and _is_link(directory):
        raise OSError("the packet store is a link")
    directory.mkdir(mode=0o700, parents=parents, exist_ok=True)
    if sys.platform != "win32":
        os.chmod(directory, 0o700)
