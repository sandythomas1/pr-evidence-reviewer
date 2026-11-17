"""Fetches a repository at a commit for the checkout cache: the git tree, the archive, single blobs.

GET only. Tree, archive and blob calls go to api.github.com with the gh token. GitHub answers the
archive call with a 302 to codeload.github.com whose URL carries a short-lived token. It is followed
only by a second client that sends no Authorization header, and it is never logged or put in an error
(threat T3). Every failure becomes a fixed-message `CheckoutUnavailableError` raised outside the
`except` block, so neither `__cause__` nor `__context__` can carry a URL or a token.
"""

import asyncio
import base64
import binascii
import os
import re
from collections.abc import Awaitable, Callable, Sequence
from pathlib import Path
from typing import Any, NoReturn

import httpx

from pr_evidence_reviewer.checkout.archive import (
    CHUNK_BYTES,
    TOO_LARGE,
    TOO_MANY_FILES,
    EntryKind,
    RepoName,
    RepoTree,
    TreeEntry,
)
from pr_evidence_reviewer.checkout.cache import CheckoutTarget
from pr_evidence_reviewer.errors import CheckoutUnavailableError, RepositoryTooLargeError
from pr_evidence_reviewer.github.client import API_BASE_URL, USER_AGENT, api_headers
from pr_evidence_reviewer.github.token import TokenProvider

CODELOAD_HOST = "codeload.github.com"
DEFAULT_TIMEOUT_SECONDS = 30.0
DEFAULT_DEADLINE_SECONDS = 180.0
REFETCH_CONCURRENCY = 8
MAX_REFETCH = 300
_SHA = re.compile(r"^[0-9a-f]{40}$")
_ENTRY_KINDS: frozenset[str] = frozenset({"blob", "tree", "commit"})
_NOFOLLOW = getattr(os, "O_NOFOLLOW", 0)

UNREACHABLE = "Couldn't download the repository from GitHub. Check your connection and try again."
TIMED_OUT = "Downloading the repository from GitHub took too long."
NOT_FOUND = "GitHub couldn't find the repository at this commit, or your gh account can't read it."
REFUSED = "GitHub didn't allow the repository at this commit to be downloaded. Try again later."
UNEXPECTED = "GitHub answered the repository download in an unexpected way."
UNWRITABLE = "The reviewer couldn't save the repository download."


class GitHubArchiveSource:
    """`ArchiveSource` backed by the GitHub REST API. Reads the gh token once per call."""

    def __init__(
        self,
        token_provider: TokenProvider,
        transport: httpx.AsyncBaseTransport | None = None,
        *,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        deadline_seconds: float = DEFAULT_DEADLINE_SECONDS,
    ) -> None:
        self._tokens = token_provider
        self._transport = transport
        self._timeout = timeout
        self._deadline = deadline_seconds

    async def fetch_tree(self, target: CheckoutTarget) -> RepoTree:
        """The recursive tree at `target.sha`; retried on the fork when the base repo 404s it."""
        _check_sha(target.sha)
        sources = [RepoName(target.owner, target.repo)]
        fork = target.head_repo
        if fork is not None and (fork.owner.lower(), fork.repo.lower()) != (
            target.owner.lower(),
            target.repo.lower(),
        ):
            sources.append(fork)
        token = await asyncio.to_thread(self._tokens)
        async with self._api(token) as http:
            for source in sources:
                response = await _guarded(
                    http.get(_repo_path(source, f"git/trees/{target.sha}"), params={"recursive": 1})
                )
                if response.status_code == 404:
                    continue
                _require_status(response, 200)
                return _parse_tree(source, _json(response))
        raise CheckoutUnavailableError(NOT_FOUND)

    async def download_archive(
        self, source: RepoName, sha: str, destination: Path, max_bytes: int
    ) -> None:
        """Stream the archive at `sha` to `destination` (created new, owner-only) under a byte cap
        and an overall deadline."""
        _check_sha(sha)
        token = await asyncio.to_thread(self._tokens)
        try:
            async with asyncio.timeout(self._deadline):
                location = await self._archive_location(token, source, sha)
                async with self._download_client() as download:
                    await _guarded(_stream_to_file(download, location, destination, max_bytes))
        except TimeoutError:
            pass
        else:
            return
        raise CheckoutUnavailableError(TIMED_OUT) from None

    async def fetch_blobs(
        self,
        source: RepoName,
        shas: Sequence[str],
        max_bytes: int,
        receive: Callable[[str, bytes], Awaitable[None]],
    ) -> None:
        """Fetch up to 300 blobs, 8 at a time, handing each to `receive` as it arrives, so at most
        8 are held in memory (threat T6). A blob GitHub can't serve is left out; auth, rate limit
        and transport failures fail the whole call so nothing half-fetched is cached."""
        wanted = [sha for sha in list(dict.fromkeys(shas))[:MAX_REFETCH] if _SHA.match(sha)]
        if not wanted:
            return
        token = await asyncio.to_thread(self._tokens)
        limit = asyncio.Semaphore(REFETCH_CONCURRENCY)
        async with self._api(token) as http:

            async def fetch(sha: str) -> None:
                async with limit:
                    data = await _fetch_blob(http, source, sha, max_bytes)
                    if data is not None:
                        await receive(sha, data)

            results = await asyncio.gather(*(fetch(sha) for sha in wanted), return_exceptions=True)
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def _archive_location(self, token: str, source: RepoName, sha: str) -> httpx.URL:
        async with self._api(token) as http:
            response = await _guarded(http.get(_repo_path(source, f"tarball/{sha}")))
        _require_status(response, 302)
        return _codeload_url(response.headers.get("location"))

    def _api(self, token: str) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=API_BASE_URL,
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            headers=api_headers(token),
        )

    def _download_client(self) -> httpx.AsyncClient:
        """No Authorization, no redirects, no transfer decoding: the codeload URL is the credential."""
        return httpx.AsyncClient(
            transport=self._transport,
            timeout=self._timeout,
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept-Encoding": "identity"},
        )


async def _guarded[T](call: Awaitable[T]) -> T:
    """Await an HTTP call; any failure becomes a fixed message with no chained exception."""
    try:
        return await call
    except httpx.TimeoutException:
        failure = CheckoutUnavailableError(TIMED_OUT)
    except (httpx.HTTPError, httpx.StreamError, httpx.InvalidURL):
        failure = CheckoutUnavailableError(UNREACHABLE)
    except OSError:
        failure = CheckoutUnavailableError(UNWRITABLE)
    raise failure from None


async def _stream_to_file(
    download: httpx.AsyncClient, url: httpx.URL, destination: Path, max_bytes: int
) -> None:
    async with download.stream("GET", url) as response:
        _require_status(response, 200)
        declared = response.headers.get("content-length", "")
        if declared.isdigit() and int(declared) > max_bytes:
            raise RepositoryTooLargeError(TOO_LARGE)
        written = 0
        with open(destination, "xb", opener=_owner_only) as out:
            async for chunk in response.aiter_raw(CHUNK_BYTES):
                written += len(chunk)
                if written > max_bytes:
                    raise RepositoryTooLargeError(TOO_LARGE)
                out.write(chunk)


async def _fetch_blob(
    http: httpx.AsyncClient, source: RepoName, sha: str, max_bytes: int
) -> bytes | None:
    response = await _guarded(http.get(_repo_path(source, f"git/blobs/{sha}")))
    if response.status_code in {404, 409, 422}:
        return None
    _require_status(response, 200)
    body = _json(response)
    content = body.get("content") if isinstance(body, dict) else None
    size = body.get("size") if isinstance(body, dict) else None
    if not isinstance(content, str) or body.get("encoding") != "base64":
        return None
    if not isinstance(size, int) or size > max_bytes:
        return None
    try:
        return base64.b64decode(content)
    except (binascii.Error, ValueError):
        return None


def _owner_only(path: str, flags: int) -> int:
    return os.open(path, flags | _NOFOLLOW, 0o600)


def _repo_path(source: RepoName, suffix: str) -> str:
    return f"/repos/{source.owner}/{source.repo}/{suffix}"


def _check_sha(sha: str) -> None:
    if not _SHA.match(sha):
        raise CheckoutUnavailableError(UNEXPECTED)


def _require_status(response: httpx.Response, expected: int) -> None:
    status = response.status_code
    if status == expected:
        return
    if status in {401, 403, 429}:
        _fail(REFUSED)
    if status == 404:
        _fail(NOT_FOUND)
    _fail(UNEXPECTED)


def _fail(message: str) -> NoReturn:
    raise CheckoutUnavailableError(message)


def _json(response: httpx.Response) -> Any:
    try:
        return response.json()
    except ValueError:
        pass
    raise CheckoutUnavailableError(UNEXPECTED)


def _codeload_url(location: str | None) -> httpx.URL:
    """Accept only `https://codeload.github.com/...` with no credentials or port in the URL."""
    url: httpx.URL | None = None
    if location:
        try:
            url = httpx.URL(location)
        except httpx.InvalidURL:
            url = None
    if (
        url is None
        or url.scheme != "https"
        or url.host != CODELOAD_HOST
        or url.port is not None
        or url.userinfo
    ):
        raise CheckoutUnavailableError(UNEXPECTED)
    return url


def _parse_tree(source: RepoName, body: Any) -> RepoTree:
    if not isinstance(body, dict):
        _fail(UNEXPECTED)
    if body.get("truncated") is True:
        raise RepositoryTooLargeError(TOO_MANY_FILES)
    tree_sha = body.get("sha")
    items = body.get("tree")
    if not isinstance(tree_sha, str) or not _SHA.match(tree_sha) or not isinstance(items, list):
        _fail(UNEXPECTED)
    return RepoTree(source=source, sha=tree_sha, entries=tuple(_tree_entry(i) for i in items))


def _tree_entry(item: Any) -> TreeEntry:
    if not isinstance(item, dict):
        _fail(UNEXPECTED)
    path, mode, kind, sha = item.get("path"), item.get("mode"), item.get("type"), item.get("sha")
    size = item.get("size")
    if (
        not isinstance(path, str)
        or not isinstance(mode, str)
        or kind not in _ENTRY_KINDS
        or not isinstance(sha, str)
        or not _SHA.match(sha)
    ):
        _fail(UNEXPECTED)
    entry_kind: EntryKind = kind
    if kind == "blob" and (not isinstance(size, int) or isinstance(size, bool) or size < 0):
        _fail(UNEXPECTED)
    return TreeEntry(
        path=path,
        mode=mode,
        kind=entry_kind,
        sha=sha,
        size=size if kind == "blob" else None,
    )
