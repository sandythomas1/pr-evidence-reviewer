"""AC2: a hostile repository is stored as inert blobs and nothing else (threats T1, T2)."""

import os
import re
import subprocess
import tarfile
import unicodedata
from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout.archive import TreeEntry, git_blob_sha
from pr_evidence_reviewer.checkout.cache import CheckoutCache, checkout_key
from tests.checkout.fakes import (
    HEAD_SHA,
    TARGET,
    FakeArchiveSource,
    Member,
    blob,
    files_under,
    make_archive,
    snapshot_tree,
)

pytestmark = pytest.mark.anyio

CAFE_NFC = unicodedata.normalize("NFC", "café.py")
CAFE_NFD = unicodedata.normalize("NFD", "café.py")

STORED = {
    "app.py": b"print('ok')\n",
    ".gitattributes": b"* filter=lfs diff=lfs merge=lfs -text\n",
    ".gitmodules": b'[submodule "lib"]\n\tpath = vendor/lib\n\turl = https://evil.example/x\n',
    "config": b"[core]\n\tfsmonitor = touch /tmp/pwned\n\thooksPath = .githooks\n",
    "CON.txt": b"reserved on Windows\n",
    "Foo.py": b"upper = 1\n",
    "foo.py": b"lower = 1\n",
    CAFE_NFC: b"nfc = 1\n",
    CAFE_NFD: b"nfd = 1\n",
}
UNSAFE = {
    ".git/hooks/pre-commit": b"#!/bin/sh\ntouch /tmp/pwned\n",
    ".git/config": b"[core]\n\tfsmonitor = touch /tmp/pwned\n",
    "../evil": b"escape\n",
    "/abs": b"absolute\n",
    "C:/x": b"drive\n",
    "a\b": b"backslash\n",
}
LINKS = {"up": "../../x", "passwd": "/etc/passwd"}
SENTINEL_FILES = {"keep.txt": b"must not change\n", "nested/deep.txt": b"also\n"}


def hostile_source() -> FakeArchiveSource:
    entries: list[TreeEntry] = [blob(p, d) for p, d in {**STORED, **UNSAFE}.items()]
    entries += [
        TreeEntry(path=p, mode="120000", kind="blob", sha=git_blob_sha(t.encode()), size=len(t))
        for p, t in LINKS.items()
    ]
    entries.append(TreeEntry(path="vendor/lib", mode="160000", kind="commit", sha="5" * 40))
    entries.append(blob("hard", b"hard link target\n"))
    members = [Member(p, d) for p, d in STORED.items()]
    members += [Member(p, d) for p, d in UNSAFE.items() if not p.startswith(("..", "/"))]
    members += [
        Member("../evil", b"escape\n", raw_name=True),
        Member("/abs", b"absolute\n", raw_name=True),
        Member("../../outside.txt", b"escape\n", raw_name=True),
        Member("hard", kind=tarfile.LNKTYPE, linkname="../../../etc/passwd"),
        Member("dev", kind=tarfile.CHRTYPE),
        Member("fifo", kind=tarfile.FIFOTYPE),
        *(Member(p, kind=tarfile.SYMTYPE, linkname=t) for p, t in LINKS.items()),
        Member("vendor/lib", kind=tarfile.DIRTYPE),
    ]
    return FakeArchiveSource({}, entries=entries, archive=make_archive(members), blobs={})


@pytest.fixture
def no_processes(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    calls: list[str] = []

    def refuse(name: str):  # type: ignore[no-untyped-def]
        def run(*_args: object, **_kwargs: object) -> None:
            calls.append(name)
            raise AssertionError(f"{name} must never run during a checkout")

        return run

    monkeypatch.setattr(subprocess, "Popen", refuse("subprocess.Popen"))
    monkeypatch.setattr(os, "system", refuse("os.system"))
    return calls


async def test_a_hostile_repository_leaves_only_our_files(
    tmp_path: Path, no_processes: list[str]
) -> None:
    sentinel = tmp_path / "sentinel"
    for name, data in SENTINEL_FILES.items():
        (sentinel / name).parent.mkdir(parents=True, exist_ok=True)
        (sentinel / name).write_bytes(data)
    before = snapshot_tree(sentinel)
    cache_root = tmp_path / "cache"

    async with CheckoutCache(cache_root, hostile_source()).lease(TARGET) as checkout:
        manifest = checkout.manifest

    key = checkout_key("acme", "shop", HEAD_SHA)
    blob_name = re.compile(rf"^checkouts/{key}/blobs/[0-9a-f]{{40}}$")
    ours = {
        "checkouts",
        f"checkouts/{key}",
        f"checkouts/{key}/blobs",
        f"checkouts/{key}/manifest.json",
        f"checkouts/{key}/last_used",
    }
    for name in files_under(cache_root):
        assert name in ours or blob_name.match(name), name
    assert sorted(os.listdir(checkout.blobs_dir)) == sorted(
        {git_blob_sha(d) for d in STORED.values()}
    )
    assert snapshot_tree(sentinel) == before
    assert not (tmp_path / "outside.txt").exists()
    assert no_processes == []

    assert sorted(f.path for f in manifest.files) == sorted(STORED)
    reasons = {s.path: s.reason for s in manifest.skipped}
    assert reasons == {
        **dict.fromkeys(UNSAFE, "unsafe_name"),
        **dict.fromkeys(LINKS, "link"),
        "vendor/lib": "submodule",
        "hard": "unverified",  # its member is a hard link, never read; the refetch got nothing
    }


async def test_case_and_unicode_variants_stay_distinct(tmp_path: Path) -> None:
    async with CheckoutCache(tmp_path / "cache", hostile_source()).lease(TARGET) as checkout:
        by_path = {f.path: f.blob_sha for f in checkout.manifest.files}

    assert by_path["Foo.py"] == git_blob_sha(STORED["Foo.py"])
    assert by_path["foo.py"] == git_blob_sha(STORED["foo.py"])
    assert by_path[CAFE_NFC] != by_path[CAFE_NFD]
