from fastapi.testclient import TestClient

from pr_evidence_reviewer.api.app import create_app
from pr_evidence_reviewer.errors import ProviderTimeoutError
from pr_evidence_reviewer.github.client import GitHubClient
from tests.client import local_client
from tests.fakes import HEAD_SHA, TOKEN, FakeGitHub
from tests.llm.fakes import FakeProvider

PR_URL = "https://github.com/acme/shop/pull/7"
REPLY = {
    "claims": [
        {
            "quote": "Refunds are idempotent now.",
            "statement": "Repeating a refund request doesn't refund twice.",
            "kind": "behavior",
        },
        {"quote": "Covered by tests.", "statement": "Tests cover the change.", "kind": "test"},
    ]
}


def make_client(fake: FakeGitHub, *providers: FakeProvider) -> TestClient:
    github = GitHubClient(lambda: TOKEN, transport=fake.transport())
    app = create_app(snapshot_source=github, providers=list(providers))
    return local_client(app, raise_server_exceptions=False)


def test_returns_the_snapshot_and_its_claims() -> None:
    client = make_client(FakeGitHub(), FakeProvider(REPLY))

    response = client.post("/api/claims", json={"pr_url": PR_URL})

    assert response.status_code == 200
    body = response.json()
    assert body["snapshot"]["head"]["sha"] == HEAD_SHA
    assert [c["id"] for c in body["claims"]["claims"]] == ["c1", "c2"]
    assert body["claims"]["claims"][0] == {
        "id": "c1",
        "quote": "Refunds are idempotent now.",
        "statement": "Repeating a refund request doesn't refund twice.",
        "kind": "behavior",
    }
    assert body["claims"]["dropped_count"] == 0
    assert body["claims"]["provider"] == "fake"


def test_uses_the_first_provider_that_is_ready() -> None:
    missing = FakeProvider(available=False)
    ready = FakeProvider(REPLY)
    client = make_client(FakeGitHub(), missing, ready)

    response = client.post("/api/claims", json={"pr_url": PR_URL})

    assert response.status_code == 200
    assert missing.requests == []
    assert len(ready.requests) == 1


def test_no_ready_provider_is_a_503_that_says_why() -> None:
    fake = FakeGitHub()
    client = make_client(fake, FakeProvider(available=False))

    response = client.post("/api/claims", json={"pr_url": PR_URL})

    assert response.status_code == 503
    error = response.json()["error"]
    assert error["code"] == "llm_unavailable"
    assert error["message"] == "No model provider is ready. Not installed."
    assert fake.requests == []


def test_an_empty_description_skips_the_model() -> None:
    fake = FakeGitHub()
    fake.pull["body"] = None
    provider = FakeProvider()
    client = make_client(fake, provider)

    response = client.post("/api/claims", json={"pr_url": PR_URL})

    assert response.status_code == 200
    claims = response.json()["claims"]
    assert claims["claims"] == []
    assert "no description" in claims["notes"][0]
    assert provider.requests == []


def test_provider_errors_use_the_error_envelope() -> None:
    client = make_client(FakeGitHub(), FakeProvider(ProviderTimeoutError("Too slow. Try again.")))

    response = client.post("/api/claims", json={"pr_url": PR_URL})

    assert response.status_code == 504
    assert response.json() == {"error": {"code": "llm_timeout", "message": "Too slow. Try again."}}


def test_bad_links_never_reach_github_or_the_model() -> None:
    fake = FakeGitHub()
    provider = FakeProvider()
    client = make_client(fake, provider)

    response = client.post("/api/claims", json={"pr_url": "https://example.com/acme/shop/pull/7"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "invalid_pr_url"
    assert fake.requests == []
    assert provider.requests == []


def test_uses_the_chosen_provider_even_when_an_earlier_one_is_ready() -> None:
    first = FakeProvider(REPLY, provider_id="first")
    chosen = FakeProvider(REPLY, provider_id="chosen")
    client = make_client(FakeGitHub(), first, chosen)

    response = client.post("/api/claims", json={"pr_url": PR_URL, "provider": "chosen"})

    assert response.status_code == 200
    assert response.json()["claims"]["provider"] == "chosen"
    assert first.requests == []
    assert len(chosen.requests) == 1


def test_a_null_provider_means_the_first_ready_one() -> None:
    ready = FakeProvider(REPLY, provider_id="ready")
    client = make_client(FakeGitHub(), FakeProvider(available=False), ready)

    response = client.post("/api/claims", json={"pr_url": PR_URL, "provider": None})

    assert response.status_code == 200
    assert response.json()["claims"]["provider"] == "ready"


def test_an_unknown_provider_is_a_422_before_github_is_called() -> None:
    fake = FakeGitHub()
    client = make_client(fake, FakeProvider(REPLY, name="Claude Code"))

    response = client.post("/api/claims", json={"pr_url": PR_URL, "provider": "nope"})

    assert response.status_code == 422
    assert response.json() == {
        "error": {
            "code": "unknown_provider",
            "message": "That model provider isn't offered here. Choose one of: Claude Code.",
        }
    }
    assert fake.requests == []


def test_a_chosen_provider_that_isnt_ready_is_a_503_with_its_reason() -> None:
    fake = FakeGitHub()
    missing = FakeProvider(available=False, provider_id="missing")
    client = make_client(fake, FakeProvider(REPLY), missing)

    response = client.post("/api/claims", json={"pr_url": PR_URL, "provider": "missing"})

    assert response.status_code == 503
    assert response.json() == {"error": {"code": "llm_unavailable", "message": "Not installed."}}
    assert fake.requests == []


def test_rejects_a_provider_that_isnt_a_short_string() -> None:
    client = make_client(FakeGitHub(), FakeProvider(REPLY))

    for provider in ("", "x" * 65, 7):
        response = client.post("/api/claims", json={"pr_url": PR_URL, "provider": provider})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "invalid_request"
