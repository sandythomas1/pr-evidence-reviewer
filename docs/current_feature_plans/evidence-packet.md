# Evidence packet (Week 2: #12–#17)
Branch: feature/2-evidence-packet · Lead run: 2026-11-16 · Status: ready (PR into dev; task 41 waits on Linux/macOS CI)
Intent: standard review builds a deterministic, stored evidence packet from a read-only checkout at the
head SHA (search, test mapping, symbols, CI, gaps), and every model citation is validated against that
checkout. ACs: spec.md AC1–AC9.
Change surfaces: new checkout/cache subsystem (security boundary: untrusted repo on disk, gh token at call
time), search, test conventions, tree-sitter symbols, packet schema + storage, citation validator,
verdicts wiring; small UI change for dropped-citation counts.
Likely touch points: src/pr_evidence_reviewer/{config.py, github/, review/evidence.py, review/citations.py,
review/verdicts.py, api/verdicts.py, domain/verdicts.py}, new src/pr_evidence_reviewer/evidence/ (or
similar), pyproject.toml deps, web/src/components/VerdictView.tsx.
Coordination: no other active plans. feature/30-public-eval-labels (local, evals/ only) does not overlap.
