"""Review round 1 for the packet store: saving never raises (CR10), and every directory level is
owner-only and never a link (S5)."""

import stat
import sys
from pathlib import Path

import pytest

from pr_evidence_reviewer.domain.evidence import EvidencePacket
from pr_evidence_reviewer.evidence.store import PacketStore, repo_key
from tests.checkout.fakes import files_under, make_dir_link
from tests.evidence.test_store import REPO, packet


def test_a_packet_that_cant_be_hashed_is_reported_not_raised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def unhashable(self: EvidencePacket) -> str:
        raise ValueError("can't serialise")

    monkeypatch.setattr(EvidencePacket, "content_hash", unhashable)

    assert PacketStore(tmp_path / "packets").save(packet()) is False


def test_a_linked_repository_folder_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "packets"
    elsewhere = tmp_path / "elsewhere"
    root.mkdir()
    elsewhere.mkdir()
    make_dir_link(root / repo_key(REPO), elsewhere)

    assert PacketStore(root).save(packet()) is False
    assert files_under(elsewhere) == []


def test_a_linked_commit_folder_is_refused(tmp_path: Path) -> None:
    root = tmp_path / "packets"
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    built = packet()
    (root / repo_key(REPO)).mkdir(parents=True)
    make_dir_link(root / repo_key(REPO) / built.snapshot.head_sha, elsewhere)

    assert PacketStore(root).save(built) is False
    assert files_under(elsewhere) == []


@pytest.mark.skipif(sys.platform == "win32", reason="POSIX permission bits")
def test_every_store_folder_is_owner_only(tmp_path: Path) -> None:
    root = tmp_path / "packets"
    built = packet()

    assert PacketStore(root).save(built)

    levels = [root, root / repo_key(REPO), root / repo_key(REPO) / built.snapshot.head_sha]
    assert [stat.S_IMODE(level.stat().st_mode) for level in levels] == [0o700] * 3
