from pr_evidence_reviewer.api.app import create_app
from pr_evidence_reviewer.llm.claude_code import ClaudeCodeProvider
from tests.client import local_client
from tests.llm.fakes import FakeProvider


def test_lists_each_provider_with_its_availability() -> None:
    app = create_app(
        providers=[ClaudeCodeProvider(which=lambda _: None), FakeProvider(available=True)]
    )

    response = local_client(app).get("/api/providers")

    assert response.status_code == 200
    claude, fake = response.json()
    assert claude["id"] == "claude_code"
    assert claude["available"] is False
    assert "isn't installed" in claude["detail"]
    assert fake == {"id": "fake", "name": "Fake model", "available": True, "detail": "Ready."}
