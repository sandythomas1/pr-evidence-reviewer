"""CheckoutCitationChecker: code citations against the checkout at head, in the plan's rule order."""

import logging
from collections.abc import Iterable
from pathlib import Path

import pytest

from pr_evidence_reviewer.checkout.reader import CheckoutReader
from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.domain.pull_request import CommitRef
from pr_evidence_reviewer.domain.verdicts import (
    CheckCitation,
    CodeCitation,
    DiffCitation,
    ProposedAssessment,
    ProposedCheckCitation,
    ProposedCodeCitation,
    ProposedDiffCitation,
)
from pr_evidence_reviewer.evidence.render import ShownIndex
from pr_evidence_reviewer.review.citations import (
    CheckoutCitationChecker,
    SnapshotCitationChecker,
)
from pr_evidence_reviewer.review.verdicts import check_assessments
from tests.checkout.fakes import HEAD_SHA
from tests.evidence.helpers import reader_for
from tests.review.builders import shown_for, snapshot

UPLOAD = "".join(f"line {n} of the upload client\n" for n in range(1, 31)).encode()
FILES = {
    "src/upload.py": UPLOAD,
    "src/short.py": b"only one line here\n",
    "assets/logo.png": b"\x89PNG\r\n\x1a\n\0\0\0binary",
    "docs/latin1.txt": "caf\xe9 au lait, s'il vous pla\xeet".encode("latin-1"),
    "media/clip.mp4": b"version https://git-lfs.github.com/spec/v1\noid sha256:"
    + b"0" * 64
    + b"\n",
}


class Shown:
    """A fake ShownLines: the lines the rendered evidence showed, per path."""

    def __init__(self, shown: dict[str, Iterable[int]]) -> None:
        self._shown = {path: set(lines) for path, lines in shown.items()}

    def is_shown(self, path: str, line: int) -> bool:
        return line in self._shown.get(path, set())


@pytest.fixture(scope="module")
def reader(tmp_path_factory: pytest.TempPathFactory) -> CheckoutReader:
    return reader_for(tmp_path_factory.mktemp("checkout"), FILES)


def index() -> ShownIndex:
    return shown_for(snapshot(head=CommitRef(ref="feature/retry", sha=HEAD_SHA)))


def checker(
    reader: CheckoutReader, shown: Shown | None = None, idx: ShownIndex | None = None
) -> CheckoutCitationChecker:
    return CheckoutCitationChecker(reader, HEAD_SHA, shown, index=idx or index())


def code(
    path: str = "src/upload.py",
    start: int = 2,
    end: int = 3,
    commit: str = HEAD_SHA,
    quote: str = "",
) -> ProposedCodeCitation:
    return ProposedCodeCitation(
        path=path, start_line=start, end_line=end, commit=commit, quote=quote
    )


def test_a_code_citation_at_head_checks_out_with_the_full_commit(reader: CheckoutReader) -> None:
    cited = checker(reader).check_code(code(commit=HEAD_SHA[:7].upper(), quote="line 2 of the"))

    assert cited == CodeCitation(path="src/upload.py", start_line=2, end_line=3, commit=HEAD_SHA)


@pytest.mark.parametrize(
    ("proposed", "reason"),
    [
        (code(commit="b" * 40), "wrong_commit"),
        (code(commit=HEAD_SHA[:6]), "wrong_commit"),
        (code(commit=HEAD_SHA + "c"), "wrong_commit"),
        (code(commit=f" {HEAD_SHA[:7]}"), "wrong_commit"),
        (code(commit="HEAD"), "wrong_commit"),
        (code(path="../src/upload.py"), "unsafe_path"),
        (code(path="src\\upload.py"), "unsafe_path"),
        (code(path="src/missing.py"), "unknown_path"),
        (code(path="src/Upload.py"), "unknown_path"),
        (code(path="assets/logo.png", start=1, end=1), "unknown_path"),
        (code(path="docs/latin1.txt", start=1, end=1), "unknown_path"),
        (code(path="media/clip.mp4", start=1, end=1), "unknown_path"),
        (code(start=30, end=31), "out_of_range"),
        (code(start=5, end=4), "out_of_range"),
        (code(path="src/short.py", start=2, end=2), "out_of_range"),
        (code(quote="line 9 of the upload client"), "quote_mismatch"),
        (code(quote="line 2 of the upload client" + "x" * 500), "quote_mismatch"),
    ],
)
def test_each_rule_drops_the_citation_and_records_why(
    reader: CheckoutReader, proposed: ProposedCodeCitation, reason: str
) -> None:
    checks = checker(reader)

    assert checks.check_code(proposed) is None
    assert checks.drop_reasons() == {reason: 1}


def test_the_first_failing_rule_is_the_one_recorded(reader: CheckoutReader) -> None:
    checks = checker(reader)

    checks.check_code(code(path="../x", start=40, end=41, commit="0" * 40, quote="nope nope nope"))
    checks.check_code(code(path="src/missing.py", start=40, end=41))

    assert checks.drop_reasons() == {"unknown_path": 1, "wrong_commit": 1}


def test_a_span_longer_than_300_lines_is_out_of_range(tmp_path: Path) -> None:
    long_file = "".join(f"{n}\n" for n in range(400)).encode()
    long_reader = reader_for(tmp_path, {"long.txt": long_file})
    checks = checker(long_reader)

    assert checks.check_code(code(path="long.txt", start=1, end=300)) is not None
    assert checks.check_code(code(path="long.txt", start=1, end=301)) is None
    assert checks.drop_reasons() == {"out_of_range": 1}


def test_with_shown_lines_every_cited_line_must_have_been_shown(reader: CheckoutReader) -> None:
    checks = checker(reader, Shown({"src/upload.py": [2, 3, 4]}))

    assert checks.check_code(code(start=2, end=4)) is not None
    assert checks.check_code(code(start=4, end=5)) is None
    assert checks.check_code(code(path="src/short.py", start=1, end=1)) is None
    assert checks.drop_reasons() == {"not_shown": 2}


def test_without_shown_lines_any_line_at_head_can_be_cited(reader: CheckoutReader) -> None:
    """Deep review lets the agent read any file, so there's no rendered packet to check against."""
    cited = checker(reader, None).check_code(code(start=29, end=30))

    assert cited == CodeCitation(path="src/upload.py", start_line=29, end_line=30, commit=HEAD_SHA)


def test_a_diff_citation_must_be_shown_in_the_diff_and_exist_at_head(
    reader: CheckoutReader,
) -> None:
    checks = checker(reader)

    assert checks.check_diff(
        ProposedDiffCitation(path="src/upload.py", start_line=21, end_line=23)
    ) == DiffCitation(path="src/upload.py", start_line=21, end_line=23)
    assert (
        checks.check_diff(ProposedDiffCitation(path="src/upload.py", start_line=5, end_line=5))
        is None
    )
    assert checks.drop_reasons() == {"not_in_diff": 1}


def test_a_diff_line_missing_from_the_checkout_is_dropped(tmp_path: Path) -> None:
    """The diff shows lines 21 to 23, but the checkout's file has only 22 lines (or is absent)."""
    short = "".join(f"{n}\n" for n in range(22)).encode()
    checks = checker(reader_for(tmp_path, {"src/upload.py": short}))

    assert checks.check_diff(ProposedDiffCitation(path="src/upload.py", start_line=1, end_line=4))
    assert (
        checks.check_diff(ProposedDiffCitation(path="src/upload.py", start_line=21, end_line=23))
        is None
    )
    assert (
        checks.check_diff(
            ProposedDiffCitation(path="tests/test_upload.py", start_line=1, end_line=1)
        )
        is None
    )
    assert checks.drop_reasons() == {"missing_at_head": 2}


def test_check_citations_are_checked_against_the_ci_results(reader: CheckoutReader) -> None:
    checks = checker(reader)

    assert checks.check_ci(ProposedCheckCitation(name="tests")) == [
        CheckCitation(name="tests", source="check_run", status="completed", conclusion="success")
    ]
    assert checks.check_ci(ProposedCheckCitation(name="deploy")) == []
    assert checks.drop_reasons() == {"unknown_check": 1}


def test_the_checker_refuses_a_head_that_is_not_the_checkouts(reader: CheckoutReader) -> None:
    with pytest.raises(ValueError, match="head commit"):
        CheckoutCitationChecker(reader, "b" * 40, None, index=index())


def test_without_a_checkout_no_code_citation_checks_out() -> None:
    checks = SnapshotCitationChecker(index())

    assert checks.check_code(code()) is None
    assert (
        checks.check_diff(ProposedDiffCitation(path="src/upload.py", start_line=9, end_line=9))
        is None
    )
    assert checks.drop_reasons() == {"no_checkout": 1, "not_in_diff": 1}


CLAIM = Claim(id="c1", quote="Retries.", statement="Retries uploads.", kind="behavior")


def proposed(*codes: ProposedCodeCitation) -> ProposedAssessment:
    return ProposedAssessment(
        claim_id="c1",
        verdict="supported",
        explanation="The loop retries.",
        diff_citations=[],
        code_citations=list(codes),
        check_citations=[],
        missing_tests=[],
        edge_cases=[],
    )


def test_a_valid_code_citation_backs_the_verdict_once(reader: CheckoutReader) -> None:
    cited = code(start=2, end=2)
    [verdict], ignored = check_assessments([proposed(cited, cited)], [CLAIM], checker(reader))

    assert ignored == 0
    assert verdict.status == "supported"
    assert verdict.citations == [
        CodeCitation(path="src/upload.py", start_line=2, end_line=2, commit=HEAD_SHA)
    ]
    assert verdict.dropped_citations == 0


def test_drop_reasons_are_logged_as_counts_without_paths_or_quotes(
    reader: CheckoutReader, caplog: pytest.LogCaptureFixture
) -> None:
    bad = [code(path="src/secret_name.py"), code(quote="a quote that is not there")]

    with caplog.at_level(logging.INFO):
        [verdict], _ = check_assessments([proposed(*bad)], [CLAIM], checker(reader))

    assert verdict.status == "insufficient_evidence"
    assert verdict.dropped_citations == 2
    [record] = [r for r in caplog.records if hasattr(r, "drop_reasons")]
    assert record.drop_reasons == {"quote_mismatch": 1, "unknown_path": 1}
    assert "secret_name" not in caplog.text
    assert "a quote that" not in caplog.text
