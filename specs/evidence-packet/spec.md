# Spec: Evidence packet (Week 2)

> **Phase:** Specify · **Status:** clarified · **Owner:** Sandy Thomas · **Date:** 2026-11-16
> Amended 2026-11-16 per `adjudication.md` C8/C5: FR5 and AC4 define "ignored" as untracked.
> Covers issues #12, #13, #14, #15, #16 (epic #2) and #17 (epic #3). Milestone "Week 2: Evidence packet".

## Problem

Today a first-pass verdict rests only on the PR's diff hunks and CI checks (#43). A claim like "adds
tests for the parser" or "`retry()` now handles timeouts" often can't be judged from the diff alone: the
evidence is in unchanged code, in the test files, or in the whole definition of a function. Reviewers then
get "insufficient evidence" where the repository actually holds the answer, and the model cannot cite
anything outside the diff.

## Goals

- Every provider in standard review receives the same deterministic evidence packet, built from a
  read-only checkout at the PR's head commit: diff, text search hits, test mapping, symbols, CI checks, and
  an explicit list of evidence gaps.
- Every citation a model returns is checked against that checkout (path, line range, commit, optional
  quote). Citations that don't check out are dropped and counted.
- The same head commit and claims always produce a byte-identical packet, stored so a review can be
  reproduced.

## Non-Goals

- Deep review (agent exploration, #23). Only the validator it will reuse is built here.
- Running PR code or tests in any form. Test evidence is test source plus CI results.
- The claim-by-claim report screen (#26), review history (#27), background jobs (#25).
- Languages beyond Python, TypeScript/JavaScript, Go, Java and C# for symbols. Others use text search.
- Private-repository evaluation data handling beyond what already exists.

## User Stories

- As a **reviewer**, I want a claim about unchanged code or tests to be checked against the repository at
  the PR's head commit, so that "insufficient evidence" means the evidence really isn't there.
- As a **reviewer**, I want every citation in a verdict to point at real lines at that commit, so that I
  can trust and follow it.
- As the **project owner**, I want the packet stored and reproducible, so that evaluation runs compare
  providers on identical evidence.

## Functional Requirements

1. **Checkout (#12).** The system MUST obtain the repository contents at the PR's head SHA (and the base
   SHA when diffs need it) into a cache under the runner data dir, keyed by repository and SHA, reused on a
   second review of the same commit.
2. The checkout MUST NOT run any repository-controlled code or configuration: no git hooks, no LFS smudge,
   no submodule recursion, no `core.fsmonitor`, no filters. GitHub credentials are supplied at call time
   from the `gh` sign-in and never written to disk or logs.
3. All reads from the checkout MUST go through one path-safe API that refuses traversal outside the
   checkout and symlinks that point outside it.
4. The cache MUST have a size limit with least-recently-used eviction.
5. **Search (#13).** The system MUST support literal and regex search over the checkout returning path,
   line, and a bounded context window; it MUST skip ignored, binary, vendored, and generated paths, cap
   result count and time, and bound regex input so a pathological pattern can't hang the runner.
   "Ignored" means untracked: only files tracked at the head commit are searched, and a tracked file that
   `.gitignore` lists is still searched (an author-controlled ignore file must not hide evidence). Files the
   PR changed are searched even when they sit in vendored or generated paths.
6. **Test mapping (#14).** For each changed source file the system MUST report the tests changed in the PR,
   existing tests that likely cover it (by an extensible conventions table for Python, JS/TS, Go,
   Java/Kotlin, C#, Ruby, Rust), and files with no test found, reported as an evidence gap.
7. **Symbols (#15).** For Python, TypeScript/JavaScript, Go, Java and C# the system MUST return the
   enclosing symbol (with line range) for a changed hunk, and the definition(s) of a named symbol.
   Unsupported languages MUST fall back to text search without error.
8. **Packet (#16).** The system MUST assemble a packet with: snapshot, diff hunks, search hits, test
   mapping, symbols, CI results, and evidence gaps. Same SHA + claims MUST give byte-identical output
   (sorted, stable ids) with a recorded content hash. A per-claim budget MUST cut oversized evidence and
   record a scope limitation. The packet MUST be stored under the runner data dir keyed by head SHA.
   Standard-review verdicts MUST use this packet.
9. **Citation validator (#17).** Every model citation MUST be checked against the checkout: unknown paths,
   out-of-range lines, wrong commits, and paths outside the checkout are rejected; an optional quoted
   snippet MUST match the cited lines (whitespace-normalized). A verdict left with no valid citation MUST
   be downgraded to **insufficient evidence**. Dropped citations MUST be counted and shown in the report.
   The validator MUST be usable by both standard and deep review.

## Non-Functional Requirements

- **Security:** PR content and repository content are untrusted (ADR 0001 §6). No repository code or git
  configuration executes. Token never stored or logged. Path-safe reads only.
- **Performance:** a review of a typical PR (≤ 50 changed files, repo ≤ ~200 MB) builds its packet in well
  under a minute on a laptop after the first fetch; second review of the same SHA reuses the cache.
- **Portability:** works on Windows, macOS and Linux (paths, symlinks, case-insensitive filesystems).

## Acceptance Criteria

- [ ] AC1 A second review of the same PR head commit reuses the cached checkout (no new download).
- [ ] AC2 A crafted repository with hooks, LFS attributes, submodules, fsmonitor config, or symlinks
      escaping the checkout runs nothing and exposes nothing outside the checkout.
- [ ] AC3 Reads of `../`, absolute paths, and escaping symlinks are refused; the cache evicts the least
      recently used checkout when over its size limit.
- [ ] AC4 Search finds literal and regex matches with path, line and context; skips binaries, vendored
      and ignored (untracked) files, while tracked files listed in `.gitignore` and PR-changed files are
      searched; a catastrophic regex returns within its time cap.
- [ ] AC5 Each listed ecosystem's convention maps a changed source file to its test file; a file with no
      test appears as an evidence gap; nothing is executed.
- [ ] AC6 For each of the five languages, a changed hunk maps to its enclosing function/class with line
      range, and a symbol name maps to its definition; an unsupported language falls back without error.
- [ ] AC7 Two builds of the packet for the same SHA and claims are byte-identical with the same content
      hash when the packet has no `search_timeout` gap (amended per review CR8: a fired search deadline
      depends on the clock and is recorded as that gap); an oversized PR records a scope limitation; the
      packet is stored keyed by head SHA.
- [ ] AC8 Bad paths, out-of-range lines, wrong commits, escaping paths, and mismatched quotes are dropped;
      a verdict with no valid citation becomes insufficient evidence; the dropped count reaches the UI.
- [ ] AC9 Standard-review verdicts in the running app use the new packet and validator.

## Open Questions

None blocking. Implementation choices (fetch mechanism, search engine, grammar packaging) are the
architect's, escalated only if they add a material dependency or cost.

## References

- ADR `docs/decisions/0001-local-first-runner-and-byo-llm.md` decisions 2, 4, 6.
- Existing first-pass packet and checker: `src/pr_evidence_reviewer/review/evidence.py`,
  `review/citations.py`, `review/verdicts.py` (#43).
- Issues #12–#17.
