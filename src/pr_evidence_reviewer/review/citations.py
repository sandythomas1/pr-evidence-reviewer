"""Check each citation the model gives against the evidence it was shown. Never repaired.

A diff citation must name a changed file and a line range whose every line was shown on the head
side of that file's diff. A check citation must name a CI check reported for the head commit. A code
citation must point at lines of a text file in the checkout at the head commit (threat T7). Anything
else is dropped, and each checker counts its drops by reason for the logs.

A check that checks out isn't always decisive: only a finished check that passed can support a claim,
and only a finished check that failed can contradict one. See check_outcome.
"""

import re
from collections import Counter
from typing import Literal, Protocol

from pr_evidence_reviewer.checkout.reader import CheckoutReader, UnsafePathError
from pr_evidence_reviewer.domain.verdicts import (
    MAX_QUOTE_CHARS,
    CheckCitation,
    CodeCitation,
    DiffCitation,
    ProposedCheckCitation,
    ProposedCodeCitation,
    ProposedDiffCitation,
)
from pr_evidence_reviewer.review.evidence import EvidenceIndex

CheckOutcome = Literal["passed", "failed"]
DropReason = Literal[
    "wrong_commit",
    "unsafe_path",
    "unknown_path",
    "out_of_range",
    "not_shown",
    "quote_mismatch",
    "not_in_diff",
    "missing_at_head",
    "unknown_check",
    "no_checkout",
]

# Check run conclusions and commit status states that mean the check ran and found a problem.
# neutral, skipped, and stale say nothing either way.
FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "cancelled", "action_required", "error"})

MAX_CITED_LINES = 300
# A quote shorter than this after normalising says too little to check, so it's ignored (C4).
MIN_QUOTE_CHARS = 8
MIN_COMMIT_PREFIX = 7

_COMMIT_PREFIX = re.compile(rf"[0-9a-fA-F]{{{MIN_COMMIT_PREFIX},40}}")
_HEAD_SHA = re.compile(r"[0-9a-f]{40}")
# ASCII whitespace plus every Unicode Zs (space separator) character. Zero-width, bidi, line and
# paragraph separators are deliberately absent: they change what the text says or where it splits.
_SPACES = re.compile("[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u202f\u205f\u3000]+")


def check_outcome(citation: CheckCitation) -> CheckOutcome | None:
    """passed or failed for a finished check with a clear result, otherwise None."""
    if citation.status != "completed":
        return None
    if citation.conclusion == "success":
        return "passed"
    if citation.conclusion in FAILED_CONCLUSIONS:
        return "failed"
    return None


def normalise_quote(text: str) -> str:
    """Collapse runs of ASCII and Unicode space-separator whitespace to one space, then trim."""
    return _SPACES.sub(" ", text).strip(" ")


class ShownLines(Protocol):
    """The repository lines the rendered evidence showed the model, by path and line number."""

    def is_shown(self, path: str, line: int) -> bool: ...


class CitationChecker(Protocol):
    """One checker per review: `drop_reasons` counts every citation it dropped since it was made."""

    def check_diff(self, proposed: ProposedDiffCitation) -> DiffCitation | None: ...

    def check_code(self, proposed: ProposedCodeCitation) -> CodeCitation | None: ...

    def check_ci(self, proposed: ProposedCheckCitation) -> list[CheckCitation]: ...

    def drop_reasons(self) -> dict[str, int]: ...


class _DropCounter:
    def __init__(self) -> None:
        self._drops: Counter[DropReason] = Counter()

    def drop_reasons(self) -> dict[str, int]:
        return dict(sorted(self._drops.items()))

    def _drop(self, reason: DropReason) -> None:
        self._drops[reason] += 1


class SnapshotCitationChecker(_DropCounter):
    """Checks citations against the diff hunks and CI checks in the evidence packet. Without a
    checkout there's nothing to check code against, so no code citation checks out."""

    def __init__(self, index: EvidenceIndex) -> None:
        super().__init__()
        self._index = index

    def check_diff(self, proposed: ProposedDiffCitation) -> DiffCitation | None:
        cited = _shown_in_diff(self._index, proposed)
        if cited is None:
            self._drop("not_in_diff")
        return cited

    def check_code(self, proposed: ProposedCodeCitation) -> CodeCitation | None:
        self._drop("no_checkout")
        return None

    def check_ci(self, proposed: ProposedCheckCitation) -> list[CheckCitation]:
        matches = _checks_named(self._index, proposed)
        if not matches:
            self._drop("unknown_check")
        return matches


class CheckoutCitationChecker(_DropCounter):
    """Checks citations against the checkout at the head commit as well as the diff and CI checks.

    `shown` is what the rendered evidence showed the model; deep review passes None because its
    agent may read any file. A blob that vanished from the cache raises `CheckoutReadError`, which
    the review handles by falling back to the diff.
    """

    def __init__(
        self,
        reader: CheckoutReader,
        head_sha: str,
        shown: ShownLines | None,
        *,
        index: EvidenceIndex,
    ) -> None:
        if not _HEAD_SHA.fullmatch(head_sha) or head_sha != reader.head_sha:
            raise ValueError("The checkout isn't at the head commit being reviewed.")
        super().__init__()
        self._reader = reader
        self._head_sha = head_sha
        self._shown = shown
        self._index = index

    def check_diff(self, proposed: ProposedDiffCitation) -> DiffCitation | None:
        """Shown in the diff, as before, and also present in the head file of the checkout."""
        cited = _shown_in_diff(self._index, proposed)
        if cited is None:
            self._drop("not_in_diff")
            return None
        count = self._line_count(cited.path)
        if count is None or cited.end_line > count:
            self._drop("missing_at_head")
            return None
        return cited

    def check_code(self, proposed: ProposedCodeCitation) -> CodeCitation | None:
        """The five rules in order; the first that fails is the reason recorded."""
        reason, cited = self._code(proposed)
        if reason is not None:
            self._drop(reason)
        return cited

    def check_ci(self, proposed: ProposedCheckCitation) -> list[CheckCitation]:
        matches = _checks_named(self._index, proposed)
        if not matches:
            self._drop("unknown_check")
        return matches

    def _code(
        self, proposed: ProposedCodeCitation
    ) -> tuple[DropReason, None] | tuple[None, CodeCitation]:
        if not self._is_head(proposed.commit):
            return "wrong_commit", None
        try:
            entry = self._reader.file(proposed.path)
        except UnsafePathError:
            return "unsafe_path", None
        if entry is None or entry.kind != "text":
            return "unknown_path", None
        lines = self._reader.lines(entry.path) or []
        start, end = proposed.start_line, proposed.end_line
        if not 1 <= start <= end <= len(lines) or end - start + 1 > MAX_CITED_LINES:
            return "out_of_range", None
        if self._shown is not None and not all(
            self._shown.is_shown(entry.path, line) for line in range(start, end + 1)
        ):
            return "not_shown", None
        if not _quote_matches(proposed.quote, lines[start - 1 : end]):
            return "quote_mismatch", None
        return None, CodeCitation(
            path=entry.path, start_line=start, end_line=end, commit=self._head_sha
        )

    def _is_head(self, commit: str) -> bool:
        """The head commit, in full or as a prefix of at least 7 hex digits. Never another commit."""
        return bool(_COMMIT_PREFIX.fullmatch(commit)) and self._head_sha.startswith(commit.lower())

    def _line_count(self, path: str) -> int | None:
        try:
            return self._reader.line_count(path)
        except UnsafePathError:
            return None


def _quote_matches(quote: str, cited_lines: list[str]) -> bool:
    """A quote too short to say anything counts as no quote; a longer one must be in the lines."""
    wanted = normalise_quote(quote)
    if len(wanted) < MIN_QUOTE_CHARS:
        return True
    return len(wanted) <= MAX_QUOTE_CHARS and wanted in normalise_quote("\n".join(cited_lines))


def _shown_in_diff(index: EvidenceIndex, proposed: ProposedDiffCitation) -> DiffCitation | None:
    """The model may cite a path as the evidence showed it, escaped; map that to the real one."""
    path = proposed.path
    if path not in index.head_lines:
        path = index.displayed_paths.get(path, path)
    lines = index.head_lines.get(path)
    start, end = proposed.start_line, proposed.end_line
    if not lines or start < 1 or end < start or end - start + 1 > len(lines):
        return None
    if any(line not in lines for line in range(start, end + 1)):
        return None
    return DiffCitation(path=path, start_line=start, end_line=end)


def _checks_named(index: EvidenceIndex, proposed: ProposedCheckCitation) -> list[CheckCitation]:
    """Every check with that name. Two checks can share a name, such as a run and a status."""
    return [
        CheckCitation(
            name=check.name,
            source=check.source,
            status=check.status,
            conclusion=check.conclusion,
        )
        for check in index.checks.get(proposed.name, ())
    ]
