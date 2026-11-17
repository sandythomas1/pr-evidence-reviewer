"""The packet store (task 34): content-addressed, atomic, idempotent, validated on load."""

import logging
from pathlib import Path

import pytest

from pr_evidence_reviewer.domain.evidence import EvidencePacket, StoredPacket
from pr_evidence_reviewer.evidence.builder import build_packet
from pr_evidence_reviewer.evidence.store import PacketStore, repo_key
from tests.review.builders import HEAD_SHA, snapshot

REPO = "acme/shop"


def packet() -> EvidencePacket:
    return build_packet(snapshot(), None, [])


def test_a_packet_is_stored_under_its_repo_head_and_hash(tmp_path: Path) -> None:
    store = PacketStore(tmp_path / "packets")
    item = packet()

    assert store.save(item)

    content_hash = item.content_hash()
    path = tmp_path / "packets" / repo_key(REPO) / HEAD_SHA / f"{content_hash}.json"
    assert path.is_file()
    assert store.load(REPO, HEAD_SHA, content_hash) == StoredPacket(
        content_hash=content_hash, packet=item
    )
    assert [p.name for p in path.parent.iterdir()] == [path.name]


def test_the_repo_key_ignores_case_and_hides_the_name() -> None:
    assert repo_key("Acme/Shop") == repo_key(REPO)
    assert len(repo_key(REPO)) == 16
    assert "acme" not in repo_key(REPO)


def test_saving_the_same_packet_twice_writes_nothing_new(tmp_path: Path) -> None:
    store = PacketStore(tmp_path)
    item = packet()
    assert store.save(item)
    path = store.path_for(REPO, HEAD_SHA, item.content_hash())
    written = path.stat().st_mtime_ns

    assert store.save(item)

    assert path.stat().st_mtime_ns == written
    assert len(list(path.parent.iterdir())) == 1


def test_a_missing_packet_loads_as_none(tmp_path: Path) -> None:
    assert PacketStore(tmp_path).load(REPO, HEAD_SHA, "0" * 64) is None


def test_a_tampered_packet_is_not_loaded(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    store = PacketStore(tmp_path)
    item = packet()
    store.save(item)
    path = store.path_for(REPO, HEAD_SHA, item.content_hash())
    path.write_text(path.read_text().replace("lint", "lunt"))

    with caplog.at_level(logging.WARNING):
        assert store.load(REPO, HEAD_SHA, item.content_hash()) is None

    assert "lunt" not in caplog.text


def test_a_corrupt_packet_is_not_loaded(tmp_path: Path) -> None:
    store = PacketStore(tmp_path)
    item = packet()
    store.save(item)
    store.path_for(REPO, HEAD_SHA, item.content_hash()).write_text("{not json")

    assert store.load(REPO, HEAD_SHA, item.content_hash()) is None


def test_a_packet_under_another_repo_is_not_loaded(tmp_path: Path) -> None:
    store = PacketStore(tmp_path)
    item = packet()
    store.save(item)
    elsewhere = store.path_for("other/repo", HEAD_SHA, item.content_hash())
    elsewhere.parent.mkdir(parents=True)
    elsewhere.write_bytes(store.path_for(REPO, HEAD_SHA, item.content_hash()).read_bytes())

    assert store.load("other/repo", HEAD_SHA, item.content_hash()) is None


@pytest.mark.parametrize("head_sha", ["../../etc", "A" * 40, "a" * 39])
def test_only_a_full_head_commit_id_names_a_directory(tmp_path: Path, head_sha: str) -> None:
    with pytest.raises(ValueError, match="full head commit id"):
        PacketStore(tmp_path).path_for(REPO, head_sha, "0" * 64)


def test_a_failed_save_is_logged_and_never_raised(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    blocker = tmp_path / "packets"
    blocker.write_text("a file where the store's directory should be")

    with caplog.at_level(logging.WARNING):
        assert not PacketStore(blocker).save(packet())

    [record] = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert record.getMessage() == "Couldn't save the evidence packet"
    assert str(tmp_path) not in caplog.text


def test_a_failed_write_leaves_no_temporary_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def fail(*_args: object) -> None:
        raise OSError("disk full")

    monkeypatch.setattr("pr_evidence_reviewer.evidence.store.os.replace", fail)
    store = PacketStore(tmp_path)
    item = packet()

    assert not store.save(item)

    directory = store.path_for(REPO, HEAD_SHA, item.content_hash()).parent
    assert list(directory.iterdir()) == []
