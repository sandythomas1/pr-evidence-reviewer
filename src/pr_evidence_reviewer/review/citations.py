"""Check each citation the model gives against the evidence it was shown. Never repaired.

A diff citation must name a changed file and a line range whose every line was shown on the head
side of that file's diff. A check citation must name a CI check reported for the head commit.
Anything else is dropped. The checkout-based validator (#17) implements the same protocol.
"""

from typing import Protocol

from pr_evidence_reviewer.domain.verdicts import (
    CheckCitation,
    DiffCitation,
    ProposedCheckCitation,
    ProposedDiffCitation,
)
from pr_evidence_reviewer.review.evidence import EvidenceIndex


class CitationChecker(Protocol):
    def check_diff(self, proposed: ProposedDiffCitation) -> DiffCitation | None: ...

    def check_ci(self, proposed: ProposedCheckCitation) -> list[CheckCitation]: ...


class SnapshotCitationChecker:
    """Checks citations against the diff hunks and CI checks in the evidence packet."""

    def __init__(self, index: EvidenceIndex) -> None:
        self._index = index

    def check_diff(self, proposed: ProposedDiffCitation) -> DiffCitation | None:
        lines = self._index.head_lines.get(proposed.path)
        start, end = proposed.start_line, proposed.end_line
        if not lines or start < 1 or end < start or end - start + 1 > len(lines):
            return None
        if any(line not in lines for line in range(start, end + 1)):
            return None
        return DiffCitation(path=proposed.path, start_line=start, end_line=end)

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
