# Verification: evidence-packet (task 41)

Branch `feature/2-evidence-packet`, staged tree against `dev`. Verified locally on Windows 11 only; the
three-OS CI run (task 3) hasn't been checked here. Date: 2026-11-16.

## Acceptance criteria

| AC | Test(s) that prove it | What they assert | Command | Result |
|---|---|---|---|---|
| AC1 second review reuses the checkout | `tests/checkout/test_cache_safety.py::test_ac1_a_second_review_of_the_same_commit_downloads_nothing`, `::test_two_concurrent_leases_fetch_once`, `::test_owner_and_repo_case_share_one_checkout`; **new** `tests/test_verdicts_api.py::test_a_second_review_at_the_same_commit_reuses_the_checkout_and_the_packet` | Cache level: a second lease downloads nothing, concurrent leases fetch once. App level (new): two `POST /api/verdicts` give `tree_calls == download_calls == 1`, no blob refetch | `uv run pytest -q` | PASS |
| AC2 hostile repo runs and exposes nothing | `tests/checkout/test_hostile_archive.py::test_a_hostile_repository_leaves_only_our_files`, `::test_case_and_unicode_variants_stay_distinct`; `tests/checkout/test_archive.py::test_links_devices_and_unlisted_members_are_never_read`; `tests/checkout/test_reader.py::test_a_link_in_the_tree_is_listed_as_skipped_not_stored` | Hooks, `.gitattributes` LFS, `.gitmodules`, fsmonitor config, escaping symlinks and devices leave only blobs and a manifest in the cache; no git or subprocess exists on the path | `uv run pytest -q` | PASS |
| AC3 unsafe reads refused, LRU eviction | `tests/checkout/test_paths.py::test_refuses_paths_that_could_escape_or_alias`; `tests/checkout/test_reader.py::test_refuses_unsafe_paths`, `::test_any_accepted_path_resolves_only_to_its_exact_manifest_key`, `::test_aliases_short_names_and_links_match_nothing`; `tests/checkout/test_cache_safety.py::test_lru_evicts_the_oldest_unleased_checkout_never_a_leased_one`; `tests/checkout/test_cache.py::test_retention_drops_invalid_and_expired_then_oldest_over_budget` | `../`, absolute, drive, UNC, `%2e%2e`, case aliases and links resolve to nothing; over budget the oldest unleased checkout goes | `uv run pytest -q` | PASS |
| AC4 search behaviour | `tests/evidence/test_search.py` (literal, regex, path/line/context, binary/vendored skips); `tests/evidence/test_search_safety.py::test_a_tracked_file_listed_in_gitignore_is_searched`, `::test_a_changed_file_in_a_vendored_folder_is_searched_and_an_unchanged_one_is_not`, `::test_a_catastrophic_pattern_on_a_50_000_character_line_returns_within_its_time_cap`, `::test_the_event_loop_keeps_running_while_a_catastrophic_regex_runs_in_a_thread`; **new** `::test_a_file_in_the_download_but_not_in_the_git_tree_is_never_searched` | Matches carry path, line, context; tracked `.gitignore`d and PR-changed vendored files are searched; `(a+)+$` etc. return inside the cap with a limitation. New: an archive member that isn't in the git tree (untracked) isn't in the manifest and is never a hit, even when a claim names it | `uv run pytest -q` | PASS |
| AC5 test mapping | `tests/evidence/test_test_mapping.py::test_each_ecosystem_maps_a_changed_source_file_to_its_existing_test`, `::test_every_listed_ecosystem_has_a_convention`, `::test_a_source_file_with_no_test_is_a_gap`; `tests/evidence/test_builder.py::test_an_untested_change_is_a_gap_but_not_incomplete` | Every listed ecosystem maps source to test; a source with no test is a gap; mapping is path-only (nothing executed: no subprocess in the module, and the hostile-archive test shows nothing runs) | `uv run pytest -q` | PASS |
| AC6 enclosing symbol, definitions, fallback | `tests/evidence/test_symbols.py::test_every_grammar_loads_and_parses`, `::test_a_line_maps_to_its_innermost_enclosing_definition`, `::test_a_name_maps_to_its_definitions_in_path_order`, `::test_an_unsupported_language_returns_nothing_without_error`; `tests/evidence/test_symbols_caps.py`; `tests/evidence/test_builder.py::test_hunks_name_their_enclosing_function`; **new** `tests/evidence/test_builder.py::test_an_unsupported_language_falls_back_to_text_search_without_error` | Python/JS/TS/Go/Java (+C#) hunks map to their enclosing function or class with line range; names map to definitions. New: a Ruby change through the whole builder gives no enclosing symbol and no definition, but the search hit is still in the packet, with no error | `uv run pytest -q` | PASS |
| AC7 deterministic, limited, keyed packet | `tests/evidence/test_determinism.py::test_shuffled_files_checks_and_claims_give_identical_bytes`, `::test_an_oversized_pull_request_is_cut_named_and_limited`, `::test_packets_are_stored_under_the_head_commit_and_saved_once`, `::test_packet_bytes_hold_no_local_path_and_no_time`; `tests/evidence/test_store.py`; **new** `tests/test_verdicts_api.py::test_a_second_review_at_the_same_commit_reuses_the_checkout_and_the_packet` | All input orders give the same bytes and hash; an oversized PR is cut with a `budget_cut` gap and scope limitation; stored at `<repo_key>/<head_sha>/<hash>.json`. New: two app reviews return the same `packet_hash` and store one file | `uv run pytest -q` | PASS |
| AC8 bad citations dropped, downgrade, count in UI | `tests/review/test_checkout_citations.py::test_each_rule_drops_the_citation_and_records_why`, `::test_the_first_failing_rule_is_the_one_recorded`; `tests/review/test_citation_properties.py` (all 14); `tests/review/test_verdicts.py::test_a_verdict_whose_citations_all_fail_is_downgraded_to_insufficient_evidence`; `tests/test_verdicts_api.py::test_a_code_citation_to_a_line_the_model_wasnt_shown_is_dropped_and_counted`; `web/src/components/ClaimsView.test.tsx` ("1 citation in all was left out", "Insufficient evidence"); `web/src/components/VerdictView.test.tsx` (code citation label and link) | unknown_path, unsafe_path, out_of_range, wrong_commit, not_shown and quote_mismatch drop; no valid citation means insufficient_evidence; `dropped_citations` reaches the API body and the UI renders it | `uv run pytest -q`; `npm run test` | PASS |
| AC9 the running app uses the packet and validator | `tests/test_verdicts_api.py::test_a_code_citation_at_the_head_commit_is_checked_and_the_packet_kept`, `::test_when_the_repository_cant_be_read_the_review_says_so_and_never_approves`; `tests/review/test_export_ignore.py`; `tests/review/test_prompt_injection.py`; **new** `tests/test_verdicts_api.py::test_the_model_reads_the_repository_evidence_from_the_packet` | Through `create_app` and `POST /api/verdicts`: code citations are checked against the checkout, the packet is stored, fallback says so. New: the provider prompt holds the packet's test mapping, the search hit and the gutter-numbered definition of `refund_once` | `uv run pytest -q` | PASS |

**9/9 proven by automated tests.** Visual verification (screenshots) wasn't done by QA; see "Screenshots".

## Threats

| T | Test(s) | Result |
|---|---|---|
| T1 code execution in git (ELIMINATED: no git, no subprocess) | `tests/checkout/test_hostile_archive.py::test_a_hostile_repository_leaves_only_our_files`; `tests/github/test_archive.py::test_only_get_requests_are_sent` | PASS |
| T2 write outside the cache (tarball) | `tests/checkout/test_archive.py::test_links_devices_and_unlisted_members_are_never_read`, `::test_stores_verified_files_by_blob_sha_only`; `tests/checkout/test_hostile_archive.py` (`../`, absolute and link members) | PASS (worktree half DEFERRED to #23) |
| T3 token exposure | `tests/checkout/test_token_secrecy.py` (3); `tests/github/test_archive.py::test_downloads_the_archive_from_codeload_without_the_token`, `::test_refuses_a_redirect_anywhere_but_codeload`; `tests/test_verdicts_api.py::test_download_tokens_never_reach_the_response_or_the_logs` (3 params) | PASS |
| T4 path-safe read bypass | `tests/checkout/test_paths.py` (6); `tests/checkout/test_reader.py::test_refuses_unsafe_paths`, `::test_any_accepted_path_resolves_only_to_its_exact_manifest_key` | PASS |
| T5 regex DoS | `tests/evidence/test_search_safety.py` ReDoS cases (`(a+)+$`, `(a\|aa)*b`, `(.*a){20}` on 50 KB), phase deadline, event-loop responsiveness | PASS |
| T6 size and parse exhaustion | `tests/checkout/test_size_limits.py` (gzip bomb, 5 MiB file, entry cap, truncated tree); `tests/github/test_archive.py::test_stops_an_archive_over_the_download_cap`; `tests/evidence/test_symbols_caps.py` | PASS |
| T7 citation validator bypass | `tests/review/test_citation_properties.py::test_line_n_as_rendered_is_line_n_validated` (property over `\r\n`, `\r`, U+2028, U+0085, `\v`, `\f`), `::test_repository_text_is_never_split_with_str_splitlines`, commit-prefix and quote properties; `tests/checkout/test_text.py::test_splits_on_newline_only_like_github_numbers_lines` | PASS |
| T8 evidence hidden by the author | `tests/review/test_export_ignore.py` (3); `tests/evidence/test_search_safety.py` gitignore and vendored cases; **new** untracked-member test; `tests/evidence/test_builder.py::test_a_changed_file_missing_from_the_download_makes_the_packet_incomplete` | PASS |
| T9 cache poisoning or partial reuse | `tests/checkout/test_cache_safety.py::test_a_failure_midway_leaves_no_entry`, `::test_a_manifest_with_the_wrong_commit_is_refetched`, `::test_owner_and_repo_case_share_one_checkout`, `::test_two_concurrent_leases_fetch_once`; `tests/evidence/test_store.py` (tampered and corrupt packets) | PASS |
| T10 prompt injection from repo content | `tests/review/test_prompt_injection.py` (5) | PASS for `\n`-separated lines; **partial**, see defect D1 |
| T11 eviction deletes outside the cache | `tests/checkout/test_cache_safety.py::test_a_link_planted_inside_a_checkout_is_evicted_without_touching_its_target`, `::test_clear_unlinks_a_planted_link_without_following_it`, `::test_a_checkout_that_is_itself_a_link_is_dropped_not_followed` | PASS (Windows junction case run here) |

**11/11 have tests; T10 has a residual gap (D1).**

## Gaps found and closed

1. AC1/AC7 were proven only at cache and builder level. Added
   `tests/test_verdicts_api.py::test_a_second_review_at_the_same_commit_reuses_the_checkout_and_the_packet`.
2. AC9 asserted verdict output but not that the model's prompt held the packet's repository evidence. Added
   `tests/test_verdicts_api.py::test_the_model_reads_the_repository_evidence_from_the_packet`.
3. AC4 "ignored (untracked) files are skipped" had no test for an archive member missing from the git tree.
   Added `tests/evidence/test_search_safety.py::test_a_file_in_the_download_but_not_in_the_git_tree_is_never_searched`.
4. AC6 fallback was proven only on `SymbolIndex`, not through the builder. Added
   `tests/evidence/test_builder.py::test_an_unsupported_language_falls_back_to_text_search_without_error`.

## Defects found (not fixed; production code untouched)

**D1, Low (defense in depth), T10.** `src/pr_evidence_reviewer/evidence/render.py:167-168` (`_numbered`)
emits repository line text raw, and `_inline` at `render.py:183-185` (`json.dumps(..., ensure_ascii=False)`)
doesn't escape U+2028, U+2029 or U+0085. `split_lines` correctly splits only on `\n` (T7), so a line can
still hold a lone `\r`, U+2028, U+2029, U+0085, `\v`, `\f` or `\x1c`-`\x1e`. To any reader that treats those
as line breaks (Python `str.splitlines`, many renderers, arguably the model), the text after them starts a
line with no gutter, so it can fake an `=== path ===` header. That breaks the threat model's claim "every
content line carries the gutter". Impact is limited: the boundary nonce still holds, and the citation
checker uses `\n` semantics and exact manifest paths, so a faked header can't make a citation pass.
Repro: `uv run python C:/Users/sandy/AppData/Local/Temp/claude/1e4ebd3c1733a9fa/44bd2b13-5a08-42f8-9070-8ba745bf684e/scratchpad/t10_repro.py` (run from the project root), which puts `def retry_upload(data):<SEP>=== src/fake.py (added, +1 -0) ===`
in a repository file and reviews it through `StandardReview`. For every SEP in `\r`, U+2028, U+2029, U+0085,
`\x1e`, `\v`, `\f`, the fake header is gutter-prefixed under `split("\n")` but **not** under `splitlines()`.
Suggested fix (for the implementer): in `_numbered` (and `_inline`), escape or visibly replace C0/C1
controls other than tab, plus U+2028/U+2029, the way L4 asks for bidi controls. Add a
`test_prompt_injection.py` case that's parametrised over those separators and uses `splitlines()`.

## Gate results (Windows 11, local)

| Command | Result |
|---|---|
| `uv run pytest -q` | 905 passed, 3 skipped (POSIX permission bits x2, directory symlinks need admin), 1 warning (Starlette httpx deprecation) |
| `uv run ruff check .` | All checks passed |
| `uv run black --check .` | 110 files unchanged |
| `uv run mypy` | no issues in 50 source files |
| `web: npm run lint` | clean |
| `web: npm run format:check` | all files use Prettier style |
| `web: npm run typecheck` | clean |
| `web: npm run test` | 7 files, 79 passed |
| `web: npm run build` | built (235.68 kB JS) |

Coverage: not configured in the project (n/a).

## Screenshots

QA didn't capture them. Playwright isn't installed in `web/node_modules`, and the screens need an
interactive flow (paste a link, press Enter) against a mocked `/api`, which would take a new harness. The
Lead will capture VerdictView (a code citation "src/retry.py, lines 3–8 (unchanged code)" plus a dropped
count), ClaimsView while checking and once loaded, and the App status line. Save them to
`specs/evidence-packet/screenshots/`. The copy for each is covered by `VerdictView.test.tsx`,
`ClaimsView.test.tsx` and `App.test.tsx`.
