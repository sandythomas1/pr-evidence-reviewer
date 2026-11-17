"""T6: oversized repositories stop early and leave nothing behind."""

from pathlib import Path

import httpx
import pytest

from pr_evidence_reviewer.checkout.archive import ArchiveLimits, git_blob_sha
from pr_evidence_reviewer.checkout.cache import CheckoutCache
from pr_evidence_reviewer.errors import RepositoryTooLargeError
from pr_evidence_reviewer.github.archive import GitHubArchiveSource
from tests.checkout.fakes import HEAD_SHA, TARGET, TREE_SHA, files_under, gzip_bomb
from tests.fakes import TOKEN
from tests.github.fake_archive import FakeGitHubArchive, streamed

pytestmark = pytest.mark.anyio

MIB = 1024 * 1024


def cache_for(tmp_path: Path, fake: FakeGitHubArchive, limits: ArchiveLimits) -> CheckoutCache:
    source = GitHubArchiveSource(lambda: TOKEN, transport=fake.transport())
    return CheckoutCache(tmp_path / "cache", source, limits=limits)


async def test_a_gzip_bomb_stops_at_the_cap_and_leaves_nothing(tmp_path: Path) -> None:
    fake = FakeGitHubArchive({"a.py": b"a = 1\n"})
    bomb = gzip_bomb(64 * MIB)
    assert len(bomb) < 256 * 1024
    fake.codeload = lambda _: streamed(200, bomb)

    with pytest.raises(RepositoryTooLargeError):
        async with cache_for(tmp_path, fake, ArchiveLimits(max_decompressed_bytes=4 * MIB)).lease(
            TARGET
        ):
            pass

    assert files_under(tmp_path / "cache") == ["checkouts"]


async def test_a_tree_with_too_many_entries_is_refused_before_downloading(tmp_path: Path) -> None:
    fake = FakeGitHubArchive({})
    items = [
        {"path": f"f{i}", "mode": "100644", "type": "blob", "sha": f"{i:040x}", "size": 1}
        for i in range(200_001)
    ]
    fake.routes[f"/repos/acme/shop/git/trees/{HEAD_SHA}"] = lambda _: httpx.Response(
        200, json={"sha": TREE_SHA, "tree": items, "truncated": False}
    )

    with pytest.raises(RepositoryTooLargeError):
        async with cache_for(tmp_path, fake, ArchiveLimits()).lease(TARGET):
            pass

    assert [r.url.path.rsplit("/", 2)[1] for r in fake.requests] == ["trees"]
    assert files_under(tmp_path / "cache") == ["checkouts"]


async def test_a_truncated_tree_is_refused(tmp_path: Path) -> None:
    fake = FakeGitHubArchive({"a.py": b"a = 1\n"})
    fake.truncated = True

    with pytest.raises(RepositoryTooLargeError):
        async with cache_for(tmp_path, fake, ArchiveLimits()).lease(TARGET):
            pass

    assert files_under(tmp_path / "cache") == ["checkouts"]


async def test_a_file_over_5_mib_is_listed_as_too_large_not_stored(tmp_path: Path) -> None:
    big = b"x" * (5 * MIB + 1)
    fake = FakeGitHubArchive({"small.py": b"s = 1\n", "huge.txt": big})

    async with cache_for(tmp_path, fake, ArchiveLimits()).lease(TARGET) as checkout:
        assert [f.path for f in checkout.manifest.files] == ["small.py"]
        assert [(s.path, s.reason) for s in checkout.manifest.skipped] == [
            ("huge.txt", "too_large")
        ]
        assert not (checkout.blobs_dir / git_blob_sha(big)).exists()

    assert not any("/git/blobs/" in r.url.path for r in fake.requests)
