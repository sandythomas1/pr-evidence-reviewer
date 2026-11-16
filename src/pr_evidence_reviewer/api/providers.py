"""GET /api/providers: which model providers this machine can use."""

from typing import Annotated

from fastapi import APIRouter, Request
from pydantic import Field

from pr_evidence_reviewer.llm.base import LLMProvider, ProviderStatus

MAX_PROVIDER_ID_LENGTH = 64

# The provider field on every request that calls a model. llm.selection.select_provider checks the
# id against this runner's providers, so here it only needs to be a short string.
ProviderChoice = Annotated[
    str | None,
    Field(
        min_length=1,
        max_length=MAX_PROVIDER_ID_LENGTH,
        description="A provider id from GET /api/providers. Omit it to use the first ready one.",
    ),
]

router = APIRouter()


@router.get("/api/providers", response_model=list[ProviderStatus])
def list_providers(request: Request) -> list[ProviderStatus]:
    providers: list[LLMProvider] = request.app.state.providers
    return [provider.status() for provider in providers]
