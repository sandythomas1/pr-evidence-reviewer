"""The per-user checkout cache: one verified, content-addressed copy of a repository per head commit.

Layout under the cache root (every name is ours, none comes from the repository):

    checkouts/<key>/manifest.json   written last; no valid manifest means no checkout
    checkouts/<key>/last_used       touched on every lease (LRU and the 7-day TTL)
    checkouts/<key>/blobs/<sha>     verified file bytes, named by git blob sha
    checkouts/.tmp-<random>/        a checkout being built
    checkouts/.trash-<random>/      a checkout being deleted

Concurrency: one asyncio lock per key gives single-flight fetches; leases are refcounted and a leased
checkout is never evicted. Cache state is only touched on the event loop thread; disk work runs in
`asyncio.to_thread`. One runner per user is assumed (no cross-process lock).
"""

import asyncio
import contextlib
import hashlib
import json
import logging
import os
import re
import secrets
import shutil
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Collection, Sequence
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError

from pr_evidence_reviewer.checkout.archive import (
    CHECKOUT_FORMAT,
    ArchiveLimits,
    BlobStore,
    ExtractionResult,
    Manifest,
    RepoName,
    RepoTree,
    TreePlan,
    build_manifest,
    extract_archive,
    plan_refetch,
    plan_tree,
    store_refetched,
)
from pr_evidence_reviewer.config import DEFAULT_CHECKOUT_CACHE_MAX_BYTES, DEFAULT_RETENTION_TTL
from pr_evidence_reviewer.errors import CheckoutUnavailableError

logger = logging.getLogger(__name__)

CHECKOUTS_DIR = "checkouts"
MANIFEST_FILE = "manifest.json"
LAST_USED_FILE = "last_used"
BLOBS_DIR = "blobs"
ARCHIVE_FILE = "archive.download"
TMP_PREFIX = ".tmp-"
TRASH_PREFIX = ".trash-"
STALE_WORK_SECONDS = 3600
_KEY = re.compile(r"^[0-9a-f]{32}$")
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

ROOT_IS_LINK = (
    "The reviewer's cache folder is a link to another location, so it wasn't used. "
    "Remove the link and try again."
)
CACHE_UNWRITABLE = "The reviewer couldn't save the repository in its cache folder."

EvictionReason = Literal["invalid", "ttl", "lru", "clear"]


@dataclass(frozen=True)
class CheckoutTarget:
    """Which repository and commit to check out. `head_repo` is the fork a PR comes from, if any."""

    owner: str
    repo: str
    sha: str
    head_repo: RepoName | None = None


class ArchiveSource(Protocol):
    """Where checkouts come from. `github/archive.py` implements it; tests pass fakes."""

    async def fetch_tree(self, target: CheckoutTarget) -> RepoTree: ...

    async def download_archive(
        self, source: RepoName, sha: str, destination: Path, max_bytes: int
    ) -> None: ...

    async def fetch_blobs(
        self,
        source: RepoName,
        shas: Sequence[str],
        max_bytes: int,
        receive: Callable[[str, bytes], Awaitable[None]],
    ) -> None:
        """Hand each fetched blob to `receive` as it arrives; never collect them all."""
        ...


@dataclass(frozen=True)
class Checkout:
    """A published checkout. Read it only through `CheckoutReader`."""

    path: Path
    manifest: Manifest

    @property
    def blobs_dir(self) -> Path:
        return self.path / BLOBS_DIR


@dataclass(frozen=True)
class ClearResult:
    checkouts: int = 0
    checkout_bytes: int = 0
    packets: int = 0
    packet_bytes: int = 0
    # Saved copies (or leftovers) that couldn't be deleted (review S6).
    failed: int = 0


@dataclass(frozen=True)
class _EntryInfo:
    key: str
    last_used: float
    total_bytes: int
    valid: bool


def checkout_key(owner: str, repo: str, sha: str) -> str:
    """Hashed key, so no owner, repo or case variant ever names a directory (threat T9)."""
    material = f"v{CHECKOUT_FORMAT}:{owner.lower()}/{repo.lower()}@{sha}"
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:32]


def is_link(path: Path) -> bool:
    """A symlink, or on Windows a junction (both can point outside the cache)."""
    return path.is_symlink() or path.is_junction()


class CheckoutCache:
    def __init__(
        self,
        root: Path,
        source: ArchiveSource,
        *,
        max_bytes: int = DEFAULT_CHECKOUT_CACHE_MAX_BYTES,
        ttl: timedelta = DEFAULT_RETENTION_TTL,
        limits: ArchiveLimits | None = None,
        clock: Callable[[], float] = time.time,
        also_retain: Callable[[], object] | None = None,
    ) -> None:
        """`also_retain` runs in every retention pass, so stored packets expire on the same
        schedule as checkouts (review S9). It must not raise."""
        self._also_retain = also_retain
        self._root = root
        self._checkouts = root / CHECKOUTS_DIR
        self._source = source
        self._max_bytes = max_bytes
        self._ttl_seconds = ttl.total_seconds()
        self._limits = limits or ArchiveLimits()
        self._clock = clock
        self._locks: dict[str, asyncio.Lock] = {}
        self._leases: dict[str, int] = {}
        self._start_lock = asyncio.Lock()
        self._started = False

    # --- leasing -------------------------------------------------------------------------------

    @contextlib.asynccontextmanager
    async def lease(self, target: CheckoutTarget) -> AsyncIterator[Checkout]:
        """Hold a checkout of `target` for the duration of the block, fetching it if needed.

        Raises `CheckoutUnavailableError` or `RepositoryTooLargeError`; nothing is published then.
        """
        await self._start()
        key = checkout_key(target.owner, target.repo, target.sha)
        async with self._lock_for(key):
            checkout, _ = await self._acquire(key, target)
            self._leases[key] = self._leases.get(key, 0) + 1
        try:
            # Every lease, hit or miss, so expired copies go while the process runs (review S4).
            await self._enforce_retention()
            yield checkout
        finally:
            self._release(key)

    def _lock_for(self, key: str) -> asyncio.Lock:
        return self._locks.setdefault(key, asyncio.Lock())

    def _release(self, key: str) -> None:
        remaining = self._leases.get(key, 0) - 1
        if remaining > 0:
            self._leases[key] = remaining
        else:
            self._leases.pop(key, None)

    async def _start(self) -> None:
        if self._started:
            return
        async with self._start_lock:
            if self._started:
                return
            await asyncio.to_thread(self._prepare)
            self._started = True
        await self._enforce_retention()

    async def _acquire(self, key: str, target: CheckoutTarget) -> tuple[Checkout, bool]:
        existing = await asyncio.to_thread(self._open_existing, key, target)
        if existing is not None:
            logger.info("Checkout reused", extra={"event": "checkout_hit"})
            return existing, False
        started = time.monotonic()
        checkout = await self._fetch(key, target)
        manifest = checkout.manifest
        logger.info(
            "Checkout fetched",
            extra={
                "event": "checkout_miss",
                "bytes": manifest.total_bytes,
                "ms": round((time.monotonic() - started) * 1000),
                "files": len(manifest.files),
                "skipped": _count_reasons(manifest),
                "refetch_capped": manifest.refetch_capped,
            },
        )
        return checkout, True

    async def _fetch(self, key: str, target: CheckoutTarget) -> Checkout:
        work = await asyncio.to_thread(self._make_work_dir)
        try:
            tree = await self._source.fetch_tree(target)
            plan = plan_tree(tree, self._limits)
            archive = work / ARCHIVE_FILE
            await self._source.download_archive(
                tree.source, target.sha, archive, self._limits.max_compressed_bytes
            )
            store = BlobStore(work / BLOBS_DIR, self._limits.max_total_stored_bytes)
            extraction = await asyncio.to_thread(self._extract, archive, plan, store)
            refetch = plan_refetch(plan, store, self._limits)
            refetched = 0
            if refetch.shas:
                refetched = await self._refetch(tree.source, refetch.shas, store)
            logger.info(
                "Checkout verified",
                extra={
                    "event": "checkout_verified",
                    "refetch_requested": len(refetch.shas),
                    "refetched": refetched,
                    "refetch_capped": refetch.capped,
                },
            )
            manifest = build_manifest(
                owner=target.owner,
                repo=target.repo,
                sha=target.sha,
                tree_sha=tree.sha,
                plan=plan,
                store=store,
                extraction=extraction,
                refetch=refetch,
            )
            return await asyncio.to_thread(self._publish, key, work, manifest, target)
        except BaseException:
            await asyncio.to_thread(self._remove, work, "invalid")
            raise

    async def _refetch(self, source: RepoName, shas: Sequence[str], store: BlobStore) -> int:
        """Verify and store each refetched blob as it arrives, so only the few in flight are held
        in memory (review S1, threat T6). Returns how many were stored."""
        wanted = frozenset(shas)
        lock = asyncio.Lock()
        stored = 0

        async def receive(sha: str, data: bytes) -> None:
            nonlocal stored
            async with lock:
                if await asyncio.to_thread(
                    _store_refetched, store, sha, data, wanted, self._limits
                ):
                    stored += 1

        await self._source.fetch_blobs(source, shas, self._limits.max_file_bytes, receive)
        return stored

    def _extract(self, archive: Path, plan: TreePlan, store: BlobStore) -> ExtractionResult:
        try:
            return extract_archive(archive, plan, store, self._limits)
        finally:
            archive.unlink(missing_ok=True)

    # --- disk work (runs in a worker thread) ---------------------------------------------------

    def _prepare(self) -> None:
        """Create the root and `checkouts/` owner-only, refuse links, sweep stale work dirs."""
        self._check_root()
        try:
            self._checkouts.mkdir(mode=0o700, parents=True, exist_ok=True)
            if sys.platform != "win32":
                os.chmod(self._root, 0o700)
                os.chmod(self._checkouts, 0o700)
        except OSError:
            unwritable = CheckoutUnavailableError(CACHE_UNWRITABLE)
        else:
            self._check_root()
            self._sweep_stale_work()
            return
        raise unwritable from None

    def _check_root(self) -> None:
        if is_link(self._root) or is_link(self._checkouts):
            logger.warning("Cache root refused", extra={"event": "checkout_root_is_link"})
            raise CheckoutUnavailableError(ROOT_IS_LINK)

    def _sweep_stale_work(self) -> None:
        cutoff = self._clock() - STALE_WORK_SECONDS
        for child in self._children():
            if not child.name.startswith((TMP_PREFIX, TRASH_PREFIX)):
                continue
            with contextlib.suppress(OSError):
                if child.lstat().st_mtime < cutoff:
                    self._remove(child, "invalid")

    def _children(self) -> list[Path]:
        try:
            return sorted(self._checkouts.iterdir())
        except FileNotFoundError:
            return []

    def _make_work_dir(self) -> Path:
        self._check_root()
        work = self._checkouts / f"{TMP_PREFIX}{secrets.token_hex(8)}"
        try:
            work.mkdir(mode=0o700)
            (work / BLOBS_DIR).mkdir(mode=0o700)
        except OSError:
            unwritable = CheckoutUnavailableError(CACHE_UNWRITABLE)
        else:
            return work
        raise unwritable from None

    def _entry(self, key: str) -> Path:
        if not _KEY.match(key):
            raise ValueError("not a checkout key")
        return self._checkouts / key

    def _open_existing(self, key: str, target: CheckoutTarget) -> Checkout | None:
        self._check_root()
        entry = self._entry(key)
        if not os.path.lexists(entry):
            return None
        manifest = None if is_link(entry) else _load_manifest(entry)
        if manifest is None or not _matches(manifest, target):
            logger.info("Checkout invalid", extra={"event": "checkout_invalid"})
            self._remove(entry, "invalid")
            return None
        if self._is_expired(entry):
            logger.info("Checkout expired", extra={"event": "checkout_expired"})
            self._remove(entry, "ttl")
            return None
        _mark_used(entry / LAST_USED_FILE, self._clock())
        return Checkout(path=entry, manifest=manifest)

    def _is_expired(self, entry: Path) -> bool:
        try:
            last_used = (entry / LAST_USED_FILE).lstat().st_mtime
        except OSError:
            return True
        return last_used < self._clock() - self._ttl_seconds

    def _publish(
        self, key: str, work: Path, manifest: Manifest, target: CheckoutTarget
    ) -> Checkout:
        """Write `last_used`, then the manifest last, then rename into place. A race loser uses
        the winner's checkout."""
        try:
            _write_new(work / LAST_USED_FILE, b"")
            _mark_used(work / LAST_USED_FILE, self._clock())
            _write_new(work / MANIFEST_FILE, manifest.model_dump_json().encode("utf-8"))
        except OSError:
            logger.warning("Couldn't save a checkout", extra={"event": "checkout_unwritable"})
            raise CheckoutUnavailableError(CACHE_UNWRITABLE) from None
        self._check_root()
        entry = self._entry(key)
        try:
            os.rename(work, entry)
        except OSError:
            self._remove(work, "invalid")
            theirs = self._open_existing(key, target)
            if theirs is None:
                raise CheckoutUnavailableError(CACHE_UNWRITABLE) from None
            return theirs
        return Checkout(path=entry, manifest=manifest)

    def _remove(self, path: Path, reason: EvictionReason) -> bool:
        """Delete a checkout or work dir. The path must sit directly in `checkouts/`; it is renamed
        to `.trash-*` first, and links are unlinked without being followed (threat T11)."""
        if path.parent != self._checkouts:
            logger.error("Refused to delete outside the cache", extra={"event": "checkout_evict"})
            return False
        try:
            if not os.path.lexists(path):
                return True
            if is_link(path) or not path.is_dir():
                _unlink(path)
                return True
            target = path
            if not path.name.startswith(TRASH_PREFIX):
                target = self._checkouts / f"{TRASH_PREFIX}{secrets.token_hex(8)}"
                os.rename(path, target)
            shutil.rmtree(target)
        except OSError:
            logger.warning(
                "Couldn't delete a cached checkout; will retry",
                extra={"event": "checkout_evict_failed", "reason": reason},
            )
            return False
        return True

    def _evict(self, key: str, reason: EvictionReason, total_bytes: int) -> None:
        self._check_root()
        if self._remove(self._entry(key), reason):
            logger.info(
                "Checkout evicted",
                extra={"event": "checkout_evicted", "reason": reason, "bytes": total_bytes},
            )

    def _scan(self) -> list[_EntryInfo]:
        self._check_root()
        entries: list[_EntryInfo] = []
        for child in self._children():
            if not _KEY.match(child.name):
                continue
            if is_link(child):
                entries.append(_EntryInfo(child.name, last_used=0.0, total_bytes=0, valid=False))
                continue
            total = _peek_total_bytes(child)
            try:
                last_used = (child / LAST_USED_FILE).lstat().st_mtime
            except OSError:
                last_used, total = 0.0, None
            entries.append(
                _EntryInfo(
                    key=child.name,
                    last_used=last_used,
                    total_bytes=total or 0,
                    valid=total is not None,
                )
            )
        return entries

    # --- retention -----------------------------------------------------------------------------

    async def _enforce_retention(self) -> None:
        """TTL first, then least recently used while over the size budget. Leased ones stay."""
        if self._also_retain is not None:
            await asyncio.to_thread(self._also_retain)
        entries = await asyncio.to_thread(self._scan)
        victims = select_victims(
            entries,
            leased=frozenset(self._leases),
            now=self._clock(),
            ttl_seconds=self._ttl_seconds,
            max_bytes=self._max_bytes,
        )
        for info, reason in victims:
            async with self._lock_for(info.key):
                if self._leases.get(info.key):
                    continue
                await asyncio.to_thread(self._evict, info.key, reason, info.total_bytes)

    # --- clear ---------------------------------------------------------------------------------

    def clear(self, packets_dir: Path | None = None) -> ClearResult:
        """Delete every checkout (and stored packets if `packets_dir` is given). Synchronous: the
        CLI runs it outside any event loop. Raises `CheckoutUnavailableError` for a linked root."""
        checkouts = checkout_bytes = failed = 0
        if os.path.lexists(self._root):
            self._check_root()
            for child in self._children():
                size = _tree_bytes(child)
                if not self._remove(child, "clear"):
                    failed += 1
                elif _KEY.match(child.name):
                    checkouts += 1
                    checkout_bytes += size
            if checkouts:
                logger.info(
                    "Checkouts cleared",
                    extra={"event": "checkout_evicted", "reason": "clear", "count": checkouts},
                )
        packets = packet_bytes = 0
        if packets_dir is not None and os.path.lexists(packets_dir):
            if is_link(packets_dir):
                raise CheckoutUnavailableError(ROOT_IS_LINK)
            packets, packet_bytes = _count_files(packets_dir)
            try:
                shutil.rmtree(packets_dir)
            except OSError:
                raise CheckoutUnavailableError(
                    "The reviewer couldn't delete its saved evidence packets."
                ) from None
        return ClearResult(checkouts, checkout_bytes, packets, packet_bytes, failed)


def select_victims(
    entries: Sequence[_EntryInfo],
    *,
    leased: Collection[str],
    now: float,
    ttl_seconds: float,
    max_bytes: int,
) -> list[tuple[_EntryInfo, EvictionReason]]:
    """Pure retention policy: invalid and expired first, then oldest while over budget."""
    victims: list[tuple[_EntryInfo, EvictionReason]] = []
    kept: list[_EntryInfo] = []
    for info in entries:
        if info.key in leased:
            kept.append(info)
        elif not info.valid:
            victims.append((info, "invalid"))
        elif info.last_used < now - ttl_seconds:
            victims.append((info, "ttl"))
        else:
            kept.append(info)
    total = sum(info.total_bytes for info in kept)
    for info in sorted(kept, key=lambda item: (item.last_used, item.key)):
        if total <= max_bytes:
            break
        if info.key in leased:
            continue
        victims.append((info, "lru"))
        total -= info.total_bytes
    return victims


def _matches(manifest: Manifest, target: CheckoutTarget) -> bool:
    return (
        manifest.format == CHECKOUT_FORMAT
        and manifest.sha == target.sha
        and manifest.owner == target.owner.lower()
        and manifest.repo == target.repo.lower()
    )


def _load_manifest(entry: Path) -> Manifest | None:
    try:
        return Manifest.model_validate_json(_read_regular(entry / MANIFEST_FILE))
    except (OSError, ValidationError, ValueError):
        return None


def _peek_total_bytes(entry: Path) -> int | None:
    """`total_bytes` from a manifest without full validation (retention scans every checkout)."""
    try:
        data = json.loads(_read_regular(entry / MANIFEST_FILE))
    except (OSError, ValueError):
        return None
    total = data.get("total_bytes") if isinstance(data, dict) else None
    if not isinstance(total, int) or isinstance(total, bool) or total < 0:
        return None
    if data.get("format") != CHECKOUT_FORMAT:
        return None
    return total


def _read_regular(path: Path) -> bytes:
    fd = os.open(path, os.O_RDONLY | getattr(os, "O_BINARY", 0) | _NOFOLLOW)
    with os.fdopen(fd, "rb") as handle:
        return handle.read()


def _mark_used(path: Path, now: float) -> None:
    """Set a checkout's last-used time. Raises `CheckoutUnavailableError` if the cache can't be
    written, so a review falls back instead of crashing (review S7)."""
    try:
        os.utime(path, (now, now))
    except OSError:
        logger.warning("Couldn't mark a checkout used", extra={"event": "checkout_unwritable"})
        raise CheckoutUnavailableError(CACHE_UNWRITABLE) from None


def _store_refetched(
    store: BlobStore, sha: str, data: bytes, wanted: frozenset[str], limits: ArchiveLimits
) -> bool:
    """`store_refetched`, with a write failure (disk full, a locked file) reported as an unwritable
    cache, so the review falls back to the diff and CI checks instead of crashing (review S11)."""
    try:
        return store_refetched(store, sha, data, wanted, limits)
    except OSError:
        logger.warning("Couldn't save a checkout", extra={"event": "checkout_unwritable"})
        raise CheckoutUnavailableError(CACHE_UNWRITABLE) from None


def _write_new(path: Path, data: bytes) -> None:
    def opener(name: str, flags: int) -> int:
        return os.open(name, flags | _NOFOLLOW, 0o600)

    with open(path, "xb", opener=opener) as handle:
        handle.write(data)


def _unlink(path: Path) -> None:
    try:
        os.unlink(path)
    except (IsADirectoryError, PermissionError):
        os.rmdir(path)  # a directory symlink or junction: removes the link, not its target


def _tree_bytes(path: Path) -> int:
    if is_link(path):
        return 0
    return _count_files(path)[1]


def _count_files(path: Path) -> tuple[int, int]:
    count = total = 0
    for directory, _dirs, files in os.walk(path, followlinks=False):
        for name in files:
            with contextlib.suppress(OSError):
                total += os.lstat(os.path.join(directory, name)).st_size
                count += 1
    return count, total


def _count_reasons(manifest: Manifest) -> dict[str, int]:
    counts: dict[str, int] = {}
    for skip in manifest.skipped:
        counts[skip.reason] = counts.get(skip.reason, 0) + 1
    return counts
