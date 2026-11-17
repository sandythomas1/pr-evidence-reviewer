# Critic adjudication: evidence-packet (2026-11-16)
Mode: adjudicate. The Lead accepted this verdict (L1). It supersedes the Lead's Phase 2 calls L2-1 (`git grep`) and
L2-3 (git required). L2-2 (LRU + 7-day TTL + a clear-cache entry point) stands and must be added to D2.

## D1 fetch: hybrid
Use the architect's tarball with per-file blob-SHA verification against `git/trees`, plus a Blobs-API refetch. Store the
verified bytes **content-addressed**: `checkouts/<key>/blobs/<40-hex>`, with path → blob resolved through the manifest. Never
write `tree/<repo path>`.
- The disk never holds a repo-controlled name. That removes T2 and most of T11 in Week 2 (Windows case-folding, MAX_PATH,
  8.3 `~1` aliasing, ADS). It also drops the `case_collision`/reserved-name skips, and the reader needs no resolve/lstat walk.
- There is no git subprocess at all, so T1 is eliminated. Tests stay hermetic (MockTransport plus crafted tar.gz and tree
  JSON). It adds no system prerequisite.
- #23 (deep review) materialises a real tree from the verified store with the T2-hardened writer. T2 becomes PRE for #23,
  not for Week 2.
- Residual to disclose, not escalate: author-controlled `.gitattributes` `export-ignore` can drop files from the tarball.
  Past the 300-blob refetch cap they become labelled gaps. This is bounded and visible once C5 is fixed.

## D3 search: pure-Python literal + `regex` with timeout
- `git grep` would bring T1 back. It also fails on a partial clone: with `--filter=blob:limit` and `GIT_NO_LAZY_FETCH=1`,
  it exited 128 with zero hits (checked on git 2.56.0).
- The pinned `regex` behaves the same on every OS. Standard review uses literal mode only. The ReDoS surface (T5) is regex
  mode alone (FR5/AC4).
- Required: `concurrent=True`; one pass over the files covering every literal query; the 2 000-char line skip applied
  before matching, not only when reporting.

## Threat coverage (must appear in plan.md as a T-ID → D-ID table)
| T | Status | Where | Fix |
|---|---|---|---|
| T1 | eliminated | D1 no git; no subprocess; AC2 patches Popen | none |
| T2 | covered by the hybrid | D2 | content-addressed store; T2 writer is PRE for #23 |
| T3 | partial | D1.2, D8, Observability | pin the httpx/httpcore loggers to WARNING; `from None` on every codeload/blob exception; sentinel-token test (caplog, exception chain, API JSON, cache bytes); note `api/errors.py:43` |
| T4 | covered | D2 paths.py, CheckoutReader exact manifest lookup, D6.2 | add T4's vector list to the AC3 tests |
| T5 | partial | D3 | `concurrent=True`; skip long lines before matching; event-loop responsiveness test |
| T6 | partial | D1.1, D2 limits, D4 caps | caps on `enclosing()` (≤ 1 MB, longest line, node cap); 5 MB minified-JS test; gzip-bomb test |
| T7 | partial | D2, D6 | resolve C4; `le=10**7` on the int fields; one shared `split_lines` for render, search, symbols and validator; property tests |
| T8 | partial | D1.4, D3 | C5 |
| T9 | mostly | D2 key, atomic publish, manifest, locks, leases | cache root 0700, refused if it is a link or junction; marker sha must equal the requested sha |
| T10 | partial | Security, D5 | extend SYSTEM_PROMPT to hits and symbols; gutter on every repo-derived line; regenerate the boundary on collision; T10 test |
| T11 | by construction | D2 rename-to-trash | assert the victim's parent is the cache root; junction-sentinel test |

## Findings
- **C1 BLOCKING.** The build-log Phase 2 L1 calls contradict plan D1/D3/D2. Resolved above. Add TTL and clear-cache to D2.
- **C2 BLOCKING.** plan.md cites no T-IDs. Add the table above.
- **C3 BLOCKING.** Each query rescans the corpus (≤ 256 MiB) through a 64 MiB LRU. About 6 queries × N claims thrash and
  hit the 20 s cap, so the `search_timeout` gap varies by run and breaks AC7 byte-identity. Fix: one pass per file matching
  every claim's literal queries; size the cache to the corpus or stream it.
- **C4.** T7 vs D6.1/D2. Keep ≥ 7-hex prefix SHAs (compared only with head). Undecodable (strict UTF-8) files are not
  citable. A quote under 8 chars counts as unquoted. Update threat-model.md to match.
- **C5.** The search skips `is_low_value` paths (vendor/dist/min/generated) even when the PR changed them, so code can hide
  in `dist/`. `evidence_incomplete` (`review/evidence.py:114`) is set from diff budget cuts only. Fix: exempt PR-changed
  paths; set `evidence_incomplete` when the refetch cap fires or a PR-changed/claim-relevant file is skipped; add a test
  with a mass `* export-ignore`.
- **C6.** `regex` in `to_thread` without `concurrent=True` holds the GIL and freezes the event loop. See T5.
- **C7.** The ADR 0003 reasons for rejecting git describe a worktree checkout. The real reasons: a required system
  install, the T1 machine-config surface, a test-only `file://` seam that skips the production https config, and the
  partial-fetch `git grep` failure.
- **C8.** spec FR5/AC4 "skip ignored files": "ignored" means untracked; tracked files that `.gitignore` lists are
  searched. Amend the spec.
- **C9.** Size caps cover `definitions()` only; `enclosing()` is unbounded. See T6.

## L2
None blocking. L2-2 (private-source retention) is stated in the brief as the default chosen (7-day TTL plus clear-cache).
