"""The evidence a first-pass verdict rests on: the snapshot's diff hunks and CI checks, nothing more.

The packet is deterministic. Files are sorted by path, with lockfiles and generated or vendored
files last, and checks by name, so the same snapshot always renders to the same bytes. Every diff
line that exists on the head side carries its line number, so the model can cite real lines.

A character budget caps the diff. It's shared fairly: every file gets its first hunk before any file
gets its second, so a long file early in the order can't push the real change out. Whatever is cut
is named in a scope limitation and told to the model as a gap, so a missing file is never read as a
missing change. The number of CI checks shown is capped the same way.

The full evidence packet (#16) replaces this with checkout search, test mapping, and symbols.
"""

import json
from collections.abc import Iterable
from dataclasses import dataclass, field

from pr_evidence_reviewer.domain.pull_request import ChangedFile, CheckResult, PullRequestSnapshot
from pr_evidence_reviewer.evidence.diff import (
    CI_CONFIG_LIMITATION,
    Hunk,
    is_low_value,
    parse_hunks,
    touches_ci_config,
)

MAX_DIFF_CHARS = 60_000
MAX_CHECK_LINES = 200
MAX_NAMED_FILES = 20


@dataclass(frozen=True)
class EvidenceIndex:
    """What the model was shown, so each citation can be checked against it."""

    head_lines: dict[str, frozenset[int]] = field(default_factory=dict)
    checks: dict[str, tuple[CheckResult, ...]] = field(default_factory=dict)
    # Paths as the model saw them (escaped), for the paths where that differs from the real path.
    displayed_paths: dict[str, str] = field(default_factory=dict)


@dataclass(frozen=True)
class EvidencePacket:
    text: str
    index: EvidenceIndex
    scope_limitations: list[str]
    # True when part of the diff or some CI checks were cut, so a verdict may miss something.
    evidence_incomplete: bool = False
    changes_ci_config: bool = False


@dataclass
class _FileDiff:
    file: ChangedFile
    header: str
    hunks: list[Hunk]
    kept: list[bool]

    def kept_hunks(self) -> list[Hunk]:
        return [hunk for hunk, kept in zip(self.hunks, self.kept, strict=True) if kept]


def build_evidence(
    snapshot: PullRequestSnapshot,
    max_diff_chars: int = MAX_DIFF_CHARS,
    max_check_lines: int = MAX_CHECK_LINES,
) -> EvidencePacket:
    files = sorted(snapshot.files, key=lambda item: (is_low_value(item.path), item.path))
    all_checks = sorted(snapshot.checks, key=_check_order)
    checks = all_checks[:max_check_lines]

    gaps: list[str] = []
    diffs: list[_FileDiff] = []
    for file in files:
        if file.patch is None:
            if file.additions or file.deletions:
                gaps.append(f"No diff is available for {_inline(file.path)} (binary or too large).")
            continue
        hunks = parse_hunks(file.patch)
        if not hunks:
            gaps.append(f"The diff for {_inline(file.path)} couldn't be read.")
            continue
        diffs.append(_FileDiff(file, _file_header(file), hunks, [False] * len(hunks)))
    _share_budget(diffs, max_diff_chars)

    shown = [diff for diff in diffs if any(diff.kept)]
    left_out = [diff.file.path for diff in diffs if not any(diff.kept)]
    partly_read = [diff.file.path for diff in shown if not all(diff.kept)]
    limitations = _budget_limitations(left_out, partly_read)
    if len(all_checks) > len(checks):
        limitations.append(
            "There are too many CI checks to check them all. "
            f"Not checked: {len(all_checks) - len(checks)} checks."
        )
    evidence_incomplete = bool(limitations)
    changes_ci_config = any(touches_ci_config(file) for file in snapshot.files)
    if changes_ci_config:
        limitations.append(CI_CONFIG_LIMITATION)
    if not all_checks:
        gaps.append("No CI checks were reported for the head commit.")
    gaps.extend(snapshot.scope_limitations)
    gaps.extend(limitations)

    diff_blocks = ["\n".join([d.header, *(h.text for h in d.kept_hunks())]) for d in shown]
    head_lines = {
        d.file.path: frozenset(n for h in d.kept_hunks() for n in h.head_lines) for d in shown
    }
    displayed = {_inline(path): path for path in head_lines if _inline(path) != path}

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
        index=EvidenceIndex(
            head_lines=head_lines, checks=_checks_by_name(checks), displayed_paths=displayed
        ),
        scope_limitations=limitations,
        evidence_incomplete=evidence_incomplete,
        changes_ci_config=changes_ci_config,
    )


def _share_budget(diffs: list[_FileDiff], max_chars: int) -> None:
    """Keep hunks round-robin: every file's first hunk, then every file's second, and so on.

    A file's header is paid for with its first kept hunk. A hunk that doesn't fit is skipped, and a
    later, smaller one may still fit.
    """
    used = 0
    rounds = max((len(diff.hunks) for diff in diffs), default=0)
    for position in range(rounds):
        for diff in diffs:
            if position >= len(diff.hunks):
                continue
            cost = len(diff.hunks[position].text) + 1
            if not any(diff.kept):
                cost += len(diff.header) + 1
            if used + cost > max_chars:
                continue
            diff.kept[position] = True
            used += cost


def _file_header(file: ChangedFile) -> str:
    renamed = f" from {_inline(file.previous_path)}" if file.previous_path else ""
    return (
        f"=== {_inline(file.path)} ({file.status}{renamed}, "
        f"+{file.additions} -{file.deletions}) ==="
    )


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
