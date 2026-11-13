# 0001 — Local-first runner, bring-your-own LLM, hybrid evidence

- **Status:** Accepted
- **Date:** 2026-11-12
- **Deciders:** Sandy Thomas (owner)

## Context

The PR Evidence Reviewer checks claims in a PR description against code, tests, and CI evidence. It is a
final project due around 2026-12-10, and it is also meant to be a real open-source tool that any developer
can run on any repository, not only Python ones. Users must be able to use their own LLM, either a
subscription (Claude Code, Codex) or an open model.

The bring-your-own-LLM design follows [syftnotes](https://github.com/joseph-rus/syftnotes)
(`engine/src/StudyStash.Core/Ai/Providers.cs`, `ApiKeys.cs`):

- An abstract provider class has one subclass per provider.
- Every provider emits the same event stream.
- The app can use the user's signed-in CLI, so it never holds a key.
- An optional API key is stored owner-only and shown back only as its last four characters.
- Ollama gives a private, free local option.

syftnotes is a desktop app. Its approach works because keys and CLIs stay on the user's machine.

## Decisions

1. **Local-first runner now, hosted site later.** The core is a Python package. One command starts a
   FastAPI server and the built React report UI on `localhost`. A hosted site comes later and pairs with
   the user's runner. The runner keeps GitHub access and LLM credentials.
   *Why:* a hosted server can't reach a user's signed-in CLI or their laptop's Ollama. Signing in to a
   consumer subscription on a third-party server is also generally not allowed by provider terms.
   *Rejected:* hosted bring-your-own-key only (no subscriptions); Docker Compose (a container can't use the
   host's `gh`, `claude`, or `codex` sign-ins).
2. **GitHub access reuses the `gh` CLI sign-in** and reads a token when needed. No GitHub secret is
   stored. The hosted phase will need a GitHub App.
3. **Bring-your-own LLM through a thin provider interface we own**, with these adapters:
   - signed-in CLI agents: Claude Code and Codex
   - Ollama
   - API keys: Anthropic, OpenAI, Gemini

   *Why not LiteLLM or similar:* schema-validated verdicts and citation checks are the core of the product,
   and each provider enforces structured output differently. We want that layer under our control and
   tested.
4. **Hybrid evidence, with both modes in the MVP.**
   - *Standard review:* every provider receives the same deterministic evidence packet. The packet holds
     the diff, text search over a read-only checkout at the head commit, test-file conventions, and CI
     check results, plus tree-sitter symbol lookup for about five common languages. The packet is stored
     with the head commit, so a review can be reproduced.
   - *Deep review:* a signed-in CLI agent explores the checkout with **read-only** tools only. It may only
     add evidence. Every citation goes through the same validator.

   *Why:* standard review keeps results comparable across providers and possible to evaluate. Deep review
   finds more evidence for claims that span several files. Comparing the two is part of the evaluation.
5. **The report appears only in the web UI.** GitHub access is read-only, and nothing is posted to PRs.
6. **The PR's code is never executed.** Test evidence comes from test source code and CI results.
   PR text and repository content are untrusted input to the LLM.
7. **The evaluation set uses the owner's repositories plus public repositories in several languages.**
   Cases from private repositories live in `evals/private/`, which is gitignored. A case moves to
   `evals/public/` only once its source repository is public.
8. **License: MIT.**

## Consequences

- Graders and users install the runner locally, so the install guide and provider setup checks are part of
  the MVP.
- Two review modes double the LLM integration work in week 3. Deep review is the first thing to cut if
  the schedule slips.
- Any provider that can't return valid structured verdicts (for example, a small Ollama model) must fail
  with a clear message. It must never produce an unvalidated verdict.
- Any CLI agent with tools is a prompt-injection risk. Read-only tool allowlists
  (`claude --allowedTools Read,Grep,Glob`, `codex --sandbox read-only`) and validating every citation
  are required, not optional.
