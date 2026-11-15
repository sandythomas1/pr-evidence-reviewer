"""POST /api/claims: the PR snapshot plus the claims pulled from its title and description."""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field

from pr_evidence_reviewer.api.errors import ErrorResponse
from pr_evidence_reviewer.api.snapshots import SnapshotRequest, SnapshotSourceDep
from pr_evidence_reviewer.domain.claims import ClaimSet
from pr_evidence_reviewer.domain.pull_request import PullRequestSnapshot, parse_pull_request_url
from pr_evidence_reviewer.llm.base import LLMProvider
from pr_evidence_reviewer.llm.selection import select_provider
from pr_evidence_reviewer.review.claim_extraction import ClaimExtractor

MAX_PROVIDER_ID_LENGTH = 64


class ClaimsRequest(SnapshotRequest):
    provider: str | None = Field(
        default=None,
        min_length=1,
        max_length=MAX_PROVIDER_ID_LENGTH,
        description="A provider id from GET /api/providers. Omit it to use the first ready one.",
    )


class ClaimsResponse(BaseModel, frozen=True):
    snapshot: PullRequestSnapshot
    claims: ClaimSet


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorResponse} for status in (401, 404, 422, 429, 502, 503, 504)
}

router = APIRouter()


@router.post("/api/claims", response_model=ClaimsResponse, responses=ERROR_RESPONSES)
async def extract_claims(
    body: ClaimsRequest, request: Request, source: SnapshotSourceDep
) -> ClaimsResponse:
    ref = parse_pull_request_url(body.pr_url)
    # Resolve the provider before calling GitHub, so a bad choice fails fast.
    providers: list[LLMProvider] = request.app.state.providers
    extractor = ClaimExtractor(select_provider(providers, body.provider))
    snapshot = await source.fetch_snapshot(ref)
    claims = await extractor.extract(snapshot.title, snapshot.body)
    return ClaimsResponse(snapshot=snapshot, claims=claims)
