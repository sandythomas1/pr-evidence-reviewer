"""Test doubles shared across test modules."""

from collections.abc import Callable
from typing import Any

import httpx

HEAD_SHA = "a" * 40
BASE_SHA = "b" * 40
TOKEN = "gho_testtoken000000000000000000000000"

Handler = Callable[[httpx.Request], httpx.Response]


def pull_payload(**overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "title": "Make refunds idempotent",
        "body": "Refunds are idempotent now. Covered by tests.",
        "user": {"login": "octocat"},
        "state": "open",
        "draft": False,
        "html_url": "https://github.com/acme/shop/pull/7",
        "base": {"ref": "dev", "sha": BASE_SHA},
        "head": {"ref": "feature/refunds", "sha": HEAD_SHA},
        "changed_files": 2,
    }
    payload.update(overrides)
    return payload


def file_payload(
    name: str, patch: str | None = "@@ -1 +1 @@\n-a\n+b", **extra: Any
) -> dict[str, Any]:
    item: dict[str, Any] = {"filename": name, "status": "modified", "additions": 1, "deletions": 1}
    if patch is not None:
        item["patch"] = patch
    item.update(extra)
    return item


class FakeGitHub:
    """Routes GitHub REST paths to canned responses and records every request."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, Handler] = {}
        self.pull = pull_payload()
        self.files: list[dict[str, Any]] = [
            file_payload("src/refunds.py"),
            file_payload("tests/test_refunds.py"),
        ]
        self.check_runs: list[dict[str, Any]] = [
            {
                "name": "test",
                "status": "completed",
                "conclusion": "success",
                "html_url": "https://ci/1",
            }
        ]
        self.statuses: list[dict[str, Any]] = [
            {"context": "lint", "state": "success", "target_url": "https://ci/2"}
        ]

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if path in self.routes:
            return self.routes[path](request)
        repo = "/repos/acme/shop"
        page = int(request.url.params.get("page", "1"))
        per_page = int(request.url.params.get("per_page", "30"))
        if path == f"{repo}/pulls/7":
            return httpx.Response(200, json=self.pull)
        if path == f"{repo}/pulls/7/files":
            start = (page - 1) * per_page
            return httpx.Response(200, json=self.files[start : start + per_page])
        if path == f"{repo}/commits/{HEAD_SHA}/check-runs":
            start = (page - 1) * per_page
            runs = self.check_runs[start : start + per_page]
            return httpx.Response(
                200, json={"total_count": len(self.check_runs), "check_runs": runs}
            )
        if path == f"{repo}/commits/{HEAD_SHA}/status":
            return httpx.Response(
                200, json={"total_count": len(self.statuses), "statuses": self.statuses}
            )
        return httpx.Response(404, json={"message": "Not Found"})
