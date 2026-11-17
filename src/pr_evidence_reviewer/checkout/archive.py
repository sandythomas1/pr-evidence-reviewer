"""Turn a git tree listing plus a downloaded repository archive into verified, content-addressed blobs.

Pure local work: no network and no subprocess. The archive is read as a stream; only regular members
whose path is a file in the git tree are read, and only bytes whose git blob hash matches the tree are
kept. Files are written as `blobs/<blob sha>`; no name on disk ever comes from the archive or a
repository path (threats T2, T6, T8). `extract`/`extractall` are never used.
"""

import gzip
import hashlib
import os
import re
import tarfile
import zlib
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import IO, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, field_validator

from pr_evidence_reviewer.checkout.paths import RepoPath, normalize_repo_path
from pr_evidence_reviewer.checkout.text import decode_strict
from pr_evidence_reviewer.errors import CheckoutUnavailableError, RepositoryTooLargeError

CHECKOUT_FORMAT = 1
BLOB_SHA_PATTERN = r"^[0-9a-f]{40}$"
BLOB_SHA = re.compile(BLOB_SHA_PATTERN)

MIB = 1024 * 1024
GIB = 1024 * MIB
CHUNK_BYTES = 64 * 1024
BINARY_SNIFF_BYTES = 8 * 1024
LFS_POINTER_PREFIX = b"version https://git-lfs.github.com/spec/"
LFS_POINTER_MAX_BYTES = 1024

REGULAR_FILE_MODES = frozenset({"100644", "100755"})
SYMLINK_MODE = "120000"
SUBMODULE_MODE = "160000"

TOO_MANY_FILES = "The repository at this commit has too many files to read in full."
TOO_LARGE = "The repository at this commit is too large to read in full."
DAMAGED = "GitHub's copy of the repository at this commit couldn't be read."

FileKind = Literal["text", "binary", "lfs", "undecodable"]
SkipReason = Literal[
    "unsafe_name",
    "link",
    "submodule",
    "unsupported_entry",
    "duplicate",
    "too_large",
    "export_ignored",
    "unverified",
]
EntryKind = Literal["blob", "tree", "commit"]

_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)


@dataclass(frozen=True)
class ArchiveLimits:
    """Size limits for one checkout (plan D2 Limits). Tests pass smaller ones."""

    max_compressed_bytes: int = 512 * MIB
    max_decompressed_bytes: int = 1 * GIB
    max_tree_entries: int = 100_000
    max_total_stored_bytes: int = 1 * GIB
    max_file_bytes: int = 5 * MIB
    max_refetch: int = 300


@dataclass(frozen=True)
class RepoName:
    owner: str
    repo: str


@dataclass(frozen=True)
class TreeEntry:
    """One entry of `GET /git/trees/{sha}?recursive=1`, already shape-checked by the source."""

    path: str
    mode: str
    kind: EntryKind
    sha: str
    size: int | None = None


@dataclass(frozen=True)
class RepoTree:
    """The git tree at the head commit and the repository that served it (base, or a fork)."""

    source: RepoName
    sha: str
    entries: tuple[TreeEntry, ...]


class _Frozen(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class ManifestFile(_Frozen):
    path: str
    size: int = Field(ge=0)
    blob_sha: str = Field(pattern=BLOB_SHA_PATTERN)
    kind: FileKind

    @field_validator("path")
    @classmethod
    def _safe_path(cls, value: str) -> str:
        if normalize_repo_path(value) is None:
            raise ValueError("unsafe path in manifest")
        return value


class ManifestSkip(_Frozen):
    path: str
    reason: SkipReason


class Manifest(_Frozen):
    """What a checkout holds. Written last, so a checkout without a valid manifest doesn't exist."""

    format: int
    owner: str
    repo: str
    sha: str = Field(pattern=BLOB_SHA_PATTERN)
    tree_sha: str = Field(pattern=BLOB_SHA_PATTERN)
    total_bytes: int = Field(ge=0)
    refetch_capped: bool
    files: tuple[ManifestFile, ...]
    skipped: tuple[ManifestSkip, ...]


@dataclass(frozen=True)
class TreePlan:
    """Tree files that can be stored, and everything else with the reason it isn't."""

    candidates: Mapping[RepoPath, TreeEntry]
    skipped: tuple[ManifestSkip, ...]


def plan_tree(tree: RepoTree, limits: ArchiveLimits) -> TreePlan:
    """Sort tree entries into storable files and skips. Checked before anything is downloaded."""
    if len(tree.entries) > limits.max_tree_entries:
        raise RepositoryTooLargeError(TOO_MANY_FILES)
    candidates: dict[RepoPath, TreeEntry] = {}
    skipped: list[ManifestSkip] = []
    duplicates: set[str] = set()
    for entry in tree.entries:
        if entry.kind == "tree":
            continue
        reason = _skip_reason(entry, limits)
        path = normalize_repo_path(entry.path)
        if reason is None and path is not None:
            if path in candidates or path in duplicates:
                duplicates.add(path)
                candidates.pop(path, None)
                continue
            candidates[path] = entry
        else:
            skipped.append(ManifestSkip(path=entry.path, reason=reason or "unsafe_name"))
    skipped.extend(ManifestSkip(path=path, reason="duplicate") for path in duplicates)
    if sum(entry.size or 0 for entry in candidates.values()) > limits.max_total_stored_bytes:
        raise RepositoryTooLargeError(TOO_LARGE)
    return TreePlan(candidates=candidates, skipped=tuple(skipped))


def _skip_reason(entry: TreeEntry, limits: ArchiveLimits) -> SkipReason | None:
    if normalize_repo_path(entry.path) is None:
        return "unsafe_name"
    if entry.mode == SYMLINK_MODE:
        return "link"
    if entry.mode == SUBMODULE_MODE or entry.kind == "commit":
        return "submodule"
    if entry.kind != "blob" or entry.mode not in REGULAR_FILE_MODES or entry.size is None:
        return "unsupported_entry"
    if entry.size > limits.max_file_bytes:
        return "too_large"
    return None


def git_blob_sha(data: bytes) -> str:
    """The id git gives these bytes as a blob: sha1 of `blob <size>\\0<data>`."""
    digest = hashlib.sha1(b"blob %d\0" % len(data), usedforsecurity=False)
    digest.update(data)
    return digest.hexdigest()


def classify(data: bytes) -> FileKind:
    if len(data) <= LFS_POINTER_MAX_BYTES and data.startswith(LFS_POINTER_PREFIX):
        return "lfs"
    if b"\0" in data[:BINARY_SNIFF_BYTES]:
        return "binary"
    if decode_strict(data) is None:
        return "undecodable"
    return "text"


def _owner_only(path: str, flags: int) -> int:
    return os.open(path, flags | _NOFOLLOW, 0o600)


class BlobStore:
    """Writes verified bytes once per blob sha under a directory this package created."""

    def __init__(self, blobs_dir: Path, max_total_bytes: int) -> None:
        self._dir = blobs_dir
        self._max_total_bytes = max_total_bytes
        self._kinds: dict[str, FileKind] = {}
        self.total_bytes = 0

    def has(self, sha: str) -> bool:
        return sha in self._kinds

    def kind(self, sha: str) -> FileKind:
        return self._kinds[sha]

    def add(self, sha: str, data: bytes) -> None:
        """Store bytes the caller has verified hash to `sha`. A blob already stored is reused."""
        if sha in self._kinds:
            return
        if not BLOB_SHA.match(sha):
            raise ValueError("not a blob sha")
        if self.total_bytes + len(data) > self._max_total_bytes:
            raise RepositoryTooLargeError(TOO_LARGE)
        try:
            with open(self._dir / sha, "xb", opener=_owner_only) as blob:
                blob.write(data)
        except FileExistsError:
            pass
        self._kinds[sha] = classify(data)
        self.total_bytes += len(data)

    def add_if_verified(self, sha: str, data: bytes, max_file_bytes: int) -> bool:
        if len(data) > max_file_bytes or git_blob_sha(data) != sha:
            return False
        self.add(sha, data)
        return True


class _Readable(Protocol):
    def read(self, size: int = -1, /) -> bytes: ...


class _CappedReader:
    """Counts decompressed bytes (headers and unread members included) and stops a gzip bomb."""

    def __init__(self, inner: _Readable, cap: int) -> None:
        self._inner = inner
        self._cap = cap
        self.count = 0

    def read(self, size: int = -1) -> bytes:
        if size < 0 or size > CHUNK_BYTES:
            size = CHUNK_BYTES
        data = self._inner.read(size)
        self.count += len(data)
        if self.count > self._cap:
            raise RepositoryTooLargeError(TOO_LARGE)
        return data


@dataclass
class ExtractionResult:
    """Tree files whose archive member was present but didn't match the tree (export-subst, eol).

    `top` is the archive's single top directory (`owner-repo-shortsha`); members outside it are
    ignored."""

    altered: set[RepoPath] = field(default_factory=set)
    top: str | None = None


def extract_archive(
    archive_path: Path, plan: TreePlan, store: BlobStore, limits: ArchiveLimits
) -> ExtractionResult:
    """Stream the gzipped tar once and store every member that verifies against the tree."""
    result = ExtractionResult()
    try:
        with archive_path.open("rb") as raw, gzip.GzipFile(fileobj=raw, mode="rb") as unzipped:
            stream = _CappedReader(unzipped, limits.max_decompressed_bytes)
            with tarfile.open(fileobj=stream, mode="r|") as tar:  # type: ignore[call-overload]
                for member in tar:
                    _consume(tar, member, plan, store, result, limits)
    except (OSError, EOFError, zlib.error, tarfile.TarError):
        damaged = CheckoutUnavailableError(DAMAGED)
    else:
        return result
    raise damaged from None


def _consume(
    tar: tarfile.TarFile,
    member: tarfile.TarInfo,
    plan: TreePlan,
    store: BlobStore,
    result: ExtractionResult,
    limits: ArchiveLimits,
) -> None:
    """Read one member if it is a regular file the tree lists. Anything else stays unread."""
    top, slash, stripped = member.name.partition("/")
    if result.top is None and normalize_repo_path(top) is not None:
        result.top = top
    if member.type not in (tarfile.REGTYPE, tarfile.AREGTYPE) or top != result.top:
        return
    path = normalize_repo_path(stripped) if slash else None
    entry = plan.candidates.get(path) if path is not None else None
    if path is None or entry is None or store.has(entry.sha):
        return
    if member.size != entry.size or member.size > limits.max_file_bytes:
        result.altered.add(path)
        return
    data = _read_member(tar.extractfile(member), member.size)
    if data is not None and git_blob_sha(data) == entry.sha:
        store.add(entry.sha, data)
    else:
        result.altered.add(path)


def _read_member(member: IO[bytes] | None, size: int) -> bytes | None:
    if member is None:
        return None
    chunks: list[bytes] = []
    remaining = size
    while remaining > 0:
        chunk = member.read(min(CHUNK_BYTES, remaining))
        if not chunk:
            return None
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


@dataclass(frozen=True)
class RefetchPlan:
    """Blob shas to fetch one by one (at most `max_refetch`), in path order."""

    shas: tuple[str, ...]
    capped: bool


def plan_refetch(plan: TreePlan, store: BlobStore, limits: ArchiveLimits) -> RefetchPlan:
    pending: list[str] = []
    for path in sorted(plan.candidates):
        sha = plan.candidates[path].sha
        if not store.has(sha) and sha not in pending:
            pending.append(sha)
    return RefetchPlan(
        shas=tuple(pending[: limits.max_refetch]), capped=len(pending) > limits.max_refetch
    )


def store_refetched(
    store: BlobStore, blobs: Mapping[str, bytes], wanted: Iterable[str], limits: ArchiveLimits
) -> int:
    """Store refetched bytes that verify. Returns how many blobs were stored."""
    stored = 0
    for sha in wanted:
        data = blobs.get(sha)
        if data is not None and store.add_if_verified(sha, data, limits.max_file_bytes):
            stored += 1
    return stored


def build_manifest(
    *,
    owner: str,
    repo: str,
    sha: str,
    tree_sha: str,
    plan: TreePlan,
    store: BlobStore,
    extraction: ExtractionResult,
    refetch: RefetchPlan,
) -> Manifest:
    """The sorted manifest: stored files, plus a reason for every tree file that wasn't stored."""
    files: list[ManifestFile] = []
    skipped = list(plan.skipped)
    requested = set(refetch.shas)
    for path in sorted(plan.candidates):
        entry = plan.candidates[path]
        if store.has(entry.sha):
            files.append(
                ManifestFile(
                    path=path, size=entry.size or 0, blob_sha=entry.sha, kind=store.kind(entry.sha)
                )
            )
        elif entry.sha in requested or path in extraction.altered:
            skipped.append(ManifestSkip(path=path, reason="unverified"))
        else:
            skipped.append(ManifestSkip(path=path, reason="export_ignored"))
    return Manifest(
        format=CHECKOUT_FORMAT,
        owner=owner.lower(),
        repo=repo.lower(),
        sha=sha,
        tree_sha=tree_sha,
        total_bytes=store.total_bytes,
        refetch_capped=refetch.capped,
        files=tuple(files),
        skipped=tuple(sorted(skipped, key=lambda skip: (skip.path, skip.reason))),
    )
