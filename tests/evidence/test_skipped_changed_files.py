"""Review round 1: changed files the checkout can't read (CR1, CR3/S3, L2-1) and structured search
limitations (CR6)."""

from pathlib import Path

import pytest

from pr_evidence_reviewer.evidence.builder import build_packet
from pr_evidence_reviewer.evidence.search import SearchLimits, search_literal, select_corpus
from tests.checkout.fakes import blob
from tests.evidence.helpers import reader_for
from tests.evidence.test_builder import CLAIM, FILES, at_head
from tests.review.builders import UPLOAD_PATCH, changed_file

LFS_POINTER = b"version https://git-lfs.github.com/spec/v1\noid sha256:" + b"0" * 64 + b"\n"


@pytest.mark.parametrize("unsafe", ["a:b.py", "src\\win.py", "x" * 256 + ".py"])
def test_a_changed_file_with_a_refused_name_is_a_gap_not_a_crash(
    tmp_path: Path, unsafe: str
) -> None:
    reader = reader_for(tmp_path, FILES, extra_entries=[blob(unsafe, b"def f():\n    pass\n")])
    snap = at_head([changed_file("src/upload.py"), changed_file(unsafe, UPLOAD_PATCH)])

    packet = build_packet(snap, reader, [CLAIM])

    [gap] = [gap for gap in packet.gaps if gap.path == unsafe and gap.kind == "skipped_files"]
    assert "wasn't read: its name or type isn't supported." in gap.message
    assert gap.message in packet.scope_limitations
    assert packet.evidence_incomplete
    assert any(file.path == unsafe for file in packet.diff)


def test_a_changed_lfs_file_marks_the_evidence_incomplete(tmp_path: Path) -> None:
    reader = reader_for(tmp_path, {**FILES, "media/clip.mp4": LFS_POINTER})
    snap = at_head([changed_file("src/upload.py"), changed_file("media/clip.mp4", None)])

    packet = build_packet(snap, reader, [CLAIM])

    [gap] = [gap for gap in packet.gaps if gap.kind == "lfs"]
    assert gap.path == "media/clip.mp4"
    assert gap.message in packet.scope_limitations
    assert packet.evidence_incomplete


def test_a_changed_binary_file_stays_a_visible_gap_only(tmp_path: Path) -> None:
    reader = reader_for(tmp_path, {**FILES, "assets/logo.png": b"\x89PNG\0\0binary"})
    snap = at_head([changed_file("src/upload.py"), changed_file("assets/logo.png", None)])

    packet = build_packet(snap, reader, [CLAIM])

    assert not packet.evidence_incomplete


def test_search_limitations_say_their_kind_and_whether_evidence_may_be_missed(
    tmp_path: Path,
) -> None:
    reader = reader_for(tmp_path, {"a.py": b"retry = 1\n" + b"x" * 3_000 + b"\n"})
    corpus = select_corpus(reader)

    long_lines = search_literal(reader, corpus, ["retry"])
    timed_out = search_literal(reader, corpus, ["retry"], limits=SearchLimits(phase_seconds=0.0))

    assert [(item.kind, item.incomplete) for item in long_lines.limitations] == [
        ("search_limit", False)
    ]
    assert [(item.kind, item.incomplete) for item in timed_out.limitations] == [
        ("search_timeout", True)
    ]


def test_skipped_long_lines_alone_are_a_limitation_but_not_incomplete(tmp_path: Path) -> None:
    files = {**FILES, "src/wide.py": b"retry_upload = 1\n" + b"x" * 3_000 + b"\n"}

    packet = build_packet(at_head(), reader_for(tmp_path, files), [CLAIM])

    [gap] = [gap for gap in packet.gaps if gap.kind == "search_limit"]
    assert gap.message.startswith("Lines over 2,000 characters weren't searched")
    assert not packet.evidence_incomplete
