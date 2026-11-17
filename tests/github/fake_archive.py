"""A MockTransport GitHub that serves trees, tarball redirects, codeload archives and blobs."""

import base64
from collections.abc import AsyncIterator, Callable, Mapping
from typing import Any

import httpx

from pr_evidence_reviewer.checkout.archive import git_blob_sha
from tests.checkout.fakes import HEAD_SHA, TREE_SHA, make_archive, regular

CODELOAD_URL = (
    f"https://codeload.github.com/acme/shop/legacy.tar.gz/{HEAD_SHA}?token=CODELOADSECRET"
)

Handler = Callable[[httpx.Request], httpx.Response]


def streamed(status: int, data: bytes, chunk: int = 1024) -> httpx.Response:
    """A response whose body arrives as a stream, like a real download (a `content=` body is
    preloaded, and `aiter_raw` refuses a preloaded body)."""

    async def body() -> AsyncIterator[bytes]:
        for start in range(0, len(data), chunk):
            yield data[start : start + chunk]

    return httpx.Response(status, content=body())


class FakeGitHubArchive:
    def __init__(self, files: Mapping[str, bytes], *, repo: str = "/repos/acme/shop") -> None:
        self.repo = repo
        self.files = dict(files)
        self.archive = make_archive(regular(files))
        self.location = CODELOAD_URL
        self.truncated = False
        self.requests: list[httpx.Request] = []
        self.routes: dict[str, Handler] = {}
        self.codeload: Handler = lambda _: streamed(200, self.archive)

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self._handle)

    def tree_body(self) -> dict[str, Any]:
        items = [
            {"path": p, "mode": "100644", "type": "blob", "sha": git_blob_sha(d), "size": len(d)}
            for p, d in self.files.items()
        ]
        return {"sha": TREE_SHA, "tree": items, "truncated": self.truncated}

    def _handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.url.host == "codeload.github.com":
            return self.codeload(request)
        if path in self.routes:
            return self.routes[path](request)
        if path == f"{self.repo}/git/trees/{HEAD_SHA}":
            return httpx.Response(200, json=self.tree_body())
        if path == f"{self.repo}/tarball/{HEAD_SHA}":
            return httpx.Response(302, headers={"location": self.location})
        if path.startswith(f"{self.repo}/git/blobs/"):
            return self._blob(path.rsplit("/", 1)[1])
        return httpx.Response(404, json={"message": "Not Found"})

    def _blob(self, sha: str) -> httpx.Response:
        for data in self.files.values():
            if git_blob_sha(data) == sha:
                content = base64.b64encode(data).decode("ascii")
                return httpx.Response(
                    200,
                    json={"sha": sha, "size": len(data), "encoding": "base64", "content": content},
                )
        return httpx.Response(404, json={"message": "Not Found"})
