# Screenshots: evidence-packet

Captured by the Lead on 2026-11-16 from the Vite dev server (`web/`), after fix round 2 (staged tree f9d4429). The API was a
stand-in runner that returned fictional canned data (example-org/payments #42), because a real review needs a signed-in
model CLI and a live GitHub PR. The notes and the "Not checked" entries are the backend's exact strings from
`review/verdicts.py` and `evidence/builder.py`; the model explanations are invented.

| File | Shows |
|---|---|
| 01-claims-checking.jpg | ClaimsView while checking: "…The first review of a commit can take a few minutes…". Screen-reader status line (visually hidden, read from the DOM): "Loaded example-org/payments #42. Found 2 claims. Checking them against the diff, the repository's code and tests, and the CI checks. This can take a few minutes…" |
| 02-verdicts-loaded.jpg | Loaded, with the repository read: the conditional scope sentence, the set-level dropped note, a "Not checked" entry (test cap), the per-verdict dropped note, the INCOMPLETE note pointing to "Not checked" above, and the "(repository code)" citation label |
| 03-repository-unreadable.jpg | Fallback to the diff and CI only: the "Not checked" entry "The repository at 3f9c2a1 couldn't be read… Code and tests outside the diff weren't checked. …", diff-only citations, and the INCOMPLETE note on every supported verdict |
