"""The evidence packet: everything the model sees about a pull request, as data.

A packet is a pure function of the snapshot as fetched, the checkout at the head commit, the claims
and the constants below. It holds no timestamps, absolute paths or machine data, so the same inputs
give the same bytes and the same content hash. The stored packet is exactly what the model saw.

Ids are a prefix (`h` hunk, `s` search hit, `y` symbol, `g` gap) plus an ordinal given after the
canonical sort, so they're stable too. Gaps are never approval: they name evidence that wasn't seen.
"""

import hashlib
import json
from typing import Final, Literal

from pydantic import BaseModel, ConfigDict, Field

from pr_evidence_reviewer.domain.claims import ClaimKind
from pr_evidence_reviewer.domain.pull_request import CheckResult, FileStatus

SCHEMA_VERSION: Final = 1
# Bump when a heuristic changes what goes into a packet (queries, budgets, sort orders, gap rules).
RULESET_VERSION = "2026-11-16.1"
MAX_SHOWN_SYMBOL_LINES = 60

GapKind = Literal[
    "budget_cut",
    "checkout_unavailable",
    "ci_config",
    "export_ignored",
    "lfs",
    "no_ci",
    "no_diff",
    "no_test_found",
    "search_limit",
    "search_timeout",
    "skipped_files",
    "snapshot_limit",
    "submodule",
    "symbols_skipped",
    "too_many_checks",
    "unreadable_diff",
]


class _Frozen(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class ClaimRef(_Frozen):
    id: str
    statement: str
    kind: ClaimKind


class PacketSnapshot(_Frozen):
    repo: str = Field(description="owner/repo of the base repository.")
    pr_number: int
    base_sha: str
    head_sha: str
    tree_sha: str | None
    checkout_status: Literal["ok", "unavailable"]
    files_verified: int = Field(description="Files at head whose content was checked against git.")
    files_skipped: int


class SymbolRef(_Frozen):
    name: str
    qualified_name: str
    kind: str
    start_line: int
    end_line: int


class DiffHunk(_Frozen):
    id: str
    header: str
    head_lines: tuple[int, ...]
    text: str = Field(description="The numbered hunk body, without the header line.")
    enclosing: SymbolRef | None = None


class DiffFile(_Frozen):
    path: str
    status: FileStatus
    previous_path: str | None = None
    additions: int
    deletions: int
    cut: bool = Field(default=False, description="Some or all hunks were left out for budget.")
    hunks: tuple[DiffHunk, ...] = ()


class TestMapping(_Frozen):
    __test__ = False

    source_path: str
    ecosystem: str
    changed_tests: tuple[str, ...] = ()
    likely_tests: tuple[str, ...] = ()


class SymbolEvidence(_Frozen):
    id: str
    name: str
    qualified_name: str
    kind: str
    path: str
    start_line: int
    end_line: int
    shown_lines: tuple[tuple[int, str], ...] = Field(default=(), max_length=MAX_SHOWN_SYMBOL_LINES)


class SearchHit(_Frozen):
    id: str
    query: str
    path: str
    line: int
    context: tuple[tuple[int, str], ...]


class ClaimEvidence(_Frozen):
    claim_id: str
    queries: tuple[str, ...] = ()
    files: tuple[str, ...] = Field(
        default=(), description="Repository files a claim names by path, matched at head."
    )
    hits: tuple[SearchHit, ...] = ()
    definition_ids: tuple[str, ...] = ()
    cut: bool = False


class EvidenceGap(_Frozen):
    id: str
    kind: GapKind
    message: str
    path: str | None = None
    claim_id: str | None = None


class EvidencePacket(_Frozen):
    schema_version: Literal[1] = SCHEMA_VERSION
    ruleset_version: str = RULESET_VERSION
    snapshot: PacketSnapshot
    claims: tuple[ClaimRef, ...] = ()
    diff: tuple[DiffFile, ...] = ()
    ci_checks: tuple[CheckResult, ...] = ()
    test_mapping: tuple[TestMapping, ...] = ()
    symbols: tuple[SymbolEvidence, ...] = ()
    claim_evidence: tuple[ClaimEvidence, ...] = ()
    gaps: tuple[EvidenceGap, ...] = ()
    scope_limitations: tuple[str, ...] = ()
    evidence_incomplete: bool = False
    changes_ci_config: bool = False

    def canonical_json(self) -> bytes:
        """Sorted keys, no spaces, UTF-8: the bytes the content hash is taken over."""
        return json.dumps(
            self.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
        ).encode("utf-8")

    def content_hash(self) -> str:
        return hashlib.sha256(self.canonical_json()).hexdigest()


class StoredPacket(_Frozen):
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    packet: EvidencePacket
