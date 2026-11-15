"""Codex CLI adapter: uses the person's own signed-in Codex (ChatGPT sign-in), so the runner holds no key.

The CLI runs as `codex exec`, headless, in a read-only sandbox, from an empty temporary directory, never
inside a checkout. The PR text goes in on stdin. The system prompt and the output schema go in files
next to that directory, so no untrusted text touches the command line.

Codex has no system-prompt flag. `-c model_instructions_file=...` replaces Codex's built-in coding-agent
instructions with ours, which is the closest supported equivalent. `--strict-config` makes Codex refuse
any override or feature name it doesn't recognize, so a future Codex that renames a setting fails loudly
instead of quietly running with it switched back on.

Checked against codex-cli 0.167 with `codex debug prompt-input` and a live run.

Disabled:
- `~/.codex/config.toml` (`--ignore-user-config`): MCP servers, profiles, plugins, notify hooks, and
  the person's default model. Execpolicy `.rules` files (`--ignore-rules`).
- Shell and exec tools, apps and plugins (connector tools), hooks, browser and computer use, image
  generation and viewing, goals, tool suggestions, skill search, and memories, through
  `--disable <feature>` (see `ALWAYS_DISABLED_FEATURES`).
- Approval prompts (`approval_policy="never"`): anything the sandbox blocks fails instead of waiting.
- Web search (`web_search="disabled"`).
- AGENTS.md files in the working directory (`project_doc_max_bytes=0`; the directory is empty anyway).
- The skills list, the sandbox-permissions text, the environment context (which names the working
  directory), and the apps and collaboration-mode instructions, through the `include_*` overrides.
- Saved sessions and prompt history (`--ephemeral`, `history.persistence="none"`). The environment
  given to any shell command is empty (`shell_environment_policy.inherit="none"`).
- API keys and other secrets in the reviewer's environment: Codex gets only an allowlist (see
  `cli_env.py` plus `CODEX_HOME`), so it can't bill `CODEX_API_KEY` or `OPENAI_API_KEY` instead of the
  sign-in, and never sees a GitHub token.

Not disabled (no flag or override exists in 0.167):
- The global `AGENTS.md` in the Codex home directory is still sent as user-level instructions. It is
  the person's own file, not PR text, and our instructions arrive with higher (developer) priority.
- Some built-in tools stay listed: the JavaScript `exec` orchestrator (no file system or network),
  `apply_patch` (blocked by the read-only sandbox, and the workspace is empty), MCP resource listing
  (no MCP servers are loaded), user-input requests (unanswerable in exec mode), and a clock and a
  sleep tool.
- The sub-agent tools (`spawn_agent`, `wait_agent`, `send_message`, `followup_task`,
  `interrupt_agent`, `list_agents`). `--disable multi_agent` is passed, but 0.167 still lists them
  even with every feature switched off. Sub-agents get the same instructions, the same read-only
  sandbox, and no more tools than the main agent.
"""

import json
import logging
import os
import shutil
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from pr_evidence_reviewer.errors import (
    ProviderFailedError,
    ProviderInvalidOutputError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from pr_evidence_reviewer.llm.base import (
    ProviderStatus,
    StructuredRequest,
    StructuredResult,
    Usage,
    validate_output,
)
from pr_evidence_reviewer.llm.cli_env import allowlisted_environment, refuse_if_in_start_folder
from pr_evidence_reviewer.llm.process import (
    AsyncProcessRunner,
    OutputTooLargeError,
    ProcessResult,
    ProcessRunner,
    ProcessTimeoutError,
)
from pr_evidence_reviewer.llm.schema import strict_json_schema

logger = logging.getLogger(__name__)

PROVIDER_ID = "codex"
PROVIDER_NAME = "Codex"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
INSTALL_HINT = (
    "Install the Codex CLI from https://developers.openai.com/codex/cli, run `codex login`, then "
    "try again."
)
SIGN_IN_HINT = "Run `codex login` in a terminal and sign in, then try again."
UPDATE_HINT = "Run `codex update`, then try again."
INSTRUCTIONS_FILE = "instructions.md"
SCHEMA_FILE = "output-schema.json"
WORKSPACE_DIR = "workspace"

ALWAYS_DISABLED_FEATURES = (
    "shell_tool",
    "unified_exec",
    "apps",
    "plugins",
    "hooks",
    "browser_use",
    "computer_use",
    "image_generation",
    "view_image",
    "goals",
    "tool_suggest",
    "skill_search",
    "multi_agent",
    "memories",
)
CONFIG_OVERRIDES = (
    'web_search="disabled"',
    "project_doc_max_bytes=0",
    "include_permissions_instructions=false",
    "include_environment_context=false",
    "include_apps_instructions=false",
    "include_collaboration_mode_instructions=false",
    "skills.include_instructions=false",
    'history.persistence="none"',
    'shell_environment_policy.inherit="none"',
    'approval_policy="never"',
)
CODEX_ENV_NAMES = ("CODEX_HOME",)

_SIGNED_OUT_MARKERS = (
    "not logged in",
    "401 unauthorized",
    "missing bearer",
    "codex login",
    "sign in again",
    "log in again",
)
_UNSUPPORTED_SETTING_MARKERS = ("unknown configuration field", "unknown feature flag")
# Batch files and PowerShell scripts re-parse their arguments, which would mangle the quoted overrides.
_SHELL_WRAPPERS = frozenset({".cmd", ".bat", ".ps1"})


def build_argv(
    executable: str, instructions_path: Path, schema_path: Path, model: str | None
) -> list[str]:
    argv = [
        executable,
        "exec",
        "--ignore-user-config",
        "--ignore-rules",
        "--strict-config",
        "--skip-git-repo-check",
        "--ephemeral",
        "--sandbox",
        "read-only",
        "--color",
        "never",
        "--json",
        "--output-schema",
        str(schema_path),
        # json.dumps gives a valid TOML basic string, including backslashes in Windows paths.
        "-c",
        f"model_instructions_file={json.dumps(str(instructions_path))}",
    ]
    for override in CONFIG_OVERRIDES:
        argv += ["-c", override]
    for feature in ALWAYS_DISABLED_FEATURES:
        argv += ["--disable", feature]
    if model:
        argv += ["--model", model]
    argv.append("-")  # Read the prompt from stdin.
    return argv


class CodexProvider:
    id = PROVIDER_ID
    name = PROVIDER_NAME

    def __init__(
        self,
        which: Callable[[str], str | None] = shutil.which,
        runner: ProcessRunner | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
        environ: Mapping[str, str] | None = None,
    ) -> None:
        self._which = which
        self._runner = runner or AsyncProcessRunner()
        self._model = model
        self._timeout = timeout
        self._max_output_bytes = max_output_bytes
        self._environ = os.environ if environ is None else environ

    def status(self) -> ProviderStatus:
        try:
            executable = self._executable()
        except ProviderUnavailableError as error:
            return ProviderStatus(id=self.id, name=self.name, available=False, detail=error.message)
        return ProviderStatus(
            id=self.id, name=self.name, available=True, detail=f"Found at {executable}."
        )

    async def complete_structured[T: BaseModel](
        self, request: StructuredRequest[T]
    ) -> StructuredResult[T]:
        executable = self._executable()
        schema = json.dumps(strict_json_schema(request.json_schema()), separators=(",", ":"))
        with tempfile.TemporaryDirectory(prefix="prev-llm-", ignore_cleanup_errors=True) as root:
            instructions_path = Path(root) / INSTRUCTIONS_FILE
            schema_path = Path(root) / SCHEMA_FILE
            workspace = Path(root) / WORKSPACE_DIR
            instructions_path.write_text(request.system_prompt, encoding="utf-8")
            schema_path.write_text(schema, encoding="utf-8")
            workspace.mkdir()
            argv = build_argv(executable, instructions_path, schema_path, self._model)
            result = await self._run(argv, request.user_content.encode("utf-8"), workspace)
        return self._parse(result, request.output_type)

    def _executable(self) -> str:
        executable = self._which("codex")
        if executable is None:
            raise ProviderUnavailableError(f"Codex isn't installed. {INSTALL_HINT}")
        if Path(executable).suffix.lower() in _SHELL_WRAPPERS:
            raise ProviderUnavailableError(
                "This Codex install runs through a script wrapper, which can't be called safely. "
                "Install the native build from https://developers.openai.com/codex/cli, then try "
                "again."
            )
        refuse_if_in_start_folder(executable, self.name)
        return executable

    async def _run(self, argv: list[str], stdin: bytes, cwd: Path) -> ProcessResult:
        try:
            return await self._runner.run(
                argv,
                stdin=stdin,
                cwd=cwd,
                timeout=self._timeout,
                max_output_bytes=self._max_output_bytes,
                env=allowlisted_environment(self._environ, CODEX_ENV_NAMES),
            )
        except ProcessTimeoutError as error:
            raise ProviderTimeoutError(
                f"Codex didn't finish within {self._timeout:.0f} seconds. Try again."
            ) from error
        except OutputTooLargeError as error:
            raise ProviderInvalidOutputError(
                "Codex returned more output than expected. Try again."
            ) from error
        except OSError as error:
            raise ProviderUnavailableError(f"Codex couldn't be started. {INSTALL_HINT}") from error

    def _parse[T: BaseModel](
        self, result: ProcessResult, output_type: type[T]
    ) -> StructuredResult[T]:
        events = _decode_events(result.stdout)
        if result.returncode != 0 or any(event.get("type") == "turn.failed" for event in events):
            raise self._failure(result, events)
        if not events:
            raise ProviderInvalidOutputError("Codex returned a reply that couldn't be read.")
        message = _final_message(events)
        if message is None:
            raise ProviderInvalidOutputError(
                "Codex replied without the structured result. Try again."
            )
        try:
            reply: object = json.loads(message)
        except ValueError:
            reply = message  # Fails validation below with the standard "expected format" message.
        output = validate_output(reply, output_type, self.name)
        return StructuredResult(
            output=output, provider=self.id, model=self._model, usage=_usage(events)
        )

    def _failure(
        self, result: ProcessResult, events: list[dict[str, Any]]
    ) -> ProviderFailedError | ProviderUnavailableError:
        detail = " ".join(_error_messages(events)) + result.stderr.decode("utf-8", "replace")
        lowered = detail.lower()
        if any(marker in lowered for marker in _SIGNED_OUT_MARKERS):
            return ProviderUnavailableError(f"Codex isn't signed in. {SIGN_IN_HINT}")
        if any(marker in lowered for marker in _UNSUPPORTED_SETTING_MARKERS):
            return ProviderUnavailableError(
                "This Codex version doesn't support a setting the reviewer uses to keep it "
                f"read-only. {UPDATE_HINT}"
            )
        logger.warning(
            "Codex failed",
            extra={"returncode": result.returncode, "stderr_bytes": len(result.stderr)},
        )
        return ProviderFailedError(
            f"Codex stopped with an error (exit code {result.returncode}). Try again."
        )


def _decode_events(stdout: bytes) -> list[dict[str, Any]]:
    """Read the JSONL event stream, skipping any line that isn't a JSON object."""
    try:
        text = stdout.decode("utf-8")
    except UnicodeDecodeError:
        return []
    events: list[dict[str, Any]] = []
    for line in text.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if isinstance(event, dict):
            events.append(event)
    return events


def _final_message(events: list[dict[str, Any]]) -> str | None:
    message: str | None = None
    for event in events:
        item = event.get("item")
        if (
            event.get("type") == "item.completed"
            and isinstance(item, dict)
            and item.get("type") == "agent_message"
            and isinstance(item.get("text"), str)
        ):
            message = item["text"]
    return message


def _error_messages(events: list[dict[str, Any]]) -> list[str]:
    messages: list[str] = []
    for event in events:
        if event.get("type") == "error":
            messages.append(str(event.get("message", "")))
        error = event.get("error")
        if event.get("type") == "turn.failed" and isinstance(error, dict):
            messages.append(str(error.get("message", "")))
    return messages


def _usage(events: list[dict[str, Any]]) -> Usage:
    usage: dict[str, Any] = {}
    for event in events:
        raw = event.get("usage")
        if event.get("type") == "turn.completed" and isinstance(raw, dict):
            usage = raw
    # OpenAI counts cached input inside input_tokens and reasoning inside output_tokens.
    return Usage(
        input_tokens=_int_or_none(usage.get("input_tokens")),
        output_tokens=_int_or_none(usage.get("output_tokens")),
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
