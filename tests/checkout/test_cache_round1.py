"""Review round 1 for the checkout cache: refetches have a total byte budget and are stored as they
arrive (S1), every lease applies retention (S4), `cache clear` reports what it couldn't delete (S6),
and a cache that can't be written fails as unavailable, not with a crash (S7)."""

import shutil
from datetime import timedelta
from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout import cache as cache_module
from pr_evidence_reviewer.checkout.archive import (
    ArchiveLimits,
    BlobStore,
    git_blob_sha,
    plan_refetch,
    plan_tree,
)
from pr_evidence_reviewer.checkout.cache import CACHE_UNWRITABLE, CheckoutCache, CheckoutTarget
from pr_evidence_reviewer.cli import main
from pr_evidence_reviewer.errors import CheckoutUnavailableError
from tests.checkout.fakes import TARGET, FakeArchiveSource, files_under, tree_entries
from tests.checkout.test_archive import tree_of
from tests.checkout.test_cache import FILES, Clock
from tests.test_cli import seed_cache, settings_in

IGNORED = {"a.txt": b"a" * 40, "b.txt": b"b" * 40, "c.txt": b"c" * 40}


def test_refetch_stops_at_the_byte_budget_and_lists_the_rest(tmp_path: Path) -> None:
    limits = ArchiveLimits(max_refetch_bytes=90)
    plan = plan_tree(tree_of(tree_entries(IGNORED)), limits)

    refetch = plan_refetch(plan, BlobStore(tmp_path, limits.max_total_stored_bytes), limits)

    assert refetch.shas == (git_blob_sha(IGNORED["a.txt"]), git_blob_sha(IGNORED["b.txt"]))
    assert refetch.over_budget == (git_blob_sha(IGNORED["c.txt"]),)
    assert refetch.capped is True


@pytest.mark.anyio
async def test_files_past_the_refetch_budget_are_unverified_and_never_requested(
    tmp_path: Path,
) -> None:
    source = FakeArchiveSource(IGNORED, archive=FakeArchiveSource({}).archive)
    cache = CheckoutCache(tmp_path / "cache", source, limits=ArchiveLimits(max_refetch_bytes=90))

    async with cache.lease(TARGET) as checkout:
        manifest = checkout.manifest

    assert [file.path for file in manifest.files] == ["a.txt", "b.txt"]
    assert [(skip.path, skip.reason) for skip in manifest.skipped] == [("c.txt", "unverified")]
    assert manifest.refetch_capped is True
    assert source.blob_requests == [
        [git_blob_sha(IGNORED["a.txt"]), git_blob_sha(IGNORED["b.txt"])]
    ]


@pytest.mark.anyio
async def test_a_reused_copy_still_applies_retention_to_the_others(tmp_path: Path) -> None:
    clock = Clock()
    other = CheckoutTarget(owner="acme", repo="shop", sha="f" * 40)
    sources = FakeArchiveSource(FILES)
    cache = CheckoutCache(tmp_path / "cache", sources, ttl=timedelta(days=1), clock=clock)
    async with cache.lease(other) as stale:
        stale_path = stale.path
    clock.now += 12 * 3600
    async with cache.lease(TARGET):
        pass

    clock.now += 13 * 3600
    async with cache.lease(TARGET):
        pass

    assert not stale_path.exists()


def _failing_rmtree(path: object, *args: object, **kwargs: object) -> None:
    raise OSError("in use")


def test_clear_counts_copies_it_couldnt_delete(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed_cache(tmp_path)
    monkeypatch.setattr(cache_module.shutil, "rmtree", _failing_rmtree)

    result = CheckoutCache(tmp_path / "cache", FakeArchiveSource({})).clear()

    assert (result.checkouts, result.failed) == (0, 1)


def test_cache_clear_says_what_it_couldnt_delete_and_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    seed_cache(tmp_path)
    monkeypatch.setattr(shutil, "rmtree", _failing_rmtree)

    assert main(["cache", "clear"], base_settings=settings_in(tmp_path)) == 1

    assert "Couldn't delete 1 saved repository copy." in capsys.readouterr().err


def _unwritable(*args: object, **kwargs: object) -> None:
    raise OSError("disk full")


@pytest.mark.anyio
async def test_a_checkout_that_cant_be_saved_is_unavailable_and_leaves_nothing(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(cache_module, "_write_new", _unwritable)

    with pytest.raises(CheckoutUnavailableError, match=CACHE_UNWRITABLE):
        async with CheckoutCache(tmp_path / "cache", FakeArchiveSource(FILES)).lease(TARGET):
            pass

    assert files_under(tmp_path / "cache") == ["checkouts"]


@pytest.mark.anyio
async def test_a_reused_copy_that_cant_be_marked_used_is_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    cache = CheckoutCache(tmp_path / "cache", FakeArchiveSource(FILES))
    async with cache.lease(TARGET):
        pass
    monkeypatch.setattr(cache_module.os, "utime", _unwritable)

    with pytest.raises(CheckoutUnavailableError, match=CACHE_UNWRITABLE):
        async with cache.lease(TARGET):
            pass
