"""Serves the built React UI and sets browser security headers."""

from collections.abc import Awaitable, Callable
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles

SECURITY_HEADERS = {
    "Content-Security-Policy": (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
    ),
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}

UI_NOT_BUILT = """<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>PR Evidence Reviewer</title></head>
<body><main><h1>The web UI hasn't been built yet</h1>
<p>Run <code>npm --prefix web ci</code> and <code>npm --prefix web run build</code>,
then restart <code>prev serve</code>. The API is available at <code>/api</code>.</p></main></body></html>
"""


def install_security_headers(app: FastAPI) -> None:
    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        for name, value in SECURITY_HEADERS.items():
            response.headers.setdefault(name, value)
        return response


def mount_web_ui(app: FastAPI, web_dist: Path) -> None:
    """Serve web/dist at `/`. Call after the API routers so `/api/*` keeps priority."""
    if (web_dist / "index.html").is_file():
        app.mount("/", StaticFiles(directory=web_dist, html=True), name="web")
        return

    @app.get("/", response_class=HTMLResponse, include_in_schema=False)
    def ui_not_built() -> str:
        return UI_NOT_BUILT
