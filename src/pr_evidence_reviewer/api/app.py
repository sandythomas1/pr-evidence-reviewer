"""FastAPI application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import APIRouter, FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from pr_evidence_reviewer import __version__
from pr_evidence_reviewer.api import claims, snapshots, verdicts
from pr_evidence_reviewer.api import providers as providers_api
from pr_evidence_reviewer.api.errors import install_error_handlers
from pr_evidence_reviewer.api.web import install_security_headers, mount_web_ui
from pr_evidence_reviewer.checkout.cache import ArchiveSource, CheckoutCache
from pr_evidence_reviewer.config import Settings
from pr_evidence_reviewer.evidence.store import PacketStore
from pr_evidence_reviewer.github.archive import GitHubArchiveSource
from pr_evidence_reviewer.github.client import GitHubClient
from pr_evidence_reviewer.github.token import GhCliTokenProvider
from pr_evidence_reviewer.llm.base import LLMProvider
from pr_evidence_reviewer.llm.claude_code import ClaudeCodeProvider
from pr_evidence_reviewer.llm.codex import CodexProvider
from pr_evidence_reviewer.llm.gemini import GeminiCliProvider


class HealthResponse(BaseModel):
    status: str
    version: str


def _health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    return router


QUIET_HTTP_LOGGERS = ("httpx", "httpcore")


def quiet_http_loggers() -> None:
    """httpx logs every request URL at INFO, and a repository download URL carries a short-lived
    token (threat T3). Keep the HTTP client loggers at WARNING whatever the root level is."""
    for name in QUIET_HTTP_LOGGERS:
        logging.getLogger(name).setLevel(logging.WARNING)


def create_app(
    settings: Settings | None = None,
    snapshot_source: snapshots.SnapshotSource | None = None,
    providers: list[LLMProvider] | None = None,
    archive_source: ArchiveSource | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own settings and dependencies."""
    quiet_http_loggers()
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # The cache's asyncio locks belong to the running loop, so it's built here, not above.
        packets = PacketStore(settings.packets_dir, ttl=settings.retention_ttl)
        app.state.checkouts = CheckoutCache(
            settings.cache_dir,
            archive_source or GitHubArchiveSource(GhCliTokenProvider()),
            max_bytes=settings.checkout_cache_max_bytes,
            ttl=settings.retention_ttl,
            also_retain=packets.remove_expired_if_due,
        )
        app.state.packets = packets
        yield

    app = FastAPI(title="PR Evidence Reviewer", version=__version__, lifespan=lifespan)
    app.state.settings = settings
    app.state.snapshot_source = snapshot_source or GitHubClient(GhCliTokenProvider())
    app.state.providers = (
        providers
        if providers is not None
        else [ClaudeCodeProvider(), CodexProvider(), GeminiCliProvider()]
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    install_security_headers(app)
    install_error_handlers(app)
    app.include_router(_health_router())
    app.include_router(snapshots.router)
    app.include_router(claims.router)
    app.include_router(verdicts.router)
    app.include_router(providers_api.router)
    mount_web_ui(app, settings.web_dist)
    return app
