import os
import stat
import sys
import tarfile
from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout.archive import (
    ArchiveLimits,
    BlobStore,
    ExtractionResult,
    RepoName,
    RepoTree,
    TreeEntry,
    build_manifest,
    classify,
    extract_archive,
    git_blob_sha,
    plan_refetch,
    plan_tree,
    store_refetched,
)
from pr_evidence_reviewer.errors import CheckoutUnavailableError, RepositoryTooLargeError
from tests.checkout.fakes import (
    HEAD_SHA,
    TREE_SHA,
    Member,
    blob,
    gzip_bomb,
    make_archive,
    regular,
    tree_entries,
)

LIMITS = ArchiveLimits()


def tree_of(entries: list[TreeEntry]) -> RepoTree:
    return RepoTree(source=RepoName("acme", "shop"), sha=TREE_SHA, entries=tuple(entries))


def run_extract(
    tmp_path: Path, files: dict[str, bytes], archive: bytes, limits: ArchiveLimits = LIMITS
) -> tuple[BlobStore, ExtractionResult, Path]:
    plan = plan_tree(tree_of(tree_entries(files)), limits)
    blobs = tmp_path / "blobs"
    blobs.mkdir()
    archive_path = tmp_path / "archive.download"
    archive_path.write_bytes(archive)
    store = BlobStore(blobs, limits.max_total_stored_bytes)
    return store, extract_archive(archive_path, plan, store, limits), blobs


def test_git_blob_sha_matches_git() -> None:
    assert git_blob_sha(b"") == "e69de29bb2d1d6434b8b29ae775ad8c2e48c5391"
    assert git_blob_sha(b"hello\n") == "ce013625030ba8dba906f756967f9e9ca394464a"


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (b"print('hi')\n", "text"),
        (b"", "text"),
        (b"\x89PNG\r\n\x1a\n\0\0", "binary"),
        (b"version https://git-lfs.github.com/spec/v1\noid sha256:abc\nsize 12\n", "lfs"),
        (b"caf\xe9\n", "undecodable"),
    ],
)
def test_classifies_stored_files(data: bytes, kind: str) -> None:
    assert classify(data) == kind


def test_plans_candidates_and_records_a_reason_for_every_other_entry() -> None:
    entries = [
        blob("src/app.py", b"x"),
        blob("bin/run", b"#!/bin/sh\n", mode="100755"),
        TreeEntry(path="src", mode="040000", kind="tree", sha="1" * 40),
        TreeEntry(path="link", mode="120000", kind="blob", sha="2" * 40, size=3),
        TreeEntry(path="vendor/lib", mode="160000", kind="commit", sha="3" * 40),
        blob(".git/hooks/pre-commit", b"evil"),
        blob("../evil", b"evil"),
        blob("big.bin", b"x" * 11),
    ]

    plan = plan_tree(tree_of(entries), ArchiveLimits(max_file_bytes=10))

    assert sorted(plan.candidates) == ["bin/run", "src/app.py"]
    assert sorted((skip.path, skip.reason) for skip in plan.skipped) == [
        ("../evil", "unsafe_name"),
        (".git/hooks/pre-commit", "unsafe_name"),
        ("big.bin", "too_large"),
        ("link", "link"),
        ("vendor/lib", "submodule"),
    ]


def test_a_path_listed_twice_is_skipped_as_a_duplicate() -> None:
    plan = plan_tree(tree_of([blob("a.py", b"1"), blob("a.py", b"2"), blob("b.py", b"3")]), LIMITS)

    assert list(plan.candidates) == ["b.py"]
    assert [(skip.path, skip.reason) for skip in plan.skipped] == [("a.py", "duplicate")]


def test_refuses_a_tree_over_the_entry_limit_before_downloading() -> None:
    entries = tree_entries({f"f{i}.py": b"x" for i in range(4)})

    with pytest.raises(RepositoryTooLargeError):
        plan_tree(tree_of(entries), ArchiveLimits(max_tree_entries=3))


def test_refuses_a_tree_whose_files_exceed_the_stored_total() -> None:
    entries = tree_entries({"a.bin": b"x" * 6, "b.bin": b"y" * 6})

    with pytest.raises(RepositoryTooLargeError):
        plan_tree(tree_of(entries), ArchiveLimits(max_total_stored_bytes=10))


def test_stores_verified_files_by_blob_sha_only(tmp_path: Path) -> None:
    files = {"src/app.py": b"print('hi')\n", "README.md": b"# shop\n"}

    store, result, blobs = run_extract(tmp_path, files, make_archive(regular(files)))

    assert sorted(os.listdir(blobs)) == sorted(git_blob_sha(data) for data in files.values())
    for data in files.values():
        assert (blobs / git_blob_sha(data)).read_bytes() == data
    assert result.altered == set()
    assert store.total_bytes == sum(len(data) for data in files.values())


def test_identical_files_share_one_blob(tmp_path: Path) -> None:
    files = {"a/__init__.py": b"", "b/__init__.py": b""}

    store, _, blobs = run_extract(tmp_path, files, make_archive(regular(files)))

    assert os.listdir(blobs) == [git_blob_sha(b"")]
    assert store.has(git_blob_sha(b""))


def test_a_member_that_differs_from_the_tree_is_not_stored(tmp_path: Path) -> None:
    files = {"version.txt": b"$Format:%H$\n"}
    archive = make_archive([Member("version.txt", b"0123456789ab\n")])

    store, result, blobs = run_extract(tmp_path, files, archive)

    assert os.listdir(blobs) == []
    assert result.altered == {"version.txt"}
    assert not store.has(git_blob_sha(files["version.txt"]))


def test_links_devices_and_unlisted_members_are_never_read(tmp_path: Path) -> None:
    files = {"a.py": b"a = 1\n"}
    archive = make_archive(
        [
            Member("a.py", kind=tarfile.SYMTYPE, linkname="/etc/passwd"),
            Member("hard", kind=tarfile.LNKTYPE, linkname="a.py"),
            Member("dev", kind=tarfile.CHRTYPE),
            Member("fifo", kind=tarfile.FIFOTYPE),
            Member("unlisted.py", b"secret = 1\n"),
            Member("../a.py", b"a = 1\n", raw_name=True),
        ]
    )

    store, _, blobs = run_extract(tmp_path, files, archive)

    assert os.listdir(blobs) == []
    assert store.total_bytes == 0


def test_a_damaged_archive_is_reported_without_details(tmp_path: Path) -> None:
    files = {"a.py": b"a = 1\n"}

    with pytest.raises(CheckoutUnavailableError) as raised:
        run_extract(tmp_path, files, make_archive(regular(files))[:40])

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None


def test_stops_at_the_decompressed_byte_cap(tmp_path: Path) -> None:
    limits = ArchiveLimits(max_decompressed_bytes=1024 * 1024)

    with pytest.raises(RepositoryTooLargeError):
        run_extract(tmp_path, {"a.py": b"a"}, gzip_bomb(8 * 1024 * 1024), limits)


def test_stops_at_the_total_stored_cap(tmp_path: Path) -> None:
    store = BlobStore(tmp_path, max_total_bytes=4)

    store.add(git_blob_sha(b"abc"), b"abc")
    with pytest.raises(RepositoryTooLargeError):
        store.add(git_blob_sha(b"de"), b"de")


def test_refetch_is_capped_and_in_path_order(tmp_path: Path) -> None:
    files = {f"f{i:02}.txt": f"{i}".encode() for i in range(5)}
    plan = plan_tree(tree_of(tree_entries(files)), LIMITS)
    store = BlobStore(tmp_path, LIMITS.max_total_stored_bytes)

    refetch = plan_refetch(plan, store, ArchiveLimits(max_refetch=3))

    assert refetch.shas == tuple(git_blob_sha(f"{i}".encode()) for i in range(3))
    assert refetch.capped is True


def test_refetched_bytes_are_verified_before_storing(tmp_path: Path) -> None:
    store = BlobStore(tmp_path, LIMITS.max_total_stored_bytes)
    good, bad = git_blob_sha(b"good"), git_blob_sha(b"bad")

    wanted = {good, bad}
    stored = [
        store_refetched(store, sha, data, wanted, LIMITS)
        for sha, data in ((good, b"good"), (bad, b"tampered"))
    ]

    assert stored == [True, False]
    assert store.has(good)
    assert not store.has(bad)


def test_manifest_lists_files_and_every_skip_sorted(tmp_path: Path) -> None:
    files = {"b.py": b"b\n", "a.png": b"\0png", "c.txt": b"$Format$", "d.txt": b"gone"}
    entries = [*tree_entries(files), blob(".git/config", b"x")]
    plan = plan_tree(tree_of(entries), LIMITS)
    store = BlobStore(tmp_path, LIMITS.max_total_stored_bytes)
    store.add(git_blob_sha(b"b\n"), b"b\n")
    store.add(git_blob_sha(b"\0png"), b"\0png")
    refetch = plan_refetch(plan, store, ArchiveLimits(max_refetch=1))

    manifest = build_manifest(
        owner="Acme",
        repo="Shop",
        sha=HEAD_SHA,
        tree_sha=TREE_SHA,
        plan=plan,
        store=store,
        extraction=ExtractionResult(altered=set()),
        refetch=refetch,
    )

    assert (manifest.owner, manifest.repo) == ("acme", "shop")
    assert [(f.path, f.kind) for f in manifest.files] == [("a.png", "binary"), ("b.py", "text")]
    assert [(s.path, s.reason) for s in manifest.skipped] == [
        (".git/config", "unsafe_name"),
        ("c.txt", "unverified"),
        ("d.txt", "export_ignored"),
    ]
    assert manifest.refetch_capped is True
    assert manifest.total_bytes == 6


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_blobs_are_owner_only(tmp_path: Path) -> None:
    files = {"a.py": b"a = 1\n"}

    _, _, blobs = run_extract(tmp_path, files, make_archive(regular(files)))

    mode = (blobs / git_blob_sha(files["a.py"])).stat().st_mode
    assert stat.S_IMODE(mode) & 0o077 == 0
