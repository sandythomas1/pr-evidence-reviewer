"""POST /api/claims: the PR snapshot plus the claims pulled from its title and description."""

from typing import Annotated

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from pr_evidence_reviewer.api.errors import ErrorResponse
from pr_evidence_reviewer.api.snapshots import SnapshotRequest, SnapshotSourceDep
from pr_evidence_reviewer.domain.claims import ClaimSet
from pr_evidence_reviewer.domain.pull_request import PullRequestSnapshot, parse_pull_request_url
from pr_evidence_reviewer.errors import ProviderUnavailableError
from pr_evidence_reviewer.llm.base import LLMProvider
from pr_evidence_reviewer.review.claim_extraction import ClaimExtractor


class ClaimsResponse(BaseModel, frozen=True):
    snapshot: PullRequestSnapshot
    claims: ClaimSet


def get_claim_extractor(request: Request) -> ClaimExtractor:
    """Use the first provider that's ready. Status checks are local and cheap (no model call)."""
    providers: list[LLMProvider] = request.app.state.providers
    statuses = [(provider, provider.status()) for provider in providers]
    for provider, status in statuses:
        if status.available:
            return ClaimExtractor(provider)
    details = " ".join(status.detail for _, status in statuses)
    raise ProviderUnavailableError(f"No model provider is ready. {details}".strip())


ClaimExtractorDep = Annotated[ClaimExtractor, Depends(get_claim_extractor)]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorResponse} for status in (401, 404, 422, 429, 502, 503, 504)
}

router = APIRouter()


@router.post("/api/claims", response_model=ClaimsResponse, responses=ERROR_RESPONSES)
async def extract_claims(
    body: SnapshotRequest, source: SnapshotSourceDep, extractor: ClaimExtractorDep
) -> ClaimsResponse:
    snapshot = await source.fetch_snapshot(parse_pull_request_url(body.pr_url))
    claims = await extractor.extract(snapshot.title, snapshot.body)
    return ClaimsResponse(snapshot=snapshot, claims=claims)
