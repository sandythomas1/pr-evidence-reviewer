"""Durable store for evidence packets: `<root>/<repo key>/<head sha>/<content hash>.json`.

The store is content-addressed. A file's name is the sha256 of the packet it holds, so saving the
same packet twice writes nothing new, and no name on disk comes from the repository: the repository
key is a hash of `owner/repo` and the head sha is checked to be 40 hex digits first. Writes go to a
temporary file in the same directory and are renamed into place, so a reader never sees half a
packet. Every load is validated by pydantic and against its own hash.

Saving is best effort: a failure is logged (by error type only) and reported to the caller, never
raised, because a review is still useful without its saved packet. Nothing is evicted; `prev cache
clear --packets` deletes the store.
"""

import hashlib
import logging
import os
import re
import sys
import tempfile
from pathlib import Path

from pydantic import ValidationError

from pr_evidence_reviewer.domain.evidence import EvidencePacket, StoredPacket

logger = logging.getLogger(__name__)

REPO_KEY_CHARS = 16
_HEX40 = re.compile(r"[0-9a-f]{40}")
_HEX64 = re.compile(r"[0-9a-f]{64}")


def repo_key(repo: str) -> str:
    """A fixed-length directory name for `owner/repo`; GitHub names are case-insensitive."""
    return hashlib.sha256(repo.lower().encode()).hexdigest()[:REPO_KEY_CHARS]


class PacketStore:
    def __init__(self, root: Path) -> None:
        self._root = root

    def path_for(self, repo: str, head_sha: str, content_hash: str) -> Path:
        if not _HEX40.fullmatch(head_sha) or not _HEX64.fullmatch(content_hash):
            raise ValueError("A packet is stored under a full head commit id and its hash.")
        return self._root / repo_key(repo) / head_sha / f"{content_hash}.json"

    def save(self, packet: EvidencePacket) -> bool:
        """Store the packet. True when it's on disk afterwards, False (logged) when it isn't."""
        content_hash = packet.content_hash()
        try:
            target = self.path_for(packet.snapshot.repo, packet.snapshot.head_sha, content_hash)
            if target.exists():
                return True
            self._write(target, StoredPacket(content_hash=content_hash, packet=packet))
        except (OSError, ValueError) as error:
            logger.warning(
                "Couldn't save the evidence packet", extra={"error": type(error).__name__}
            )
            return False
        logger.info("Saved the evidence packet", extra={"packet_hash": content_hash})
        return True

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
        if os.path.lexists(self._root) and (self._root.is_symlink() or self._root.is_junction()):
            raise OSError("the packet store is a link")
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if sys.platform != "win32":
            os.chmod(target.parent, 0o700)
        data = stored.model_dump_json().encode()
        handle, temporary = tempfile.mkstemp(dir=target.parent, prefix=".tmp-", suffix=".json")
        try:
            with os.fdopen(handle, "wb") as file:
                file.write(data)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, target)
        except BaseException:
            Path(temporary).unlink(missing_ok=True)
            raise
