from pathlib import Path

import pytest

from pr_evidence_reviewer.api.app import create_app
from pr_evidence_reviewer.config import Settings
from tests.client import local_client


@pytest.fixture
def built_ui(tmp_path: Path) -> Path:
    (tmp_path / "index.html").write_text("<!doctype html><title>UI</title><div id=root></div>")
    (tmp_path / "assets").mkdir()
    (tmp_path / "assets" / "app.js").write_text("console.log('ui')")
    return tmp_path


def test_serves_the_built_ui_at_root(built_ui: Path) -> None:
    client = local_client(create_app(Settings(web_dist=built_ui)))

    assert "<div id=root>" in client.get("/").text
    assert client.get("/assets/app.js").status_code == 200


def test_api_routes_win_over_the_ui(built_ui: Path) -> None:
    client = local_client(create_app(Settings(web_dist=built_ui)))

    assert client.get("/api/health").json()["status"] == "ok"


def test_explains_how_to_build_a_missing_ui(tmp_path: Path) -> None:
    client = local_client(create_app(Settings(web_dist=tmp_path / "missing")))

    response = client.get("/")

    assert response.status_code == 200
    assert "npm --prefix web run build" in response.text


def test_sets_browser_security_headers(built_ui: Path) -> None:
    response = local_client(create_app(Settings(web_dist=built_ui))).get("/")

    assert "default-src 'self'" in response.headers["content-security-policy"]
    assert response.headers["x-content-type-options"] == "nosniff"
    assert response.headers["x-frame-options"] == "DENY"


@pytest.mark.parametrize("host", ["127.0.0.1:8765", "localhost:8765", "[::1]:8765"])
def test_answers_loopback_host_headers(host: str) -> None:
    client = local_client(create_app())

    assert client.get("/api/health", headers={"host": host}).status_code == 200


def test_rejects_other_host_headers_to_block_dns_rebinding() -> None:
    client = local_client(create_app())

    response = client.get("/api/health", headers={"host": "attacker.example:8765"})

    assert response.status_code == 400


def test_allows_an_explicitly_bound_host() -> None:
    client = local_client(create_app(Settings(host="192.168.1.20")))

    assert client.get("/api/health", headers={"host": "192.168.1.20:8765"}).status_code == 200
