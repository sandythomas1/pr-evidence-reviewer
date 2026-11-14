from pr_evidence_reviewer import __version__
from pr_evidence_reviewer.api.app import create_app
from tests.client import local_client


def test_health_reports_ok_and_version() -> None:
    client = local_client(create_app())

    response = client.get("/api/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}
