"""Check each citation the model gives against the evidence it was shown. Never repaired.

A diff citation must name a changed file and a line range whose every line was shown on the head
side of that file's diff. A check citation must name a CI check reported for the head commit.
Anything else is dropped. The checkout-based validator (#17) implements the same protocol.

A check that checks out isn't always decisive: only a finished check that passed can support a claim,
and only a finished check that failed can contradict one. See check_outcome.
"""

from typing import Literal, Protocol

from pr_evidence_reviewer.domain.verdicts import (
    CheckCitation,
    DiffCitation,
    ProposedCheckCitation,
    ProposedDiffCitation,
)
from pr_evidence_reviewer.review.evidence import EvidenceIndex

CheckOutcome = Literal["passed", "failed"]

# Check run conclusions and commit status states that mean the check ran and found a problem.
# neutral, skipped, and stale say nothing either way.
FAILED_CONCLUSIONS = frozenset({"failure", "timed_out", "cancelled", "action_required", "error"})


def check_outcome(citation: CheckCitation) -> CheckOutcome | None:
    """passed or failed for a finished check with a clear result, otherwise None."""
    if citation.status != "completed":
        return None
    if citation.conclusion == "success":
        return "passed"
    if citation.conclusion in FAILED_CONCLUSIONS:
        return "failed"
    return None


class CitationChecker(Protocol):
    def check_diff(self, proposed: ProposedDiffCitation) -> DiffCitation | None: ...

    def check_ci(self, proposed: ProposedCheckCitation) -> list[CheckCitation]: ...


class SnapshotCitationChecker:
    """Checks citations against the diff hunks and CI checks in the evidence packet."""

    def __init__(self, index: EvidenceIndex) -> None:
        self._index = index

    def check_diff(self, proposed: ProposedDiffCitation) -> DiffCitation | None:
        """The model may cite a path as the evidence showed it, escaped; map that to the real one."""
        path = proposed.path
        if path not in self._index.head_lines:
            path = self._index.displayed_paths.get(path, path)
        lines = self._index.head_lines.get(path)
        start, end = proposed.start_line, proposed.end_line
        if not lines or start < 1 or end < start or end - start + 1 > len(lines):
            return None
        if any(line not in lines for line in range(start, end + 1)):
            return None
        return DiffCitation(path=path, start_line=start, end_line=end)

    def check_ci(self, proposed: ProposedCheckCitation) -> list[CheckCitation]:
        """Every check with that name. Two checks can share a name, such as a run and a status."""
        return [
            CheckCitation(
                name=check.name,
                source=check.source,
                status=check.status,
                conclusion=check.conclusion,
            )
            for check in self._index.checks.get(proposed.name, ())
        ]
