"""Read-only GitHub REST client. Only GET requests, only to api.github.com."""

import asyncio
import re
from typing import Any

import httpx

from pr_evidence_reviewer import __version__
from pr_evidence_reviewer.domain.pull_request import (
    ChangedFile,
    CheckResult,
    CommitRef,
    PullRequestRef,
    PullRequestSnapshot,
)
from pr_evidence_reviewer.errors import (
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)
from pr_evidence_reviewer.github.token import SIGN_IN_HINT, TokenProvider

API_BASE_URL = "https://api.github.com"
PER_PAGE = 100
MAX_FILE_PAGES = 30  # GitHub returns at most 3000 files for a pull request
MAX_CHECK_PAGES = 10
DEFAULT_TIMEOUT_SECONDS = 20.0
_SHA = re.compile(r"^[0-9a-f]{40}$")

JsonObject = dict[str, Any]


class _OptionalEvidenceError(Exception):
    """A request for optional evidence (CI) failed; the snapshot records a gap instead."""


class GitHubClient:
    def __init__(
        self,
        token_provider: TokenProvider,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
    ) -> None:
        self._token_provider = token_provider
        self._transport = transport
        self._timeout = timeout

    async def fetch_snapshot(self, ref: PullRequestRef) -> PullRequestSnapshot:
        try:
            return await self._fetch_snapshot(ref)
        except (
            KeyError,
            TypeError,
            ValueError,
        ) as error:  # pydantic's ValidationError is a ValueError
            raise GitHubUnavailableError(
                "GitHub returned a response in an unexpected format."
            ) from error

    async def _fetch_snapshot(self, ref: PullRequestRef) -> PullRequestSnapshot:
        token = await asyncio.to_thread(self._token_provider)
        async with self._open(token) as http:
            pull = await self._get_json(http, ref, self._repo_path(ref, f"pulls/{ref.number}"))
            head_sha = _validated_sha(pull["head"]["sha"])
            files, checks = await asyncio.gather(
                self._fetch_files(http, ref), self._fetch_checks(http, ref, head_sha)
            )

        changed_files = list(files)
        limitations = _file_limitations(changed_files, int(pull.get("changed_files", 0)))
        check_results, check_limitations = checks
        return PullRequestSnapshot(
            ref=ref,
            title=pull.get("title") or "",
            body=pull.get("body") or "",
            author=(pull.get("user") or {}).get("login"),
            state=pull.get("state", "unknown"),
            draft=bool(pull.get("draft", False)),
            html_url=pull["html_url"],
            base=CommitRef(ref=pull["base"]["ref"], sha=_validated_sha(pull["base"]["sha"])),
            head=CommitRef(ref=pull["head"]["ref"], sha=head_sha),
            changed_files_count=int(pull.get("changed_files", len(changed_files))),
            files=changed_files,
            checks=check_results,
            scope_limitations=limitations + check_limitations,
        )

    def _open(self, token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=API_BASE_URL,
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            headers={
                "Accept": "application/vnd.github+json",
                "Authorization": f"Bearer {token}",
                "User-Agent": f"pr-evidence-reviewer/{__version__}",
                "X-GitHub-Api-Version": "2022-11-28",
            },
        )

    @staticmethod
    def _repo_path(ref: PullRequestRef, suffix: str) -> str:
        return f"/repos/{ref.owner}/{ref.repo}/{suffix}"

    async def _fetch_files(self, http: httpx.AsyncClient, ref: PullRequestRef) -> list[ChangedFile]:
        files: list[ChangedFile] = []
        path = self._repo_path(ref, f"pulls/{ref.number}/files")
        for page in range(1, MAX_FILE_PAGES + 1):
            batch = await self._get_json(http, ref, path, {"per_page": PER_PAGE, "page": page})
            files.extend(_changed_file(item) for item in batch)
            if len(batch) < PER_PAGE:
                break
        return files

    async def _fetch_checks(
        self, http: httpx.AsyncClient, ref: PullRequestRef, sha: str
    ) -> tuple[list[CheckResult], list[str]]:
        checks: list[CheckResult] = []
        limitations: list[str] = []
        try:
            checks.extend(await self._fetch_check_runs(http, ref, sha))
        except _OptionalEvidenceError:
            limitations.append(f"CI check runs for {sha[:7]} couldn't be read.")
        try:
            statuses, total = await self._fetch_statuses(http, ref, sha)
            checks.extend(statuses)
            if total > len(statuses):
                limitations.append(f"Only {len(statuses)} of {total} commit statuses were read.")
        except _OptionalEvidenceError:
            limitations.append(f"Commit statuses for {sha[:7]} couldn't be read.")
        return sorted(checks, key=lambda check: (check.name.lower(), check.source)), limitations

    async def _fetch_check_runs(
        self, http: httpx.AsyncClient, ref: PullRequestRef, sha: str
    ) -> list[CheckResult]:
        runs: list[CheckResult] = []
        path = self._repo_path(ref, f"commits/{sha}/check-runs")
        for page in range(1, MAX_CHECK_PAGES + 1):
            body = await self._get_optional_json(
                http, ref, path, {"per_page": PER_PAGE, "page": page}
            )
            batch = body.get("check_runs", [])
            runs.extend(
                CheckResult(
                    name=run.get("name") or "unnamed check",
                    source="check_run",
                    status=run.get("status") or "unknown",
                    conclusion=run.get("conclusion"),
                    url=run.get("html_url"),
                )
                for run in batch
            )
            if len(batch) < PER_PAGE:
                break
        return runs

    async def _fetch_statuses(
        self, http: httpx.AsyncClient, ref: PullRequestRef, sha: str
    ) -> tuple[list[CheckResult], int]:
        path = self._repo_path(ref, f"commits/{sha}/status")
        body = await self._get_optional_json(http, ref, path, {"per_page": PER_PAGE})
        statuses = [
            CheckResult(
                name=status.get("context") or "unnamed status",
                source="status",
                status="pending" if status.get("state") == "pending" else "completed",
                conclusion=None if status.get("state") == "pending" else status.get("state"),
                url=status.get("target_url"),
            )
            for status in body.get("statuses", [])
        ]
        return statuses, int(body.get("total_count", len(statuses)))

    async def _get_optional_json(
        self, http: httpx.AsyncClient, ref: PullRequestRef, path: str, params: dict[str, int]
    ) -> JsonObject:
        """GET for evidence the review can go without. Auth and rate limits still fail the request."""
        try:
            body = await self._get_json(http, ref, path, params)
        except (GitHubNotFoundError, GitHubUnavailableError) as error:
            raise _OptionalEvidenceError from error
        if not isinstance(body, dict):
            raise _OptionalEvidenceError
        return body

    async def _get_json(
        self,
        http: httpx.AsyncClient,
        ref: PullRequestRef,
        path: str,
        params: dict[str, int] | None = None,
    ) -> Any:
        try:
            response = await http.get(path, params=params)
        except httpx.TimeoutException as error:
            raise GitHubUnavailableError("GitHub took too long to respond. Try again.") from error
        except httpx.HTTPError as error:
            raise GitHubUnavailableError(
                "Couldn't reach GitHub. Check your connection and try again."
            ) from error
        _raise_for_status(response, ref)
        return response.json()


def _raise_for_status(response: httpx.Response, ref: PullRequestRef) -> None:
    status = response.status_code
    if status < 300:
        return
    if status == 401:
        raise GitHubAuthError(f"GitHub didn't accept your gh sign-in. {SIGN_IN_HINT}")
    rate_limited = status == 429 or (
        status == 403
        and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in response.headers
        )
    )
    if rate_limited:
        raise GitHubRateLimitedError(
            "GitHub's rate limit was reached. Wait a few minutes and try again."
        )
    if status in {403, 404}:
        raise GitHubNotFoundError(
            f"Couldn't find {ref.slug}, or your gh account doesn't have access to it."
        )
    raise GitHubUnavailableError(f"GitHub returned an error ({status}). Try again later.")


def _validated_sha(value: object) -> str:
    if not isinstance(value, str) or not _SHA.match(value):
        raise GitHubUnavailableError("GitHub returned a commit id in an unexpected format.")
    return value


def _changed_file(item: JsonObject) -> ChangedFile:
    return ChangedFile(
        path=item["filename"],
        status=item.get("status", "modified"),
        additions=int(item.get("additions", 0)),
        deletions=int(item.get("deletions", 0)),
        previous_path=item.get("previous_filename"),
        patch=item.get("patch"),
    )


def _file_limitations(files: list[ChangedFile], reported_count: int) -> list[str]:
    limitations: list[str] = []
    if reported_count > len(files):
        limitations.append(
            f"GitHub listed {len(files)} of {reported_count} changed files; "
            "the rest weren't inspected."
        )
    without_patch = [
        file.path for file in files if file.patch is None and (file.additions or file.deletions)
    ]
    if without_patch:
        limitations.append(
            f"No diff was available for {len(without_patch)} file(s) (binary or too large to show)."
        )
    return limitations
