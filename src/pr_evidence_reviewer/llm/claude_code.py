"""Claude Code CLI adapter: uses the person's own signed-in Claude Code, so the runner holds no key.

The CLI runs headless with every tool disabled, no MCP servers, no settings files (so no hooks), no
skills, and no saved session. It runs in an empty temporary directory, never inside a checkout. The
prompt goes in on stdin, so PR text never touches the command line.
"""

import json
import logging
import shutil
import tempfile
from collections.abc import Callable
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
from pr_evidence_reviewer.llm.process import (
    AsyncProcessRunner,
    OutputTooLargeError,
    ProcessResult,
    ProcessRunner,
    ProcessTimeoutError,
)

logger = logging.getLogger(__name__)

PROVIDER_ID = "claude_code"
PROVIDER_NAME = "Claude Code"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
INSTALL_HINT = "Install Claude Code from https://claude.com/claude-code, sign in, then try again."
SIGN_IN_HINT = "Run `claude` in a terminal and sign in, then try again."
_SIGNED_OUT_MARKERS = ("not logged in", "please run /login", "invalid api key", "authentication")
# Batch files re-parse their arguments through cmd.exe, which would mangle the JSON Schema argument.
_SHELL_WRAPPERS = frozenset({".cmd", ".bat"})


def build_argv(
    executable: str, system_prompt: str, schema_json: str, model: str | None
) -> list[str]:
    argv = [
        executable,
        "--print",
        "--output-format",
        "json",
        "--tools",
        "",
        "--strict-mcp-config",
        "--setting-sources",
        "",
        "--disable-slash-commands",
        "--no-session-persistence",
        "--system-prompt",
        system_prompt,
        "--json-schema",
        schema_json,
    ]
    if model:
        argv += ["--model", model]
    return argv


class ClaudeCodeProvider:
    id = PROVIDER_ID
    name = PROVIDER_NAME

    def __init__(
        self,
        which: Callable[[str], str | None] = shutil.which,
        runner: ProcessRunner | None = None,
        model: str | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES,
    ) -> None:
        self._which = which
        self._runner = runner or AsyncProcessRunner()
        self._model = model
        self._timeout = timeout
        self._max_output_bytes = max_output_bytes

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
        argv = build_argv(
            self._executable(), request.system_prompt, request.json_schema_text(), self._model
        )
        with tempfile.TemporaryDirectory(prefix="prev-llm-", ignore_cleanup_errors=True) as cwd:
            result = await self._run(argv, request.user_content.encode("utf-8"), Path(cwd))
        return self._parse(result, request.output_type)

    def _executable(self) -> str:
        executable = self._which("claude")
        if executable is None:
            raise ProviderUnavailableError(f"Claude Code isn't installed. {INSTALL_HINT}")
        if Path(executable).suffix.lower() in _SHELL_WRAPPERS:
            raise ProviderUnavailableError(
                "This Claude Code install runs through a batch file, which can't be called safely. "
                "Install the native build from https://claude.com/claude-code, then try again."
            )
        return executable

    async def _run(self, argv: list[str], stdin: bytes, cwd: Path) -> ProcessResult:
        try:
            return await self._runner.run(
                argv,
                stdin=stdin,
                cwd=cwd,
                timeout=self._timeout,
                max_output_bytes=self._max_output_bytes,
            )
        except ProcessTimeoutError as error:
            raise ProviderTimeoutError(
                f"Claude Code didn't finish within {self._timeout:.0f} seconds. Try again."
            ) from error
        except OutputTooLargeError as error:
            raise ProviderInvalidOutputError(
                "Claude Code returned more output than expected. Try again."
            ) from error
        except OSError as error:
            raise ProviderUnavailableError(
                f"Claude Code couldn't be started. {INSTALL_HINT}"
            ) from error

    def _parse[T: BaseModel](
        self, result: ProcessResult, output_type: type[T]
    ) -> StructuredResult[T]:
        envelope = _decode(result.stdout)
        if result.returncode != 0 or envelope is None or envelope.get("is_error"):
            raise self._failure(result, envelope)
        if "structured_output" not in envelope:
            raise ProviderInvalidOutputError(
                "Claude Code replied without the structured result. Try again."
            )
        output = validate_output(envelope["structured_output"], output_type, self.name)
        return StructuredResult(
            output=output, provider=self.id, model=_model(envelope), usage=_usage(envelope)
        )

    def _failure(
        self, result: ProcessResult, envelope: dict[str, Any] | None
    ) -> ProviderFailedError | ProviderUnavailableError | ProviderInvalidOutputError:
        detail = str((envelope or {}).get("result", "")) + result.stderr.decode("utf-8", "replace")
        if any(marker in detail.lower() for marker in _SIGNED_OUT_MARKERS):
            return ProviderUnavailableError(f"Claude Code isn't signed in. {SIGN_IN_HINT}")
        logger.warning(
            "Claude Code failed",
            extra={"returncode": result.returncode, "stderr_bytes": len(result.stderr)},
        )
        if result.returncode == 0 and envelope is None:
            return ProviderInvalidOutputError("Claude Code returned a reply that couldn't be read.")
        return ProviderFailedError(
            f"Claude Code stopped with an error (exit code {result.returncode}). Try again."
        )


def _decode(stdout: bytes) -> dict[str, Any] | None:
    try:
        envelope = json.loads(stdout)
    except (UnicodeDecodeError, ValueError):
        return None
    return envelope if isinstance(envelope, dict) else None


def _model(envelope: dict[str, Any]) -> str | None:
    models = envelope.get("modelUsage")
    if isinstance(models, dict) and models:
        return str(next(iter(models)))
    return None


def _usage(envelope: dict[str, Any]) -> Usage:
    raw = envelope.get("usage")
    usage: dict[str, Any] = raw if isinstance(raw, dict) else {}
    cost = envelope.get("total_cost_usd")
    input_parts = [
        _int_or_none(usage.get(key))
        for key in ("input_tokens", "cache_creation_input_tokens", "cache_read_input_tokens")
    ]
    counted = [part for part in input_parts if part is not None]
    return Usage(
        input_tokens=sum(counted) if counted else None,
        output_tokens=_int_or_none(usage.get("output_tokens")),
        cost_usd=float(cost) if isinstance(cost, int | float) else None,
    )


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
