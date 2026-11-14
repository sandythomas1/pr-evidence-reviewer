"""FastAPI application factory."""

from fastapi import APIRouter, FastAPI
from pydantic import BaseModel

from pr_evidence_reviewer import __version__
from pr_evidence_reviewer.config import Settings


class HealthResponse(BaseModel):
    status: str
    version: str


def _health_router() -> APIRouter:
    router = APIRouter()

    @router.get("/api/health", response_model=HealthResponse)
    def health() -> HealthResponse:
        return HealthResponse(status="ok", version=__version__)

    return router


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the app. Tests call this with their own settings and dependencies."""
    app = FastAPI(title="PR Evidence Reviewer", version=__version__)
    app.state.settings = settings or Settings()
    app.include_router(_health_router())
    return app
