# Plan: Evidence packet (Week 2, #12–#17)

> **Phase:** Plan · **Status:** revised for tasks · **Date:** 2026-11-16 · Spec: `spec.md` (AC1–AC9) · ADR 0001 §2,
> §4, §6 binding. `adjudication.md` (L1, accepted) is applied. Mitigations cite `threat-model.md` by T-ID; coverage
> is in **Security & Privacy: threat coverage**.

## Summary
Standard review gets the repo at head from GitHub's **tarball API** and verifies every file against the git tree's
blob SHA (Blobs-API refetch for anything missing or altered). Verified bytes are stored **content-addressed** per
user (`checkouts/<key>/blobs/<40-hex>`) and a manifest maps repo path → blob; no repo-controlled name ever reaches
the disk, and no git or subprocess runs. All reads go through one path-safe reader doing exact manifest lookups. A
pure builder turns (snapshot, checkout, claims, budgets) into a frozen, hashed, stored `EvidencePacket` rendered to
the model. `CheckoutCitationChecker` validates citations (deep review reuses it with `shown=None`). Checkout failure
degrades to diff-only with an explicit gap, never to approval.

## Architecture
```
api/verdicts.py ─> review/standard.py   (orchestrates one standard review)
   1 snapshot (existing GitHubClient)
   2 checkouts.lease(ref, sha) ─> checkout/cache.py ─> ArchiveSource port <─ github/archive.py
        on CheckoutError: no lease + gap              └> checkout/archive.py (stream, verify, store blobs)
   3 to_thread(build_packet) ─> evidence/{queries,search,test_mapping,symbols,diff}.py
        every file read via checkout/reader.py (manifest lookup → blob)
   4 to_thread(store.save)  ─> evidence/store.py (data_dir/packets/...)
   5 render_packet -> (text, ShownIndex) ─> ClaimAssessor (existing LLM call)
   6 check_assessments(..., CheckoutCitationChecker(reader, head_sha, shown))
```

### D7 Module layout and dependency direction
Dependencies point one way: `api → review → evidence → checkout → domain`. `github/archive.py` implements the
`ArchiveSource` port declared in `checkout/cache.py`; `checkout/` never imports `github/`, and `app.py` wires them.
`domain/` stays pure pydantic. tree-sitter is imported only in `evidence/symbols.py`.

| Package | New modules |
|---|---|
| `domain/` | `evidence.py` (packet schema, `canonical_json()`, `content_hash()`); `verdicts.py` adds code citations, `packet_hash` |
| `checkout/` | `paths.py` (`normalize_repo_path`), `text.py` (`decode_strict`, `split_lines`), `archive.py` (stream, verify, limits, blob store, manifest), `cache.py` (root checks, key, single flight, leases, atomic publish, LRU+TTL, clear), `reader.py` (`CheckoutReader`) |
| `github/` | `archive.py` (trees, tarball, blobs calls) |
| `evidence/` | `diff.py`, `queries.py`, `search.py`, `test_mapping.py`, `symbols.py`, `builder.py`, `render.py`, `store.py` |
| `review/` | `standard.py`; `citations.py` gains `CheckoutCitationChecker` |
| `cli.py` | `prev cache clear [--packets]` |

Hunk parsing, `is_low_value` and CI-config detection move unchanged to `evidence/diff.py` at #13;
`review/evidence.py` is deleted at #16. *Rejected:* growing `review/evidence.py` (mixes IO, parsing and policy);
putting the checkout in `github/` (ties cache and safety policy to the transport).

## Decisions (each with the rejected alternative)
### D1 Fetch: GitHub tarball verified against the git tree, stored by blob SHA
1. `GET /repos/{o}/{r}/git/trees/{head}?recursive=1` gives path, mode, blob SHA, size. Limits are checked before any
   download; `truncated` → `RepositoryTooLargeError`. Modes 100644/100755 are candidates; 120000 → `link` skip;
   160000 → `submodule` gap. A path that `normalize_repo_path` refuses → `unsafe_name` skip (it could never be cited).
2. `GET …/tarball/{head}` with `follow_redirects=False`; the only accepted reply is a 302 to
   `https://codeload.github.com/…`, fetched by a **separate client with no Authorization**. The `Location` carries a
   short-lived token: never logged or put in an exception (T3).
3. The archive streams to a temp file under a compressed-byte cap. A thread opens it as `gzip.GzipFile` behind a
   counting reader that raises past the decompressed cap (gzip bomb, T6), then `tarfile.open(mode="r|")`. Only
   regular-file members whose stripped path is a tree candidate are read (`extractfile`, chunked, per-member cap);
   all other members are consumed unread. `extract`/`extractall` are never called and **no filesystem name is ever
   derived from a member name** (T2).
4. Each file is hashed as a git blob (`sha1(b"blob %d\0"+data)`, `usedforsecurity=False`). A match is written to
   `blobs/<sha>` (`open(..., "xb")`, 0o600; an existing blob is reused, so identical files dedupe). Missing
   (`export-ignore`) or altered (`export-subst`, `eol`, `ident`) files are refetched via `GET …/git/blobs/{sha}`
   (sha validated `^[0-9a-f]{40}$`, base64 decoded, re-verified; cap 300 blobs and 128 MiB in total, concurrency
   8; each blob is verified and written to the blob store as it arrives, never held all in memory). Anything past
   the count cap is skipped as `export_ignored`, past the byte cap or still unverified as `unverified`, and either
   raises `evidence_incomplete` (C5, T8).
5. If the base repo 404s a fork's head SHA, retry on the snapshot's new optional `head_repo` (`head.repo.full_name`,
   validated with the existing owner/repo regexes); the result is still verified against `snapshot.head.sha`.

Base is not fetched (diffs come from the files API; citations refer to head). #12's hook/LFS/submodule/fsmonitor/
filter ACs hold by construction: no git, no `.git/`, no subprocess (T1 eliminated). The token stays in memory.
**Residual (disclosed, T8):** author `export-ignore` can drop files from the tarball; past the refetch cap they are
labelled gaps and the review is marked incomplete.
*Rejected: `git`* (see ADR 0003 draft). *Rejected: a real tree in Week 2* — brings T2 back (case folding, MAX_PATH,
8.3 `~1`, ADS, reserved names); #23 materialises one from the verified store with the T2 writer (**PRE for #23**).

### D2 Cache: location, layout, retention, concurrency, limits
**Location.** `platformdirs` via new `Settings.cache_dir`/`data_dir` (tests pass `tmp_path`): checkouts in
`user_cache_dir("pr-evidence-reviewer", appauthor=False)/checkouts`; packets in `user_data_dir(...)/packets`.
*Rejected:* hand-rolled per-OS paths (gets XDG/macOS wrong); checkouts in the data dir (backups copy evictable GBs).

**Root safety (T9, T11).** The cache root and `checkouts/` are created 0o700 (on Windows the per-user LocalAppData
ACL applies). At startup, before each publish and before each eviction, either one being a symlink or junction
(`is_symlink()` / `is_junction()`) raises `CheckoutUnavailableError`, so the review degrades and nothing is written.

**Key and layout.** Key `sha256(f"v{CHECKOUT_FORMAT}:{owner.lower()}/{repo.lower()}@{sha}")[:32]`. Entry
`checkouts/<key>/{manifest.json, last_used, blobs/<40-hex>}`; work in progress in `checkouts/.tmp-<rand>/`. Every
on-disk name is ours. `manifest.json`: format, owner, repo, sha, tree_sha, total_bytes, `refetch_capped`, sorted
files (path, size, blob_sha, kind `text|binary|lfs|undecodable`) and sorted `skipped` (path, reason). An entry is
valid only if the manifest parses and its sha, owner and repo equal the request (T9 marker); else it is removed and
refetched.

**Retention: LRU + 7-day TTL (L2-2).** Each acquire does `os.utime(last_used)`. At startup and on every lease (review S4):
evict unleased entries whose `last_used` is older than **7 days**, then the oldest unleased while the manifests'
`total_bytes` sum exceeds `checkout_cache_max_bytes` (default **2 GiB**). An expired entry found on acquire is
evicted and refetched. Stored packets get the same 7-day TTL (review S9, one shared retention
setting): a packet written more than **7 days** ago is removed by the same pass, before each packet save
and on every lease; removal never follows a link and a linked packets root is refused. TTL runs
only while `prev` runs; `prev cache clear` (`--packets` for packets) is the immediate control.
*Rejected:* filesystem atime (off on noatime/relatime, unreliable on NTFS).

**Eviction (T11).** The victim path is built from the key and must have `checkouts/` as its parent; it is renamed to
`.trash-<rand>`, then `shutil.rmtree` (3.12 does not follow symlinks or junctions); failures are logged and retried
next eviction. Our store only ever contains regular files we wrote, so a link inside is foreign and is unlinked.

**Clear-cache entry point.** `prev cache clear` evicts every checkout via the same root checks and eviction routine;
`--packets` also deletes `data_dir/packets`; it prints counts and bytes only. A review running in another process
whose blobs vanish gets `CheckoutReadError` and degrades (D8). *Rejected:* an API route (a state-changing localhost
endpoint adds CSRF/DNS-rebinding surface for no MVP gain).

**Concurrency.** Per-key `asyncio.Lock` single flight; refcounted leases held from build through validation. Atomic
publish: build in `.tmp-<rand>`, write `manifest.json` last, `os.rename(tmp, <key>)`; if the target exists
(`FileExistsError`, ENOTEMPTY/EEXIST, Windows `PermissionError`) use theirs. Stale `.tmp-*`/`.trash-*` (> 1 h) are
swept at startup. One runner per user assumed (no cross-process lock).

**Limits (T6).** Archive ≤ 512 MiB compressed and ≤ 1 GiB decompressed stream (headers and unread members count),
≤ 100 000 tree entries, ≤ 1 GiB total stored, 180 s fetch deadline. Files > 5 MiB are listed, not stored
(`too_large`). Over-limit trees raise `RepositoryTooLargeError`.

**Paths (T4, `checkout/paths.py`).** `normalize_repo_path(str) -> RepoPath | None` is the one validator for tree
entries, the reader, search, symbols, test mapping, the citation checker and deep review. It refuses: empty;
> 1024 bytes or a segment > 255; NUL or control chars; `\`; a leading `/`; any `:` (drive, ADS); `.`, `..` or empty
segments; any `.git` segment. No Unicode or case normalisation: membership is exact bytes.

**CheckoutReader (T4).** Untrusted path → `normalize_repo_path` → **exact, case-sensitive manifest lookup** → read
`blobs/<blob_sha>` (sha from our manifest). Reads never walk the filesystem or touch a repo-named path, so
traversal, 8.3 names, ADS and case aliases all miss; `O_NOFOLLOW` on POSIX as defence in depth. API: `files()`,
`has()`, `text()`, `lines()`, `line_count()`, `iter_text(paths)` (search; bypasses the cache). 64 MiB decoded-file
LRU for symbols and validation. Refusal raises internal `UnsafePathError`; a vanished blob raises
`CheckoutReadError` (neither is a `ReviewerError`).

**Text (T7, C4, `checkout/text.py`).** `decode_strict(bytes)`: strict UTF-8, one leading BOM stripped; failure →
kind `undecodable` (not searched, not citable, counted as a skip). `split_lines(text)`: split on `\n` only, strip one
trailing `\r`, no phantom last line (GitHub numbering), never `str.splitlines()`. It is the only line splitter for
render, search, symbols and the validator.

### D3 Search: pure Python over the reader, `regex` for regex mode
**Literal** (all standard review uses): queries are deduped across claims (≤ 64 per packet; the rest are a scope
limitation) and run in **one pass over the corpus** (C3): each file is read once via `iter_text`, each query gets a
whole-file `in` precheck, then `split_lines` with lines > 2 000 chars **skipped before matching** (T5), `str.find`
plus an optional identifier-boundary check. Cost is bounded by corpus bytes × queries, so caps, not the clock,
decide results; the 20 s phase deadline is a safety net expected never to fire in literal mode.
**Regex** (FR5/AC4 API; not used by standard review): the `regex` package with `timeout=` and `concurrent=True`
(releases the GIL so the event loop keeps running, C6/T5); pattern ≤ 256 chars, pre-scan rejects repeat counts
> 1000, long lines skipped before matching, 2 s per query (remaining deadline passed to each `finditer`), 20 s per
phase. A fired timeout is a `search_timeout` gap and the only nondeterministic case.

**Corpus.** Manifest text files ≤ 1 MiB, no NUL in the first 8 KiB, not LFS, strictly decodable, and not
`is_low_value` (which gains `build/`, `third_party/`, `__generated__/`, `*.pb.go`, `*_pb2.py`, `*.generated.*`,
`*.min.*`) — **except PR-changed paths, which are never skipped by that heuristic** (C5, T8). Order: PR-changed
files first, then path; capped at 256 MiB, the excess a scope limitation.

**"Ignored" means untracked (C8).** The archive holds tracked files only, so untracked/ignored files are absent by
construction. Tracked files are **not** skipped by `.gitignore` or `linguist-*`: both are author-controlled.

**Results.** Path, line, ±2 context lines cut to 240 chars; ≤ 20 hits per query, ≤ 3 per file; ordered changed
files first, then path, line. Caps count in corpus order, never by time.

*Rejected:* `git grep` (reintroduces T1; on a partial clone with `GIT_NO_LAZY_FETCH=1` it exits 128 with zero hits,
git 2.56.0); ripgrep (native prerequisite + subprocess); `google-re2` (wheels need macOS ≥ 13, sdist needs abseil);
`re` (uninterruptible); per-query rescans (thrash the 64 MiB LRU, hit the clock and break AC7, C3).

### D4 tree-sitter: core plus individual grammar wheels, pinned
Exact pins (releases ≥ 2 weeks old on 2026-11-16): `tree-sitter==0.26.0`; `tree-sitter-python`, `-javascript`,
`-go` `==0.25.0`; `-typescript==0.23.2` (`language_typescript`, `language_tsx`); `-java`, `-c-sharp` `==0.23.5`.
Wheels: core cp312/cp313 win amd64/arm64, macOS ≥ 10.13, manylinux x86_64/aarch64; grammars abi3; all MIT.
Extensions: `.py .pyi .js .jsx .mjs .cjs .ts .mts .cts .tsx .go .java .cs`.

**Definitions** via a node-type table per language (no `.scm` files; node list in task text). **Operations:**
`enclosing(path, line)` → innermost definition + qualified chain (`Class.method`); `definitions(name)` parses only
files containing the literal (≤ 30 files).
**Caps on both operations (C9, T6):** file ≤ 512 KiB, longest line ≤ 5 000 chars, iterative `TreeCursor` walk with
a 200 000-node cap. Over a cap, another extension or a parse error → `[]`; the caller falls back to search and a
`symbols_skipped` gap names the file.

*Rejected: `tree-sitter-language-pack`* — wheels need macOS ≥ 15 and glibc ≥ 2.34; bundles ~170 grammars (wide
supply-chain surface, mixed licences); API differs from the core bindings.

### D5 Packet schema, determinism, budgets, rendering, storage
Models in `domain/evidence.py`, all `frozen`, `extra="forbid"`:
```
EvidencePacket: schema_version=1, snapshot, claims[ClaimRef id,statement,kind], diff[DiffFile],
  ci_checks[CheckResult], test_mapping[TestMapping], symbols[SymbolEvidence],
  claim_evidence[ClaimEvidence], gaps[EvidenceGap], scope_limitations[str], evidence_incomplete
PacketSnapshot: repo, pr_number, base_sha, head_sha, tree_sha|None, checkout_status ok|unavailable,
  files_verified, files_skipped
DiffFile: path, status, previous_path, additions, deletions, cut, hunks[id, header, head_lines, text,
  enclosing: SymbolRef|None]
TestMapping: source_path, ecosystem, changed_tests[path], likely_tests[path]   (both empty -> gap)
SymbolEvidence: id, name, qualified_name, kind, path, start_line, end_line, shown_lines (<= 60)
ClaimEvidence: claim_id, queries, hits[SearchHit id,path,line,context[(line,text)]], definition_ids, cut
EvidenceGap: id, kind (no_test_found|checkout_unavailable|skipped_files|export_ignored|submodule|lfs|
  symbols_skipped|search_timeout|no_ci|no_diff|budget_cut|...), message, path|None, claim_id|None
StoredPacket: content_hash, packet
```
**Stable ids:** prefix (`h`, `s`, `y`, `g`) + ordinal assigned *after* canonical sort — diff `(is_low_value,
path)`; hits `(claim_id, changed-first, path, line)`; symbols `(path, start_line, name)`; gaps `(kind, path,
claim_id, message)`. **Hash:** `sha256(json.dumps(model_dump(mode="json"), sort_keys=True, separators=(",",":"),
ensure_ascii=False).encode())`. No timestamps, absolute paths, machine data or package version; `schema_version`
and `RULESET_VERSION` mark heuristic changes. **Determinism** = pure function of (snapshot as fetched, checkout at
SHA, claims, constants). CI results can drift between fetches; a later packet then differs only in `ci_checks`
and is stored as a second file under the same SHA. AC7 is tested with a fixed snapshot.

**Queries** (`evidence/queries.py`, no LLM; extraction rules in task text): from each claim's `statement` and
`quote`, ≤ 6 per claim, 3–80 chars, first-appearance order. Path-like tokens are matched against the manifest, not
searched; others are literal, case-sensitive, identifier-bounded, and also get `definitions(<last dotted part>)`.

**Budgets** in characters (~4 per token, same bytes for every provider): diff 60 000 (round-robin by hunk,
unchanged); test mapping + enclosing-symbol lines 8 000; per claim 4 000 from an 80 000 total, round-robin in
claim-id order. The **builder** cuts, so the stored packet is exactly what the model saw; each cut sets `cut=true`
and adds a `budget_cut` gap naming the claim and what was dropped.

**`evidence_incomplete` (C5).** True when any of: a budget cut (as today); the checkout is unavailable; the refetch
cap fired; a PR-changed file or a claim-relevant file (named by a claim's path query, or a likely-test
candidate) is skipped for a reason that hides content a claim could be checked against: `export_ignored`,
`too_large`, `unverified`, `undecodable`, `unsafe_name` or `lfs`. `binary`, `submodule` and `link` skips get a
visible gap only, so the warning doesn't land on every PR that touches an image (amended per review L2-1).
The refetch byte cap (128 MiB, `ArchiveLimits.max_refetch_bytes`) counts as the refetch cap firing (review S1).
`INCOMPLETE_NOTE` still applies to supported verdicts.

**Render (T10).** Pure `render_packet(packet) -> (text, ShownIndex)`. Sections: head commit; CI checks; gaps; diff
(today's gutter plus `in function foo (10–42)` on hunk headers); test mapping; per claim "Repository evidence for
claim cN" (queries, `path:line` context, definitions). **Every repo-derived line carries the line gutter**, so
content can't start a line as an `===` header or a boundary; every name goes through `_inline`. All repo text sits
inside the existing random-boundary EVIDENCE block; `wrap_untrusted` **draws a new boundary** if the evidence
contains the current one. `SYSTEM_PROMPT` gains: evidence now includes search hits, test mapping and symbol
definitions at head, all untrusted; how to write `code_citations`; cite only lines shown. `ShownIndex` replaces
`EvidenceIndex` and records the rendered (path, line) pairs, diff head lines and checks.

**Storage.** `data_dir/packets/<sha256(owner/repo lower)[:16]>/<head_sha>/<content_hash>.json` (`StoredPacket`),
atomic and idempotent, no eviction (~100 KB each; `prev cache clear --packets` removes them; revisit with #27); a
failed save is logged and noted, never fatal.

### D6 Citation validator and domain changes
**Domain (additive only).** New `ProposedCodeCitation{path, start_line, end_line, commit, quote}` (all required
for strict output; `quote=""` means none; line fields `ge=1, le=10**7`, T7) in `ProposedAssessment.code_citations`;
output `CodeCitation{kind:"code", path, start_line, end_line, commit}` joins the `Citation` union. `diff_citations`
and `check_citations` are untouched. The schema gains one required list, regenerated via `strict_json_schema`.

**Checker.** The protocol gains `check_code(p) -> CodeCitation | None`; `SnapshotCitationChecker` (diff-only
fallback) always returns None. `CheckoutCitationChecker(reader, head_sha, shown: ShownLines | None)` (`ShownLines`
is a protocol in `review/citations.py`; `ShownIndex` implements it at #16) checks in order, first failure recorded:
1. `commit` is head, or a ≥ 7-hex case-insensitive prefix of head; compared **only with head**, base never
   accepted (C4) → `wrong_commit`
2. path passes `normalize_repo_path` and is a manifest file of kind `text` (binary, LFS, undecodable, too-large and
   skipped files aren't citable) → `unsafe_path` / `unknown_path`
3. `1 ≤ start ≤ end ≤ line_count` via `split_lines`, span ≤ 300 lines → `out_of_range`
4. if `shown` is given, every cited line was rendered → `not_shown`
5. quote normalised by collapsing ASCII and Unicode `Zs` whitespace (zero-width and bidi chars kept); under 8 chars
   after normalising counts as **unquoted** (C4); otherwise ≤ 500 chars and a substring of the normalised cited
   lines → `quote_mismatch`

`check_diff` keeps its shown-in-diff rule and, with a reader, also requires the lines to exist at head; `check_ci`
is unchanged. `check_assessments` adds a code loop; the downgrade to `insufficient_evidence` and dropped notes still
apply. Deep review (#23) calls it with `shown=None`, tested now. Logs carry counts per drop reason only.

**UI.** `CodeCitation` in `types.ts`, rendered via `DiffCitationItem` as "unchanged code"; existing dropped counts
stay; copy says evidence includes repo search/tests/symbols and a first review can take minutes.

### D8 Wiring and errors
The endpoint calls `StandardReview.run(snapshot, claims)`: claims budget-cut first; extraction, eviction, build,
save and citation reads in `asyncio.to_thread`; async httpx download; cache state only on the loop thread. The
request stays synchronous (jobs are #25). `create_app` injects `CheckoutCache(cache_dir,
GitHubArchiveSource(tokens))` and `PacketStore(data_dir)`, and pins the `httpx`/`httpcore` loggers to WARNING (T3).

**Errors.** Two new user-safe `ReviewerError`s with fixed messages: `CheckoutUnavailableError`
(`checkout_unavailable`, 502) and `RepositoryTooLargeError` (`repository_too_large`, 413). Every httpx/codeload/blob
exception is re-raised as one of these **`from None`** (T3), so no URL or token reaches `api/errors.py:43`, whose
`logger.exception` logs the whole chain. Inside a review both are **caught**, as are `CheckoutReadError` and a
mid-review `GitHubAuthError`, and the review degrades: `checkout_status="unavailable"` plus a gap ("The repository
at abc1234 couldn't be read: …; only the diff and CI were used."); search, symbols and likely-test mapping fall back
to changed-file data; `SnapshotCitationChecker` checks citations; `evidence_incomplete=True`, never approval.
*Rejected:* failing the whole review on checkout errors (loses the diff-only value the tool already delivers).

## Stack & Dependencies
Python 3.12/3.13, FastAPI, pydantic v2. **New deps** (exact `==` pins, each released ≥ 2 weeks before 2026-11-16):
`tree-sitter` + 6 grammars (D4, **MIT**); `regex==2026.9.29` (**Apache-2.0 AND CNRI-Python**); `platformdirs==4.14.3`
(**MIT**, pure Python). Dev: `types-regex==2026.9.29.20261030`, `hypothesis==6.170.14` (MPL-2.0, test-only; T7
property tests). No new system prerequisite. *Trade-off:* exact pins block silent upgrades of native parsers but
need deliberate bumps; `uv.lock` holds the full tree.

## Deployment Target
- **Cloud/platform:** none — local runner (ADR 0001 §1). **Compute:** user's machine via `prev serve`.
- **IaC/CI:** existing `ci.yml` plus `windows-latest` and `macos-latest` py3.12 jobs (paths, junctions, wheels).
  **Guidance skill:** n/a.

## Data Model and API Contract
No database: evictable checkout blob store (D2) and durable packet store (D5); blob SHAs are validated
`^[0-9a-f]{40}$` before any URL or file-name use. `POST /api/verdicts` is unchanged except `VerdictSet.packet_hash:
str | None` and `Citation` gaining `kind:"code"`. No new endpoints. GitHub stays GET-only: trees, tarball, blobs.

## Security & Privacy: threat coverage (adjudication.md)
Nothing from the repo runs and no repo-controlled name reaches the disk (ADR 0001 §6). Private source at rest:
owner-only dirs, 7-day TTL, `prev cache clear [--packets]` (L2-2).

| T | Status | Where | Mitigation in this plan |
|---|---|---|---|
| T1 | eliminated | D1 | no git, no subprocess; AC2 test patches `subprocess.Popen` to fail |
| T2 | removed for Week 2 | D1.3–4, D2 layout | content-addressed store, no member-derived names; T2 writer is **PRE for #23** |
| T3 | covered | D1.2, D8, Observability | no-auth codeload client; Location never logged; httpx/httpcore at WARNING; `from None`; sentinel test |
| T4 | covered | D2 Paths, CheckoutReader, D6.2 | one `normalize_repo_path`; exact manifest lookup; T4 vector list in AC3 tests |
| T5 | covered | D3 | `concurrent=True`; long-line skip before matching; deadlines; event-loop responsiveness test |
| T6 | covered | D1.3, D2 Limits, D4 caps | compressed/decompressed/member/total caps; `enclosing()`+`definitions()` caps; bomb + 5 MB JS tests |
| T7 | covered | D2 Text, D6 | ≥ 7-hex head prefix only; strict UTF-8 citable; < 8-char quote = unquoted; `le=10**7`; one `split_lines`; property tests |
| T8 | covered + residual | D1.4, D3 Corpus, D5 | blob refetch; PR-changed exempt; `evidence_incomplete` on cap/skip; mass `export-ignore` test |
| T9 | covered | D2 | hashed key; manifest sha/owner/repo must match; atomic publish; locks, leases; root 0700, no link/junction |
| T10 | covered | D5 Render | gutter on every repo line; boundary regenerated; `SYSTEM_PROMPT` extended; T10 test |
| T11 | by construction | D2 Eviction | victim parent asserted; rename-to-trash; junction-sentinel test |

## Observability
Structured logs (`extra={}`), never paths, repo names, quotes or URLs: checkout hit/miss/expired, bytes, ms, skips
and refetches by reason, evictions (LRU/TTL/clear); packet build ms, cuts, gaps by kind, `content_hash`; search
timeouts; citation drops by reason; degraded review with error code.

## Testing Strategy (by AC; all hermetic, existing 372 tests stay green)
| AC | Proof |
|---|---|
| 1 | Fake `ArchiveSource` counts calls: same-SHA second lease → 0 downloads; two concurrent → 1; corrupt or wrong-sha manifest refetched |
| 2 | Crafted tar.gz + tree JSON: `.git/hooks`, `filter=lfs`, `.gitmodules`, fsmonitor config, links to `../../x` and `/etc/passwd`, hardlink, device, fifo, `../evil`, `/abs`, `C:/x`, `a\b`, `CON.txt`, `Foo.py`/`foo.py`, NFD/NFC. Cache holds only our names; sentinel sibling unchanged; skips recorded; `subprocess.Popen` patched to fail |
| 3 | Reader refuses the T4 vector list (`../x`, `a/../../x`, `/etc/passwd`, `C:/x`, `C:x`, UNC, `\\?\`, `a\..\x`, `a:b`, `a/./b`, `%2e%2e/x`, case alias); LRU and TTL evict oldest/expired unleased; root link/junction refused |
| 4 | Literal + regex hits with context; binary/vendor/minified/LFS skipped unless PR-changed; tracked `.gitignore`d file found; `(a+)+$` on `"a"*50_000+"b"` ends within cap + 0.5 s with `search_timeout` while an asyncio ticker keeps ticking |
| 5 | Table per ecosystem (Python, JS/TS, Go, Java/Kotlin, C#, Ruby, Rust); changed tests reported; untested → `no_test_found`; docs/config never gaps |
| 6 | py/js/ts/tsx/go/java/cs: hunk → enclosing symbol + range; name → definitions; `.rb` → `[]`; 5 MB minified JS → `[]` + gap for both ops; grammar smoke on 3 OSes |
| 7 | Shuffled inputs → identical bytes + hash; budget cut → gap, scope limitation, `cut=true`; store by SHA, idempotent; golden render |
| 8 | Every rejection reason, prefix/base/short SHAs, quote normalisation and < 8 chars, `shown=None`; property tests (rendered N == validated N); sole citation dropped → `insufficient_evidence`; Vitest |
| 9 | API with fake source + provider: code citation + `packet_hash`; checkout failure → gap + `INCOMPLETE_NOTE`; T3 sentinel absent from API JSON |

## Risks & Trade-offs
- **Risk:** fork head SHA not served by the base repo → `head_repo` fallback + smoke test on a real fork PR.
- **Risk:** slow first fetch in a sync request → limits, 180 s deadline, diff-only fallback, copy.
- **Risk:** tree-sitter C parsers on untrusted bytes can crash the runner → caps, candidate files only, pins.
- **Risk:** `export-ignore`-heavy repos eat rate limit → cap 300, then labelled gaps and `evidence_incomplete`.
- **Trade-offs:** blob store, not a tree (no T2 now; #23 materialises one) · strict UTF-8 only (Latin-1 sources are
  reported skips, not mis-cited) · char budgets, not tokens (same bytes per provider) · code citations must have
  been shown (stricter than #17) · TTL enforced only while `prev` runs (`prev cache clear` covers it).

## ADR draft: `docs/decisions/0003-checkout-from-github-archive.md` (Lead to file)
**Decision:** D1 + D2 layout. **Rejected — `git` (bare partial fetch, `ls-tree`/`cat-file`, `git grep`):**
(1) a required system install — `gh` doesn't bring git, and on Windows git often lives only inside GitHub Desktop,
off PATH (lesson 2026-11-02); (2) the T1 machine-config surface — system/global gitconfig, credential helpers,
templates and `GIT_*` env need ~25 overrides where one miss is code execution or a token leak; (3) hermetic tests
would need a test-only `file://` remote seam that skips the production https and credential path they should prove;
(4) `git grep` fails on a partial clone (`--filter=blob:limit`, `GIT_NO_LAZY_FETCH=1`: exit 128, zero hits, git
2.56.0), so search needs our own reader anyway. **Consequences:** #12's git-specific ACs hold by construction; base
content is never fetched; a truncated tree listing degrades to diff-only; `export-ignore` is a disclosed, bounded
residual; #23 materialises a tree from the verified store with the T2 writer.

## Skills Consulted
system_design, software_architecture, security (path traversal, ReDoS, archive extraction), testing.
