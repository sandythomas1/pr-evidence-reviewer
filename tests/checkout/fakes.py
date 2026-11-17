"""Crafted archives, trees and a fake `ArchiveSource` for hermetic checkout tests."""

import asyncio
import gzip
import io
import os
import sys
import tarfile
from collections.abc import Awaitable, Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from pr_evidence_reviewer.checkout.archive import (
    RepoName,
    RepoTree,
    TreeEntry,
    git_blob_sha,
)
from pr_evidence_reviewer.checkout.cache import Checkout, CheckoutCache, CheckoutTarget
from pr_evidence_reviewer.errors import RepositoryTooLargeError

HEAD_SHA = "c" * 40
TREE_SHA = "d" * 40
TOP_DIR = "acme-shop-ccccccc"
TARGET = CheckoutTarget(owner="acme", repo="shop", sha=HEAD_SHA)


@dataclass(frozen=True)
class Member:
    """One tar member. `kind` is a tarfile type: REGTYPE, SYMTYPE, LNKTYPE, CHRTYPE, FIFOTYPE, DIRTYPE."""

    name: str
    data: bytes = b""
    kind: bytes = tarfile.REGTYPE
    linkname: str = ""
    raw_name: bool = False  # True: name is used as is, without the top directory


def regular(files: Mapping[str, bytes]) -> list[Member]:
    return [Member(path, data) for path, data in files.items()]


def make_archive(members: Iterable[Member], *, top: str = TOP_DIR) -> bytes:
    """A gzipped tar shaped like GitHub's: a global pax header, then `<top>/...` members."""
    buffer = io.BytesIO()
    with tarfile.open(
        fileobj=buffer, mode="w:gz", format=tarfile.PAX_FORMAT, pax_headers={"comment": HEAD_SHA}
    ) as tar:
        top_dir = tarfile.TarInfo(top + "/")
        top_dir.type = tarfile.DIRTYPE
        tar.addfile(top_dir)
        for member in members:
            info = tarfile.TarInfo(member.name if member.raw_name else f"{top}/{member.name}")
            info.type = member.kind
            info.linkname = member.linkname
            info.mode = 0o755
            if member.kind == tarfile.REGTYPE:
                info.size = len(member.data)
                tar.addfile(info, io.BytesIO(member.data))
            else:
                tar.addfile(info)
    return buffer.getvalue()


class _Zeros(io.RawIOBase):
    def readable(self) -> bool:
        return True

    def readinto(self, buffer: "bytearray | memoryview") -> int:  # type: ignore[override]
        view = memoryview(buffer)
        view[:] = b"\0" * len(view)
        return len(view)


def gzip_bomb(member_bytes: int, *, top: str = TOP_DIR) -> bytes:
    """A tiny tar.gz holding one huge member of zeros. Unread members still decompress, so only
    the decompressed-byte cap stops it."""
    buffer = io.BytesIO()
    with (
        gzip.GzipFile(fileobj=buffer, mode="wb", compresslevel=9) as unzipped,
        tarfile.open(fileobj=unzipped, mode="w|", format=tarfile.PAX_FORMAT) as tar,
    ):
        info = tarfile.TarInfo(f"{top}/padding.bin")
        info.size = member_bytes
        tar.addfile(info, io.BufferedReader(_Zeros()))
    return buffer.getvalue()


def blob(path: str, data: bytes, mode: str = "100644") -> TreeEntry:
    return TreeEntry(path=path, mode=mode, kind="blob", sha=git_blob_sha(data), size=len(data))


def tree_entries(files: Mapping[str, bytes]) -> list[TreeEntry]:
    return [blob(path, data) for path, data in files.items()]


class FakeArchiveSource:
    """Serves one tree, one archive and blobs; counts every call."""

    def __init__(
        self,
        files: Mapping[str, bytes],
        *,
        entries: Sequence[TreeEntry] | None = None,
        archive: bytes | None = None,
        blobs: Mapping[str, bytes] | None = None,
    ) -> None:
        self.entries = list(entries) if entries is not None else tree_entries(files)
        self.archive = archive if archive is not None else make_archive(regular(files))
        self.blobs = (
            dict(blobs) if blobs is not None else {git_blob_sha(d): d for d in files.values()}
        )
        self.tree_calls = 0
        self.download_calls = 0
        self.blob_requests: list[list[str]] = []
        self.fail_download: BaseException | None = None
        self.fail_blobs: BaseException | None = None
        self.gate: asyncio.Event | None = None

    async def fetch_tree(self, target: CheckoutTarget) -> RepoTree:
        self.tree_calls += 1
        if self.gate is not None:
            await self.gate.wait()
        return RepoTree(
            source=RepoName(target.owner, target.repo), sha=TREE_SHA, entries=tuple(self.entries)
        )

    async def download_archive(
        self, source: RepoName, sha: str, destination: Path, max_bytes: int
    ) -> None:
        self.download_calls += 1
        if self.fail_download is not None:
            destination.write_bytes(self.archive[: len(self.archive) // 2])
            raise self.fail_download
        if len(self.archive) > max_bytes:
            raise RepositoryTooLargeError("too large")
        with open(destination, "xb") as out:
            out.write(self.archive)

    async def fetch_blobs(
        self,
        source: RepoName,
        shas: Sequence[str],
        max_bytes: int,
        receive: Callable[[str, bytes], Awaitable[None]],
    ) -> None:
        self.blob_requests.append(list(shas))
        if self.fail_blobs is not None:
            raise self.fail_blobs
        for sha in shas:
            if sha in self.blobs:
                await receive(sha, self.blobs[sha])


def files_under(root: Path) -> list[str]:
    """Every file and directory under `root`, relative and slash-separated, links not followed."""
    found: list[str] = []
    for directory, dirs, files in os.walk(root, followlinks=False):
        rel = Path(directory).relative_to(root)
        found.extend((rel / name).as_posix() for name in dirs + files)
    return sorted(found)


def publish(root: Path, source: FakeArchiveSource, target: CheckoutTarget = TARGET) -> Checkout:
    """Fetch and publish a checkout synchronously; it stays on disk after the lease ends."""

    async def run() -> Checkout:
        async with CheckoutCache(root, source).lease(target) as checkout:
            return checkout

    return asyncio.run(run())


def make_dir_link(link: Path, target: Path) -> None:
    """A directory link: a junction on Windows (no admin rights needed), a symlink elsewhere."""
    if sys.platform == "win32":
        import _winapi

        _winapi.CreateJunction(str(target), str(link))
    else:
        os.symlink(target, link, target_is_directory=True)


def snapshot_tree(root: Path) -> dict[str, bytes]:
    """Relative path → bytes of every file under `root` (links not followed)."""
    found: dict[str, bytes] = {}
    for directory, _dirs, files in os.walk(root, followlinks=False):
        for name in files:
            path = Path(directory) / name
            found[path.relative_to(root).as_posix()] = path.read_bytes()
    return found
