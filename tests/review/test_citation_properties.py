"""T7 / C4: the citation validator agrees with what the model was shown, and can't be talked round.

Line N rendered by `split_lines` is line N the validator checks, whatever line-break look-alikes the
file holds. Commits must be the head (or a 7+ hex prefix of it), lines must exist, quotes must be in
the cited lines after whitespace collapsing, and only strict UTF-8 text files can be cited.
"""

import ast
import sys
import tempfile
import unicodedata
from collections.abc import Mapping
from pathlib import Path

import pytest
from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from pydantic import ValidationError

import pr_evidence_reviewer
from pr_evidence_reviewer.checkout.cache import CheckoutTarget
from pr_evidence_reviewer.checkout.reader import CheckoutReader
from pr_evidence_reviewer.checkout.text import decode_strict, split_lines
from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.domain.pull_request import CommitRef
from pr_evidence_reviewer.domain.verdicts import (
    MAX_LINE_NUMBER,
    CodeCitation,
    ProposedAssessment,
    ProposedCodeCitation,
)
from pr_evidence_reviewer.review.citations import (
    CheckoutCitationChecker,
    ShownLines,
    normalise_quote,
)
from pr_evidence_reviewer.review.evidence import build_evidence
from pr_evidence_reviewer.review.verdicts import check_assessments
from tests.checkout.fakes import FakeArchiveSource, publish
from tests.review.builders import BASE_SHA, snapshot

# Mixed letters and digits, so upper-casing a prefix actually changes it.
HEAD = "0123456789abcdef0123456789abcdef01234567"
TARGET = CheckoutTarget(owner="acme", repo="shop", sha=HEAD)
PATH = "src/auth.py"

LOOKALIKE_BREAKS = ["\r", "\u2028", "\u2029", "\u0085", "\v", "\f", "\x1c", "\x1e"]
SPACES = ["\t", " ", "\u00a0", "\u2003", "\u3000"]


def make_reader(root: Path, files: Mapping[str, bytes]) -> CheckoutReader:
    return CheckoutReader(publish(root, FakeArchiveSource(files), TARGET))


def make_checker(
    reader: CheckoutReader, shown: ShownLines | None = None
) -> CheckoutCitationChecker:
    index = build_evidence(snapshot(head=CommitRef(ref="feature", sha=HEAD))).index
    return CheckoutCitationChecker(reader, HEAD, shown, index=index)


def cite(
    start: int, end: int | None = None, *, quote: str = "", commit: str = HEAD, path: str = PATH
) -> ProposedCodeCitation:
    return ProposedCodeCitation(
        path=path,
        start_line=start,
        end_line=start if end is None else end,
        commit=commit,
        quote=quote,
    )


# Each line starts with a unique marker, so a quote of line N can only be found on line N.
line_bodies = st.lists(
    st.text(alphabet=st.sampled_from([*"ab=(", *LOOKALIKE_BREAKS, *SPACES, "\u200b"]), max_size=12),
    min_size=1,
    max_size=12,
)


@st.composite
def generated_files(draw: st.DrawFn) -> bytes:
    bodies = draw(line_bodies)
    endings = draw(st.lists(st.sampled_from(["\n", "\r\n"]), min_size=len(bodies)))
    lines = [f"marker{n:03d} {body}" for n, body in enumerate(bodies, start=1)]
    text = "".join(line + ending for line, ending in zip(lines, endings, strict=False))
    if not draw(st.booleans()):
        text = text.removesuffix(endings[len(bodies) - 1])
    return text.encode()


@settings(max_examples=40, deadline=None, suppress_health_check=[HealthCheck.too_slow])
@given(data=generated_files())
def test_line_n_as_rendered_is_line_n_validated(data: bytes) -> None:
    with tempfile.TemporaryDirectory() as root:
        reader = make_reader(Path(root), {PATH: data})
        rendered = split_lines(decode_strict(data) or "")
        checker = make_checker(reader)

        assert reader.lines(PATH) == rendered
        assert reader.line_count(PATH) == len(rendered)
        for number, line in enumerate(rendered, start=1):
            assert checker.check_code(cite(number, quote=line)) == CodeCitation(
                path=PATH, start_line=number, end_line=number, commit=HEAD
            )
            marker = line.split(" ", 1)[0]
            for other in {number - 1, number + 1} & set(range(1, len(rendered) + 1)):
                assert checker.check_code(cite(other, quote=marker)) is None
        assert checker.check_code(cite(len(rendered) + 1)) is None
        assert set(checker.drop_reasons()) <= {"quote_mismatch", "out_of_range"}


THREE_LINES = b"first line of auth\nsecond line of auth\nthird line of auth"


@pytest.fixture(scope="module")
def reader(tmp_path_factory: pytest.TempPathFactory) -> CheckoutReader:
    files = {
        PATH: THREE_LINES,
        "src/spaced.py": "if user is\u00a0admin:  # nbsp\n    allow()\n".encode(),
        "src/latin1.py": "name = 'caf\xe9 au lait'\n".encode("latin-1"),
        "src/blob.bin": b"\0\0\0 not text at all",
        "src/empty.py": b"",
    }
    return make_reader(tmp_path_factory.mktemp("t7"), files)


@pytest.mark.parametrize(
    ("start", "end", "valid"),
    [(1, 1, True), (3, 3, True), (1, 3, True), (3, 4, False), (4, 4, False), (3, 2, False)],
)
def test_line_ranges_inside_the_file_only(
    reader: CheckoutReader, start: int, end: int, valid: bool
) -> None:
    checker = make_checker(reader)

    assert (checker.check_code(cite(start, end)) is not None) is valid


def test_the_ten_million_cap_is_out_of_range_but_parses(reader: CheckoutReader) -> None:
    checker = make_checker(reader)

    assert checker.check_code(cite(MAX_LINE_NUMBER)) is None
    assert checker.drop_reasons() == {"out_of_range": 1}


@pytest.mark.parametrize("line", [0, -1, MAX_LINE_NUMBER + 1, 10**18])
def test_the_schema_rejects_line_zero_and_absurd_line_numbers(line: int) -> None:
    with pytest.raises(ValidationError):
        cite(line)
    with pytest.raises(ValidationError):
        cite(1, line)


def test_an_empty_file_has_no_citable_line(reader: CheckoutReader) -> None:
    assert make_checker(reader).check_code(cite(1, path="src/empty.py")) is None


@pytest.mark.parametrize(
    ("commit", "valid"),
    [
        (HEAD, True),
        (HEAD.upper(), True),
        (HEAD[:7], True),
        (HEAD[:7].upper(), True),
        (HEAD[:20], True),
        (HEAD[:6], False),
        (HEAD[:6].upper(), False),
        (BASE_SHA, False),
        (BASE_SHA[:7], False),
        (HEAD + "0", False),
        (HEAD[:7] + "x", False),
        (f"{HEAD[:7]}\n", False),
        ("", False),
        ("HEAD", False),
    ],
)
def test_only_the_head_commit_or_a_7_hex_prefix_of_it_is_accepted(
    reader: CheckoutReader, commit: str, valid: bool
) -> None:
    checker = make_checker(reader)

    cited = checker.check_code(cite(1, commit=commit))

    assert (cited is not None) is valid
    if cited is not None:
        assert cited.commit == HEAD


@settings(max_examples=200)
@given(commit=st.text(alphabet="0123456789abcdefABCDEFxg \n", max_size=42))
def test_any_commit_is_accepted_exactly_when_it_is_a_long_enough_head_prefix(
    reader: CheckoutReader, commit: str
) -> None:
    accepted = len(commit) >= 7 and HEAD.startswith(commit.lower())

    assert (make_checker(reader).check_code(cite(1, commit=commit)) is not None) is accepted


@pytest.mark.parametrize(
    ("path", "start", "end", "quote", "valid"),
    [
        ("src/spaced.py", 1, 1, "if user is admin:", True),  # NBSP in the file, space in the quote
        ("src/spaced.py", 1, 1, "if  user\tis\u3000admin:", True),
        ("src/spaced.py", 1, 1, "if user is ad\u200bmin:", False),  # zero-width insert
        ("src/spaced.py", 1, 1, "if user\u2028is admin:", False),  # line separator isn't space
        (PATH, 1, 1, "second line of auth", False),  # the adjacent line
        (PATH, 1, 2, "of auth second line", True),  # across the cited lines
        (PATH, 1, 1, "first line of auth\nsecond", False),
        (PATH, 1, 1, "", True),  # empty: no quote
        (PATH, 1, 1, "zzzzzzz", True),  # 7 chars: counts as no quote
        (PATH, 1, 1, "   zz z zz   ", True),  # 7 chars after collapsing
        (PATH, 1, 1, "zzzzzzzz", False),  # 8 chars: checked
        (PATH, 1, 1, "first line of auth" + " x" * 300, False),  # over 500 chars
    ],
)
def test_quotes_are_checked_after_collapsing_whitespace(
    reader: CheckoutReader, path: str, start: int, end: int, quote: str, valid: bool
) -> None:
    cited = make_checker(reader).check_code(cite(start, end, quote=quote, path=path))

    assert (cited is not None) is valid


def test_whitespace_collapsing_covers_ascii_and_every_unicode_space_separator() -> None:
    ascii_spaces = set(" \t\n\v\f\r")
    expected = ascii_spaces | {
        chr(c) for c in range(sys.maxunicode + 1) if unicodedata.category(chr(c)) == "Zs"
    }
    collapsed = {
        chr(c) for c in range(sys.maxunicode + 1) if normalise_quote(f"a{chr(c)}b") == "a b"
    }

    assert collapsed == expected
    for kept in ("\u200b", "\u200d", "\u202e", "\u2066", "\u2028", "\u0085", "\ufeff"):
        assert normalise_quote(f"a{kept}b") == f"a{kept}b"


@pytest.mark.parametrize("path", ["src/latin1.py", "src/blob.bin", "src/missing.py"])
def test_undecodable_binary_and_unknown_files_are_not_citable(
    reader: CheckoutReader, path: str
) -> None:
    checker = make_checker(reader)

    assert checker.check_code(cite(1, path=path)) is None
    assert checker.drop_reasons() == {"unknown_path": 1}


class ShownOnly:
    def __init__(self, *lines: int) -> None:
        self._lines = set(lines)

    def is_shown(self, path: str, line: int) -> bool:
        return path == PATH and line in self._lines


def test_shown_lines_limit_what_can_be_cited_and_none_lifts_the_limit(
    reader: CheckoutReader,
) -> None:
    shown = make_checker(reader, ShownOnly(1, 2))
    unlimited = make_checker(reader, None)

    assert shown.check_code(cite(1, 2)) is not None
    assert shown.check_code(cite(2, 3)) is None
    assert shown.drop_reasons() == {"not_shown": 1}
    assert unlimited.check_code(cite(2, 3)) is not None


CLAIM = Claim(id="c1", quote="Checks admins.", statement="Only admins pass.", kind="security")


@pytest.mark.parametrize("verdict", ["supported", "contradicted"])
def test_a_verdict_whose_only_citation_is_dropped_is_insufficient_evidence(
    reader: CheckoutReader, verdict: str
) -> None:
    proposed = ProposedAssessment.model_validate(
        {
            "claim_id": "c1",
            "verdict": verdict,
            "explanation": "Line 1 checks the role.",
            "diff_citations": [],
            "code_citations": [cite(1, commit=BASE_SHA).model_dump()],
            "check_citations": [],
            "missing_tests": [],
            "edge_cases": [],
        }
    )

    [result], _ = check_assessments([proposed], [CLAIM], make_checker(reader))

    assert result.status == "insufficient_evidence"
    assert result.citations == []
    assert result.dropped_citations == 1


REPO_TEXT_PACKAGES = ("checkout", "evidence", "review")


def test_repository_text_is_never_split_with_str_splitlines() -> None:
    """One splitter (`split_lines`) for render, search, symbols and validation (T7)."""
    root = Path(pr_evidence_reviewer.__file__).parent
    offenders = [
        f"{path.relative_to(root)}:{node.lineno}"
        for package in REPO_TEXT_PACKAGES
        for path in sorted((root / package).rglob("*.py"))
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8")))
        if isinstance(node, ast.Attribute) and node.attr == "splitlines"
    ]

    assert offenders == []
