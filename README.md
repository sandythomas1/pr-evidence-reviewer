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
- **Bring your own LLM.** Use your signed-in Claude Code, Codex, or Gemini CLI (your subscription or
  Google account), a local model through Ollama, or an API key for Anthropic, OpenAI, or Gemini.
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

Requires [uv](https://docs.astral.sh/uv/), Python 3.12+, Node 24, and a signed-in [GitHub CLI](https://cli.github.com/) (`gh auth login`).

```sh
uv sync                 # install the runner and dev tools
npm --prefix web ci     # install the web UI's tools
npm --prefix web run build
uv run prev serve       # open http://127.0.0.1:8765
```

Checks:

```sh
uv run pytest
uv run ruff check . && uv run black --check . && uv run mypy
npm --prefix web run lint && npm --prefix web test
```

For UI work, run `uv run prev serve` in one terminal and `npm --prefix web run dev` in another. Vite
serves the UI with hot reload and forwards `/api` to the runner.

The runner only answers requests addressed to `localhost`, `127.0.0.1`, or `[::1]`, so a web page can't
reach it through DNS rebinding.

## What a review looks at

A standard review gives the model one evidence packet for the PR's head commit:

- the diff and the CI check results for that commit;
- the tests found for each changed source file, from the changed files and the repository's test-file
  conventions;
- the function or class around each change;
- search results and definitions for the names and files each claim mentions.

To get the repository's files, the runner downloads GitHub's archive of the head commit and checks every
file against the commit's file list ([ADR 0003](docs/decisions/0003-checkout-from-github-archive.md)). It
doesn't use git and never runs the repository's code. The first review of a commit can take minutes for a
large repository; later reviews of the same commit reuse the download. If the repository can't be read,
the review uses only the diff and CI checks, says so, and marks the evidence incomplete.

### What's kept on your computer

| What | Where | How long |
|---|---|---|
| Repository downloads | `checkouts` in the cache folder | removed after 7 days unused, and least recently used first once they pass 2 GiB, while `prev` runs |
| Evidence packets | `packets` in the data folder | until you clear them |

| | Cache folder | Data folder |
|---|---|---|
| Windows | `%LOCALAPPDATA%\pr-evidence-reviewer\Cache` | `%LOCALAPPDATA%\pr-evidence-reviewer` |
| macOS | `~/Library/Caches/pr-evidence-reviewer` | `~/Library/Application Support/pr-evidence-reviewer` |
| Linux | `~/.cache/pr-evidence-reviewer` | `~/.local/share/pr-evidence-reviewer` |

Repository downloads can hold private source code. To delete them now:

```sh
uv run prev cache clear             # delete repository downloads
uv run prev cache clear --packets   # also delete saved evidence packets
```

## Contributing

Work happens on feature branches, which are merged into `dev` through pull requests. `dev` is promoted to
`staging` and then to `main`. See [`CLAUDE.md`](CLAUDE.md) for conventions.

## License

[MIT](LICENSE)
