"""Builds the evidence packet: the diff, CI checks, test mapping, search hits and symbol definitions.

`build_packet` is a pure function of the snapshot, the checkout at the head commit and the claims.
Everything is sorted before ids are given, so the same inputs give the same packet. The builder also
cuts each part to its character budget, so the stored packet is exactly what the model sees: every
cut sets `cut`, adds a `budget_cut` gap and a scope limitation, and marks the packet incomplete.

With no checkout (`reader=None`) the packet holds only the diff and CI checks, with a gap that says
so. A gap is never approval: it names evidence the model didn't see.
"""

import json
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field

from pr_evidence_reviewer.checkout.reader import CheckoutReader
from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.domain.evidence import (
    MAX_SHOWN_SYMBOL_LINES,
    ClaimEvidence,
    ClaimRef,
    DiffFile,
    DiffHunk,
    EvidenceGap,
    EvidencePacket,
    GapKind,
    PacketSnapshot,
    SearchHit,
    SymbolEvidence,
    SymbolRef,
    TestMapping,
)
from pr_evidence_reviewer.domain.pull_request import ChangedFile, CheckResult, PullRequestSnapshot
from pr_evidence_reviewer.evidence import search
from pr_evidence_reviewer.evidence.diff import (
    CI_CONFIG_LIMITATION,
    Hunk,
    is_low_value,
    parse_hunks,
    touches_ci_config,
)
from pr_evidence_reviewer.evidence.queries import (
    ClaimQueries,
    definition_name,
    extract_queries,
    match_paths,
)
from pr_evidence_reviewer.evidence.symbols import Symbol, SymbolIndex, SymbolLookup, supports
from pr_evidence_reviewer.evidence.test_mapping import MappedTests, map_tests

MAX_NAMED_FILES = 20
MAX_SHOWN_LINE_CHARS = 240
# Rough rendering overhead per shown line (gutter, separators), so the budget tracks the text sent.
_LINE_OVERHEAD = 10

# Skip reasons that hide a file's content at the head commit. When the pull request changed the
# file, or a claim points at it, the verdict may miss something (C5). Binary, LFS and link entries
# have nothing to read anyway, and the diff already says what changed in them.
_INCOMPLETE_REASONS = frozenset({"export_ignored", "too_large", "unverified", "undecodable"})
_SKIP_PHRASES = {
    "export_ignored": "it's left out of GitHub's download of the repository",
    "too_large": "it's too large",
    "unverified": "its content couldn't be checked against the commit",
    "undecodable": "it isn't UTF-8 text",
    "binary": "it's a binary file",
    "lfs": "it's stored in Git LFS",
    "link": "it's a link",
    "submodule": "it's a submodule",
    "low_value": "it's a lockfile or a generated file",
    "corpus_cap": "the repository is too large to search in full",
}
_SKIP_GAP_KINDS: dict[str, GapKind] = {
    "export_ignored": "export_ignored",
    "lfs": "lfs",
    "submodule": "submodule",
}


@dataclass(frozen=True)
class Budgets:
    """Character budgets, the same bytes for every provider (about four characters a token)."""

    diff_chars: int = 60_000
    mapping_chars: int = 8_000
    claim_chars: int = 4_000
    claims_total_chars: int = 80_000
    max_checks: int = 200


DEFAULT_BUDGETS = Budgets()
_DEFAULT_SEARCH_LIMITS = search.SearchLimits()


@dataclass
class _Gaps:
    gaps: list[EvidenceGap] = field(default_factory=list)
    limitations: list[str] = field(default_factory=list)
    incomplete: bool = False

    def add(
        self,
        kind: GapKind,
        message: str,
        *,
        path: str | None = None,
        claim_id: str | None = None,
        limitation: bool = False,
        incomplete: bool = False,
    ) -> None:
        self.gaps.append(
            EvidenceGap(id="", kind=kind, message=message, path=path, claim_id=claim_id)
        )
        if limitation and message not in self.limitations:
            self.limitations.append(message)
        self.incomplete = self.incomplete or incomplete

    def sorted(self) -> tuple[EvidenceGap, ...]:
        unique = {
            (gap.kind, gap.path or "", gap.claim_id or "", gap.message): gap for gap in self.gaps
        }
        return tuple(
            gap.model_copy(update={"id": f"g{n}"})
            for n, (_key, gap) in enumerate(sorted(unique.items()), start=1)
        )


@dataclass
class _FileDiff:
    file: ChangedFile
    header_cost: int
    hunks: list[Hunk]
    kept: list[bool]


@dataclass(frozen=True)
class _Item:
    """One piece of a claim's evidence, with its cost in characters."""

    cost: int
    hit: search.SearchHit | None = None
    symbol: Symbol | None = None


def build_packet(
    snapshot: PullRequestSnapshot,
    reader: CheckoutReader | None,
    claims: Sequence[Claim],
    budgets: Budgets = DEFAULT_BUDGETS,
    *,
    checkout_problem: str | None = None,
    search_limits: search.SearchLimits = _DEFAULT_SEARCH_LIMITS,
) -> EvidencePacket:
    """The packet for `claims`. Raises `CheckoutReadError` if the checkout changes while it's read;
    the caller then builds again with `reader=None`."""
    gaps = _Gaps()
    ordered_claims = sorted(claims, key=lambda claim: (len(claim.id), claim.id))
    diff, kept_hunks = _diff(snapshot, budgets.diff_chars, gaps)
    checks = _checks(snapshot, budgets.max_checks, gaps)
    changes_ci_config = any(touches_ci_config(file) for file in snapshot.files)
    if changes_ci_config:
        gaps.add("ci_config", CI_CONFIG_LIMITATION, limitation=True)
    for limitation in snapshot.scope_limitations:
        gaps.add("snapshot_limit", limitation)

    if reader is None:
        _checkout_unavailable(snapshot.head.sha, checkout_problem, gaps)
        mapping = map_tests(snapshot.files, None)
        repository = _RepositoryEvidence(
            diff=diff, test_mapping=_test_mapping(mapping, budgets.mapping_chars, gaps)
        )
    else:
        repo = _Repository(snapshot, reader, search_limits)
        repository = repo.evidence(diff, kept_hunks, ordered_claims, budgets, gaps)

    return EvidencePacket(
        snapshot=PacketSnapshot(
            repo=f"{snapshot.ref.owner}/{snapshot.ref.repo}",
            pr_number=snapshot.ref.number,
            base_sha=snapshot.base.sha,
            head_sha=snapshot.head.sha,
            tree_sha=reader.tree_sha if reader else None,
            checkout_status="ok" if reader else "unavailable",
            files_verified=len(reader.files()) if reader else 0,
            files_skipped=len(reader.skipped()) if reader else 0,
        ),
        claims=tuple(ClaimRef(id=c.id, statement=c.statement, kind=c.kind) for c in ordered_claims),
        diff=repository.diff,
        ci_checks=checks,
        test_mapping=repository.test_mapping,
        symbols=repository.symbols,
        claim_evidence=repository.claim_evidence,
        gaps=gaps.sorted(),
        scope_limitations=tuple(gaps.limitations),
        evidence_incomplete=gaps.incomplete,
        changes_ci_config=changes_ci_config,
    )


@dataclass(frozen=True)
class _RepositoryEvidence:
    diff: tuple[DiffFile, ...] = ()
    test_mapping: tuple[TestMapping, ...] = ()
    symbols: tuple[SymbolEvidence, ...] = ()
    claim_evidence: tuple[ClaimEvidence, ...] = ()


class _Repository:
    """The checkout-backed parts of one build. Not thread-safe: one per build."""

    def __init__(
        self,
        snapshot: PullRequestSnapshot,
        reader: CheckoutReader,
        search_limits: search.SearchLimits,
    ) -> None:
        self.reader = reader
        self.snapshot_files = snapshot.files
        self.changed = sorted({file.path for file in snapshot.files if file.status != "removed"})
        self.all_paths = sorted(
            {entry.path for entry in reader.files()} | {skip.path for skip in reader.skipped()}
        )
        self.symbols = SymbolIndex(reader, changed_paths=self.changed)
        self.search_limits = search_limits

    def evidence(
        self,
        diff: tuple[DiffFile, ...],
        kept_hunks: Sequence[tuple[str, DiffHunk, Hunk]],
        claims: Sequence[Claim],
        budgets: Budgets,
        gaps: _Gaps,
    ) -> _RepositoryEvidence:
        enclosing = self.enclosing(kept_hunks, gaps)
        mapping = map_tests(self.snapshot_files, self.all_paths)
        vocabulary = self.vocabulary(symbol.name for symbol in enclosing.values() if symbol)
        claim_queries = {
            claim.id: extract_queries(claim, vocabulary=vocabulary) for claim in claims
        }
        named_files = {
            claim_id: match_paths(queries.paths, self.all_paths)
            for claim_id, queries in claim_queries.items()
        }
        relevant = {path for paths in named_files.values() for path in paths}
        relevant.update(test for source in mapping.sources for test in source.likely_tests)
        corpus = self.corpus(relevant)
        _skip_gaps(corpus, self.reader, gaps)

        queries = [query for parsed in claim_queries.values() for query in parsed.literals]
        found = self.search(corpus, queries, gaps)
        names = [name for query in queries if (name := definition_name(query))]
        definitions = self.definitions(names, gaps, claim_queries)

        test_mapping = _test_mapping(mapping, budgets.mapping_chars, gaps)
        enclosing_symbols = sorted({s for s in enclosing.values() if s}, key=_symbol_order)
        enclosing_lines = _symbols_within(
            self, enclosing_symbols, budgets.mapping_chars - _mapping_cost(test_mapping), gaps
        )
        picks = _claim_picks(self, claims, claim_queries, found, definitions, budgets, gaps)
        symbols, claim_evidence = _number_symbols(
            self, enclosing_lines, picks, claim_queries, named_files
        )
        return _RepositoryEvidence(
            diff=_annotate(diff, enclosing),
            test_mapping=test_mapping,
            symbols=symbols,
            claim_evidence=claim_evidence,
        )

    def enclosing(
        self, kept: Sequence[tuple[str, DiffHunk, Hunk]], gaps: _Gaps
    ) -> dict[tuple[str, str], Symbol | None]:
        found: dict[tuple[str, str], Symbol | None] = {}
        for path, hunk, parsed in kept:
            lines = parsed.added_lines or parsed.head_lines
            if not lines or not supports(path) or not self.reader.has(path):
                continue
            lookup = self.symbols.enclosing(path, min(lines))
            _symbol_skip_gaps(lookup, gaps)
            found[(path, hunk.id)] = lookup.symbols[0] if lookup.symbols else None
        return found

    def vocabulary(self, symbol_names: Iterable[str]) -> frozenset[str]:
        stems = {path.rsplit("/", 1)[-1].split(".", 1)[0] for path in self.changed}
        return frozenset({*stems, *symbol_names} - {""})

    def corpus(self, relevant: Iterable[str]) -> search.Corpus:
        return search.select_corpus(
            self.reader,
            changed_paths=self.changed,
            relevant_paths=relevant,
            limits=self.search_limits,
        )

    def search(
        self, corpus: search.Corpus, queries: Sequence[str], gaps: _Gaps
    ) -> search.SearchResult:
        result = search.search_literal(self.reader, corpus, queries, limits=self.search_limits)
        unique = len(dict.fromkeys(query for query in queries if query.strip()))
        dropped = unique > self.search_limits.max_queries
        for limitation in result.limitations:
            incomplete = dropped or bool(result.timed_out)
            kind: GapKind = "search_timeout" if "out of time" in limitation else "search_limit"
            gaps.add(kind, limitation, limitation=True, incomplete=incomplete)
        return result

    def definitions(
        self,
        names: Sequence[str],
        gaps: _Gaps,
        claim_queries: dict[str, ClaimQueries],
    ) -> dict[str, tuple[Symbol, ...]]:
        lookups = self.symbols.find_definitions(list(dict.fromkeys(names)))
        for claim_id, queries in claim_queries.items():
            for query in queries.literals:
                name = definition_name(query)
                lookup = lookups.get(name or "")
                if lookup is not None and (lookup.truncated or lookup.skipped):
                    gaps.add(
                        "symbols_skipped",
                        f"Not every definition of {_inline(name or '')} was looked at.",
                        claim_id=claim_id,
                    )
        return {name: lookup.symbols for name, lookup in lookups.items()}

    def shown_lines(self, symbol: Symbol) -> tuple[tuple[int, str], ...]:
        lines = self.reader.lines(symbol.path) or []
        last = min(symbol.end_line, symbol.start_line + MAX_SHOWN_SYMBOL_LINES - 1, len(lines))
        return tuple(
            (number, lines[number - 1][:MAX_SHOWN_LINE_CHARS])
            for number in range(symbol.start_line, last + 1)
        )


def _diff(
    snapshot: PullRequestSnapshot, max_chars: int, gaps: _Gaps
) -> tuple[tuple[DiffFile, ...], list[tuple[str, DiffHunk, Hunk]]]:
    files = sorted(snapshot.files, key=lambda item: (is_low_value(item.path), item.path))
    diffs: list[_FileDiff] = []
    for file in files:
        if file.patch is None:
            if file.additions or file.deletions:
                gaps.add(
                    "no_diff",
                    f"No diff is available for {_inline(file.path)} (binary or too large).",
                    path=file.path,
                )
            continue
        hunks = parse_hunks(file.patch)
        if not hunks:
            gaps.add(
                "unreadable_diff",
                f"The diff for {_inline(file.path)} couldn't be read.",
                path=file.path,
            )
            continue
        diffs.append(_FileDiff(file, len(_file_header(file)), hunks, [False] * len(hunks)))
    _share_budget(diffs, max_chars)

    left_out = [diff.file.path for diff in diffs if not any(diff.kept)]
    partly = [diff.file.path for diff in diffs if any(diff.kept) and not all(diff.kept)]
    if left_out:
        _budget_gap(gaps, f"The diff is too long to check in full. Not checked: {_name(left_out)}.")
    if partly:
        _budget_gap(
            gaps,
            "The diff is too long to check in full. Only some changes were checked in: "
            f"{_name(partly)}.",
        )

    shown: list[DiffFile] = []
    kept_hunks: list[tuple[str, DiffHunk, Hunk]] = []
    ordinal = 0
    for diff in diffs:
        if not any(diff.kept):
            continue
        models: list[DiffHunk] = []
        for hunk, kept in zip(diff.hunks, diff.kept, strict=True):
            if not kept:
                continue
            ordinal += 1
            header, _newline, body = hunk.text.partition("\n")
            model = DiffHunk(
                id=f"h{ordinal}",
                header=header,
                head_lines=tuple(sorted(hunk.head_lines)),
                text=body,
            )
            models.append(model)
            kept_hunks.append((diff.file.path, model, hunk))
        file = diff.file
        shown.append(
            DiffFile(
                path=file.path,
                status=file.status,
                previous_path=file.previous_path,
                additions=file.additions,
                deletions=file.deletions,
                cut=not all(diff.kept),
                hunks=tuple(models),
            )
        )
    return tuple(shown), kept_hunks


def _annotate(
    diff: tuple[DiffFile, ...], enclosing: dict[tuple[str, str], Symbol | None]
) -> tuple[DiffFile, ...]:
    """Name the function or class around each hunk on its header line."""
    return tuple(
        file.model_copy(
            update={
                "hunks": tuple(
                    hunk.model_copy(update={"enclosing": _ref(enclosing.get((file.path, hunk.id)))})
                    for hunk in file.hunks
                )
            }
        )
        for file in diff
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
                cost += diff.header_cost + 1
            if used + cost > max_chars:
                continue
            diff.kept[position] = True
            used += cost


def _checks(snapshot: PullRequestSnapshot, max_checks: int, gaps: _Gaps) -> tuple[CheckResult, ...]:
    ordered = sorted(snapshot.checks, key=_check_order)
    if not ordered:
        gaps.add("no_ci", "No CI checks were reported for the head commit.")
    if len(ordered) > max_checks:
        gaps.add(
            "too_many_checks",
            "There are too many CI checks to check them all. "
            f"Not checked: {len(ordered) - max_checks} checks.",
            limitation=True,
            incomplete=True,
        )
    return tuple(ordered[:max_checks])


def _checkout_unavailable(head_sha: str, problem: str | None, gaps: _Gaps) -> None:
    message = (
        f"The repository at {head_sha[:7]} couldn't be read, so only the diff and CI checks were "
        "used."
    )
    if problem:
        message = f"{message} {problem}"
    gaps.add("checkout_unavailable", message, limitation=True, incomplete=True)


def _skip_gaps(corpus: search.Corpus, reader: CheckoutReader, gaps: _Gaps) -> None:
    for skipped in corpus.skipped:
        if skipped.reason == "binary" and skipped.path in corpus.changed:
            continue
        phrase = _SKIP_PHRASES.get(skipped.reason, "its name or type isn't supported")
        message = f"{_inline(skipped.path)} wasn't read: {phrase}."
        incomplete = skipped.reason in _INCOMPLETE_REASONS
        gaps.add(
            _SKIP_GAP_KINDS.get(skipped.reason, "skipped_files"),
            message,
            path=skipped.path,
            limitation=incomplete,
            incomplete=incomplete,
        )
    for limitation in corpus.limitations:
        gaps.add("search_limit", limitation, limitation=True, incomplete=True)
    if reader.refetch_capped:
        ignored = sum(1 for skip in reader.skipped() if skip.reason == "export_ignored")
        gaps.add(
            "export_ignored",
            "Too many files are left out of GitHub's download of this repository to fetch them "
            f"one by one. Not read: {_count(ignored, 'file')}.",
            limitation=True,
            incomplete=True,
        )


def _symbol_skip_gaps(lookup: SymbolLookup, gaps: _Gaps) -> None:
    for skip in lookup.skipped:
        gaps.add(
            "symbols_skipped",
            f"The functions and classes in {_inline(skip.path)} couldn't be read.",
            path=skip.path,
        )


def _test_mapping(mapping: MappedTests, max_chars: int, gaps: _Gaps) -> tuple[TestMapping, ...]:
    where = "" if mapping.repository_checked else " among the changed files"
    for source in mapping.gaps:
        gaps.add(
            "no_test_found",
            f"No test was found for {_inline(source.source_path)}{where}.",
            path=source.source_path,
        )
    entries = [
        TestMapping(
            source_path=source.source_path,
            ecosystem=source.ecosystem,
            changed_tests=source.changed_tests,
            likely_tests=source.likely_tests,
        )
        for source in mapping.sources
        if not source.no_test_found
    ]
    kept, cut = _within(entries, [_entry_cost(entry) for entry in entries], max_chars)
    if cut:
        _budget_gap(
            gaps,
            f"The test mapping is too long to show in full. Not shown: {_count(cut, 'file')}.",
        )
    return tuple(kept)


def _symbols_within(
    repo: _Repository, symbols: Sequence[Symbol], max_chars: int, gaps: _Gaps
) -> dict[Symbol, tuple[tuple[int, str], ...]]:
    shown = [(symbol, repo.shown_lines(symbol)) for symbol in symbols]
    kept, cut = _within(shown, [_lines_cost(lines) for _symbol, lines in shown], max_chars)
    if cut:
        _budget_gap(
            gaps,
            "The code around the changes is too long to show in full. "
            f"Not shown: {_count(cut, 'definition')}.",
        )
    return dict(kept)


@dataclass
class _ClaimPick:
    claim: Claim
    hits: list[SearchHit]
    symbols: list[Symbol]
    cut: bool


def _claim_picks(
    repo: _Repository,
    claims: Sequence[Claim],
    claim_queries: dict[str, ClaimQueries],
    found: search.SearchResult,
    definitions: dict[str, tuple[Symbol, ...]],
    budgets: Budgets,
    gaps: _Gaps,
) -> list[_ClaimPick]:
    """Each claim's hits and definitions, shared round-robin in claim order: every claim gets its
    first item before any gets its second, within its own budget and the total."""
    changed = set(repo.changed)
    candidates: dict[str, list[_Item]] = {}
    for claim in claims:
        literals = claim_queries[claim.id].literals
        items: list[_Item] = []
        seen: set[tuple[str, int]] = set()
        hits = [hit for query in literals for hit in found.for_query(query)]
        for hit in sorted(hits, key=lambda h: (h.path not in changed, h.path, h.line)):
            if (hit.path, hit.line) not in seen:
                seen.add((hit.path, hit.line))
                items.append(_Item(cost=_hit_cost(hit), hit=hit))
        claim_symbols = {
            symbol
            for query in literals
            for symbol in definitions.get(definition_name(query) or "", ())
        }
        for symbol in sorted(claim_symbols, key=_symbol_order):
            cost = _lines_cost(repo.shown_lines(symbol)) + len(symbol.path)
            items.append(_Item(cost=cost, symbol=symbol))
        candidates[claim.id] = items

    kept = _share_claim_budget(candidates, budgets)
    picks: list[_ClaimPick] = []
    ordinal = 0
    for claim in claims:
        chosen = [
            item for item, keep in zip(candidates[claim.id], kept[claim.id], strict=True) if keep
        ]
        hits_kept: list[SearchHit] = []
        for item in chosen:
            if item.hit is not None:
                ordinal += 1
                hits_kept.append(_search_hit(f"s{ordinal}", item.hit))
        dropped = len(candidates[claim.id]) - len(chosen)
        if dropped:
            _budget_gap(
                gaps,
                f"The evidence for claim {_inline(claim.id)} is too long to show in full. "
                f"Not shown: {_count(dropped, 'item')}.",
                claim_id=claim.id,
            )
        symbols = [item.symbol for item in chosen if item.symbol is not None]
        picks.append(_ClaimPick(claim, hits_kept, symbols, bool(dropped)))
    return picks


def _number_symbols(
    repo: _Repository,
    enclosing: dict[Symbol, tuple[tuple[int, str], ...]],
    picks: Sequence[_ClaimPick],
    claim_queries: dict[str, ClaimQueries],
    named_files: dict[str, tuple[str, ...]],
) -> tuple[tuple[SymbolEvidence, ...], tuple[ClaimEvidence, ...]]:
    """Give every kept symbol a `y` id in (path, start line, name) order and point claims at them."""
    lines = dict(enclosing)
    for pick in picks:
        for symbol in pick.symbols:
            lines.setdefault(symbol, repo.shown_lines(symbol))
    ordered = sorted(lines, key=_symbol_order)
    ids = {symbol: f"y{n}" for n, symbol in enumerate(ordered, start=1)}
    symbols = tuple(_symbol_evidence(symbol, lines[symbol], ids[symbol]) for symbol in ordered)
    evidence = tuple(
        ClaimEvidence(
            claim_id=pick.claim.id,
            queries=claim_queries[pick.claim.id].literals,
            files=named_files[pick.claim.id],
            hits=tuple(pick.hits),
            definition_ids=tuple(ids[symbol] for symbol in pick.symbols),
            cut=pick.cut,
        )
        for pick in picks
    )
    return symbols, evidence


def _share_claim_budget(
    candidates: dict[str, list[_Item]], budgets: Budgets
) -> dict[str, list[bool]]:
    kept = {claim_id: [False] * len(items) for claim_id, items in candidates.items()}
    used = dict.fromkeys(candidates, 0)
    total = 0
    rounds = max((len(items) for items in candidates.values()), default=0)
    for position in range(rounds):
        for claim_id, items in candidates.items():
            if position >= len(items):
                continue
            cost = items[position].cost
            if used[claim_id] + cost > budgets.claim_chars:
                continue
            if total + cost > budgets.claims_total_chars:
                continue
            kept[claim_id][position] = True
            used[claim_id] += cost
            total += cost
    return kept


def _within[T](items: Sequence[T], costs: Sequence[int], max_chars: int) -> tuple[list[T], int]:
    """Keep items in order while they fit; one that doesn't is skipped and a smaller one may fit."""
    kept: list[T] = []
    used = 0
    for item, cost in zip(items, costs, strict=True):
        if used + cost <= max_chars:
            kept.append(item)
            used += cost
    return kept, len(items) - len(kept)


def _budget_gap(gaps: _Gaps, message: str, *, claim_id: str | None = None) -> None:
    gaps.add("budget_cut", message, claim_id=claim_id, limitation=True, incomplete=True)


def _search_hit(hit_id: str, hit: search.SearchHit) -> SearchHit:
    return SearchHit(id=hit_id, query=hit.query, path=hit.path, line=hit.line, context=hit.context)


def _symbol_evidence(
    symbol: Symbol, lines: tuple[tuple[int, str], ...], symbol_id: str = ""
) -> SymbolEvidence:
    return SymbolEvidence(
        id=symbol_id,
        name=symbol.name,
        qualified_name=symbol.qualified_name,
        kind=symbol.kind,
        path=symbol.path,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
        shown_lines=lines,
    )


def _ref(symbol: Symbol | None) -> SymbolRef | None:
    if symbol is None:
        return None
    return SymbolRef(
        name=symbol.name,
        qualified_name=symbol.qualified_name,
        kind=symbol.kind,
        start_line=symbol.start_line,
        end_line=symbol.end_line,
    )


def _symbol_order(symbol: Symbol) -> tuple[str, int, str, int, str]:
    return (symbol.path, symbol.start_line, symbol.name, symbol.end_line, symbol.qualified_name)


def _check_order(check: CheckResult) -> tuple[str, str, str, str, str]:
    return (check.name, check.source, check.status, check.conclusion or "", check.url or "")


def _entry_cost(entry: TestMapping) -> int:
    paths = (entry.source_path, *entry.changed_tests, *entry.likely_tests)
    return sum(len(path) + 4 for path in paths) + _LINE_OVERHEAD


def _mapping_cost(entries: Iterable[TestMapping]) -> int:
    return sum(_entry_cost(entry) for entry in entries)


def _lines_cost(lines: Iterable[tuple[int, str]]) -> int:
    return sum(len(text) + _LINE_OVERHEAD for _number, text in lines) + _LINE_OVERHEAD


def _hit_cost(hit: search.SearchHit) -> int:
    return len(hit.path) + len(hit.query) + _lines_cost(hit.context)


def _file_header(file: ChangedFile) -> str:
    renamed = f" from {_inline(file.previous_path)}" if file.previous_path else ""
    return (
        f"=== {_inline(file.path)} ({file.status}{renamed}, "
        f"+{file.additions} -{file.deletions}) ==="
    )


def _name(paths: list[str]) -> str:
    named = ", ".join(_inline(path) for path in paths[:MAX_NAMED_FILES])
    rest = len(paths) - MAX_NAMED_FILES
    return f"{named}, and {rest} more" if rest > 0 else named


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _inline(text: str) -> str:
    """Escape line breaks and other control characters so a name can't start a new line."""
    return json.dumps(text, ensure_ascii=False)[1:-1]
