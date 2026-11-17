"""The only way to read repository files from a checkout (threat T4).

An untrusted path is validated by `normalize_repo_path`, looked up exactly (case- and byte-sensitive)
in the checkout's manifest, and the bytes are read from `blobs/<blob sha>` using the sha from our own
manifest. Reads never walk the filesystem or open a path built from repository text, so traversal,
8.3 short names, alternate data streams and case aliases simply don't match anything.
"""

import os
import threading
from collections import OrderedDict
from collections.abc import Iterable, Iterator

from pr_evidence_reviewer.checkout.archive import ManifestFile, ManifestSkip, git_blob_sha
from pr_evidence_reviewer.checkout.cache import Checkout
from pr_evidence_reviewer.checkout.paths import RepoPath, normalize_repo_path
from pr_evidence_reviewer.checkout.text import decode_strict, split_lines

DEFAULT_DECODED_CACHE_BYTES = 64 * 1024 * 1024
_OPEN_FLAGS = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)


class UnsafePathError(Exception):
    """A path that `normalize_repo_path` refuses. Internal; never shown to the user as is."""


class CheckoutReadError(Exception):
    """A blob the manifest lists is missing or changed on disk (for example, the cache was cleared
    by another process). The review degrades to the diff."""


class CheckoutReader:
    """Read-only, thread-safe access to one checkout's text files."""

    def __init__(
        self, checkout: Checkout, *, cache_bytes: int = DEFAULT_DECODED_CACHE_BYTES
    ) -> None:
        self._blobs_dir = checkout.blobs_dir
        self._manifest = checkout.manifest
        self._files = {entry.path: entry for entry in checkout.manifest.files}
        self._cache_bytes = cache_bytes
        self._cache: OrderedDict[str, tuple[str, int]] = OrderedDict()
        self._cached_bytes = 0
        self._lock = threading.Lock()

    @property
    def head_sha(self) -> str:
        return self._manifest.sha

    def files(self) -> tuple[ManifestFile, ...]:
        """Every stored file, sorted by path."""
        return self._manifest.files

    def skipped(self) -> tuple[ManifestSkip, ...]:
        """Tree entries the checkout doesn't hold, each with the reason (link, export-ignored, ...)."""
        return self._manifest.skipped

    def file(self, path: str) -> ManifestFile | None:
        """The manifest entry for exactly `path`, or None. Raises `UnsafePathError` on refusal."""
        return self._files.get(_checked(path))

    def has(self, path: str) -> bool:
        return self.file(path) is not None

    def text(self, path: str) -> str | None:
        """Decoded text of a `text` file, or None for unknown, binary, LFS or undecodable files."""
        entry = self._text_entry(path)
        if entry is None:
            return None
        with self._lock:
            cached = self._cache.get(entry.blob_sha)
            if cached is not None:
                self._cache.move_to_end(entry.blob_sha)
                return cached[0]
        text = self._decode(entry)
        self._remember(entry, text)
        return text

    def lines(self, path: str) -> list[str] | None:
        text = self.text(path)
        return None if text is None else split_lines(text)

    def line_count(self, path: str) -> int | None:
        lines = self.lines(path)
        return None if lines is None else len(lines)

    def iter_text(self, paths: Iterable[str]) -> Iterator[tuple[RepoPath, str]]:
        """Yield (path, text) for each text file in `paths`, read fresh (the decoded LRU is left
        alone so a corpus scan doesn't evict files symbols and validation keep re-reading)."""
        for path in paths:
            entry = self._text_entry(path)
            if entry is not None:
                yield RepoPath(entry.path), self._decode(entry)

    def _text_entry(self, path: str) -> ManifestFile | None:
        entry = self.file(path)
        return entry if entry is not None and entry.kind == "text" else None

    def _decode(self, entry: ManifestFile) -> str:
        text = decode_strict(self._read_blob(entry))
        if text is None:
            raise CheckoutReadError("a cached file no longer decodes")
        return text

    def _read_blob(self, entry: ManifestFile) -> bytes:
        try:
            fd = os.open(self._blobs_dir / entry.blob_sha, _OPEN_FLAGS)
            with os.fdopen(fd, "rb") as blob:
                data = blob.read(entry.size + 1)
        except OSError:
            raise CheckoutReadError("a cached file is missing") from None
        if len(data) != entry.size or git_blob_sha(data) != entry.blob_sha:
            raise CheckoutReadError("a cached file changed on disk")
        return data

    def _remember(self, entry: ManifestFile, text: str) -> None:
        if entry.size > self._cache_bytes:
            return
        with self._lock:
            if entry.blob_sha in self._cache:
                return
            self._cache[entry.blob_sha] = (text, entry.size)
            self._cached_bytes += entry.size
            while self._cached_bytes > self._cache_bytes:
                _sha, (_text, size) = self._cache.popitem(last=False)
                self._cached_bytes -= size


def _checked(path: str) -> RepoPath:
    safe = normalize_repo_path(path)
    if safe is None:
        raise UnsafePathError("refused repository path")
    return safe
