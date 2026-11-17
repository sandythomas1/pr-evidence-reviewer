"""T9/T11 and AC1: cache keys, single flight, retention and link-safe eviction."""

import asyncio
import json
import os
import sys
import time
from datetime import timedelta
from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout.cache import CheckoutCache, CheckoutTarget, checkout_key
from pr_evidence_reviewer.errors import CheckoutUnavailableError
from tests.checkout.fakes import (
    HEAD_SHA,
    TARGET,
    FakeArchiveSource,
    files_under,
    make_dir_link,
    snapshot_tree,
)

pytestmark = pytest.mark.anyio

FILES = {"a.py": b"a = 1\n", "b.py": b"b = 2\n"}
ENTRY_BYTES = sum(len(data) for data in FILES.values())
DAY = 86400.0


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


def target(n: int) -> CheckoutTarget:
    return CheckoutTarget(owner="acme", repo="shop", sha=f"{n:040x}")


def entry_dir(root: Path, t: CheckoutTarget) -> Path:
    return root / "checkouts" / checkout_key(t.owner, t.repo, t.sha)


@pytest.fixture
def sentinel(tmp_path: Path) -> Path:
    folder = tmp_path / "sentinel"
    (folder / "inner").mkdir(parents=True)
    (folder / "keep.txt").write_bytes(b"keep\n")
    (folder / "inner" / "deep.txt").write_bytes(b"deep\n")
    return folder


async def test_ac1_a_second_review_of_the_same_commit_downloads_nothing(tmp_path: Path) -> None:
    source = FakeArchiveSource(FILES)
    cache = CheckoutCache(tmp_path / "cache", source)

    async with cache.lease(TARGET):
        pass
    async with cache.lease(TARGET) as again:
        assert again.manifest.sha == HEAD_SHA

    assert (source.tree_calls, source.download_calls, source.blob_requests) == (1, 1, [])


async def test_owner_and_repo_case_share_one_checkout(tmp_path: Path) -> None:
    source = FakeArchiveSource(FILES)
    cache = CheckoutCache(tmp_path / "cache", source)

    async with cache.lease(CheckoutTarget("Acme", "Shop", HEAD_SHA)):
        pass
    async with cache.lease(CheckoutTarget("acme", "shop", HEAD_SHA)):
        pass

    assert source.download_calls == 1
    assert len(os.listdir(tmp_path / "cache" / "checkouts")) == 1


async def test_two_concurrent_leases_fetch_once(tmp_path: Path) -> None:
    source = FakeArchiveSource(FILES)
    source.gate = asyncio.Event()
    cache = CheckoutCache(tmp_path / "cache", source)

    async def review() -> str:
        async with cache.lease(TARGET) as checkout:
            return checkout.manifest.sha

    tasks = [asyncio.create_task(review()) for _ in range(2)]
    await asyncio.sleep(0.05)
    source.gate.set()

    assert await asyncio.gather(*tasks) == [HEAD_SHA, HEAD_SHA]
    assert source.download_calls == 1


async def test_a_manifest_with_the_wrong_commit_is_refetched(tmp_path: Path) -> None:
    source = FakeArchiveSource(FILES)
    async with CheckoutCache(tmp_path / "cache", source).lease(TARGET) as checkout:
        manifest_path = checkout.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["sha"] = "f" * 40
    manifest_path.write_text(json.dumps(manifest))

    async with CheckoutCache(tmp_path / "cache", source).lease(TARGET) as checkout:
        assert checkout.manifest.sha == HEAD_SHA

    assert source.download_calls == 2


@pytest.mark.parametrize("failure", ["download", "blobs"])
async def test_a_failure_midway_leaves_no_entry(tmp_path: Path, failure: str) -> None:
    source = FakeArchiveSource(FILES, archive=FakeArchiveSource({}).archive)
    error = CheckoutUnavailableError("down")
    if failure == "download":
        source.fail_download = error
    else:
        source.fail_blobs = error

    with pytest.raises(CheckoutUnavailableError):
        async with CheckoutCache(tmp_path / "cache", source).lease(TARGET):
            pass

    assert files_under(tmp_path / "cache") == ["checkouts"]


async def test_lru_evicts_the_oldest_unleased_checkout_never_a_leased_one(tmp_path: Path) -> None:
    clock = Clock()
    root = tmp_path / "cache"
    cache = CheckoutCache(root, FakeArchiveSource(FILES), max_bytes=ENTRY_BYTES, clock=clock)

    async with cache.lease(target(1)):  # oldest, and held for the whole test
        clock.now += 60
        async with cache.lease(target(2)):
            pass
        clock.now += 60
        async with cache.lease(target(3)):
            assert entry_dir(root, target(1)).exists()
            assert not entry_dir(root, target(2)).exists()
            assert entry_dir(root, target(3)).exists()


async def test_ttl_expiry_is_enforced_at_startup(tmp_path: Path) -> None:
    clock = Clock()
    root = tmp_path / "cache"
    source = FakeArchiveSource(FILES)
    async with CheckoutCache(root, source, clock=clock).lease(target(1)):
        pass

    clock.now += 8 * DAY
    async with CheckoutCache(root, source, ttl=timedelta(days=7), clock=clock).lease(target(2)):
        assert not entry_dir(root, target(1)).exists()


async def test_ttl_expiry_is_enforced_on_acquire(tmp_path: Path) -> None:
    clock = Clock()
    source = FakeArchiveSource(FILES)
    cache = CheckoutCache(tmp_path / "cache", source, ttl=timedelta(days=7), clock=clock)
    async with cache.lease(TARGET):
        pass

    clock.now += 8 * DAY
    async with cache.lease(TARGET):
        pass

    assert source.download_calls == 2


@pytest.mark.parametrize("linked", ["root", "checkouts"])
async def test_a_linked_cache_folder_is_refused(
    tmp_path: Path, sentinel: Path, linked: str
) -> None:
    root = tmp_path / "cache"
    if linked == "root":
        make_dir_link(root, sentinel)
    else:
        root.mkdir()
        make_dir_link(root / "checkouts", sentinel)
    before = snapshot_tree(sentinel)
    source = FakeArchiveSource(FILES)

    with pytest.raises(CheckoutUnavailableError, match="link"):
        async with CheckoutCache(root, source).lease(TARGET):
            pass
    with pytest.raises(CheckoutUnavailableError, match="link"):
        CheckoutCache(root, source).clear()

    assert source.download_calls == 0
    assert snapshot_tree(sentinel) == before


async def test_a_link_planted_inside_a_checkout_is_evicted_without_touching_its_target(
    tmp_path: Path, sentinel: Path
) -> None:
    clock = Clock()
    root = tmp_path / "cache"
    cache = CheckoutCache(root, FakeArchiveSource(FILES), clock=clock)
    async with cache.lease(target(1)) as checkout:
        planted = checkout.path
    make_dir_link(planted / "blobs" / "planted", sentinel)
    before = snapshot_tree(sentinel)

    clock.now += 8 * DAY
    async with cache.lease(target(2)):
        pass

    assert not os.path.lexists(planted)
    assert snapshot_tree(sentinel) == before


async def test_a_checkout_that_is_itself_a_link_is_dropped_not_followed(
    tmp_path: Path, sentinel: Path
) -> None:
    root = tmp_path / "cache"
    (root / "checkouts").mkdir(parents=True)
    linked = entry_dir(root, TARGET)
    make_dir_link(linked, sentinel)
    before = snapshot_tree(sentinel)
    source = FakeArchiveSource(FILES)

    async with CheckoutCache(root, source).lease(TARGET) as checkout:
        assert not checkout.path.is_symlink()
        assert not checkout.path.is_junction()

    assert source.download_calls == 1
    assert snapshot_tree(sentinel) == before


async def test_clear_unlinks_a_planted_link_without_following_it(
    tmp_path: Path, sentinel: Path
) -> None:
    root = tmp_path / "cache"
    cache = CheckoutCache(root, FakeArchiveSource(FILES))
    async with cache.lease(TARGET) as checkout:
        make_dir_link(checkout.path / "blobs" / "planted", sentinel)
    before = snapshot_tree(sentinel)

    result = cache.clear()

    assert result.checkouts == 1
    assert files_under(root) == ["checkouts"]
    assert snapshot_tree(sentinel) == before


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
async def test_cache_folders_are_owner_only(tmp_path: Path) -> None:
    root = tmp_path / "cache"
    async with CheckoutCache(root, FakeArchiveSource(FILES)).lease(TARGET):
        pass

    assert root.stat().st_mode & 0o077 == 0
    assert (root / "checkouts").stat().st_mode & 0o077 == 0
