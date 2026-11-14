import pytest
from fastapi.testclient import TestClient

from pr_evidence_reviewer.api.app import create_app
from pr_evidence_reviewer.errors import GitHubAuthError
from pr_evidence_reviewer.github.client import GitHubClient
from tests.fakes import HEAD_SHA, TOKEN, FakeGitHub


@pytest.fixture
def fake() -> FakeGitHub:
    return FakeGitHub()


@pytest.fixture
def client(fake: FakeGitHub) -> TestClient:
    github = GitHubClient(lambda: TOKEN, transport=fake.transport())
    return TestClient(create_app(snapshot_source=github), raise_server_exceptions=False)


def test_returns_the_snapshot_for_a_pr_link(client: TestClient) -> None:
    response = client.post("/api/snapshots", json={"pr_url": "https://github.com/acme/shop/pull/7"})

    assert response.status_code == 200
    body = response.json()
    assert body["head"]["sha"] == HEAD_SHA
    assert body["ref"] == {"owner": "acme", "repo": "shop", "number": 7}
    assert len(body["files"]) == 2


def test_bad_links_never_reach_github(client: TestClient, fake: FakeGitHub) -> None:
    response = client.post("/api/snapshots", json={"pr_url": "https://gitlab.com/acme/shop/pull/7"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_pr_url"
    assert fake.requests == []


def test_missing_body_uses_the_error_envelope(client: TestClient) -> None:
    response = client.post("/api/snapshots", json={})

    assert response.status_code == 422
    assert response.json() == {
        "error": {"code": "invalid_request", "message": "Check these fields: pr_url."}
    }


def test_signed_out_gh_is_a_401_with_a_fix(fake: FakeGitHub) -> None:
    def signed_out() -> str:
        raise GitHubAuthError("You're not signed in to GitHub in gh. Run `gh auth login`.")

    app = create_app(snapshot_source=GitHubClient(signed_out, transport=fake.transport()))

    response = TestClient(app).post(
        "/api/snapshots", json={"pr_url": "https://github.com/acme/shop/pull/7"}
    )

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "github_auth"
    assert "gh auth login" in response.json()["error"]["message"]


def test_unexpected_errors_hide_internals(fake: FakeGitHub) -> None:
    class Broken:
        async def fetch_snapshot(self, _: object) -> object:
            raise RuntimeError(f"secret detail {TOKEN}")

    client = TestClient(create_app(snapshot_source=Broken()), raise_server_exceptions=False)  # type: ignore[arg-type]

    response = client.post("/api/snapshots", json={"pr_url": "https://github.com/acme/shop/pull/7"})

    assert response.status_code == 500
    assert TOKEN not in response.text
    assert response.json()["error"]["code"] == "internal_error"
