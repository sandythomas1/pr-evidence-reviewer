"""FastAPI application factory."""

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from pr_evidence_reviewer import __version__
from pr_evidence_reviewer.api import snapshots
from pr_evidence_reviewer.api.errors import install_error_handlers
from pr_evidence_reviewer.config import Settings
from pr_evidence_reviewer.github.client import GitHubClient
from pr_evidence_reviewer.github.token import GhCliTokenProvider


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
) -> FastAPI:
    """Build the app. Tests pass their own settings and dependencies."""
    app = FastAPI(title="PR Evidence Reviewer", version=__version__)
    app.state.settings = settings or Settings()
    app.state.snapshot_source = snapshot_source or GitHubClient(GhCliTokenProvider())
    install_error_handlers(app)
    app.include_router(_health_router())
    app.include_router(snapshots.router)
    return app
