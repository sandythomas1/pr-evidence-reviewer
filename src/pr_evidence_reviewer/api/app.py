"""FastAPI application factory."""

from fastapi import APIRouter, FastAPI
from fastapi.middleware.trustedhost import TrustedHostMiddleware
from pydantic import BaseModel

from pr_evidence_reviewer import __version__
from pr_evidence_reviewer.api import claims, snapshots
from pr_evidence_reviewer.api import providers as providers_api
from pr_evidence_reviewer.api.errors import install_error_handlers
from pr_evidence_reviewer.api.web import install_security_headers, mount_web_ui
from pr_evidence_reviewer.config import Settings
from pr_evidence_reviewer.github.client import GitHubClient
from pr_evidence_reviewer.github.token import GhCliTokenProvider
from pr_evidence_reviewer.llm.base import LLMProvider
from pr_evidence_reviewer.llm.claude_code import ClaudeCodeProvider


class HealthResponse(BaseModel):
    status: str
    version: str


def _health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    return router


def create_app(
    settings: Settings | None = None,
    snapshot_source: snapshots.SnapshotSource | None = None,
    providers: list[LLMProvider] | None = None,
) -> FastAPI:
    """Build the app. Tests pass their own settings and dependencies."""
    settings = settings or Settings()
    app = FastAPI(title="PR Evidence Reviewer", version=__version__)
    app.state.settings = settings
    app.state.snapshot_source = snapshot_source or GitHubClient(GhCliTokenProvider())
    app.state.providers = providers if providers is not None else [ClaudeCodeProvider()]
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=settings.allowed_hosts)
    install_security_headers(app)
    install_error_handlers(app)
    app.include_router(_health_router())
    app.include_router(snapshots.router)
    app.include_router(claims.router)
    app.include_router(providers_api.router)
    mount_web_ui(app, settings.web_dist)
    return app
