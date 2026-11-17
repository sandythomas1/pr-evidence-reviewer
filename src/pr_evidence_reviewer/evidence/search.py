"""Search the checkout at the head commit: literal terms for standard review, regex for the API.

**Corpus.** Text files from the checkout manifest, at most 1 MiB each, that aren't lockfiles or
generated, minified or vendored code. Files the pull request changed are always searched, wherever
they sit, so code can't hide in `dist/` (threat T8). Nothing is skipped because `.gitignore` or
`.gitattributes` lists it: the checkout holds only files tracked at the head commit, so "ignored"
already means untracked, and an author-controlled ignore file must not hide evidence. Changed files
come first, then the rest by path, up to a total byte cap.

**Literal search** reads each file once and matches every term in that one pass, so results are set
by caps counted in corpus order, never by the clock, and the same inputs give the same results.

**Regex search** uses the `regex` package with a timeout and `concurrent=True`, which releases the
GIL so the event loop keeps running while a worker thread matches (threat T5). Patterns are length-
and repeat-checked first. A fired timeout is the one nondeterministic outcome, reported as such.

In both modes lines longer than 2 000 characters are skipped before matching.
"""

import logging
import time
from collections import Counter, defaultdict
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from typing import Literal, Protocol

import regex

from pr_evidence_reviewer.checkout.archive import MIB, ManifestFile
from pr_evidence_reviewer.checkout.paths import RepoPath, normalize_repo_path
from pr_evidence_reviewer.checkout.reader import CheckoutReader
from pr_evidence_reviewer.checkout.text import split_lines
from pr_evidence_reviewer.evidence.diff import is_low_value

logger = logging.getLogger(__name__)

Clock = Callable[[], float]

_REPEAT_COUNT = regex.compile(r"\{\s*(\d*)\s*(?:,\s*(\d*)\s*)?\}")


@dataclass(frozen=True)
class SearchLimits:
    max_file_bytes: int = 1 * MIB
    max_corpus_bytes: int = 256 * MIB
    max_queries: int = 64
    max_line_chars: int = 2_000
    max_hits_per_query: int = 20
    max_hits_per_file: int = 3
    context_lines: int = 2
    max_context_chars: int = 240
    max_pattern_chars: int = 256
    max_repeat: int = 1_000
    query_seconds: float = 2.0
    phase_seconds: float = 20.0


@dataclass(frozen=True)
class SkippedFile:
    """A changed or claim-relevant file that wasn't searched, and why."""

    path: str
    reason: str


@dataclass(frozen=True)
class Corpus:
    paths: tuple[RepoPath, ...]
    changed: frozenset[str]
    skipped: tuple[SkippedFile, ...]
    # Every file left out, by reason, sorted by reason.
    skip_counts: tuple[tuple[str, int], ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class SearchLimitation:
    """Why a search didn't cover everything. `incomplete` says whether a verdict may miss evidence:
    a dropped term or a fired deadline can; skipped very long lines alone don't."""

    kind: Literal["search_limit", "search_timeout"]
    message: str
    incomplete: bool


@dataclass(frozen=True)
class SearchHit:
    query: str
    path: RepoPath
    line: int
    # (line number, text cut to the context width) for the hit and its neighbours.
    context: tuple[tuple[int, str], ...]


@dataclass(frozen=True)
class SearchResult:
    queries: tuple[str, ...]
    # Grouped by query in `queries` order, then in corpus order and by line.
    hits: tuple[SearchHit, ...]
    # Queries that reached the hit cap: more matches exist than are listed.
    capped: tuple[str, ...]
    # Queries whose search ran out of time before covering the corpus.
    timed_out: tuple[str, ...]
    limitations: tuple[SearchLimitation, ...]

    def for_query(self, query: str) -> tuple[SearchHit, ...]:
        return tuple(hit for hit in self.hits if hit.query == query)


class SearchPatternError(ValueError):
    """A regex pattern that is too long, repeats too much, or doesn't compile."""


def select_corpus(
    reader: CheckoutReader,
    *,
    changed_paths: Iterable[str] = (),
    relevant_paths: Iterable[str] = (),
    limits: SearchLimits = SearchLimits(),
) -> Corpus:
    """The files to search, in search order. `relevant_paths` are files a claim names or likely
    tests: like changed files, they get a skip record when they can't be searched. A changed or
    relevant path the checkout refuses as unsafe gets an `unsafe_name` record (threat T8)."""
    changed = _safe_paths(changed_paths)
    tracked = {*changed_paths, *relevant_paths}
    entries = sorted(reader.files(), key=lambda entry: (entry.path not in changed, entry.path))

    paths: list[RepoPath] = []
    reasons: dict[str, str] = {}
    corpus_bytes = 0
    for entry in entries:
        reason = _skip_reason(entry, entry.path in changed, limits)
        if reason is None and corpus_bytes + entry.size > limits.max_corpus_bytes:
            reason = "corpus_cap"
        if reason is not None:
            reasons[entry.path] = reason
            continue
        corpus_bytes += entry.size
        paths.append(RepoPath(entry.path))
    for skip in reader.skipped():
        reasons.setdefault(skip.path, skip.reason)
    for path in tracked:
        if normalize_repo_path(path) is None:
            reasons.setdefault(path, "unsafe_name")

    counts = Counter(reasons.values())
    limitations = []
    if counts["corpus_cap"]:
        limitations.append(
            "The repository is too large to search in full. "
            f"Not searched: {_count(counts['corpus_cap'], 'file')}."
        )
    return Corpus(
        paths=tuple(paths),
        changed=frozenset(changed),
        skipped=tuple(
            SkippedFile(path, reasons[path]) for path in sorted(tracked) if path in reasons
        ),
        skip_counts=tuple(sorted(counts.items())),
        limitations=tuple(limitations),
    )


def search_literal(
    reader: CheckoutReader,
    corpus: Corpus,
    queries: Sequence[str],
    *,
    limits: SearchLimits = SearchLimits(),
    clock: Clock = time.monotonic,
) -> SearchResult:
    """Case-sensitive, identifier-bounded literal search for every query in one corpus pass."""
    kept, dropped = _dedupe(queries, limits.max_queries)
    return _Search(kept, dropped, limits, clock).run(reader, corpus, _LiteralMatcher(kept))


def search_regex(
    reader: CheckoutReader,
    corpus: Corpus,
    patterns: Sequence[str],
    *,
    limits: SearchLimits = SearchLimits(),
    clock: Clock = time.monotonic,
) -> SearchResult:
    """Regex search, each pattern bounded by its own time budget and the phase deadline."""
    kept, dropped = _dedupe(patterns, limits.max_queries)
    compiled = {pattern: _compile(pattern, limits) for pattern in kept}
    search = _Search(kept, dropped, limits, clock)
    return search.run(reader, corpus, _RegexMatcher(compiled, search))


class _Matcher(Protocol):
    def candidates(self, text: str, active: Sequence[str]) -> list[str]:
        """The active queries worth checking line by line in this file."""

    def matches(self, query: str, line: str) -> bool | None:
        """True or False, or None when the query ran out of time."""


class _LiteralMatcher:
    def __init__(self, queries: Sequence[str]) -> None:
        self._bounds = {query: (_is_word(query[0]), _is_word(query[-1])) for query in queries}

    def candidates(self, text: str, active: Sequence[str]) -> list[str]:
        return [query for query in active if query in text]

    def matches(self, query: str, line: str) -> bool:
        check_start, check_end = self._bounds[query]
        start = line.find(query)
        while start != -1:
            end = start + len(query)
            if not (check_start and start > 0 and _is_word(line[start - 1])) and not (
                check_end and end < len(line) and _is_word(line[end])
            ):
                return True
            start = line.find(query, start + 1)
        return False


class _RegexMatcher:
    def __init__(self, compiled: dict[str, "regex.Pattern[str]"], search: "_Search") -> None:
        self._compiled = compiled
        self._search = search

    def candidates(self, text: str, active: Sequence[str]) -> list[str]:
        return list(active)

    def matches(self, query: str, line: str) -> bool | None:
        remaining = self._search.remaining(query)
        if remaining <= 0:
            return None
        started = self._search.clock()
        try:
            found = self._compiled[query].search(line, timeout=remaining, concurrent=True)
        except TimeoutError:
            return None
        finally:
            self._search.spend(query, self._search.clock() - started)
        return found is not None


@dataclass
class _Search:
    queries: list[str]
    dropped: int
    limits: SearchLimits
    clock: Clock
    hits: dict[str, list[SearchHit]] = field(default_factory=dict)
    timed_out: set[str] = field(default_factory=set)
    spent: defaultdict[str, float] = field(default_factory=lambda: defaultdict(float))
    deadline: float = 0.0
    long_line_files: int = 0

    def run(self, reader: CheckoutReader, corpus: Corpus, matcher: _Matcher) -> SearchResult:
        started = self.clock()
        self.deadline = started + self.limits.phase_seconds
        self.hits = {query: [] for query in self.queries}
        for path, text in reader.iter_text(corpus.paths):
            active = [query for query in self.queries if self._open(query)]
            if not active:
                break
            if self.clock() >= self.deadline:
                self.timed_out.update(active)
                break
            candidates = matcher.candidates(text, active)
            if candidates:
                self._search_file(path, text, candidates, matcher)
        result = self._result()
        logger.info(
            "Search finished",
            extra={
                "event": "search_finished",
                "queries": len(result.queries),
                "hits": len(result.hits),
                "timed_out": len(result.timed_out),
                "ms": round((self.clock() - started) * 1000),
            },
        )
        return result

    def remaining(self, query: str) -> float:
        budget = self.limits.query_seconds - self.spent[query]
        return min(budget, self.deadline - self.clock())

    def spend(self, query: str, seconds: float) -> None:
        self.spent[query] += seconds

    def _open(self, query: str) -> bool:
        return (
            query not in self.timed_out and len(self.hits[query]) < self.limits.max_hits_per_query
        )

    def _search_file(
        self, path: RepoPath, text: str, queries: list[str], matcher: _Matcher
    ) -> None:
        lines = split_lines(text)
        in_file: Counter[str] = Counter()
        skipped_long_line = False
        for index, line in enumerate(lines):
            if len(line) > self.limits.max_line_chars:
                skipped_long_line = True
                continue
            for query in queries:
                if in_file[query] >= self.limits.max_hits_per_file or not self._open(query):
                    continue
                found = matcher.matches(query, line)
                if found is None:
                    self.timed_out.add(query)
                elif found:
                    in_file[query] += 1
                    self.hits[query].append(
                        SearchHit(query, path, index + 1, self._context(lines, index))
                    )
        if skipped_long_line:
            self.long_line_files += 1

    def _context(self, lines: list[str], index: int) -> tuple[tuple[int, str], ...]:
        first = max(0, index - self.limits.context_lines)
        last = min(len(lines), index + self.limits.context_lines + 1)
        width = self.limits.max_context_chars
        return tuple((number + 1, lines[number][:width]) for number in range(first, last))

    def _result(self) -> SearchResult:
        limitations: list[SearchLimitation] = []
        if self.dropped:
            limitations.append(
                SearchLimitation(
                    "search_limit",
                    "There were too many search terms. "
                    f"Not searched: {_count(self.dropped, 'term')}.",
                    incomplete=True,
                )
            )
        if self.timed_out:
            limitations.append(
                SearchLimitation(
                    "search_timeout",
                    "The search ran out of time. "
                    f"Search terms not searched in full: {len(self.timed_out)}.",
                    incomplete=True,
                )
            )
        if self.long_line_files:
            limitations.append(
                SearchLimitation(
                    "search_limit",
                    f"Lines over {self.limits.max_line_chars:,} characters weren't searched in "
                    f"{_count(self.long_line_files, 'file')}.",
                    incomplete=False,
                )
            )
        return SearchResult(
            queries=tuple(self.queries),
            hits=tuple(hit for query in self.queries for hit in self.hits[query]),
            capped=tuple(
                query
                for query in self.queries
                if len(self.hits[query]) >= self.limits.max_hits_per_query
            ),
            timed_out=tuple(query for query in self.queries if query in self.timed_out),
            limitations=tuple(limitations),
        )


def _skip_reason(entry: ManifestFile, changed: bool, limits: SearchLimits) -> str | None:
    if entry.kind != "text":
        return entry.kind
    if entry.size > limits.max_file_bytes:
        return "too_large"
    if not changed and is_low_value(entry.path):
        return "low_value"
    return None


def _safe_paths(paths: Iterable[str]) -> set[str]:
    return {safe for path in paths if (safe := normalize_repo_path(path)) is not None}


def _dedupe(queries: Sequence[str], cap: int) -> tuple[list[str], int]:
    unique = list(dict.fromkeys(query for query in queries if query.strip()))
    return unique[:cap], max(0, len(unique) - cap)


def _compile(pattern: str, limits: SearchLimits) -> "regex.Pattern[str]":
    if len(pattern) > limits.max_pattern_chars:
        raise SearchPatternError(
            f"The search pattern is longer than {limits.max_pattern_chars} characters."
        )
    for match in _REPEAT_COUNT.finditer(pattern):
        if any(count and int(count) > limits.max_repeat for count in match.groups()):
            raise SearchPatternError(
                f"The search pattern repeats something more than {limits.max_repeat:,} times."
            )
    try:
        return regex.compile(pattern)
    except regex.error:
        raise SearchPatternError("The search pattern isn't a valid regular expression.") from None


def _is_word(char: str) -> bool:
    return char.isalnum() or char == "_"


def _count(number: int, noun: str) -> str:
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"
