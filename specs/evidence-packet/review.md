# Review: evidence-packet (#12–#17)
Diff: `git diff --cached dev` (92 files). Reviewers: critic (review mode), security-reviewer (audit),
ux-language-advocate (review, against screenshots). Blocking means CRITICAL/HIGH security, or a BLOCKING critic/UX finding.

## Critic (review) — REQUEST CHANGES
It found no silent false "supported" on any path. Every degraded path sets a gap plus incomplete, and every supported
verdict carries INCOMPLETE_NOTE when evidence is incomplete.

| ID | Sev | Where | Finding | Disposition |
|---|---|---|---|---|
| CR1 | BLOCKING | evidence/builder.py:275, review/standard.py:76-84 | `reader.has()` raises `UnsafePathError` for a PR-changed path the tool refuses (`a:b.py`, a backslash, a segment > 255 B). It escapes StandardReview, so the review returns a 500. | fixed in round 1: treat it as not present, and StandardReview degrades visibly on any reader UnsafePathError. Tests in builder and StandardReview |
| CR2 | NON-BLOCKING | review/citations.py:158-161 | A diff citation into a changed file that isn't readable text at head (Latin-1, too_large, …) is dropped as `missing_at_head` even though those lines were shown. That breaks "shown ⇒ citable", and the verdict depends on whether the checkout worked. | fixed in round 1 (Lead L1, resolves critic L2-2): fall back to the shown-in-diff check when the head entry isn't text or was skipped; keep `missing_at_head` for readable text that is too short. Tests for undecodable and too_large changed files |
| CR3 | NON-BLOCKING | evidence/search.py:113-114, builder.py:468-492 | A PR-changed file with an unsafe name is filtered out before `_skip_gaps`, so it gets no per-file gap. | fixed in round 1: keep unsafe changed paths in the skip-record pass, with a gap |
| CR4 | NON-BLOCKING | review/citations.py:164 vs :225 | `displayed_paths` mapping is applied only to diff citations, not code citations. | fixed in round 1, plus a render → check round-trip test with a quoted path |
| CR5 | NON-BLOCKING | review/standard.py:80-82 | A mid-build CheckoutReadError rebuilds with `problem=None`, so the gap gives no reason. | fixed in round 1: pass a "changed while reading" problem |
| CR6 | NON-BLOCKING | builder.py:300-303 | The gap kind is chosen by checking the message text for "out of time"; incomplete is applied to every limitation. | fixed in round 1: structured limitations (kind, incomplete) from `search()` |
| CR7 | NON-BLOCKING | builder.py:254 `_symbol_order` | The sort key leaves out `kind`, so ties follow set order, which depends on PYTHONHASHSEED. Packet bytes can then differ across processes (AC7). | fixed in round 1: full-tuple key, plus a test with two PYTHONHASHSEED values |
| CR8 | NON-BLOCKING | search 20 s deadline | The packet depends on wall-clock time when the deadline fires (it is gapped as `search_timeout`, incomplete). | accepted. Amend the AC7 wording: determinism holds when there is no `search_timeout` gap |
| CR9 | NON-BLOCKING | tests | The determinism test is single-process; nothing tests `check_diff` on non-text files; nothing sends an unsafe changed path through `build_packet`. | fixed by the CR1, CR2 and CR7 tests |
| CR10 | NON-BLOCKING | evidence/store.py:49 | `content_hash()` sits outside the try, which breaks the "never raised" save contract. | fixed in round 1: move it inside the try |

Critic L2 candidates, resolved by the Lead as L1 (each answered by an existing rule, no product tradeoff):
- **L2-2, shown diff lines into a non-UTF-8 head file are citable.** Yes, by the C4 rule "what the model is shown is
  what it can cite" and for parity with degraded mode. → CR2.
- **L2-1, which skipped PR-changed files set `evidence_incomplete`.** Add `unsafe_name` and `lfs` (a claim about the
  file can't be checked against its content). Keep `binary`, `submodule` and `link` as visible gaps only, so an
  INCOMPLETE_NOTE doesn't land on every PR that touches an image. Recorded as a Key decision; plan C5 is amended to
  match.

Deviation verdicts: all accepted (see build-log Phase 5).

### Critic re-check (fix round 1): APPROVE
CR1–CR7 and CR10 are closed, confirmed by tests and by probes against tree 6a33c40. No new BLOCKING.

| ID | Sev | Where | Finding | Disposition |
|---|---|---|---|---|
| CR11 | NON-BLOCKING perf | evidence/store.py:67 | `save()` sweeps the whole packet store on every review (O(all packets)), and the sweep can rmdir a folder a concurrent save just made (caught, so NOT_SAVED_NOTE). | fixed in round 2: expire packets only in the cache retention pass and at startup, rate-limited to once per hour, not on every save |

## Security (audit) — FINDINGS, none blocking (highest MEDIUM)
T1, T3, T4, T5, T7, T9 and T11 are mitigated, and T2 is mitigated for Week 2 (the writer is PRE for #23). T6, T8 and T10
are partial (see below). The token path, redirect allow-list, `from None` chains, content-addressed names, junction
safety and boundary regeneration were all verified clean, and the diff adds no subprocess or shell call. pip-audit
was **not run** because the permission classifier refused it; the Lead did not retry it. Dependency CVEs are unchecked
and disclosed in the brief.

| ID | Sev | Where | Finding | Disposition |
|---|---|---|---|---|
| S1 | MEDIUM T6 | github/archive.py:113-132, checkout/cache.py:235-241 | `fetch_blobs` holds up to about 1 GiB of refetched blobs in memory before checking them. A mass `export-ignore` with large files can OOM the runner. | fixed in round 1: verify and write each blob to BlobStore as it arrives; total refetch byte cap of 128 MiB → `unverified` plus `refetch_capped`; test |
| S2 | LOW T10 | render.py:167-168,183-185, diff.py:97,100, builder.py:749-751 | The gutter guards only `\n`; a lone CR, NEL, U+2028/2029, VT or FF can fake a header. | fixed in round 1: escape C0 (except tab), C1 and U+2028/2029 as visible `\uXXXX` in the numbered, hunk and inline output |
| S3 | LOW T8 | search.py:113-114,349-350, symbols.py:165, builder.py:467-480 | A refused changed path is dropped without a gap. | same as CR3: an `unsafe_name` skip that sets incomplete |
| S4 | LOW T9 | checkout/cache.py:174-176,191-199 | TTL retention runs only at startup and on publish, so a runner that only hits the cache keeps other checkouts past 7 days. | fixed in round 1: run retention on every lease |
| S5 | LOW L5 | evidence/store.py:89-91 | Packet parent directories get the umask default (0755) on POSIX. | fixed in round 1: 0700 at every level; refuse a linked root |
| S6 | LOW L5 | checkout/cache.py:456-460 | `prev cache clear` ignores rmtree failures, so it can report "Deleted" while private blobs remain. | fixed in round 1: count failures, print them, exit non-zero |
| S7 | LOW avail. | checkout/cache.py:342,357-360, review/standard.py:48 | An OSError (disk full) during publish escapes as a 500 instead of a diff fallback. | fixed in round 1: `CheckoutUnavailableError(CACHE_UNWRITABLE)` from None (same family as CR1) |
| S8 | LOW suspected | runtime Python 3.12.9 | Later tarfile CVE fixes; stream-parse only, never extract, so not reachable. | deferred (L1): follow-up issue to require 3.12.12 or later and run pip-audit on the new pins; not bumped in this PR, to avoid an interpreter change two weeks before the demo |

Security L2: **packet retention** (private excerpts in `packets/` have no TTL). The architect, critic and security
reviewer all raised it independently, so it went to Sandy as DECISION NEEDED. Sandy chose "7 days, like checkouts".

| ID | Sev | Where | Finding | Disposition |
|---|---|---|---|---|
| S9 | L2 (decided) | evidence/store.py, checkout/cache.py retention | Packets have no TTL. | fixed in round 1: the same 7-day TTL as checkouts, from one shared retention setting; README and plan D2 updated; tests |

### Security re-audit (fix round 1): CLEAR, BLOCKING no
S1, S2, S4, S5, S6 and S9 are closed. S7 is closed except the refetch path (S11). No new CRITICAL or HIGH.

| ID | Sev | Where | Finding | Disposition |
|---|---|---|---|---|
| S10 | LOW | evidence/store.py:98-112 | One OSError (for example a locked file) stops the whole expiry sweep, so later packets never expire. | fixed in round 2: catch per entry, carry on, log a count |
| S11 | LOW | checkout/archive.py:231 via cache.py:283 | An OSError while writing a refetched blob isn't wrapped, so the review gets a 500. | fixed in round 2: `CheckoutUnavailableError(CACHE_UNWRITABLE)` from None |
| S12 | LOW | review/verdicts.py:204-213 | The CLAIMS JSON leaves C1, U+2028 and U+2029 from PR-author text unescaped (the boundary still holds). | fixed in round 2: escape with the same table as `escape.inline` |

## QA (verification)
9/9 ACs and 11/11 T-IDs have tests; full gate green on Windows. QA's D1 (T10 gutter bypass via CR, U+2028 and
similar separators) is the same as S2. Screenshots are captured by the Lead after the fix round.

## UX / language (review) — CHANGES NEEDED
Reviewed the screenshots (01, 02) and the staged copy. The cli.py `cache clear` copy, UNSAFE_NAME, the mid-review
limitation and the README list of what a review looks at are fine as written. All findings go to fix round 2 with the
reviewer's exact text, except where noted.

| ID | Sev | Where | Finding | Disposition |
|---|---|---|---|---|
| U1 | BLOCKING | review/verdicts.py:38 INCOMPLETE_NOTE | "Some changes weren't checked" understates the gap: most incomplete causes (repository unreadable, CI cap, search limits) leave the diff fully checked. As a muted aside under a green "Supported", it reads as near-approval. | fixed in round 2: "Some evidence wasn't checked (see “Not checked”), so this verdict may be incomplete. Look at those gaps before you rely on it." Say "this support" only if the note is attached to supported verdicts alone. Say "above" or "below" only if it matches where "Not checked" renders. |
| U2 | BLOCKING | web ClaimsView.tsx:80-83 | The fixed scope sentence says the repository code and tests were searched, even when it fell back to the diff and CI only. That contradicts the "Not checked" gap on the same screen. | fixed in round 2: the reviewer's conditional sentence (copy only, no API field) |
| U3 | BLOCKING | web VerdictView.tsx:89 | "(unchanged code)" can label lines this PR changed, because `check_code` doesn't exclude diff lines. | fixed in round 2: "(repository code)" |
| U4 | NB | builder.py:465-467 | The repository-unreadable gap doesn't say what it means for the decision. | fixed in round 2: append "Code and tests outside the diff weren't checked." |
| U5 | NB | standard.py:50 | CHANGED_WHILE_READING gives no next step. | fixed in round 2 |
| U6 | NB | standard.py:49 | NOT_SAVED_NOTE doesn't say the verdicts are unaffected. | fixed in round 2 |
| U7 | NB | verdicts.py:340-364 | The per-verdict and set-level dropped notes word the same thing differently. "doesn't point to shown lines" is wrong for `no_checkout` drops. | fixed in round 2: "couldn't be confirmed against the code or CI checks at this commit" (all 4 variants) |
| U8 | NB | App.tsx:54 | The screen-reader status line doesn't match the visible text and omits the wait time. | fixed in round 2 |
| U9 | NB | ClaimsView.tsx:63-64 | Says "first review of a repository", but the cache is per commit. | fixed in round 2: "of a commit" |
| U10 | NB | builder.py:605-608 | The claim budget gap shows the internal claim id and the vague "items"/"Not shown". | fixed in round 2: claim statement cut to about 80 chars via `inline()`, plus "Not checked:" |
| U11 | NB | builder.py:532, 545-546 | "test mapping" and "definitions" are jargon, and the gaps say "Not shown" instead of "Not checked". | fixed in round 2 |
| U12 | NB | builder.py:494-495, 66, 68 | The export-ignore gap doesn't explain the cause; "checked against the commit" overloads "checked". | fixed in round 2 |
| U13 | NB | README.md:78-98 | Three names for the same thing. | fixed in round 2: "saved repository copies" everywhere |
| U14 | NB | README.md:86-87 | "while `prev` runs" reads as a duration. | fixed in round 2 |

UX recommendation, Lead action: re-capture 02 with a realistic "Not checked" section, and add 03 for the
repository-unreadable fallback, where U1 and U2 meet. The docs/domain.md glossary is deferred as a follow-up (not in
this feature's scope).

## Final state (2026-11-16)
- Fix round 2 (the last): tree f9d4429. Lead re-ran the gates: pytest 956 passed / 4 skipped (POSIX-only); ruff, black and
  mypy clean; web lint, format, typecheck, 79/79 tests and build green.
- UX scoped re-check: **APPROVE**. U1–U3 are confirmed closed in the re-captured screenshots 01–03, with no new
  blocking problem.
- S10–S12 and CR11: the Lead verified the round-2 diff (per-entry catch with a count; `_store_refetched` wrap, placed
  in cache.py to avoid a circular import; `visible()` over the claim JSON; `remove_expired_if_due`, hourly). Each has a
  regression test. No reviewer re-dispatch, because each finding was LOW or NON-BLOCKING with an exact fix.
- **Open blocking findings: none.** Deferred: S8 (Python ≥ 3.12.12 plus pip-audit) becomes a follow-up issue; a
  docs/domain.md glossary is a follow-up; task 41 waits on CI for Linux and macOS.
- Residual risks, disclosed: author `.gitattributes` export-ignore beyond the refetch caps becomes a labelled gap
  (incomplete); a narrow race between the hourly sweep and a save gives NOT_SAVED_NOTE; dependency CVEs are unchecked.
