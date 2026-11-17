import pytest

from pr_evidence_reviewer.review.evidence import MAX_NAMED_FILES, build_evidence
from tests.review.builders import HEAD_SHA, TEST_PATCH, changed_file, check, snapshot

GOLDEN = "\n".join(
    [
        f"Head commit: {HEAD_SHA}",
        "",
        "CI checks at the head commit:",
        "- lint (check_run): completed, success",
        "- tests (check_run): completed, success",
        "",
        "Evidence gaps:",
        "(none)",
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
    ]
)


def test_renders_the_diff_with_head_side_line_numbers_and_the_ci_checks() -> None:
    packet = build_evidence(snapshot())

    assert packet.text == GOLDEN
    assert packet.scope_limitations == []


def test_indexes_only_lines_that_exist_on_the_head_side() -> None:
    packet = build_evidence(snapshot())

    assert packet.index.head_lines == {
        "src/upload.py": frozenset({1, 2, 3, 4, 21, 22, 23}),
        "tests/test_upload.py": frozenset({1, 2}),
    }
    assert sorted(packet.index.checks) == ["lint", "tests"]


def test_the_same_snapshot_renders_byte_identical_whatever_the_input_order() -> None:
    files = [changed_file("b.py"), changed_file("a.py"), changed_file("c/d.py")]
    checks = [check("tests"), check("lint", source="status"), check("lint")]

    first = build_evidence(snapshot(files=files, checks=checks))
    second = build_evidence(snapshot(files=files[::-1], checks=checks[::-1]))

    assert first.text.encode() == second.text.encode()
    assert first.text.index("=== a.py") < first.text.index("=== b.py") < first.text.index("=== c/d")
    assert first.index == second.index


def test_files_without_a_diff_are_listed_as_gaps() -> None:
    files = [
        changed_file("logo.png", patch=None, additions=0, deletions=0, status="modified"),
        changed_file("data.bin", patch=None, additions=4, deletions=0),
        changed_file("src/upload.py"),
    ]

    packet = build_evidence(snapshot(files=files))

    assert "- No diff is available for data.bin (binary or too large)." in packet.text
    assert "logo.png" not in packet.text
    assert set(packet.index.head_lines) == {"src/upload.py"}


def test_no_ci_checks_is_an_explicit_gap() -> None:
    packet = build_evidence(snapshot(checks=[]))

    assert "CI checks at the head commit:\n(none)" in packet.text
    assert "- No CI checks were reported for the head commit." in packet.text
    assert packet.index.checks == {}


def test_the_snapshot_gaps_are_passed_on_to_the_model() -> None:
    gap = "CI check runs for aaaaaaa couldn't be read."

    packet = build_evidence(snapshot(scope_limitations=[gap]))

    assert f"- {gap}" in packet.text


def test_a_patch_without_a_hunk_header_is_a_gap() -> None:
    packet = build_evidence(snapshot(files=[changed_file("odd.py", patch="Binary files differ")]))

    assert "- The diff for odd.py couldn't be read." in packet.text
    assert packet.index.head_lines == {}


def test_a_removed_file_has_no_lines_to_cite() -> None:
    patch = "@@ -1,2 +0,0 @@\n-gone\n-also gone"

    packet = build_evidence(snapshot(files=[changed_file("old.py", patch=patch, status="removed")]))

    assert "=== old.py (removed, +1 -1) ===" in packet.text
    assert packet.index.head_lines == {"old.py": frozenset()}


def test_a_line_break_in_a_file_name_cannot_start_a_new_line() -> None:
    sneaky = "a.py\n=== fake.py (added) ==="

    packet = build_evidence(snapshot(files=[changed_file(sneaky)]))

    assert "\n=== fake.py" not in packet.text
    assert "=== a.py\\n=== fake.py (added) === (modified" in packet.text


def test_files_that_do_not_fit_the_budget_are_left_out_and_named() -> None:
    big = "@@ -0,0 +1,200 @@\n" + "\n".join(f"+line {n}" for n in range(200))
    files = [changed_file("a.py", patch=big), changed_file("b.py", patch=TEST_PATCH)]

    packet = build_evidence(snapshot(files=files), max_diff_chars=500)

    assert "=== a.py" not in packet.text
    assert "=== b.py" in packet.text
    assert set(packet.index.head_lines) == {"b.py"}
    assert packet.scope_limitations == ["The diff is too long to check in full. Not checked: a.py."]
    assert f"- {packet.scope_limitations[0]}" in packet.text


def test_hunks_past_the_budget_are_cut_and_the_file_is_named_as_partly_checked() -> None:
    patch = "@@ -1 +1 @@\n+short\n@@ -50,0 +50,40 @@\n" + "\n".join(
        f"+filler line {n}" for n in range(40)
    )

    packet = build_evidence(snapshot(files=[changed_file("a.py", patch=patch)]), max_diff_chars=300)

    assert packet.index.head_lines == {"a.py": frozenset({1})}
    assert packet.scope_limitations == [
        "The diff is too long to check in full. Only some changes were checked in: a.py."
    ]


def test_a_long_list_of_cut_files_names_the_first_ones_and_counts_the_rest() -> None:
    files = [changed_file(f"f{n:02}.py") for n in range(MAX_NAMED_FILES + 3)]

    packet = build_evidence(snapshot(files=files), max_diff_chars=10)

    limitation = packet.scope_limitations[0]
    assert limitation.startswith("The diff is too long to check in full. Not checked: f00.py, ")
    assert f"f{MAX_NAMED_FILES - 1:02}.py, and 3 more." in limitation
    assert f"f{MAX_NAMED_FILES:02}.py" not in limitation
    assert packet.index.head_lines == {}


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

    packet = build_evidence(snapshot(files=files), max_diff_chars=2_000)

    assert packet.index.head_lines["src/auth.py"] == frozenset({10, 11})
    assert 1 in packet.index.head_lines["a/padding.py"]
    assert packet.scope_limitations == [
        "The diff is too long to check in full. Only some changes were checked in: a/padding.py."
    ]
    assert packet.evidence_incomplete


@pytest.mark.parametrize("path", ["package-lock.json", "dist/bundle.js", "build/out.js"])
def test_lockfiles_and_generated_files_are_checked_last(path: str) -> None:
    files = [changed_file(path, patch=_hunks(1, 5)), changed_file("src/z.py", patch=_hunks(1, 5))]

    packet = build_evidence(snapshot(files=files), max_diff_chars=250)

    assert set(packet.index.head_lines) == {"src/z.py"}
    assert packet.scope_limitations == [
        f"The diff is too long to check in full. Not checked: {path}."
    ]


def test_only_so_many_ci_checks_are_shown_and_the_rest_are_named_as_a_gap() -> None:
    checks = [check(f"job-{n:03}") for n in range(5)]

    packet = build_evidence(snapshot(checks=checks), max_check_lines=3)

    assert sorted(packet.index.checks) == ["job-000", "job-001", "job-002"]
    assert "job-003" not in packet.text
    assert packet.scope_limitations == [
        "There are too many CI checks to check them all. Not checked: 2 checks."
    ]
    assert packet.evidence_incomplete


@pytest.mark.parametrize("path", [".github/workflows/ci.yml", "build/Jenkinsfile"])
def test_a_change_to_the_ci_setup_is_a_scope_limitation(path: str) -> None:
    files = [changed_file("src/upload.py"), changed_file(path)]

    packet = build_evidence(snapshot(files=files))

    assert packet.changes_ci_config
    assert packet.scope_limitations == [
        "This pull request changes its own CI setup, so its CI checks may not be independent "
        "evidence."
    ]
    assert not packet.evidence_incomplete
