"""GET /api/providers: which model providers this machine can use."""

from fastapi import APIRouter, Request

from pr_evidence_reviewer.llm.base import LLMProvider, ProviderStatus

router = APIRouter()


@router.get("/api/providers", response_model=list[ProviderStatus])
def list_providers(request: Request) -> list[ProviderStatus]:
    providers: list[LLMProvider] = request.app.state.providers
    return [provider.status() for provider in providers]
