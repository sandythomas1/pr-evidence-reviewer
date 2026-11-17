"""Renders an evidence packet as the text the model reads, and records what that text shows.

Rendering is pure: the same packet gives the same bytes. Every line that comes from the repository
(diff lines, search context, symbol lines) starts with a line-number gutter, so no file content can
start a line as a `===` header or a boundary marker, and line-breaking characters in that content are
shown as escapes. Every name (paths, check names, symbols, search terms) is escaped the same way, so
nothing in it can start a new line (threat T10).

`ShownIndex` records the diff lines, repository lines and CI checks the text shows, so a citation is
only accepted for something the model actually saw.
"""

from collections.abc import Iterable
from dataclasses import dataclass, field

from pr_evidence_reviewer.domain.evidence import (
    ClaimEvidence,
    DiffFile,
    DiffHunk,
    EvidencePacket,
    SearchHit,
    SymbolEvidence,
    TestMapping,
)
from pr_evidence_reviewer.domain.pull_request import CheckResult
from pr_evidence_reviewer.evidence.escape import inline, visible

_GUTTER = 7


@dataclass(frozen=True)
class ShownIndex:
    """What the model was shown. Implements `ShownLines` for the citation checker."""

    # Head-side diff lines per changed file.
    head_lines: dict[str, frozenset[int]] = field(default_factory=dict)
    checks: dict[str, tuple[CheckResult, ...]] = field(default_factory=dict)
    # Paths as the model saw them (escaped), for the paths where that differs from the real path.
    displayed_paths: dict[str, str] = field(default_factory=dict)
    # Repository lines shown outside the diff: search context and symbol lines.
    code_lines: dict[str, frozenset[int]] = field(default_factory=dict)

    def is_shown(self, path: str, line: int) -> bool:
        return line in self.code_lines.get(path, frozenset()) or line in self.head_lines.get(
            path, frozenset()
        )


def render_packet(packet: EvidencePacket) -> tuple[str, ShownIndex]:
    definitions = {item for claim in packet.claim_evidence for item in claim.definition_ids}
    symbols = {symbol.id: symbol for symbol in packet.symbols}
    around = [symbol for symbol in packet.symbols if symbol.id not in definitions]
    lines = [
        f"Head commit: {packet.snapshot.head_sha}",
        "",
        "CI checks at the head commit:",
        *(_check_line(check) for check in packet.ci_checks),
        *(["(none)"] if not packet.ci_checks else []),
        "",
        "Evidence gaps:",
        *(f"- {gap.message}" for gap in packet.gaps),
        *(["(none)"] if not packet.gaps else []),
        "",
        "Diff, one block per changed file. The number on the left is the head-side line number; "
        "removed lines have none.",
        *(_file_block(file) for file in packet.diff),
        *(["(no diff lines)"] if not packet.diff else []),
        "",
        "Tests for the changed source files:",
        *(_mapping_line(entry) for entry in packet.test_mapping),
        *(["(none)"] if not packet.test_mapping else []),
    ]
    if packet.snapshot.checkout_status == "ok":
        lines += [
            "",
            "Code around the changes at the head commit. The number on the left is the line "
            "number.",
            *(_symbol_block(symbol) for symbol in around),
            *(["(none)"] if not around else []),
        ]
        for claim in packet.claim_evidence:
            lines += ["", *_claim_block(claim, symbols)]
    return "\n".join(lines), _index(packet)


def _index(packet: EvidencePacket) -> ShownIndex:
    head_lines = {
        file.path: frozenset(n for hunk in file.hunks for n in hunk.head_lines)
        for file in packet.diff
    }
    code: dict[str, set[int]] = {}
    hits = (hit for claim in packet.claim_evidence for hit in claim.hits)
    if packet.snapshot.checkout_status == "ok":
        for path, numbers in _shown_code(hits, packet.symbols):
            code.setdefault(path, set()).update(numbers)
    paths = {*head_lines, *code}
    return ShownIndex(
        head_lines=head_lines,
        checks=_checks_by_name(packet.ci_checks),
        displayed_paths={_inline(path): path for path in paths if _inline(path) != path},
        code_lines={path: frozenset(numbers) for path, numbers in code.items()},
    )


def _shown_code(
    hits: Iterable[SearchHit], symbols: Iterable[SymbolEvidence]
) -> Iterable[tuple[str, list[int]]]:
    for hit in hits:
        yield hit.path, [number for number, _text in hit.context]
    for symbol in symbols:
        yield symbol.path, [number for number, _text in symbol.shown_lines]


def _file_block(file: DiffFile) -> str:
    renamed = f" from {_inline(file.previous_path)}" if file.previous_path else ""
    header = (
        f"=== {_inline(file.path)} ({file.status}{renamed}, "
        f"+{file.additions} -{file.deletions}) ==="
    )
    return "\n".join([header, *(_hunk_block(hunk) for hunk in file.hunks)])


def _hunk_block(hunk: DiffHunk) -> str:
    header = _inline(hunk.header)
    if hunk.enclosing is not None:
        symbol = hunk.enclosing
        header += (
            f"  [in {symbol.kind} {_inline(symbol.qualified_name)}, "
            f"lines {symbol.start_line}-{symbol.end_line}]"
        )
    return f"{header}\n{hunk.text}" if hunk.text else header


def _mapping_line(entry: TestMapping) -> str:
    parts = []
    if entry.changed_tests:
        parts.append("changed tests: " + ", ".join(_inline(t) for t in entry.changed_tests))
    if entry.likely_tests:
        parts.append("likely tests: " + ", ".join(_inline(t) for t in entry.likely_tests))
    return f"- {_inline(entry.source_path)} ({entry.ecosystem}): {'; '.join(parts)}"


def _symbol_block(symbol: SymbolEvidence) -> str:
    header = (
        f"--- {symbol.kind} {_inline(symbol.qualified_name)} in {_inline(symbol.path)}, "
        f"lines {symbol.start_line}-{symbol.end_line} ---"
    )
    return "\n".join([header, *_numbered(symbol.shown_lines)])


def _claim_block(claim: ClaimEvidence, symbols: dict[str, SymbolEvidence]) -> list[str]:
    lines = [f"Repository evidence for claim {_inline(claim.claim_id)}:"]
    terms = ", ".join(f'"{_inline(query)}"' for query in claim.queries)
    lines.append(f"Search terms: {terms or '(none)'}")
    if claim.files:
        lines.append("Files the claim names: " + ", ".join(_inline(path) for path in claim.files))
    for hit in claim.hits:
        term = f'"{_inline(hit.query)}"'
        lines.append(f"--- {_inline(hit.path)}:{hit.line} (search term {term}) ---")
        lines.extend(_numbered(hit.context))
    for symbol_id in claim.definition_ids:
        lines.append("Definition: " + _symbol_block(symbols[symbol_id]))
    if not claim.hits and not claim.definition_ids:
        lines.append("(no matches at the head commit)")
    return lines


def _numbered(lines: Iterable[tuple[int, str]]) -> list[str]:
    return [f"{number:>{_GUTTER}} | {visible(text)}" for number, text in lines]


def _check_line(check: CheckResult) -> str:
    result = check.conclusion or "no result"
    return f"- {_inline(check.name)} ({check.source}): {check.status}, {result}"


def _checks_by_name(checks: Iterable[CheckResult]) -> dict[str, tuple[CheckResult, ...]]:
    grouped: dict[str, list[CheckResult]] = {}
    for check in checks:
        grouped.setdefault(check.name, []).append(check)
    return {name: tuple(items) for name, items in grouped.items()}


def _inline(text: str) -> str:
    """Escape line breaks and other control characters so a name can't start a new line."""
    return inline(text)
