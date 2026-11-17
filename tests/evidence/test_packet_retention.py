"""Review S9: stored packets share the checkouts' 7-day retention setting and are removed by the
same pass, never through a link."""

import logging
import os
from datetime import timedelta
from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout.cache import CheckoutCache
from pr_evidence_reviewer.config import DEFAULT_RETENTION_TTL, Settings
from pr_evidence_reviewer.evidence.store import EXPIRY_INTERVAL, PacketStore, repo_key
from tests.checkout.fakes import TARGET, FakeArchiveSource, files_under, make_dir_link
from tests.checkout.test_cache import FILES, Clock
from tests.evidence.test_store import REPO, packet

DAY = 86400.0


def _saved(root: Path, clock: Clock) -> Path:
    store = PacketStore(root, clock=clock)
    built = packet()
    assert store.save(built)
    path = store.path_for(REPO, built.snapshot.head_sha, built.content_hash())
    os.utime(path, (clock.now, clock.now))
    return path


def test_packets_and_checkouts_share_one_seven_day_retention_setting() -> None:
    assert timedelta(days=7) == DEFAULT_RETENTION_TTL
    assert Settings().retention_ttl == DEFAULT_RETENTION_TTL


def test_an_expired_packet_is_removed_with_its_empty_folders(tmp_path: Path) -> None:
    clock = Clock()
    root = tmp_path / "packets"
    _saved(root, clock)
    clock.now += 8 * DAY

    assert PacketStore(root, clock=clock).remove_expired() == 1

    assert files_under(root) == []


def test_a_fresh_packet_is_kept(tmp_path: Path) -> None:
    clock = Clock()
    root = tmp_path / "packets"
    path = _saved(root, clock)
    clock.now += 6 * DAY

    assert PacketStore(root, clock=clock).remove_expired() == 0

    assert path.exists()


def test_saving_an_expired_packet_again_writes_it_fresh(tmp_path: Path) -> None:
    clock = Clock()
    root = tmp_path / "packets"
    path = _saved(root, clock)
    clock.now += 8 * DAY

    assert PacketStore(root, clock=clock).save(packet())

    assert path.exists()
    assert path.stat().st_mtime > clock.now - DAY


def test_a_linked_packets_root_is_refused(tmp_path: Path) -> None:
    clock = Clock()
    elsewhere = tmp_path / "elsewhere"
    _saved(elsewhere, clock)
    root = tmp_path / "packets"
    make_dir_link(root, elsewhere)
    clock.now += 8 * DAY

    assert PacketStore(root, clock=clock).remove_expired() == 0

    assert len(files_under(elsewhere)) == 3


def test_a_linked_repository_folder_is_skipped_not_followed(tmp_path: Path) -> None:
    clock = Clock()
    elsewhere = tmp_path / "elsewhere"
    _saved(elsewhere, clock)
    root = tmp_path / "packets"
    root.mkdir()
    make_dir_link(root / "0123456789abcdef", elsewhere)
    clock.now += 8 * DAY

    assert PacketStore(root, clock=clock).remove_expired() == 0

    assert len(files_under(elsewhere)) == 3


@pytest.mark.anyio
async def test_the_checkout_retention_pass_also_expires_packets(tmp_path: Path) -> None:
    clock = Clock()
    root = tmp_path / "packets"
    _saved(root, clock)
    clock.now += 8 * DAY
    packets = PacketStore(root, clock=clock)
    cache = CheckoutCache(
        tmp_path / "cache",
        FakeArchiveSource(FILES),
        clock=clock,
        also_retain=packets.remove_expired_if_due,
    )

    async with cache.lease(TARGET):
        pass

    assert files_under(root) == []


@pytest.mark.anyio
async def test_a_packet_saved_while_running_expires_in_a_later_retention_pass(
    tmp_path: Path,
) -> None:
    clock = Clock()
    root = tmp_path / "packets"
    packets = PacketStore(root, clock=clock)
    cache = CheckoutCache(
        tmp_path / "cache",
        FakeArchiveSource(FILES),
        clock=clock,
        also_retain=packets.remove_expired_if_due,
    )
    async with cache.lease(TARGET):
        pass
    _saved(root, clock)

    clock.now += 6 * DAY
    async with cache.lease(TARGET):
        pass
    assert len(files_under(root)) == 3

    clock.now += 2 * DAY
    async with cache.lease(TARGET):
        pass
    assert files_under(root) == []


def _plant_expired(root: Path, name: str, clock: Clock) -> Path:
    """A packet file in its own commit folder, written eight days ago."""
    path = root / repo_key(f"acme/{name}") / ("b" * 40) / f"{'c' * 64}.json"
    path.parent.mkdir(parents=True)
    path.write_bytes(b"{}")
    then = clock.now - 8 * DAY
    os.utime(path, (then, then))
    return path


def test_saving_a_packet_never_sweeps_the_store(tmp_path: Path) -> None:
    """Review CR11: a save writes one packet; expiry belongs to the retention pass."""
    clock = Clock()
    root = tmp_path / "packets"
    expired = _plant_expired(root, "old", clock)

    assert PacketStore(root, clock=clock).save(packet())

    assert expired.exists()


def test_packet_expiry_runs_at_most_once_an_hour(tmp_path: Path) -> None:
    """Review CR11: the retention pass runs on every lease, but the packet sweep at most hourly."""
    clock = Clock()
    root = tmp_path / "packets"
    store = PacketStore(root, clock=clock)
    _plant_expired(root, "first", clock)
    assert store.remove_expired_if_due() == 1

    second = _plant_expired(root, "second", clock)
    clock.now += EXPIRY_INTERVAL.total_seconds() - 1
    assert store.remove_expired_if_due() == 0
    assert second.exists()

    clock.now += 1
    assert store.remove_expired_if_due() == 1
    assert files_under(root) == []


def test_one_packet_that_cant_be_removed_doesnt_stop_the_rest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """Review S10: a locked file is counted and skipped; every other expired packet still goes."""
    clock = Clock()
    root = tmp_path / "packets"
    locked = _plant_expired(root, "locked", clock)
    other = _plant_expired(root, "other", clock)
    unlink = Path.unlink

    def unlink_unless_locked(self: Path, missing_ok: bool = False) -> None:
        if self == locked:
            raise PermissionError("in use")
        unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", unlink_unless_locked)

    with caplog.at_level(logging.INFO):
        assert PacketStore(root, clock=clock).remove_expired() == 1

    assert locked.exists()
    assert not other.parent.exists()
    [failed] = [r for r in caplog.records if getattr(r, "event", "") == "packets_expire_failed"]
    assert failed.levelno == logging.WARNING
    assert failed.count == 1
    assert all(str(root) not in record.getMessage() for record in caplog.records)
