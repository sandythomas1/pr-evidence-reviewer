"""A pull request reference and the snapshot of what gets reviewed."""

import re
from typing import Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from pr_evidence_reviewer.errors import InvalidPullRequestUrlError

MAX_URL_LENGTH = 2048
MAX_PR_NUMBER = 10**9
_OWNER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9-]{0,38})$")
_REPO = re.compile(r"^[A-Za-z0-9._-]{1,100}$")
_GITHUB_HOSTS = frozenset({"github.com", "www.github.com"})
_EXPECTED_SHAPE = "Paste a link like https://github.com/owner/repo/pull/123."


class PullRequestRef(BaseModel, frozen=True):
    owner: str
    repo: str
    number: int

    @property
    def slug(self) -> str:
        return f"{self.owner}/{self.repo}#{self.number}"


def parse_pull_request_url(url: str) -> PullRequestRef:
    """Parse a github.com pull request link. Anything else is rejected before any network call."""
    text = url.strip()
    if not text or len(text) > MAX_URL_LENGTH:
        raise InvalidPullRequestUrlError(f"That isn't a pull request link. {_EXPECTED_SHAPE}")

    parts = urlsplit(text)
    host = (parts.hostname or "").lower()
    if parts.scheme != "https" or host not in _GITHUB_HOSTS or parts.port or parts.username:
        raise InvalidPullRequestUrlError(
            f"Only github.com pull requests are supported. {_EXPECTED_SHAPE}"
        )

    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 4 or segments[2] != "pull":
        raise InvalidPullRequestUrlError(f"That isn't a pull request link. {_EXPECTED_SHAPE}")

    owner, repo, _, number_text = segments[:4]
    if not _OWNER.match(owner) or not _REPO.match(repo) or repo in {".", ".."}:
        raise InvalidPullRequestUrlError(
            f"The owner or repository name isn't valid. {_EXPECTED_SHAPE}"
        )
    if not number_text.isascii() or not number_text.isdigit():
        raise InvalidPullRequestUrlError(f"The pull request number isn't valid. {_EXPECTED_SHAPE}")
    number = int(number_text)
    if not 0 < number < MAX_PR_NUMBER:
        raise InvalidPullRequestUrlError(f"The pull request number isn't valid. {_EXPECTED_SHAPE}")

    return PullRequestRef(owner=owner, repo=repo.removesuffix(".git"), number=number)


class CommitRef(BaseModel, frozen=True):
    ref: str
    sha: str


FileStatus = Literal["added", "removed", "modified", "renamed", "copied", "changed", "unchanged"]


class ChangedFile(BaseModel, frozen=True):
    path: str
    status: FileStatus
    additions: int
    deletions: int
    previous_path: str | None = None
    patch: str | None = Field(
        default=None,
        description="Unified diff hunk. None when GitHub omits it (binary or too large).",
    )


class CheckResult(BaseModel, frozen=True):
    """One CI signal for the head commit: a GitHub check run or a commit status."""

    name: str
    source: Literal["check_run", "status"]
    status: str = Field(
        description="queued, in_progress, completed (check runs) or pending/completed."
    )
    conclusion: str | None = Field(
        description="success, failure, neutral, cancelled, skipped, timed_out, action_required, "
        "error, or None while the check hasn't finished."
    )
    url: str | None = None


class PullRequestSnapshot(BaseModel, frozen=True):
    """Everything about the PR that a review is pinned to. All evidence refers to `head.sha`."""

    ref: PullRequestRef
    title: str
    body: str
    author: str | None
    state: str
    draft: bool
    html_url: str
    base: CommitRef
    head: CommitRef
    changed_files_count: int
    files: list[ChangedFile]
    checks: list[CheckResult]
    scope_limitations: list[str] = Field(
        default_factory=list,
        description="What couldn't be inspected. Shown in the report as gaps, never as approval.",
    )
