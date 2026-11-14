import httpx
import pytest

from pr_evidence_reviewer.domain.pull_request import PullRequestRef
from pr_evidence_reviewer.errors import (
    GitHubAuthError,
    GitHubNotFoundError,
    GitHubRateLimitedError,
    GitHubUnavailableError,
)
from pr_evidence_reviewer.github.client import GitHubClient
from tests.fakes import BASE_SHA, HEAD_SHA, TOKEN, FakeGitHub, file_payload

pytestmark = pytest.mark.anyio

REF = PullRequestRef(owner="acme", repo="shop", number=7)


def client_for(fake: FakeGitHub) -> GitHubClient:
    return GitHubClient(lambda: TOKEN, transport=fake.transport())


async def test_snapshot_is_pinned_to_the_head_commit() -> None:
    fake = FakeGitHub()

    snapshot = await client_for(fake).fetch_snapshot(REF)

    assert snapshot.title == "Make refunds idempotent"
    assert snapshot.author == "octocat"
    assert snapshot.head.sha == HEAD_SHA
    assert snapshot.base.sha == BASE_SHA
    assert [file.path for file in snapshot.files] == ["src/refunds.py", "tests/test_refunds.py"]
    assert snapshot.scope_limitations == []


async def test_ci_results_come_from_the_head_sha() -> None:
    fake = FakeGitHub()

    snapshot = await client_for(fake).fetch_snapshot(REF)

    assert [(c.name, c.source, c.conclusion) for c in snapshot.checks] == [
        ("lint", "status", "success"),
        ("test", "check_run", "success"),
    ]
    ci_paths = [r.url.path for r in fake.requests if "/commits/" in r.url.path]
    assert ci_paths
    assert all(HEAD_SHA in path for path in ci_paths)


async def test_only_reads_github_with_the_token_and_get_requests() -> None:
    fake = FakeGitHub()

    await client_for(fake).fetch_snapshot(REF)

    assert {r.method for r in fake.requests} == {"GET"}
    assert {r.url.host for r in fake.requests} == {"api.github.com"}
    assert all(r.headers["authorization"] == f"Bearer {TOKEN}" for r in fake.requests)


async def test_follows_file_pages_until_a_short_page() -> None:
    fake = FakeGitHub()
    fake.files = [file_payload(f"src/file_{i}.py") for i in range(250)]
    fake.pull["changed_files"] = 250

    snapshot = await client_for(fake).fetch_snapshot(REF)

    assert len(snapshot.files) == 250
    assert snapshot.scope_limitations == []


async def test_records_a_gap_when_github_lists_fewer_files_than_changed() -> None:
    fake = FakeGitHub()
    fake.pull["changed_files"] = 3500

    snapshot = await client_for(fake).fetch_snapshot(REF)

    assert any("2 of 3500 changed files" in gap for gap in snapshot.scope_limitations)


async def test_records_a_gap_for_files_without_a_diff() -> None:
    fake = FakeGitHub()
    fake.files = [
        file_payload("logo.png", patch=None),
        file_payload("empty.txt", patch=None, additions=0, deletions=0),
    ]

    snapshot = await client_for(fake).fetch_snapshot(REF)

    assert any("No diff was available for 1 file" in gap for gap in snapshot.scope_limitations)


async def test_missing_ci_is_a_gap_not_a_failure() -> None:
    fake = FakeGitHub()
    fake.routes[f"/repos/acme/shop/commits/{HEAD_SHA}/check-runs"] = lambda _: httpx.Response(404)
    fake.routes[f"/repos/acme/shop/commits/{HEAD_SHA}/status"] = lambda _: httpx.Response(500)

    snapshot = await client_for(fake).fetch_snapshot(REF)

    assert snapshot.checks == []
    assert len(snapshot.scope_limitations) == 2


async def test_records_a_gap_when_statuses_are_truncated() -> None:
    fake = FakeGitHub()
    fake.routes[f"/repos/acme/shop/commits/{HEAD_SHA}/status"] = lambda _: httpx.Response(
        200, json={"total_count": 150, "statuses": []}
    )

    snapshot = await client_for(fake).fetch_snapshot(REF)

    assert any("0 of 150 commit statuses" in gap for gap in snapshot.scope_limitations)


async def test_pending_status_has_no_conclusion() -> None:
    fake = FakeGitHub()
    fake.statuses = [{"context": "deploy", "state": "pending"}]

    snapshot = await client_for(fake).fetch_snapshot(REF)

    status = next(c for c in snapshot.checks if c.source == "status")
    assert (status.status, status.conclusion) == ("pending", None)


@pytest.mark.parametrize(
    ("response", "error"),
    [
        (httpx.Response(401), GitHubAuthError),
        (httpx.Response(404), GitHubNotFoundError),
        (httpx.Response(403), GitHubNotFoundError),
        (httpx.Response(403, headers={"x-ratelimit-remaining": "0"}), GitHubRateLimitedError),
        (httpx.Response(429), GitHubRateLimitedError),
        (httpx.Response(503), GitHubUnavailableError),
        (httpx.Response(301, headers={"location": "https://evil.example"}), GitHubUnavailableError),
    ],
)
async def test_maps_github_errors_on_the_pull_request(
    response: httpx.Response, error: type[Exception]
) -> None:
    fake = FakeGitHub()
    fake.routes["/repos/acme/shop/pulls/7"] = lambda _: response

    with pytest.raises(error):
        await client_for(fake).fetch_snapshot(REF)


async def test_network_failures_are_reported_as_unavailable() -> None:
    def fail(_: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route")

    client = GitHubClient(lambda: TOKEN, transport=httpx.MockTransport(fail))

    with pytest.raises(GitHubUnavailableError, match="Couldn't reach GitHub"):
        await client.fetch_snapshot(REF)


@pytest.mark.parametrize("bad_sha", ["../../evil", "A" * 40, "", None])
async def test_rejects_malformed_commit_ids(bad_sha: object) -> None:
    fake = FakeGitHub()
    fake.pull["head"] = {"ref": "x", "sha": bad_sha}

    with pytest.raises(GitHubUnavailableError):
        await client_for(fake).fetch_snapshot(REF)


async def test_malformed_payloads_are_reported_not_crashed() -> None:
    fake = FakeGitHub()
    del fake.pull["base"]

    with pytest.raises(GitHubUnavailableError, match="unexpected format"):
        await client_for(fake).fetch_snapshot(REF)


async def test_errors_never_contain_the_token() -> None:
    fake = FakeGitHub()
    fake.routes["/repos/acme/shop/pulls/7"] = lambda _: httpx.Response(401)

    with pytest.raises(GitHubAuthError) as error:
        await client_for(fake).fetch_snapshot(REF)

    assert TOKEN not in str(error.value)
