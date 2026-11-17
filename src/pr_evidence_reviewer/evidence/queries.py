"""Search queries from a claim's own words, with fixed rules and no model.

Rules, applied to the statement and then the quote, in this order:
1. backticked spans;
2. code-shaped tokens: containing `_`, an inner capital, a `.` between identifiers, or ending `()`;
3. plain words, only when one names a changed file (its stem) or a symbol enclosing a hunk;
4. path-like tokens, which are matched against the checkout's file list instead of searched.

Queries keep their first appearance, drop duplicates, are 3 to 80 characters, and there are at
most six per claim, so the same claim always asks the same questions.
"""

import re
from collections.abc import Iterable
from dataclasses import dataclass

from pr_evidence_reviewer.domain.claims import Claim

MAX_QUERIES_PER_CLAIM = 6
MAX_PATH_MATCHES = 10
MIN_QUERY_CHARS = 3
MAX_QUERY_CHARS = 80

_URL = re.compile(r"\S+://\S*")
_BACKTICKED = re.compile(r"`([^`]+)`")
_TOKEN = re.compile(r"[A-Za-z0-9_./-]+(?:\(\))?")
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_DOTTED = re.compile(r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)+")
_FILE_SUFFIXES = frozenset(
    {
        "c", "cc", "cfg", "cpp", "cs", "css", "go", "h", "hpp", "html", "ini", "java", "js", "json",
        "jsx", "kt", "lock", "md", "php", "py", "rb", "rs", "rst", "scss", "sh", "sql", "swift",
        "toml", "ts", "tsx", "txt", "vue", "xml", "yaml", "yml",
    }
)  # fmt: skip


@dataclass(frozen=True)
class ClaimQueries:
    literals: tuple[str, ...] = ()
    paths: tuple[str, ...] = ()


def extract_queries(claim: Claim, *, vocabulary: frozenset[str] = frozenset()) -> ClaimQueries:
    """The literal queries and path-like tokens for one claim.

    `vocabulary` holds the plain words that count: changed-file stems and enclosing symbol names.
    """
    texts = [_URL.sub(" ", claim.statement), _URL.sub(" ", claim.quote)]
    spans = [span for text in texts for span in _backticked(text)]
    tokens = [token for text in texts for token in _tokens(text)]

    literals: list[str] = []
    paths: list[str] = []
    for span in spans:
        _add(paths if _is_path_like(span) else literals, _strip_call(span))
    for token in tokens:
        if _is_path_like(token):
            _add(paths, token)
        elif _is_code_shaped(token):
            _add(literals, _strip_call(token))
    for token in tokens:
        if token in vocabulary:
            _add(literals, token)
    return ClaimQueries(
        literals=tuple(literals[:MAX_QUERIES_PER_CLAIM]), paths=tuple(paths[:MAX_QUERIES_PER_CLAIM])
    )


def match_paths(tokens: Iterable[str], files: Iterable[str]) -> tuple[str, ...]:
    """Repository files a claim names: the same path, a path ending in it, or the same file name."""
    candidates = sorted(set(files))
    matched: set[str] = set()
    for token in tokens:
        wanted = token.removeprefix("./").lstrip("/")
        if not wanted:
            continue
        found = [path for path in candidates if path == wanted or path.endswith("/" + wanted)]
        matched.update(found[:MAX_PATH_MATCHES])
    return tuple(sorted(matched))


def definition_name(query: str) -> str | None:
    """The name to look up definitions for: the last part of a dotted name, if it's an identifier."""
    last = query.rsplit(".", 1)[-1]
    return last if _IDENTIFIER.fullmatch(last) else None


def _backticked(text: str) -> list[str]:
    return [span.strip() for span in _BACKTICKED.findall(text) if "\n" not in span]


def _tokens(text: str) -> list[str]:
    return [token.rstrip(".-") for token in _TOKEN.findall(text.replace("`", " "))]


def _add(queries: list[str], query: str) -> None:
    if MIN_QUERY_CHARS <= len(query) <= MAX_QUERY_CHARS and query not in queries:
        queries.append(query)


def _strip_call(token: str) -> str:
    return token.removesuffix("()")


def _is_path_like(token: str) -> bool:
    if "/" in token.strip("/"):
        return True
    stem, dot, suffix = token.rpartition(".")
    return bool(dot and stem and suffix in _FILE_SUFFIXES)


def _is_code_shaped(token: str) -> bool:
    name = _strip_call(token)
    if not _IDENTIFIER.fullmatch(name) and not _DOTTED.fullmatch(name):
        return False
    if token.endswith("()") or "_" in name:
        return True
    if _DOTTED.fullmatch(name):
        return any(len(part) > 1 for part in name.split("."))
    return any(c.isupper() for c in name[1:]) and any(c.islower() for c in name)
