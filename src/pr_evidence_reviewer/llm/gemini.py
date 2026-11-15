"""Gemini CLI adapter: uses the person's own Google sign-in to the Gemini CLI, so the runner holds no key.

The CLI runs headless (`--output-format json`, prompt on stdin) from an empty temporary directory, never
inside a checkout. The PR text goes in on stdin. The command line is the same on every run: no PR text,
system prompt, or schema is ever an argument.

Windows: `npm install -g` puts a `gemini.cmd` shim on PATH. Running a batch file makes cmd.exe re-parse
the arguments, so we never run it. We resolve the shim to what it runs, `node` plus the package's
JavaScript entry point (read from the package's `package.json` `bin`, and required to sit inside the
package), and start that directly. If the layout isn't the standard npm one, the provider reports itself
unavailable instead of falling back to the shim.

Gemini has no structured-output flag in headless mode, so the JSON Schema goes in the system prompt, the
reply is parsed as one JSON object (one surrounding code fence is tolerated, nothing else), and
`validate_output` checks it like every other provider.

How the CLI is locked down, checked against @google/gemini-cli 0.68.0 source and a signed-out run:
- System prompt: `GEMINI_SYSTEM_MD` points at our file, which replaces the built-in prompt entirely.
- Settings: a `.gemini/settings.json` inside the temporary workspace, made trusted with
  `GEMINI_CLI_TRUST_WORKSPACE=true`. Workspace settings override the person's `~/.gemini/settings.json`,
  and lists such as `tools.core` replace rather than merge. (A system settings file would rank higher,
  but on Windows the CLI ignores one whose folder isn't owned by an administrator, so it can't be used.)

Disabled:
- Every built-in tool: `tools.core: []` registers none, including shell, file read/write/edit, grep,
  glob, web fetch, web search, memory, todos, skills, plan mode, and sub-agents.
  `tools.discoveryCommand` is cleared, so no discovered tools.
- Extensions (`--extensions none`), MCP servers (`mcp.allowed: []` allows none, and `admin.mcp.enabled`
  is false), skills, hooks, and sub-agents (`experimental.enableAgents`).
- Context files: `context.fileName` names a file that doesn't exist, so no `GEMINI.md` is loaded from the
  home folder or anywhere else, and `context.discoveryMaxDirs: 0`.
- Checkpointing, telemetry, and usage statistics.
- The environment: the CLI gets only an allowlist (see `cli_env.py` plus `GEMINI_ENV_NAMES`). API keys
  (`GEMINI_API_KEY`, `GOOGLE_API_KEY`), Vertex AI and service-account settings, endpoint overrides
  (`CODE_ASSIST_ENDPOINT`, `GOOGLE_GEMINI_BASE_URL`), and GitHub or other providers' tokens are left
  out, so the run uses the Google sign-in and talks only to Google's default endpoints.
- `.env` files: the CLI looks for `.gemini/.env` or `.env` in its working directory and every folder
  above it, and loads the first one it finds with full trust. Anyone who can write to a folder above
  the temporary directory (on Windows, `C:\\.gemini\\.env` needs no admin rights) could otherwise set
  an endpoint that receives the sign-in token and the PR text. An empty `.gemini/.env` in the workspace
  ends that search before it leaves the workspace. Side effect: the person's own `~/.gemini/.env` isn't
  loaded either, so a setting such as `GOOGLE_CLOUD_PROJECT` has to come from the real environment.
- Self-relaunch: the CLI normally restarts itself as a child process with more memory, which a timeout
  kill wouldn't reach. `GEMINI_CLI_NO_RELAUNCH=true` stops that, and the runner kills the whole process
  tree anyway.

Not disabled:
- Admin system settings (`C:\\ProgramData\\gemini-cli\\settings.json` and equivalents) still rank above
  ours. They belong to the machine's administrator, not to PR text.
- Session records: each run saves its conversation, including the PR text, under `~/.gemini`, filed
  under the run's temporary folder. 0.68 has no switch to stop recording. `general.sessionRetention`
  could shorten how long records are kept, but it applies to every project's sessions, so setting it
  here would delete the person's other chats. The CLI's own retention (30 days by default) applies.

Not verified, because no signed-in account was available: the token counts under `stats` (parsed
defensively, from the source's `stats.models.<model>.tokens` shape) and an end-to-end reply.
"""

import json
import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable, Iterable, Mapping
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

logger = logging.getLogger(__name__)

PROVIDER_ID = "gemini"
PROVIDER_NAME = "Gemini CLI"
DEFAULT_TIMEOUT_SECONDS = 300.0
DEFAULT_MAX_OUTPUT_BYTES = 2 * 1024 * 1024
PACKAGE = ("@google", "gemini-cli")
INSTALL_HINT = (
    "Install it with `npm install -g @google/gemini-cli`, run `gemini` in a terminal, choose Sign in "
    "with Google, then try again."
)
SIGN_IN_HINT = "Run `gemini` in a terminal, choose Sign in with Google, then try again."
PRIVACY_WARNING = (
    "With a free Google account, Google may use what you send to improve its products, so avoid it "
    "for private repositories."
)
SYSTEM_PROMPT_FILE = "system.md"
EMPTY_ENV_FILE = ".env"
WORKSPACE_DIR = "workspace"
STDIN_INSTRUCTION = "Follow the system instructions for the input above. Reply with only JSON."
EXIT_SIGNED_OUT = 41  # FatalAuthenticationError in the Gemini CLI.

LOCKED_DOWN_SETTINGS: dict[str, Any] = {
    "tools": {"core": [], "exclude": [], "discoveryCommand": "", "callCommand": ""},
    "mcp": {"allowed": [], "excluded": []},
    "admin": {
        "extensions": {"enabled": False},
        "mcp": {"enabled": False},
        "skills": {"enabled": False},
    },
    "skills": {"enabled": False},
    "hooksConfig": {"enabled": False},
    "experimental": {"enableAgents": False},
    "context": {
        "fileName": ["PREV_NO_CONTEXT_FILE.md"],
        "discoveryMaxDirs": 0,
        "loadMemoryFromIncludeDirectories": False,
    },
    "general": {"checkpointing": {"enabled": False}},
    "telemetry": {"enabled": False},
    "privacy": {"usageStatisticsEnabled": False},
}
# Where the sign-in lives and which Google Cloud project a Workspace account uses; nothing else.
GEMINI_ENV_NAMES = (
    "GEMINI_CLI_HOME",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_PROJECT_ID",
    "GOOGLE_CLOUD_LOCATION",
    "NODE_EXTRA_CA_CERTS",
)

_SIGNED_OUT_MARKERS = (
    "auth method",
    "sign in",
    "login required",
    "reauthenticate",
    "invalid_grant",
    "unauthenticated",
)
_SHELL_WRAPPERS = frozenset({".cmd", ".bat", ".ps1"})
_JS_SUFFIXES = frozenset({".js", ".mjs", ".cjs"})
_FENCED = re.compile(r"\A```(?:json)?[ \t]*\n(?P<body>.*?)\n?```\Z", re.DOTALL | re.IGNORECASE)


def build_argv(command: list[str], model: str | None) -> list[str]:
    argv = [
        *command,
        "--output-format",
        "json",
        "--extensions",
        "none",
        "--approval-mode",
        "default",
    ]
    if model:
        argv += ["--model", model]
    return [*argv, "--prompt", STDIN_INSTRUCTION]


def build_system_prompt[T: BaseModel](request: StructuredRequest[T]) -> str:
    return (
        f"{request.system_prompt}\n\n"
        "## Reply format\n\n"
        "Reply with exactly one JSON object that matches this JSON Schema. Write nothing before or "
        "after it.\n\n"
        f"{request.json_schema_text()}\n"
    )


def child_environment(system_prompt_path: Path, base: Mapping[str, str]) -> dict[str, str]:
    env = allowlisted_environment(base, GEMINI_ENV_NAMES)
    env["GEMINI_SYSTEM_MD"] = str(system_prompt_path)
    env["GEMINI_CLI_TRUST_WORKSPACE"] = "true"
    env["GEMINI_CLI_NO_RELAUNCH"] = "true"
    return env


class GeminiCliProvider:
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
        self._environ = environ

    def status(self) -> ProviderStatus:
        try:
            command = self._command()
        except ProviderUnavailableError as error:
            return ProviderStatus(id=self.id, name=self.name, available=False, detail=error.message)
        return ProviderStatus(
            id=self.id,
            name=self.name,
            available=True,
            detail=f"Found at {command[-1]}. {PRIVACY_WARNING}",
        )

    async def complete_structured[T: BaseModel](
        self, request: StructuredRequest[T]
    ) -> StructuredResult[T]:
        command = self._command()
        with tempfile.TemporaryDirectory(prefix="prev-llm-", ignore_cleanup_errors=True) as root:
            system_prompt_path = Path(root) / SYSTEM_PROMPT_FILE
            workspace = Path(root) / WORKSPACE_DIR
            settings_dir = workspace / ".gemini"
            settings_dir.mkdir(parents=True)
            system_prompt_path.write_text(build_system_prompt(request), encoding="utf-8")
            (settings_dir / "settings.json").write_text(
                json.dumps(LOCKED_DOWN_SETTINGS, indent=2), encoding="utf-8"
            )
            # Found first, so the CLI never loads a .env from a folder above the workspace.
            (settings_dir / EMPTY_ENV_FILE).write_bytes(b"")
            environ = os.environ if self._environ is None else self._environ
            result = await self._run(
                build_argv(command, self._model),
                request.user_content.encode("utf-8"),
                workspace,
                child_environment(system_prompt_path, environ),
            )
        return self._parse(result, request.output_type)

    def _command(self) -> list[str]:
        """The argv prefix that starts the CLI without going through a batch file."""
        found = self._which("gemini")
        if found is None:
            raise ProviderUnavailableError(f"Gemini CLI isn't installed. {INSTALL_HINT}")
        refuse_if_in_start_folder(found, self.name)
        if Path(found).suffix.lower() not in _SHELL_WRAPPERS:
            return [found]
        return self._resolve_npm_shim(Path(found))

    def _resolve_npm_shim(self, shim: Path) -> list[str]:
        unsupported = ProviderUnavailableError(
            "This Gemini CLI install can't be started safely, because its launcher is a script and "
            f"the program behind it wasn't found. Reinstall it. {INSTALL_HINT}"
        )
        package = shim.parent.joinpath("node_modules", *PACKAGE)
        try:
            manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        except (OSError, ValueError) as error:
            raise unsupported from error
        bin_entry = manifest.get("bin") if isinstance(manifest, dict) else None
        if isinstance(bin_entry, dict):
            bin_entry = bin_entry.get("gemini")
        if not isinstance(bin_entry, str):
            raise unsupported
        root = package.resolve()
        entry = (root / bin_entry).resolve()
        if (
            not entry.is_relative_to(root)
            or entry.suffix not in _JS_SUFFIXES
            or not entry.is_file()
        ):
            raise unsupported
        node = self._node(shim.parent)
        if node is None:
            raise unsupported
        return [node, str(entry)]

    def _node(self, shim_dir: Path) -> str | None:
        bundled = shim_dir / "node.exe"  # The shim prefers a node.exe next to it, as we do.
        found = str(bundled) if bundled.is_file() else self._which("node")
        if found is None or Path(found).suffix.lower() in _SHELL_WRAPPERS:
            return None
        refuse_if_in_start_folder(found, "Node.js")
        return found

    async def _run(
        self, argv: list[str], stdin: bytes, cwd: Path, env: dict[str, str]
    ) -> ProcessResult:
        try:
            return await self._runner.run(
                argv,
                stdin=stdin,
                cwd=cwd,
                timeout=self._timeout,
                max_output_bytes=self._max_output_bytes,
                env=env,
            )
        except ProcessTimeoutError as error:
            raise ProviderTimeoutError(
                f"Gemini CLI didn't finish within {self._timeout:.0f} seconds. Try again."
            ) from error
        except OutputTooLargeError as error:
            raise ProviderInvalidOutputError(
                "Gemini CLI returned more output than expected. Try again."
            ) from error
        except OSError as error:
            raise ProviderUnavailableError(
                f"Gemini CLI couldn't be started. {INSTALL_HINT}"
            ) from error

    def _parse[T: BaseModel](
        self, result: ProcessResult, output_type: type[T]
    ) -> StructuredResult[T]:
        payload = _decode_object(result.stdout)
        if result.returncode != 0 or payload.get("error"):
            raise self._failure(result, payload)
        response = payload.get("response")
        if not isinstance(response, str):
            raise ProviderInvalidOutputError("Gemini CLI returned a reply that couldn't be read.")
        reply = _json_reply(response)
        output = validate_output(reply, output_type, self.name)
        models = _model_stats(payload)
        return StructuredResult(
            output=output,
            provider=self.id,
            model=self._model or _main_model(models),
            usage=_usage(models),
        )

    def _failure(
        self, result: ProcessResult, payload: dict[str, Any]
    ) -> ProviderFailedError | ProviderUnavailableError:
        error = payload.get("error")
        message = str(error.get("message", "")) if isinstance(error, dict) else ""
        detail = (message + " " + result.stderr.decode("utf-8", "replace")).lower()
        if result.returncode == EXIT_SIGNED_OUT or any(m in detail for m in _SIGNED_OUT_MARKERS):
            return ProviderUnavailableError(f"Gemini CLI isn't signed in. {SIGN_IN_HINT}")
        logger.warning(
            "Gemini CLI failed",
            extra={"returncode": result.returncode, "stderr_bytes": len(result.stderr)},
        )
        return ProviderFailedError(
            f"Gemini CLI stopped with an error (exit code {result.returncode}). Try again."
        )


def _decode_object(data: bytes) -> dict[str, Any]:
    try:
        value = json.loads(data.decode("utf-8"))
    except ValueError:
        return {}
    return value if isinstance(value, dict) else {}


def _json_reply(response: str) -> object:
    """Parse the reply as one JSON object, allowing a single surrounding code fence and nothing else."""
    text = response.strip()
    fenced = _FENCED.match(text)
    if fenced:
        text = fenced.group("body").strip()
    try:
        return json.loads(text)
    except ValueError:
        return text  # Fails validation with the standard "expected format" message.


def _model_stats(payload: dict[str, Any]) -> dict[str, dict[str, Any]]:
    stats = payload.get("stats")
    models = stats.get("models") if isinstance(stats, dict) else None
    if not isinstance(models, dict):
        return {}
    return {
        name: tokens
        for name, entry in models.items()
        if isinstance(entry, dict) and isinstance(tokens := entry.get("tokens"), dict)
    }


def _main_model(models: dict[str, dict[str, Any]]) -> str | None:
    """The model that did the work. A routing model may also appear, with far fewer tokens."""
    if not models:
        return None
    return max(models, key=lambda name: _int_or_none(models[name].get("total")) or 0)


def _usage(models: dict[str, dict[str, Any]]) -> Usage:
    if not models:
        return Usage()
    return Usage(
        input_tokens=_sum(tokens.get("prompt") for tokens in models.values()),
        output_tokens=_sum(tokens.get("candidates") for tokens in models.values()),
    )


def _sum(values: Iterable[object]) -> int | None:
    counts = [count for value in values if (count := _int_or_none(value)) is not None]
    return sum(counts) if counts else None


def _int_or_none(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None
