"""Reads a GitHub token from the gh CLI when it's needed. The token is never stored or logged."""

import shutil
import subprocess
from collections.abc import Callable
from typing import Protocol

from pr_evidence_reviewer.errors import GitHubAuthError

GH_TIMEOUT_SECONDS = 10
SIGN_IN_HINT = "Run `gh auth login` in a terminal, then try again."


class TokenProvider(Protocol):
    def __call__(self) -> str: ...


Runner = Callable[..., subprocess.CompletedProcess[str]]


class GhCliTokenProvider:
    """Calls `gh auth token --hostname github.com` for each request that needs a token."""

    def __init__(
        self, which: Callable[[str], str | None] = shutil.which, run: Runner = subprocess.run
    ):
        self._which = which
        self._run = run

    def __call__(self) -> str:
        gh = self._which("gh")
        if gh is None:
            raise GitHubAuthError(
                "The GitHub CLI (gh) isn't installed. Install it from https://cli.github.com, "
                f"then sign in. {SIGN_IN_HINT}"
            )
        try:
            result = self._run(
                [gh, "auth", "token", "--hostname", "github.com"],
                capture_output=True,
                text=True,
                timeout=GH_TIMEOUT_SECONDS,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise GitHubAuthError(
                f"Couldn't read your GitHub sign-in from gh. {SIGN_IN_HINT}"
            ) from error

        token = result.stdout.strip()
        if result.returncode != 0 or not token:
            raise GitHubAuthError(f"You're not signed in to GitHub in gh. {SIGN_IN_HINT}")
        return token
