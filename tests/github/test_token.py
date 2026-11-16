import subprocess
from typing import Any

import pytest

from pr_evidence_reviewer.errors import GitHubAuthError
from pr_evidence_reviewer.github.token import GhCliTokenProvider
from tests.fakes import TOKEN


def completed(returncode: int = 0, stdout: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


def test_reads_the_token_from_gh_without_a_shell() -> None:
    calls: list[tuple[Any, dict[str, Any]]] = []

    def run(args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        calls.append((args, kwargs))
        return completed(stdout=TOKEN + "\n")

    provider = GhCliTokenProvider(which=lambda _: "/usr/bin/gh", run=run)

    assert provider() == TOKEN
    args, kwargs = calls[0]
    assert args == ["/usr/bin/gh", "auth", "token", "--hostname", "github.com"]
    assert kwargs.get("shell", False) is False
    assert kwargs["timeout"] > 0


def test_missing_gh_explains_how_to_install() -> None:
    provider = GhCliTokenProvider(which=lambda _: None)

    with pytest.raises(GitHubAuthError, match="isn't installed"):
        provider()


@pytest.mark.parametrize("result", [completed(returncode=1), completed(stdout="  \n")])
def test_signed_out_gh_asks_the_user_to_sign_in(result: subprocess.CompletedProcess[str]) -> None:
    provider = GhCliTokenProvider(which=lambda _: "gh", run=lambda *a, **k: result)

    with pytest.raises(GitHubAuthError, match="gh auth login"):
        provider()


@pytest.mark.parametrize(
    "error", [OSError("boom"), subprocess.TimeoutExpired(cmd="gh", timeout=10)]
)
def test_gh_failures_become_auth_errors(error: Exception) -> None:
    def run(*_: Any, **__: Any) -> subprocess.CompletedProcess[str]:
        raise error

    provider = GhCliTokenProvider(which=lambda _: "gh", run=run)

    with pytest.raises(GitHubAuthError):
        provider()
