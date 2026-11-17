import json
import os
import time
from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout.archive import git_blob_sha
from pr_evidence_reviewer.checkout.cache import (
    CheckoutCache,
    CheckoutTarget,
    _EntryInfo,
    checkout_key,
    select_victims,
)
from pr_evidence_reviewer.errors import CheckoutUnavailableError
from tests.checkout.fakes import HEAD_SHA, TARGET, TREE_SHA, FakeArchiveSource, files_under

pytestmark = pytest.mark.anyio

FILES = {"src/app.py": b"print('hi')\n", "README.md": b"# shop\n"}


class Clock:
    def __init__(self) -> None:
        self.now = time.time()

    def __call__(self) -> float:
        return self.now


def cache_for(tmp_path: Path, source: FakeArchiveSource, **options: object) -> CheckoutCache:
    return CheckoutCache(tmp_path / "cache", source, **options)  # type: ignore[arg-type]


def test_key_is_case_insensitive_on_owner_and_repo_and_hides_the_names() -> None:
    key = checkout_key("Acme", "Shop", HEAD_SHA)

    assert key == checkout_key("acme", "shop", HEAD_SHA)
    assert key != checkout_key("acme", "shop", "d" * 40)
    assert len(key) == 32
    assert int(key, 16) >= 0
    assert "acme" not in key


async def test_publishes_only_our_own_names(tmp_path: Path) -> None:
    source = FakeArchiveSource(FILES)

    async with cache_for(tmp_path, source).lease(TARGET) as checkout:
        assert checkout.manifest.sha == HEAD_SHA
        assert checkout.manifest.tree_sha == TREE_SHA
        assert [f.path for f in checkout.manifest.files] == ["README.md", "src/app.py"]

    key = checkout_key("acme", "shop", HEAD_SHA)
    blobs = sorted(f"checkouts/{key}/blobs/{git_blob_sha(d)}" for d in FILES.values())
    assert files_under(tmp_path / "cache") == sorted(
        [
            "checkouts",
            f"checkouts/{key}",
            f"checkouts/{key}/blobs",
            f"checkouts/{key}/last_used",
            f"checkouts/{key}/manifest.json",
            *blobs,
        ]
    )


async def test_missing_files_are_refetched_and_verified(tmp_path: Path) -> None:
    files = {"kept.py": b"k = 1\n", "export_ignored.py": b"e = 1\n"}
    source = FakeArchiveSource(files, archive=FakeArchiveSource({"kept.py": b"k = 1\n"}).archive)

    async with cache_for(tmp_path, source).lease(TARGET) as checkout:
        assert [f.path for f in checkout.manifest.files] == ["export_ignored.py", "kept.py"]

    assert source.blob_requests == [[git_blob_sha(b"e = 1\n")]]


async def test_a_failed_fetch_leaves_no_entry_and_no_work_dir(tmp_path: Path) -> None:
    source = FakeArchiveSource(FILES)
    source.fail_download = CheckoutUnavailableError("down")

    with pytest.raises(CheckoutUnavailableError):
        async with cache_for(tmp_path, source).lease(TARGET):
            pass

    assert files_under(tmp_path / "cache") == ["checkouts"]


async def test_a_lease_touches_last_used(tmp_path: Path) -> None:
    clock = Clock()
    cache = cache_for(tmp_path, FakeArchiveSource(FILES), clock=clock)
    async with cache.lease(TARGET) as checkout:
        last_used = checkout.path / "last_used"
    first = last_used.stat().st_mtime

    clock.now += 3600
    async with cache.lease(TARGET):
        pass

    assert last_used.stat().st_mtime == pytest.approx(first + 3600, abs=1)


async def test_a_manifest_for_another_repo_is_replaced(tmp_path: Path) -> None:
    source = FakeArchiveSource(FILES)
    cache = cache_for(tmp_path, source)
    async with cache.lease(TARGET) as checkout:
        manifest_path = checkout.path / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["repo"] = "other"
    manifest_path.write_text(json.dumps(manifest))

    async with cache_for(tmp_path, source).lease(TARGET) as checkout:
        assert checkout.manifest.repo == "shop"

    assert source.download_calls == 2


async def test_a_race_loser_discards_its_copy_and_uses_the_winner(tmp_path: Path) -> None:
    source = FakeArchiveSource(FILES)
    first, second = cache_for(tmp_path, source), cache_for(tmp_path, source)
    async with first.lease(TARGET) as winner:
        winner_path = winner.path

    other = CheckoutTarget(owner="ACME", repo="shop", sha=HEAD_SHA)
    second._started = True  # skip startup retention; simulate a second process racing
    published = second._publish(
        checkout_key("acme", "shop", HEAD_SHA),
        second._make_work_dir(),
        winner.manifest,
        other,
    )

    assert published.path == winner_path
    assert [p for p in os.listdir(tmp_path / "cache" / "checkouts") if p.startswith(".")] == []


async def test_startup_sweeps_stale_work_and_trash_dirs(tmp_path: Path) -> None:
    checkouts = tmp_path / "cache" / "checkouts"
    for name in (".tmp-old", ".trash-old", ".tmp-fresh"):
        (checkouts / name / "blobs").mkdir(parents=True)
        (checkouts / name / "blobs" / ("e" * 40)).write_bytes(b"x")
    old = time.time() - 7200
    for name in (".tmp-old", ".trash-old"):
        os.utime(checkouts / name, (old, old))

    async with cache_for(tmp_path, FakeArchiveSource(FILES)).lease(TARGET):
        pass

    leftovers = sorted(p for p in os.listdir(checkouts) if p.startswith("."))
    assert leftovers == [".tmp-fresh"]


async def test_clear_deletes_checkouts_and_optionally_packets(tmp_path: Path) -> None:
    cache = cache_for(tmp_path, FakeArchiveSource(FILES))
    async with cache.lease(TARGET):
        pass
    packets = tmp_path / "data" / "packets" / "abc" / HEAD_SHA
    packets.mkdir(parents=True)
    (packets / "p.json").write_text("{}")

    result = cache.clear(tmp_path / "data" / "packets")

    assert result.checkouts == 1
    assert result.checkout_bytes > sum(len(data) for data in FILES.values())
    assert (result.packets, result.packet_bytes) == (1, 2)
    assert files_under(tmp_path / "cache") == ["checkouts"]
    assert not (tmp_path / "data" / "packets").exists()


def test_clear_on_a_missing_cache_is_a_no_op(tmp_path: Path) -> None:
    result = cache_for(tmp_path, FakeArchiveSource(FILES)).clear(tmp_path / "packets")

    assert (result.checkouts, result.packets) == (0, 0)
    assert not (tmp_path / "cache").exists()


def info(key: str, last_used: float, total: int = 10, valid: bool = True) -> _EntryInfo:
    return _EntryInfo(key=key, last_used=last_used, total_bytes=total, valid=valid)


def test_retention_drops_invalid_and_expired_then_oldest_over_budget() -> None:
    now = 1_000_000.0
    entries = [
        info("bad", now, valid=False),
        info("expired", now - 8 * 86400),
        info("old", now - 300),
        info("mid", now - 200),
        info("new", now - 100),
    ]

    victims = select_victims(entries, leased=(), now=now, ttl_seconds=7 * 86400, max_bytes=20)

    assert [(v.key, reason) for v, reason in victims] == [
        ("bad", "invalid"),
        ("expired", "ttl"),
        ("old", "lru"),
    ]


def test_retention_never_picks_a_leased_checkout() -> None:
    now = 1_000_000.0
    entries = [info("oldest", now - 900), info("expired", now - 30 * 86400), info("new", now)]

    victims = select_victims(
        entries, leased={"oldest", "expired"}, now=now, ttl_seconds=86400, max_bytes=5
    )

    assert [(v.key, reason) for v, reason in victims] == [("new", "lru")]
