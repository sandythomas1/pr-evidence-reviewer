"""The evidence packet builder (task 32): repository evidence, budgets, gaps and C5 completeness."""

from pathlib import Path
from typing import Any

from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.domain.pull_request import ChangedFile, CommitRef, PullRequestSnapshot
from pr_evidence_reviewer.evidence.builder import Budgets, build_packet
from tests.checkout.fakes import HEAD_SHA
from tests.evidence.helpers import reader_for
from tests.review.builders import UPLOAD_PATCH, changed_file, snapshot

UPLOAD = b"def upload(data):\n    for attempt in range(3):\n        send(data)\n    return True\n"
FILES = {
    "src/upload.py": UPLOAD + b"\n" * 16 + b"# retry\nRETRIES = 3\n# end\n",
    "src/client.py": b"from src.upload import upload\n\ndef retry_upload(data):\n    return upload(data)\n",
    "tests/test_upload.py": b"def test_retries():\n    assert upload(b'x')\n",
}
CLAIM = Claim(
    id="c1", quote="Adds retry_upload.", statement="Adds `retry_upload`.", kind="behavior"
)


def at_head(files: list[ChangedFile] | None = None, **overrides: Any) -> PullRequestSnapshot:
    return snapshot(
        files=files or [changed_file("src/upload.py", UPLOAD_PATCH)],
        head=CommitRef(ref="feature/retry", sha=HEAD_SHA),
        **overrides,
    )


def test_without_a_checkout_only_the_diff_and_ci_are_used_and_it_is_a_gap() -> None:
    packet = build_packet(at_head(), None, [CLAIM], checkout_problem="It took too long.")

    assert packet.snapshot.checkout_status == "unavailable"
    assert packet.evidence_incomplete
    assert packet.claim_evidence == ()
    [gap] = [gap for gap in packet.gaps if gap.kind == "checkout_unavailable"]
    assert gap.message == (
        f"The repository at {HEAD_SHA[:7]} couldn't be read, so only the diff and CI checks were "
        "used. It took too long."
    )
    assert gap.message in packet.scope_limitations


def test_hunks_name_their_enclosing_function(tmp_path: Path) -> None:
    packet = build_packet(at_head(), reader_for(tmp_path, FILES), [CLAIM])

    [file] = packet.diff
    first, second = file.hunks
    assert (first.id, second.id) == ("h1", "h2")
    assert first.enclosing is not None
    assert (first.enclosing.name, first.enclosing.start_line) == ("upload", 1)
    assert second.enclosing is None
    assert any(symbol.name == "upload" and symbol.shown_lines for symbol in packet.symbols)


def test_claim_queries_find_hits_and_definitions_at_head(tmp_path: Path) -> None:
    packet = build_packet(at_head(), reader_for(tmp_path, FILES), [CLAIM])

    [evidence] = packet.claim_evidence
    assert evidence.queries == ("retry_upload",)
    assert [(hit.id, hit.path, hit.line) for hit in evidence.hits] == [("s1", "src/client.py", 3)]
    [definition] = [s for s in packet.symbols if s.id in evidence.definition_ids]
    assert (definition.name, definition.path) == ("retry_upload", "src/client.py")
    assert definition.shown_lines[0] == (3, "def retry_upload(data):")
    assert not packet.evidence_incomplete


def test_changed_sources_are_mapped_to_tests(tmp_path: Path) -> None:
    packet = build_packet(at_head(), reader_for(tmp_path, FILES), [])

    [mapping] = packet.test_mapping
    assert (mapping.source_path, mapping.likely_tests) == (
        "src/upload.py",
        ("tests/test_upload.py",),
    )


def test_a_claim_over_its_budget_is_cut_named_and_incomplete(tmp_path: Path) -> None:
    budgets = Budgets(claim_chars=60)

    packet = build_packet(at_head(), reader_for(tmp_path, FILES), [CLAIM], budgets)

    [evidence] = packet.claim_evidence
    assert evidence.cut
    assert packet.evidence_incomplete
    [gap] = [gap for gap in packet.gaps if gap.kind == "budget_cut"]
    assert gap.claim_id == "c1"
    assert gap.message.startswith("The evidence for claim c1 is too long to show in full.")
    assert gap.message in packet.scope_limitations


def test_a_changed_file_missing_from_the_download_makes_the_packet_incomplete(
    tmp_path: Path,
) -> None:
    archived = {path: data for path, data in FILES.items() if path != "src/upload.py"}
    reader = reader_for(tmp_path, FILES, archived=archived)

    packet = build_packet(at_head(), reader, [])

    [gap] = [gap for gap in packet.gaps if gap.path == "src/upload.py"]
    assert (
        gap.message
        == "src/upload.py wasn't read: its content couldn't be checked against the commit."
    )
    assert packet.evidence_incomplete


def test_a_skipped_file_a_claim_names_makes_the_packet_incomplete(tmp_path: Path) -> None:
    archived = {path: data for path, data in FILES.items() if path != "src/client.py"}
    named = Claim(
        id="c1", quote="Edits src/client.py.", statement="Edits src/client.py.", kind="refactor"
    )

    packet = build_packet(at_head(), reader_for(tmp_path, FILES, archived=archived), [named])

    assert packet.claim_evidence[0].files == ("src/client.py",)
    assert packet.evidence_incomplete


def test_an_untested_change_is_a_gap_but_not_incomplete(tmp_path: Path) -> None:
    files = {"src/upload.py": FILES["src/upload.py"]}

    packet = build_packet(at_head(), reader_for(tmp_path, files), [])

    assert [gap.message for gap in packet.gaps if gap.kind == "no_test_found"] == [
        "No test was found for src/upload.py."
    ]
    assert not packet.evidence_incomplete


def test_gap_ids_follow_the_canonical_sort() -> None:
    files = [
        changed_file("b.bin", patch=None, additions=1),
        changed_file("a.bin", patch=None, additions=1),
    ]

    packet = build_packet(at_head(files=files, checks=[]), None, [])

    assert [(gap.id, gap.kind, gap.path) for gap in packet.gaps] == [
        ("g1", "checkout_unavailable", None),
        ("g2", "no_ci", None),
        ("g3", "no_diff", "a.bin"),
        ("g4", "no_diff", "b.bin"),
    ]
