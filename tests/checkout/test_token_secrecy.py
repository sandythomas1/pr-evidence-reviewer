"""T3: the gh token and the codeload URL token never reach logs, errors or the cache."""

import logging
from collections.abc import Callable
from pathlib import Path

import httpx
import pytest

from pr_evidence_reviewer.api.app import QUIET_HTTP_LOGGERS, create_app
from pr_evidence_reviewer.checkout.archive import git_blob_sha
from pr_evidence_reviewer.checkout.cache import CheckoutCache
from pr_evidence_reviewer.errors import ReviewerError
from pr_evidence_reviewer.github.archive import GitHubArchiveSource
from tests.checkout.fakes import HEAD_SHA, TARGET, make_archive, snapshot_tree
from tests.github.fake_archive import FakeGitHubArchive

pytestmark = pytest.mark.anyio

TOKEN_SENTINEL = "gho_SENTINELTOKEN7f3a9c"
URL_SENTINEL = "SENTINELCODELOAD41b2"
SENTINELS = (TOKEN_SENTINEL, URL_SENTINEL)
LOCATION = f"https://codeload.github.com/acme/shop/legacy.tar.gz/{HEAD_SHA}?token={URL_SENTINEL}"
FILES = {"a.py": b"a = 1\n", "b.py": b"b = 2\n"}


def leaky_failure(request: httpx.Request) -> httpx.Response:
    """A transport error whose message carries the URL and the Authorization header."""
    detail = f"{request.url} {request.headers.get('authorization', '')}"
    raise httpx.ConnectError(f"boom {detail}", request=request)


def fail_trees(fake: FakeGitHubArchive) -> None:
    fake.routes[f"/repos/acme/shop/git/trees/{HEAD_SHA}"] = leaky_failure


def fail_tarball(fake: FakeGitHubArchive) -> None:
    fake.routes[f"/repos/acme/shop/tarball/{HEAD_SHA}"] = leaky_failure


def fail_codeload(fake: FakeGitHubArchive) -> None:
    fake.codeload = leaky_failure


def fail_blobs(fake: FakeGitHubArchive) -> None:
    fake.archive = make_archive([])  # every file must be refetched
    for data in FILES.values():
        fake.routes[f"/repos/acme/shop/git/blobs/{git_blob_sha(data)}"] = leaky_failure


def chain(error: BaseException) -> list[BaseException]:
    seen: list[BaseException] = []
    current: BaseException | None = error
    while current is not None and current not in seen:
        seen.append(current)
        current = current.__cause__ or current.__context__
    return seen


@pytest.fixture
def debug_logs(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    create_app()  # pins the HTTP client loggers, as the running app does
    caplog.set_level(logging.DEBUG)
    return caplog


def test_http_client_loggers_are_pinned_to_warning() -> None:
    for name in QUIET_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.DEBUG)

    create_app()

    for name in QUIET_HTTP_LOGGERS:
        assert logging.getLogger(name).level == logging.WARNING


@pytest.mark.parametrize("break_step", [fail_trees, fail_tarball, fail_codeload, fail_blobs])
async def test_no_sentinel_escapes_a_failed_checkout(
    tmp_path: Path,
    debug_logs: pytest.LogCaptureFixture,
    break_step: Callable[[FakeGitHubArchive], None],
) -> None:
    fake = FakeGitHubArchive(FILES)
    fake.location = LOCATION
    break_step(fake)
    source = GitHubArchiveSource(lambda: TOKEN_SENTINEL, transport=fake.transport())

    with pytest.raises(ReviewerError) as raised:
        async with CheckoutCache(tmp_path / "cache", source).lease(TARGET):
            pass

    assert raised.value.__cause__ is None
    assert raised.value.__context__ is None
    for error in chain(raised.value):
        for sentinel in SENTINELS:
            assert sentinel not in str(error)
            assert sentinel not in repr(error)
    assert_absent_everywhere(tmp_path, debug_logs)


async def test_no_sentinel_is_stored_or_logged_by_a_good_checkout(
    tmp_path: Path, debug_logs: pytest.LogCaptureFixture
) -> None:
    fake = FakeGitHubArchive(FILES)
    fake.location = LOCATION
    source = GitHubArchiveSource(lambda: TOKEN_SENTINEL, transport=fake.transport())

    async with CheckoutCache(tmp_path / "cache", source).lease(TARGET) as checkout:
        assert checkout.manifest.files

    assert any(r.url.host == "codeload.github.com" for r in fake.requests)
    assert_absent_everywhere(tmp_path, debug_logs)


def assert_absent_everywhere(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    logged = "\n".join(f"{r.name} {r.getMessage()} {r.__dict__}" for r in caplog.records)
    stored = b"\n".join(snapshot_tree(tmp_path).values())
    for sentinel in SENTINELS:
        assert sentinel not in logged
        assert sentinel.encode() not in stored
