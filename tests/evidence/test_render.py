"""Rendering the evidence packet for the model (task 33), ported from the diff-only evidence tests."""

from pathlib import Path
from typing import Any

import pytest

from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.domain.evidence import EvidencePacket
from pr_evidence_reviewer.domain.pull_request import PullRequestSnapshot
from pr_evidence_reviewer.evidence.builder import MAX_NAMED_FILES, Budgets, build_packet
from pr_evidence_reviewer.evidence.render import ShownIndex, render_packet
from tests.evidence.helpers import reader_for
from tests.evidence.test_builder import CLAIM, FILES, at_head
from tests.review.builders import HEAD_SHA, TEST_PATCH, changed_file, check, snapshot

UNAVAILABLE = (
    f"The repository at {HEAD_SHA[:7]} couldn't be read, so only the diff and CI checks were used. "
    "Code and tests outside the diff weren't checked."
)
DIFF_ONLY = "\n".join(
    [
        f"Head commit: {HEAD_SHA}",
        "",
        "CI checks at the head commit:",
        "- lint (check_run): completed, success",
        "- tests (check_run): completed, success",
        "",
        "Evidence gaps:",
        f"- {UNAVAILABLE}",
        "",
        "Diff, one block per changed file. The number on the left is the head-side line number; "
        "removed lines have none.",
        "=== src/upload.py (modified, +1 -1) ===",
        "@@ +1 @@ def upload(data):",
        "      1   def upload(data):",
        "        -     send(data)",
        "      2 +     for attempt in range(3):",
        "      3 +         send(data)",
        "      4       return True",
        "@@ +21 @@",
        "     21   # retry",
        "     22 + RETRIES = 3",
        "     23   # end",
        "=== tests/test_upload.py (added, +1 -0) ===",
        "@@ +1 @@",
        "      1 + def test_retries():",
        "      2 +     assert upload(b'x')",
        "",
        "Tests for the changed source files:",
        "- src/upload.py (Python): changed tests: tests/test_upload.py",
    ]
)
WITH_CHECKOUT = "\n".join(
    [
        "Diff, one block per changed file. The number on the left is the head-side line number; "
        "removed lines have none.",
        "=== src/upload.py (modified, +1 -1) ===",
        "@@ +1 @@ def upload(data):  [in function upload, lines 1-4]",
        "      1   def upload(data):",
        "        -     send(data)",
        "      2 +     for attempt in range(3):",
        "      3 +         send(data)",
        "      4       return True",
        "@@ +21 @@",
        "     21   # retry",
        "     22 + RETRIES = 3",
        "     23   # end",
        "",
        "Tests for the changed source files:",
        "- src/upload.py (Python): likely tests: tests/test_upload.py",
        "",
        "Code around the changes at the head commit. The number on the left is the line number.",
        "--- function upload in src/upload.py, lines 1-4 ---",
        "      1 | def upload(data):",
        "      2 |     for attempt in range(3):",
        "      3 |         send(data)",
        "      4 |     return True",
        "",
        "Repository evidence for claim c1:",
        'Search terms: "retry_upload"',
        '--- src/client.py:3 (search term "retry_upload") ---',
        "      1 | from src.upload import upload",
        "      2 | ",
        "      3 | def retry_upload(data):",
        "      4 |     return upload(data)",
        "Definition: --- function retry_upload in src/client.py, lines 3-4 ---",
        "      3 | def retry_upload(data):",
        "      4 |     return upload(data)",
    ]
)


def diff_only(snap: PullRequestSnapshot, **budgets: Any) -> tuple[EvidencePacket, str, ShownIndex]:
    packet = build_packet(snap, None, [], Budgets(**budgets))
    text, index = render_packet(packet)
    return packet, text, index


def limitations(packet: EvidencePacket) -> list[str]:
    """The scope limitations other than the missing checkout every diff-only packet has."""
    return [item for item in packet.scope_limitations if item != UNAVAILABLE]


def test_renders_the_diff_with_head_side_line_numbers_and_the_ci_checks() -> None:
    _packet, text, _index = diff_only(snapshot())

    assert text == DIFF_ONLY


def test_renders_the_repository_evidence_with_a_line_number_on_every_line(tmp_path: Path) -> None:
    packet = build_packet(at_head(), reader_for(tmp_path, FILES), [CLAIM])

    text, index = render_packet(packet)

    assert text.endswith(WITH_CHECKOUT)
    assert index.code_lines == {
        "src/client.py": frozenset({1, 2, 3, 4}),
        "src/upload.py": frozenset({1, 2, 3, 4}),
    }
    assert index.is_shown("src/client.py", 3)
    assert index.is_shown("src/upload.py", 22)
    assert not index.is_shown("src/upload.py", 10)
    assert not index.is_shown("src/other.py", 1)


def test_a_claim_with_no_matches_says_so(tmp_path: Path) -> None:
    claim = Claim(
        id="c1",
        quote="Adds never_defined_anywhere.",
        statement="Adds `never_defined_anywhere`.",
        kind="behavior",
    )

    text, _index = render_packet(build_packet(at_head(), reader_for(tmp_path, FILES), [claim]))

    assert text.endswith('Search terms: "never_defined_anywhere"\n(no matches at the head commit)')


def test_indexes_only_lines_that_exist_on_the_head_side() -> None:
    _packet, _text, index = diff_only(snapshot())

    assert index.head_lines == {
        "src/upload.py": frozenset({1, 2, 3, 4, 21, 22, 23}),
        "tests/test_upload.py": frozenset({1, 2}),
    }
    assert sorted(index.checks) == ["lint", "tests"]
    assert index.code_lines == {}


def test_the_same_snapshot_renders_byte_identical_whatever_the_input_order() -> None:
    files = [changed_file("b.py"), changed_file("a.py"), changed_file("c/d.py")]
    checks = [check("tests"), check("lint", source="status"), check("lint")]

    _first, first, first_index = diff_only(snapshot(files=files, checks=checks))
    _second, second, second_index = diff_only(snapshot(files=files[::-1], checks=checks[::-1]))

    assert first.encode() == second.encode()
    assert first.index("=== a.py") < first.index("=== b.py") < first.index("=== c/d")
    assert first_index == second_index


def test_files_without_a_diff_are_listed_as_gaps() -> None:
    files = [
        changed_file("logo.png", patch=None, additions=0, deletions=0, status="modified"),
        changed_file("data.bin", patch=None, additions=4, deletions=0),
        changed_file("src/upload.py"),
    ]

    _packet, text, index = diff_only(snapshot(files=files))

    assert "- No diff is available for data.bin (binary or too large)." in text
    assert "logo.png" not in text
    assert set(index.head_lines) == {"src/upload.py"}


def test_no_ci_checks_is_an_explicit_gap() -> None:
    _packet, text, index = diff_only(snapshot(checks=[]))

    assert "CI checks at the head commit:\n(none)" in text
    assert "- No CI checks were reported for the head commit." in text
    assert index.checks == {}


def test_the_snapshot_gaps_are_passed_on_to_the_model() -> None:
    gap = "CI check runs for aaaaaaa couldn't be read."

    _packet, text, _index = diff_only(snapshot(scope_limitations=[gap]))

    assert f"- {gap}" in text


def test_a_patch_without_a_hunk_header_is_a_gap() -> None:
    files = [changed_file("odd.py", patch="Binary files differ")]

    _packet, text, index = diff_only(snapshot(files=files))

    assert "- The diff for odd.py couldn't be read." in text
    assert index.head_lines == {}


def test_a_removed_file_has_no_lines_to_cite() -> None:
    patch = "@@ -1,2 +0,0 @@\n-gone\n-also gone"
    files = [changed_file("old.py", patch=patch, status="removed")]

    _packet, text, index = diff_only(snapshot(files=files))

    assert "=== old.py (removed, +1 -1) ===" in text
    assert index.head_lines == {"old.py": frozenset()}


def test_a_line_break_in_a_file_name_cannot_start_a_new_line() -> None:
    sneaky = "a.py\n=== fake.py (added) ==="

    _packet, text, index = diff_only(snapshot(files=[changed_file(sneaky)]))

    assert "\n=== fake.py" not in text
    assert "=== a.py\\n=== fake.py (added) === (modified" in text
    assert index.displayed_paths == {"a.py\\n=== fake.py (added) ===": sneaky}


def test_files_that_do_not_fit_the_budget_are_left_out_and_named() -> None:
    big = "@@ -0,0 +1,200 @@\n" + "\n".join(f"+line {n}" for n in range(200))
    files = [changed_file("a.py", patch=big), changed_file("b.py", patch=TEST_PATCH)]

    packet, text, index = diff_only(snapshot(files=files), diff_chars=500)

    assert "=== a.py" not in text
    assert "=== b.py" in text
    assert set(index.head_lines) == {"b.py"}
    assert limitations(packet) == ["The diff is too long to check in full. Not checked: a.py."]
    assert "- The diff is too long to check in full. Not checked: a.py." in text


def test_hunks_past_the_budget_are_cut_and_the_file_is_named_as_partly_checked() -> None:
    patch = "@@ -1 +1 @@\n+short\n@@ -50,0 +50,40 @@\n" + "\n".join(
        f"+filler line {n}" for n in range(40)
    )
    files = [changed_file("a.py", patch=patch)]

    packet, _text, index = diff_only(snapshot(files=files), diff_chars=300)

    assert index.head_lines == {"a.py": frozenset({1})}
    assert limitations(packet) == [
        "The diff is too long to check in full. Only some changes were checked in: a.py."
    ]


def test_a_long_list_of_cut_files_names_the_first_ones_and_counts_the_rest() -> None:
    files = [changed_file(f"f{n:02}.py") for n in range(MAX_NAMED_FILES + 3)]

    packet, _text, index = diff_only(snapshot(files=files), diff_chars=10)

    [limitation] = [item for item in limitations(packet) if item.startswith("The diff")]
    assert limitation.startswith("The diff is too long to check in full. Not checked: f00.py, ")
    assert f"f{MAX_NAMED_FILES - 1:02}.py, and 3 more." in limitation
    assert f"f{MAX_NAMED_FILES:02}.py" not in limitation
    assert index.head_lines == {}


def _hunks(count: int, lines_per_hunk: int) -> str:
    return "\n".join(
        f"@@ -{n * 100 + 1},0 +{n * 100 + 1},{lines_per_hunk} @@\n"
        + "\n".join(f"+padding {n}.{m}" for m in range(lines_per_hunk))
        for n in range(count)
    )


def test_every_file_gets_its_first_hunk_before_any_file_gets_a_second() -> None:
    auth = "@@ -10,1 +10,2 @@\n context\n+    require_login()"
    files = [
        changed_file("a/padding.py", patch=_hunks(50, 5)),
        changed_file("src/auth.py", patch=auth),
    ]

    packet, _text, index = diff_only(snapshot(files=files), diff_chars=2_000)

    assert index.head_lines["src/auth.py"] == frozenset({10, 11})
    assert 1 in index.head_lines["a/padding.py"]
    assert limitations(packet) == [
        "The diff is too long to check in full. Only some changes were checked in: a/padding.py."
    ]
    assert packet.evidence_incomplete


@pytest.mark.parametrize("path", ["package-lock.json", "dist/bundle.js", "build/out.js"])
def test_lockfiles_and_generated_files_are_checked_last(path: str) -> None:
    files = [changed_file(path, patch=_hunks(1, 5)), changed_file("src/z.py", patch=_hunks(1, 5))]

    packet, _text, index = diff_only(snapshot(files=files), diff_chars=250)

    assert set(index.head_lines) == {"src/z.py"}
    assert limitations(packet) == [f"The diff is too long to check in full. Not checked: {path}."]


def test_only_so_many_ci_checks_are_shown_and_the_rest_are_named_as_a_gap() -> None:
    checks = [check(f"job-{n:03}") for n in range(5)]

    packet, text, index = diff_only(snapshot(checks=checks), max_checks=3)

    assert sorted(index.checks) == ["job-000", "job-001", "job-002"]
    assert "job-003" not in text
    assert limitations(packet) == [
        "There are too many CI checks to check them all. Not checked: 2 checks."
    ]
    assert packet.evidence_incomplete


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "build/Jenkinsfile"])
def test_a_change_to_the_ci_setup_is_a_scope_limitation(path: str) -> None:
    files = [changed_file("src/upload.py"), changed_file(path)]

    packet, _text, _index = diff_only(snapshot(files=files))

    assert packet.changes_ci_config
    assert limitations(packet) == [
        "This pull request changes its own CI setup, so its CI checks may not be independent "
        "evidence."
    ]
