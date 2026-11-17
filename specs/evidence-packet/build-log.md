# Build log: evidence-packet

## Phase 0: Intake (2026-11-16)
- Sandy's instruction: "work up until the progress for checkpoint 1 is reached". Clarified via question:
  "Week 2 / Update #2 (Recommended)", meaning milestone Week 2 issues #12–#17, in milestone order.
- Base: `dev` @ 60f3081 (fast-forwarded local dev to origin/dev; set upstream). Baseline: 372 tests pass.
- Branch: `feature/2-evidence-packet` (project convention `feature/<issue#>-<slug>`, epic #2).
- L1 decision: one integrated branch and one PR into dev closing #12–#17, not six stacked PRs. Why: last
  week's stacked PRs were merged out of order and never reached dev (memory, 2026-11-15); the issues form
  one dependency chain (#12 → #13/#14/#15/#17 → #16). Each issue still gets its own commit.
- #50 (Claude design mockup) is in the milestone but outside Sandy's list; not started.

## Phase 1: Route
Surfaces: architecture (new subsystem, deps, storage), security/data (untrusted repo content on disk, gh
token, path traversal, ReDoS), domain (evidence-gap semantics, verdict downgrade), small UI (dropped count).
- architect (plan → tasks): *Reduces uncertainty about module boundaries, the fetch mechanism (git vs
  tarball), search engine and regex bounding, grammar packaging, packet schema/determinism, and how this
  replaces the #43 packet without breaking verdicts.*
- security-reviewer (threat-model): *Reduces risk on the new trust boundary: repository-controlled bytes
  and git config written to disk, the gh token at fetch time, symlink/traversal/case-folding escapes, and
  pathological regex.*
- product-owner: skip, issues carry concrete ACs. domain-expert: skip, the verdict rules (gap ≠ approval,
  no valid citation → insufficient evidence) are explicit in CLAUDE.md and #17. ux-language-advocate: decide
  at Phase 5 if the UI diff is more than a count.
- implementer, qa-tester: default.

## Phase 2: Consult (dispatched 2026-11-16, in parallel)
- architect, mode plan → specs/evidence-packet/plan.md. Pending.
- security-reviewer, mode threat-model → specs/evidence-packet/threat-model.md. Pending.
- security-reviewer returned FINDINGS (design-time BLOCKING until mitigations are in plan.md by ID):
  T1 git execution vectors (CRITICAL, PRE), T2 archive extraction (CRITICAL if tarball), T3 token leak
  (HIGH, PRE; api/errors.py:43 logs exception chain), T4 read-API escape (HIGH, PRE), T7 validator bypass
  (HIGH), T5/T6/T8/T9/T10/T11 MEDIUM. Prefers bare shallow partial git fetch with no worktree; reads via
  ls-tree + cat-file; search via git grep. L2 candidates resolved by Lead (L1, conservative defaults):
  - L2-1 regex engine → `git grep` in a killable subprocess; no new dependency.
  - L2-2 cache retention → LRU + 7-day TTL; a cache-clear entry point. Private source never leaves disk.
  - L2-3 git required (≥ 2.50.1) → yes; missing/old git degrades to diff-only evidence with an explicit gap,
    never a failed review. gh is already required, git is near-universal.
- architect returned DONE: plan.md (D1–D8). D1 fetch = GitHub tarball + per-file blob-SHA verification,
  own extractor, Blobs-API refetch; D3 search = pure-Python literal + `regex` with timeout; D4 tree-sitter
  core + 6 grammar wheels; new deps tree-sitter (+grammars, MIT), regex (Apache-2.0/CNRI), platformdirs
  (MIT). ADR 0003 drafted at end of plan.md. No L2 claimed.

## Phase 3: Synthesize
- Material disagreement on D1/D3: security prefers bare shallow git fetch (no worktree) + git grep;
  architect prefers tarball + own extractor + `regex`. Security-critical and costly to reverse.
- critic, mode adjudicate: *Reduces the risk of picking the wrong trust-boundary design between two
  defensible specialist positions, and checks plan.md covers every threat-model ID (T1–T11).* Pending.
- 2026-11-16: the session crashed before the critic returned, so its result is lost. Resumed in a new session.
  Sandy chose: "Re-run critic, continue (Recommended)". dev is still @ 60f3081, the tree is unchanged, and the
  local git is 2.56.0. Re-dispatched the critic (adjudicate) with the same routing sentence. Pending.
- critic returned → `specs/evidence-packet/adjudication.md`. D1 = hybrid: tarball + blob-SHA verification, stored
  content-addressed (no repo-named paths on disk; T2 becomes PRE for #23). D3 = literal + `regex`, `concurrent=True`.
  Lead accepts (L1). This **supersedes** Phase 2 L2-1 (git grep) and L2-3 (git required). L2-2 (LRU + 7-day TTL +
  clear-cache) stands. BLOCKING C1–C3 go to the architect. No L2. New deps: tree-sitter (required by the issues) and
  `regex`/`platformdirs` (small, permissive licences), treated as L1 and disclosed in the brief.
- Phase 3.1, architect (mode tasks, fresh dispatch because the crash lost the original agent): *Reduces the risk that
  the implementer builds against a plan that still contradicts the threat model. It folds adjudication.md (C1–C9, T-ID
  table) into plan.md and spec.md and produces owner-tagged tasks.md for #12–#17.* Pending.
- architect returned DONE. plan.md now has the hybrid applied and a T1–T11 table. spec.md is amended (C8).
  threat-model.md: T1 eliminated, T2 deferred to #23, T7 aligned with C4. tasks.md has 42 tasks in batches B1 #12,
  B2 #13–#15, B3 #17, B4 #16. Clear-cache is CLI only (`prev cache clear [--packets]`); an API route was rejected
  because of CSRF/DNS-rebinding risk. Deps are pinned `==`, each at least 2 weeks old.
- Lead L1 calls (disclosed in the brief, not escalated):
  - Stored packets keep private excerpts with no TTL (FR8 reproducibility); `--packets` clears them.
  - The TTL only runs while `prev` runs.
  - Strict UTF-8 makes Latin-1/UTF-16 files uncitable, reported as labelled gaps.
  - Devops task 3 (windows/macos CI jobs) is a single workflow edit, so it goes to the implementer in B1 rather than a
    separate devops spawn. Task 15 (junction rmtree) counts only once the Windows job proves it.

## Phase 4: Implement (branch feature/2-evidence-packet; not a default branch, so no new branch)
- No commits before ship (§9). To keep the agreed one-commit-per-issue history without committing early, the
  implementer snapshots the tree after each issue with `git add -A && git write-tree` and reports the tree hash.
  This changes no refs. At ship the Lead chains the hashes with `git commit-tree`.
- Dispatch plan: B1 → B2 → B3 → B4, sequential because of the dependency chain; the suite runs between batches. QA
  tasks inside a batch that are tests of that batch's code stay with the implementer, test-first. Phase 5 qa-tester
  owns task 41 (AC/T traceability), verification.md and the screenshots.
- B1 implementer (#12, tasks 1–15): *Builds the checkout/cache trust boundary that every later batch depends on.
  Security-sensitive, so frontier tier (Opus, as pinned).* Pending.
- B1 stopped at the 60-turn maxTurns limit with no report, so the result is PARTIAL (L0). Continued with SendMessage,
  not respawned. Pending.
- B1 DONE (#12). Tree **e47d2d9e6516d8ebd789fc8df304ff98ed04b9a4**. The Lead re-ran the suite: 589 passed, 3 skipped
  (POSIX-only / admin symlink). The tree hash matches. ruff, black, mypy and the lock are clean.
- Deviations (L0, plan wins): tree-entry cap 100k (task 14 said 200k); path ≤ 1024 B and ≤ 255 B per segment (the
  threat model said 4096).
- Task 3 CI matrix is [~]: actionlint was blocked by the permission classifier, and the Lead will not run it either. The
  first PR CI run validates it. Task 15 (junction) passes locally on Windows through `_winapi.CreateJunction`.
- Carry-forward to B4: task 35 must construct CheckoutCache inside lifespan (its asyncio locks).
- Lead L0: added `.hypothesis/` to .gitignore. It lands in the #13 tree.
- B2 implementer (#13–#15, tasks 16–24), fresh dispatch because B1's context is large: *Builds search, test mapping
  and tree-sitter symbols on the B1 reader; ReDoS/size-cap surfaces T5/T6/T8.* Pending.
- B2 stopped at maxTurns (60) with no report, so the result is PARTIAL (L0). Continued with SendMessage. Pending.
- B2 DONE. Trees: #13 **baaf5907c715d1a26eda18b18f29507b8103b048**, #14 **3dd4b284c5bc172761813cf3d35a15ec7966d19f**,
  #15 **ba06e77ca9f8c4f880713a05e2928de0e027a3b7**. The Lead re-ran: 753 passed, 3 skipped. ruff and mypy are clean,
  and the tree hash matches. The #13 tree includes the `.hypothesis/` .gitignore line.
- L1, for Key decisions: on Windows with py3.12, tree-sitter 0.26.0 corrupts memory when `Point.row` is read by
  attribute. Unpacking the point is safe. symbols.py unpacks only, and an AST test forbids `.row`/`.column`. Re-check
  on any tree-sitter bump. The B3/B4 briefs carry this.
- Other B2 notes: `CheckoutReader.skipped()` added; `find_definitions` is one pass over many names (C3);
  `is_low_value` widened, with PR-changed paths exempt.
- B3 implementer (#17, tasks 25–29), fresh dispatch: *Builds the citation validator that enforces "no valid citation
  → insufficient evidence" against the checkout (T7, C4), plus the dropped-citation UI count.* Pending.
- B3 stopped at maxTurns (60) with no report, so the result is PARTIAL (L0). Continued with SendMessage. Pending.
  Third batch in a row to hit 60 turns, so the batches are sized too large for the turn cap. Lesson candidate.
- B3 DONE (#17). Tree **fb69c8c9286a439b564ac82b98338fa6efd20eaf**. The Lead re-ran: 832 passed, 3 skipped. ruff
  and mypy are clean; web has 79 tests passing and typecheck is clean.
- Deviations (L1):
  - No `maxLength` on `quote` in the schema, because Codex's strict structured output is believed to reject it. The
    500-character cap is enforced in the checker, and a guard test is added.
  - `le=10**7` on the diff line fields too.
  - `CheckoutCitationChecker(reader, head_sha, shown, *, index)`.
  - A per-reason drop log with no paths or quotes.
- User-visible: VerdictView code-citation label; ClaimsView copy; App status line; backend notes now say "evidence".
  ux-language-advocate is routed for Phase 5.
- Stopgap prompt line: "leave code_citations empty". Task 33 must replace it. Interim inconsistency: the UI copy
  describes repo search before #16 wires it. Acceptable because #16 ships in the same PR.
- Carry-forward to B4:
  - `ShownIndex` must implement `is_shown(path, line)`.
  - `StandardReview` must catch `CheckoutReadError` → diff fallback with a gap.
  - `checkout/text.py` `_UTF8_BOM` is a literal invisible char; make it `"﻿"`.
- B4 implementer (#16, tasks 30–40 and 42; task 41 goes to QA), fresh dispatch: *Wires the checkout, search, symbols
  and citation validator into one deterministic, stored packet used by StandardReview (AC5–AC9, T10, C5).*
  Expected to need a SendMessage continuation (13 tasks).
- B4 stopped at maxTurns (60) with no report, as expected, so the result is PARTIAL (L0). Continued with SendMessage.
  Pending.
- B4 hit maxTurns a second time, again with no report. Lead check: tasks 30–35 are [x]; the suite is green with 882
  passed and 3 skipped; 86 entries in porcelain. 36–40 remain. Task 42 (lessons) moves to the Lead (§9: the Lead owns
  memory writes). Second continuation sent, scoped to 36–40 only.
- B4 DONE (#16). Tree **215f5f24386d04f93b8d9dc0254bfd256d1abfc7**. The Lead re-ran: 901 passed, 3 skipped. ruff,
  black and mypy are clean, and the tree hash matches. The diff against dev is 92 files, +11 476 / −754.
- Deviations (L1, recorded; none changes an AC):
  - Degraded mode skips repo search, and test mapping covers changed files only.
  - PacketStore is rooted at `packets_dir`.
  - New NOT_SAVED_NOTE.
  - A CheckoutReadError during the citation check falls back to SnapshotCitationChecker, plus incomplete.
  - ClaimAssessor is split.
  - Every scope limitation is also a gap.
  - Unchanged, unnamed export-ignored files are covered only by the aggregate refetch-cap gap.
- Phase 4 summary: 4 fresh implementer dispatches and 5 continuations. Every batch hit the 60-turn cap at least once.

## Phase 5: Targeted verification (the diff implicates security, UI copy, and a large cross-cutting change)
- qa-tester: *Proves AC1–AC9 with evidence independent of the implementers (task 41 traceability, verification.md),
  closes coverage gaps, and captures screenshots of the changed verdict and claims screens.* Pending.
- security-reviewer (audit): *Mandatory. The diff adds the trust boundary for untrusted repository bytes and the gh
  token on a new network path (codeload), plus private source persisted on disk. It audits the code against the
  T1–T11 mitigations claimed in plan.md.* Pending.
- critic (review): *Large, high-consequence diff (11k lines, replaces the evidence path for every verdict). It gives an
  independent correctness read for silent false-"supported" paths, a gap treated as approval, and nondeterminism,
  which tests written by the same implementers can miss.* Pending.
- ux-language-advocate: deferred until QA screenshots exist, so it reviews real screens rather than strings.
- QA stopped at maxTurns (40) with no report, so the result is PARTIAL (L0). Continued with SendMessage and asked it to
  report early.
- critic returned REQUEST CHANGES. One BLOCKING (CR1, an UnsafePathError crash becomes a 500) and nine NON-BLOCKING,
  all recorded in review.md. No silent false "supported" was found, and every deviation was accepted.
- Its two L2 candidates are resolved as L1 because existing rules answer them (C4 shown ⇒ citable; gap ≠ approval).
  Neither goes to Sandy.
- The fix round is held until the security audit returns, so one implementer round covers both (the cap is 2 rounds).
- security-reviewer returned FINDINGS with nothing blocking: one MEDIUM (S1, refetch memory) and seven LOW, recorded in
  review.md. It wrote no file, since audit mode writes nothing, so the Lead recorded the findings. pip-audit was refused
  by the classifier; the Lead did not retry it.
- S8 (Python 3.12.12 or later) deferred to a follow-up issue (L1). Packet retention is a real L2: three specialists
  raised it independently, and it is a privacy policy question. It goes to Sandy while the fix round runs.
- Fix round 1, fresh implementer: *Fixes CR1 (blocking) and CR2–CR7, CR10, S1–S7 in one round, with regression
  tests.* Pending.
- architect re-check: skipped. The deviations are local and are listed above; there is no structural departure from D1–D8.
- DECISION NEEDED (packet retention). Sandy, verbatim: "7 days, like checkouts (Recommended)". Added to fix round 1 as
  S9: the packet store gets the same 7-day TTL as checkouts.
- QA returned PARTIAL: 905 passed / 3 skipped, full gate green (Windows only), 9/9 ACs proven, 4 gap tests staged,
  verification.md written. D1 (T10 gutter bypass) is the same as S2, already in the fix round. No screenshots
  (Playwright isn't installed), so the Lead captures them after the fix round in case copy changes. Task 41 `[~]` until
  CI runs on Linux and macOS.
- The implementer hit maxTurns (60) without reporting, so the result is PARTIAL (L0). Continued with SendMessage, with
  S9 added and an instruction to report early.
- Fix round 1 DONE: CR1–CR10, S1–S7, S9 and L2-1 fixed, each with a regression test the implementer ran against the
  pre-fix index. The Lead re-ran the gate: 945 passed / 4 skipped, ruff, black and mypy clean, tree
  6a33c40d955ede379e7907674126cbb83c0658a0 matches. The only subprocess calls in src/ are the existing token.py and
  llm/process.py, which this diff doesn't touch.
- CR2 interpretation accepted (L1): a path absent from the head tree and not skipped stays `missing_at_head`.
- Settings rename `checkout_ttl` → `retention_ttl`: the Lead fixes tasks.md line 28.
- Scoped re-checks (round 1 of 2), continued with SendMessage:
  - critic: *Did the CR1/CR2/CR7 fixes close the failures without opening a new path to a false "supported" or a
    500?*
  - security-reviewer: *Do the S1 streaming refetch and the new S9 packet deletion code hold T6/T9/T11 (no link
    following, no deletion outside the packets root)?*
- The Lead captured 2 screenshots (specs/evidence-packet/screenshots/), using a stand-in runner with fictional data.
  Port 8799 belonged to an unrelated local app, so it moved to 8811. Servers stopped and the tab closed afterwards.
- ux-language-advocate (review): *Does the new user-visible copy (the checking, loaded, dropped and incomplete notes, the
  checkout gap, the cache clear errors and the README retention row) read in the reviewer's language and set honest
  expectations?* Dispatched.
- Security re-audit round 1: CLEAR (no blocking). New LOW S10, S11, S12.
- Critic re-check round 1: APPROVE. New NB CR11 (sweep runs on every save).
- ux-language-advocate review: CHANGES_NEEDED. BLOCKING U1 (INCOMPLETE_NOTE understates the gap), U2 (scope sentence
  claims a repository search during the diff-only fallback), U3 ("unchanged code" label). NB U4–U14. Lead accepted all
  (review.md). The round-1 implementer isn't resumable after the crash and compaction, so it gets a fresh dispatch.
- implementer, fix round 2 of 2 (the last), fresh: *Closes the 3 UX blocking items plus S10–S12, CR11 and U4–U14,
  so nothing open stays in review.md at ship.* Dispatched.
- implementer round 2 stopped at its 60-turn limit (PARTIAL). Continued with SendMessage.
- Fix round 2: DONE after one continuation. Tree f9d4429187b68ee7cd1bf439407e1c3cb4e24fd7.
  Lead independent check: pytest 956 passed / 4 skipped; ruff, black and mypy clean; web lint, format, typecheck,
  test 79/79 and build all green. Lead read the round-2 diff for S10, S11, S12, CR11, U1, U2 and U7: matches the
  dispositions. Implementer deviation accepted (L1): S11 is wrapped in cache.py, not archive.py, to avoid a
  circular import.
- Screenshots re-captured (01, 02 with a realistic "Not checked" list, new 03 for the repository-unreadable fallback).
  The stand-in carries the backend's exact strings.
- ux-language-advocate (scoped re-check, continued): *Did the round-2 copy close U1–U3 as shown in the new
  screenshots, without opening a new misleading state?* Dispatched.
