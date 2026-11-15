# 0002 — Gemini through the signed-in Gemini CLI

- **Status:** Accepted
- **Date:** 2026-11-14
- **Deciders:** Sandy Thomas (owner)

## Context

ADR 0001 lists Gemini only as an API-key provider. Many developers have a Google account but no Gemini
API key, and the point of the signed-in CLI adapters is that the runner never holds a key. The Gemini
CLI (`@google/gemini-cli`) signs in with a Google account and can run headless, like Claude Code and
Codex.

The Gemini CLI differs from those two in ways that matter for safety:

- It has no structured-output flag in headless mode.
- On Windows, npm installs it as a `.cmd` batch shim, and batch files re-parse their arguments
  through `cmd.exe`.
- It has no flags to turn off its built-in tools. Its settings files can, but on Windows a system
  settings file is ignored unless an administrator owns its folder.

## Decision

Gemini joins Claude Code and Codex as a **signed-in CLI adapter** (`llm/gemini.py`, provider id
`gemini`), offered after them. The Gemini API-key adapter from ADR 0001 is still planned.

- **No batch shim.** On Windows the shim is resolved to `node` plus the package's JavaScript entry
  point, which the package's `package.json` declares, and that is started directly. If the npm layout
  isn't recognised, the provider reports itself unavailable instead of falling back to the shim.
- **A constant command line.** The PR text goes on stdin. The system prompt goes in a file that
  `GEMINI_SYSTEM_MD` points to, and it replaces the CLI's built-in prompt.
- **Tools off through workspace settings.** The CLI runs from an empty temporary workspace that holds
  only a `.gemini/settings.json`. `GEMINI_CLI_TRUST_WORKSPACE=true` makes the CLI load that file, which
  overrides the person's own settings. The file registers no built-in tools, allows no MCP servers, and
  turns off skills, hooks, sub-agents, context files, checkpointing, and telemetry. `--extensions none`
  turns off extensions.
- **Structured output by prompt and validation.** The JSON Schema goes in the system prompt. The reply
  must be one JSON object, optionally wrapped in a single code fence, and it goes through the same
  `validate_output` as every other provider.
- **Sign-in only, allowlisted environment.** The CLI gets only the variables it needs to start and find
  its sign-in. API keys, Vertex AI settings, endpoint overrides, and other tokens are left out.
- **No `.env` from above the workspace.** The CLI loads the first `.gemini/.env` or `.env` it finds
  walking up from its working directory. An empty `.gemini/.env` in the workspace ends that search, so
  a planted file (such as `C:\.gemini\.env`) can't redirect the sign-in token or the PR text. The
  person's own `~/.gemini/.env` isn't loaded either.
- **No self-relaunch.** `GEMINI_CLI_NO_RELAUNCH=true`, and the runner kills the whole process tree on
  timeout, so no CLI process outlives a stopped run.
- **Session records stay.** Each run's conversation, PR text included, is saved under `~/.gemini`.
  0.68 can't switch recording off, and `general.sessionRetention` would also prune the person's other
  chats, so the CLI's default 30-day cleanup applies.

*Rejected:*
- Calling the `.cmd` shim with a constant command line. It's safe today, but it depends on `cmd.exe`
  parsing staying harmless.
- A system settings file in the temporary folder. The CLI ignores it on Windows.
- The Gemini API with a key. That stays a separate, later adapter.

## Consequences

- **Privacy warning.** Prompts sent from a free-tier Google account may be used to improve Google
  products. A review sends the PR's description and code, so reviewing a private repository from a free
  account can disclose it. The provider's status detail says this every time Gemini is listed, so the
  person sees it before choosing Gemini.
- **Admin settings win.** An administrator's system settings still rank above ours. They belong to the
  machine's owner, not to PR text.
- **Format errors.** Structured output relies on the model following the prompt, so replies that don't
  match the format will be more common than with Claude Code or Codex. They fail with the standard
  "didn't match the expected format" error and never produce an unvalidated result.
- **Fragile settings.** The lockdown depends on Gemini CLI settings names (checked against 0.68.0). A
  rename could quietly switch something back on. `tests/llm/test_gemini.py` pins the settings we write,
  and the adapter's module docstring records what was checked and what wasn't.
- **Manual sign-in.** Signing in can't be automated. The person runs `gemini` once and chooses Sign in
  with Google. Until then the adapter reports "Gemini CLI isn't signed in" with those steps.
