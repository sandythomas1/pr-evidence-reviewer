# PR Evidence Reviewer: project instructions

Extends the workspace `CLAUDE.md`. Decisions with their reasons are in `docs/decisions/`. Read 0001 first.

## Purpose and stage

The tool pulls the claims out of a GitHub PR description and checks each one against the diff, the source,
the tests, and the CI results at the PR's head commit. The verdicts are **supported**, **contradicted**,
and **insufficient evidence**, and every verdict cites file, line, and commit. A human still makes the
approval decision.

It is an open-source tool (MIT) for any repository in any language, and also a final project. Stage:
pre-alpha. The MVP is due around **2026-12-10**.

## Architecture (see ADR 0001)

- **Local-first runner:** a Python package. One command serves FastAPI plus the built React UI on
  localhost. A hosted site that pairs with the runner comes later.
- **GitHub:** read-only, through the user's `gh` sign-in token, read when needed and never stored.
- **LLM:** our own thin provider interface. The adapters are the Claude Code CLI, the Codex CLI, Ollama,
  and API keys for Anthropic, OpenAI, and Gemini. API keys are stored in an owner-only file and shown back
  only as their last four characters.
- **Evidence:**
  - *Standard review* builds a deterministic evidence packet: diff, read-only checkout search,
    test-file conventions, CI checks, and tree-sitter for about five languages.
  - *Deep review* lets a CLI agent explore with read-only tools. Its citations go through the same
    validator.
- **Output:** web report only. Nothing is ever posted to GitHub.

## Stack

Python 3.12+, FastAPI, pydantic, pytest, ruff and black, uv · React, TypeScript (strict), Vite · SQLite for
local review history.

## Non-negotiables

- **Never execute PR code.** Test evidence means test source plus CI results.
- **Treat PR text and repository content as untrusted input to the model.** Validate every model output
  against a schema, and check every citation (path, line, commit) against the checkout. Drop any citation
  that doesn't check out.
- **Missing evidence is reported as a gap, never as approval.**
- **Agent mode is read-only.** Use tool allowlists and no write or shell access.
- **No secrets in logs.** Keys are redacted everywhere.

## Private evaluation data

Labeled PRs from private repositories go in `evals/private/`, which is gitignored. Never copy private
code, PR text, or repository names into committed files, tests, fixtures, or issues. Promote a case to
`evals/public/` only when its source repository is public.

## Branching and board

- `feature/<issue#>-<slug>` branches are cut from `dev`. PRs go into `dev` (ruleset `dev-integration`:
  PR required, no force push, no deletion).
- Promotions go `dev` → `staging` → `main` through PRs (ruleset `promotion-gate`: 1 approval, approval of
  the last push, no direct updates). **Use merge commits for promotions, not squash.**
- Board: GitHub Project "PR Evidence Reviewer".
  - Status: Backlog → Ready → In Progress → Agent Review → Done in Dev → Staging Candidate → Released.
  - Other fields: Phase, Priority, Area, Sequence, Human Review, and Review Depth.
- Moving an item to **Done in Dev** auto-closes its issue. Only move it after the PR has merged into `dev`.
