"""The evidence a first-pass verdict rests on: the snapshot's diff hunks and CI checks, nothing more.

The packet is deterministic. Files are sorted by path and checks by name, so the same snapshot
always renders to the same bytes. Every diff line that exists on the head side carries its line number, so
the model can cite real lines. A character budget caps the diff; whatever is cut is named in a scope
limitation and told to the model as a gap, so a missing file is never read as a missing change.

The full evidence packet (#16) replaces this with checkout search, test mapping, and symbols.
"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass, field

from pr_evidence_reviewer.domain.pull_request import ChangedFile, CheckResult, PullRequestSnapshot

MAX_DIFF_CHARS = 60_000
MAX_NAMED_FILES = 20
_HUNK_HEADER = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@(.*)$")
_GUTTER = 7


@dataclass(frozen=True)
class EvidenceIndex:
    """What the model was shown, so each citation can be checked against it."""

    head_lines: dict[str, frozenset[int]] = field(default_factory=dict)
    checks: dict[str, tuple[CheckResult, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidencePacket:
    text: str
    index: EvidenceIndex
    scope_limitations: list[str]


@dataclass(frozen=True)
class _Hunk:
    text: str
    head_lines: frozenset[int]


def build_evidence(
    snapshot: PullRequestSnapshot, max_diff_chars: int = MAX_DIFF_CHARS
) -> EvidencePacket:
    files = sorted(snapshot.files, key=lambda item: item.path)
    checks = sorted(snapshot.checks, key=_check_order)

    diff_blocks: list[str] = []
    head_lines: dict[str, frozenset[int]] = {}
    gaps: list[str] = []
    left_out: list[str] = []
    partly_read: list[str] = []
    used = 0

    for file in files:
        header = _file_header(file)
        if file.patch is None:
            if file.additions or file.deletions:
                gaps.append(f"No diff is available for {_inline(file.path)} (binary or too large).")
            continue
        hunks = _parse_hunks(file.patch)
        if not hunks:
            gaps.append(f"The diff for {_inline(file.path)} couldn't be read.")
            continue

        kept: list[_Hunk] = []
        cost = len(header) + 1
        for hunk in hunks:
            if used + cost + len(hunk.text) + 1 > max_diff_chars:
                continue
            kept.append(hunk)
            cost += len(hunk.text) + 1
        if not kept:
            left_out.append(file.path)
            continue
        if len(kept) < len(hunks):
            partly_read.append(file.path)
        used += cost
        diff_blocks.append("\n".join([header, *(hunk.text for hunk in kept)]))
        head_lines[file.path] = frozenset(n for hunk in kept for n in hunk.head_lines)

    limitations = _budget_limitations(left_out, partly_read)
    if not checks:
        gaps.append("No CI checks were reported for the head commit.")
    gaps.extend(snapshot.scope_limitations)
    gaps.extend(limitations)

    text = "\n".join(
        [
            f"Head commit: {snapshot.head.sha}",
            "",
            "CI checks at the head commit:",
            *(_check_line(check) for check in checks),
            *(["(none)"] if not checks else []),
            "",
            "Evidence gaps:",
            *(f"- {gap}" for gap in gaps),
            *(["(none)"] if not gaps else []),
            "",
            "Diff, one block per changed file. The number on the left is the head-side line "
            "number; removed lines have none.",
            *(diff_blocks or ["(no diff lines)"]),
        ]
    )
    return EvidencePacket(
        text=text,
        index=EvidenceIndex(head_lines=head_lines, checks=_checks_by_name(checks)),
        scope_limitations=limitations,
    )


def _file_header(file: ChangedFile) -> str:
    renamed = f" from {_inline(file.previous_path)}" if file.previous_path else ""
    return (
        f"=== {_inline(file.path)} ({file.status}{renamed}, "
        f"+{file.additions} -{file.deletions}) ==="
    )


def _parse_hunks(patch: str) -> list[_Hunk]:
    """Split a unified diff into hunks. Lines before the first hunk header are skipped."""
    hunks: list[_Hunk] = []
    header: re.Match[str] | None = None
    body: list[str] = []
    for line in patch.split("\n"):
        match = _HUNK_HEADER.match(line)
        if match is None:
            body.append(line)
            continue
        if header is not None:
            hunks.append(_render_hunk(header, body))
        header, body = match, []
    if header is not None:
        hunks.append(_render_hunk(header, body))
    return hunks


def _render_hunk(header: re.Match[str], body: list[str]) -> _Hunk:
    """Number every line that exists on the head side: context and added lines."""
    next_line = int(header.group(1))
    lines = [f"@@ +{next_line} @@{header.group(2)}"]
    numbers: set[int] = set()
    for raw in body:
        marker, content = (raw[0], raw[1:]) if raw else (" ", "")
        if marker == "-":
            lines.append(f"{'':>{_GUTTER}} - {content}")
        elif marker in " +":
            lines.append(f"{next_line:>{_GUTTER}} {marker} {content}")
            numbers.add(next_line)
            next_line += 1
    return _Hunk(text="\n".join(lines), head_lines=frozenset(numbers))


def _check_line(check: CheckResult) -> str:
    result = check.conclusion or "no result"
    return f"- {_inline(check.name)} ({check.source}): {check.status}, {result}"


def _check_order(check: CheckResult) -> tuple[str, str, str, str, str]:
    return (check.name, check.source, check.status, check.conclusion or "", check.url or "")


def _checks_by_name(checks: Iterable[CheckResult]) -> dict[str, tuple[CheckResult, ...]]:
    grouped: dict[str, list[CheckResult]] = {}
    for check in checks:
        grouped.setdefault(check.name, []).append(check)
    return {name: tuple(items) for name, items in grouped.items()}


def _budget_limitations(left_out: list[str], partly_read: list[str]) -> list[str]:
    limitations: list[str] = []
    if left_out:
        limitations.append(
            f"The diff is too long to check in full. Not checked: {_name_files(left_out)}."
        )
    if partly_read:
        limitations.append(
            f"The diff is too long to check in full. Only some changes were checked in: "
            f"{_name_files(partly_read)}."
        )
    return limitations


def _name_files(paths: list[str]) -> str:
    named = ", ".join(_inline(path) for path in paths[:MAX_NAMED_FILES])
    rest = len(paths) - MAX_NAMED_FILES
    return f"{named}, and {rest} more" if rest > 0 else named


def _inline(text: str) -> str:
    """Escape line breaks and other control characters so a name can't start a new line."""
    return json.dumps(text, ensure_ascii=False)[1:-1]
