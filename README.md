# PR Evidence Reviewer

> **Status: pre-alpha, under active development.** Nothing here is usable yet.

Paste a GitHub pull request link. The reviewer pulls out each claim the PR description makes
("refunds are idempotent", "adds a retry with backoff", "covered by tests") and checks it against the
code changes, the surrounding source, the tests, and the CI results for that exact commit. Each claim
gets one of three verdicts:

| Verdict | Meaning |
|---|---|
| **Supported** | The inspected evidence backs the claim. Citations point to file, line, and commit. |
| **Contradicted** | The inspected evidence conflicts with the claim. |
| **Insufficient evidence** | The evidence needed to judge the claim isn't available. |

It also lists missing tests and suggests edge cases to check. **A human still decides whether to approve the PR.**

## What it can't tell you

Reading code can't prove how it behaves when it runs. A test existing doesn't mean it ran, and a passing
test covers only the scenarios it tests. The reviewer says what it inspected and reports missing evidence
as a gap, never as approval.

## How it will work

- **Local-first.** A runner on your machine reads GitHub through your existing `gh` sign-in and serves
  the report on `localhost`. Your code and credentials stay on your computer.
- **Bring your own LLM.** Use your signed-in Claude Code or Codex CLI (your subscription), a local model
  through Ollama, or an API key for Anthropic, OpenAI, or Gemini.
- **Any language.** Evidence comes from the diff, a read-only checkout, test-file conventions, and CI
  checks, with deeper symbol lookup for common languages.
- **Two review modes.** *Standard* gives every provider the same evidence packet, so results are
  reproducible. *Deep review* lets a signed-in CLI agent explore the checkout read-only. Its citations
  are checked the same way.
- **Read-only.** The report appears in the web UI. The reviewer never posts to your PR and never runs
  the PR's code.

Design: [`docs/design/PR_Evidence_Reviewer_Full_Flow.drawio`](docs/design/PR_Evidence_Reviewer_Full_Flow.drawio)
· Decisions: [`docs/decisions/`](docs/decisions/)

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12+.

```sh
uv sync                 # install the runner and dev tools
uv run prev serve       # start on http://127.0.0.1:8765
uv run pytest           # tests
uv run ruff check . && uv run black --check . && uv run mypy
```

## Contributing

Work happens on feature branches, which are merged into `dev` through pull requests. `dev` is promoted to
`staging` and then to `main`. See [`CLAUDE.md`](CLAUDE.md) for conventions.

## License

[MIT](LICENSE)
