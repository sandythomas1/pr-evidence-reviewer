import pytest

from pr_evidence_reviewer.domain.verdicts import (
    CheckCitation,
    DiffCitation,
    ProposedCheckCitation,
    ProposedDiffCitation,
)
from pr_evidence_reviewer.review.citations import SnapshotCitationChecker, check_outcome
from pr_evidence_reviewer.review.evidence import build_evidence
from tests.review.builders import changed_file, check, snapshot


def checker() -> SnapshotCitationChecker:
    checks = [check("tests"), check("lint"), check("lint", source="status", conclusion="failure")]
    return SnapshotCitationChecker(build_evidence(snapshot(checks=checks)).index)


@pytest.mark.parametrize(("start", "end"), [(1, 1), (1, 4), (2, 3), (21, 23), (23, 23)])
def test_a_range_inside_one_hunk_checks_out(start: int, end: int) -> None:
    cited = ProposedDiffCitation(path="src/upload.py", start_line=start, end_line=end)

    assert checker().check_diff(cited) == DiffCitation(
        path="src/upload.py", start_line=start, end_line=end
    )


@pytest.mark.parametrize(
    ("path", "start", "end"),
    [
        ("src/upload.py", 4, 21),  # spans lines between hunks that weren't shown
        ("src/upload.py", 5, 5),  # past the end of the first hunk
        ("src/upload.py", 0, 1),
        ("src/upload.py", -3, 2),
        ("src/upload.py", 3, 2),  # backwards
        ("src/upload.py", 1, 10**9),  # absurdly long, rejected without walking the range
        ("src/Upload.py", 1, 1),  # paths are case-sensitive
        ("./src/upload.py", 1, 1),
        ("/src/upload.py", 1, 1),
        ("src/../src/upload.py", 1, 1),
        ("README.md", 1, 1),  # not a changed file
    ],
)
def test_citations_that_do_not_point_at_shown_head_lines_are_rejected(
    path: str, start: int, end: int
) -> None:
    cited = ProposedDiffCitation(path=path, start_line=start, end_line=end)

    assert checker().check_diff(cited) is None


def test_a_check_citation_carries_the_result_github_reported() -> None:
    assert checker().check_ci(ProposedCheckCitation(name="tests")) == [
        CheckCitation(name="tests", source="check_run", status="completed", conclusion="success")
    ]


def test_a_name_shared_by_two_checks_cites_both_results() -> None:
    results = checker().check_ci(ProposedCheckCitation(name="lint"))

    assert [(c.source, c.conclusion) for c in results] == [
        ("check_run", "success"),
        ("status", "failure"),
    ]


@pytest.mark.parametrize("name", ["Tests", "test", "", "build"])
def test_unknown_check_names_are_rejected(name: str) -> None:
    assert checker().check_ci(ProposedCheckCitation(name=name)) == []


def test_a_path_cited_in_its_displayed_escaped_form_maps_back_to_the_real_path() -> None:
    raw = 'docs/a\nb "c".py'
    files = [changed_file(raw)]
    index = build_evidence(snapshot(files=files)).index

    cited = SnapshotCitationChecker(index).check_diff(
        ProposedDiffCitation(path='docs/a\\nb \\"c\\".py', start_line=1, end_line=1)
    )

    assert cited == DiffCitation(path=raw, start_line=1, end_line=1)


@pytest.mark.parametrize(
    ("status", "conclusion", "outcome"),
    [
        ("completed", "success", "passed"),
        ("completed", "failure", "failed"),
        ("completed", "timed_out", "failed"),
        ("completed", "cancelled", "failed"),
        ("completed", "action_required", "failed"),
        ("completed", "error", "failed"),
        ("completed", "neutral", None),
        ("completed", "skipped", None),
        ("completed", None, None),
        ("in_progress", None, None),
        ("queued", "success", None),
    ],
)
def test_a_check_result_is_decisive_only_when_it_finished_and_passed_or_failed(
    status: str, conclusion: str | None, outcome: str | None
) -> None:
    citation = CheckCitation(name="t", source="check_run", status=status, conclusion=conclusion)

    assert check_outcome(citation) == outcome
