"""POST /api/verdicts: a first-pass verdict for each claim, from the snapshot's diff and CI checks.

The browser sends back the claims that /api/claims returned. The snapshot is fetched again here,
and each claim's quote must still appear in the pull request's text. Only the quote is checked: the
id, statement, and kind are bounded by the request schema but otherwise taken as sent, so they reach
the model as untrusted data inside its boundary markers, never as instructions.
"""

from fastapi import APIRouter, Request
from pydantic import BaseModel, Field, field_validator

from pr_evidence_reviewer.api.errors import ErrorResponse
from pr_evidence_reviewer.api.providers import ProviderChoice
from pr_evidence_reviewer.api.snapshots import SnapshotSourceDep
from pr_evidence_reviewer.domain.claims import Claim
from pr_evidence_reviewer.domain.pull_request import (
    MAX_URL_LENGTH,
    PullRequestSnapshot,
    parse_pull_request_url,
)
from pr_evidence_reviewer.domain.verdicts import VerdictSet
from pr_evidence_reviewer.errors import ClaimsOutOfDateError
from pr_evidence_reviewer.llm.base import LLMProvider
from pr_evidence_reviewer.llm.selection import select_provider
from pr_evidence_reviewer.review.claim_extraction import (
    MAX_CLAIMS,
    MAX_DESCRIPTION_CHARS,
    grounding_text,
    is_grounded,
)
from pr_evidence_reviewer.review.verdicts import ClaimAssessor

MAX_CLAIM_ID_CHARS = 10
MAX_STATEMENT_CHARS = 2_000


class VerdictsRequest(BaseModel):
    pr_url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)
    claims: list[Claim] = Field(min_length=1, max_length=MAX_CLAIMS)
    provider: ProviderChoice = None

    @field_validator("claims")
    @classmethod
    def _distinct_and_bounded(cls, claims: list[Claim]) -> list[Claim]:
        ids = [claim.id for claim in claims]
        if len(set(ids)) != len(ids):
            raise ValueError("Each claim needs its own id.")
        for claim in claims:
            if not 0 < len(claim.id) <= MAX_CLAIM_ID_CHARS:
                raise ValueError("A claim id is missing or too long.")
            if (
                len(claim.statement) > MAX_STATEMENT_CHARS
                or len(claim.quote) > MAX_DESCRIPTION_CHARS
            ):
                raise ValueError("A claim is too long.")
        return claims


class VerdictsResponse(BaseModel, frozen=True):
    snapshot: PullRequestSnapshot
    verdicts: VerdictSet


def ensure_grounded(claims: list[Claim], snapshot: PullRequestSnapshot) -> None:
    haystack = grounding_text(snapshot.title, snapshot.body)
    if not all(is_grounded(claim.quote, haystack) for claim in claims):
        raise ClaimsOutOfDateError(
            "These claims don't match the pull request's description any more. Load the pull "
            "request again to find its current claims."
        )


ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorResponse} for status in (401, 404, 409, 422, 429, 502, 503, 504)
}

router = APIRouter()


@router.post("/api/verdicts", response_model=VerdictsResponse, responses=ERROR_RESPONSES)
async def assess_claims(
    body: VerdictsRequest, request: Request, source: SnapshotSourceDep
) -> VerdictsResponse:
    ref = parse_pull_request_url(body.pr_url)
    # Same rules as /api/claims, and resolved before calling GitHub so a bad choice fails fast.
    providers: list[LLMProvider] = request.app.state.providers
    assessor = ClaimAssessor(select_provider(providers, body.provider))
    snapshot = await source.fetch_snapshot(ref)
    ensure_grounded(body.claims, snapshot)
    verdicts = await assessor.assess(snapshot, body.claims)
    return VerdictsResponse(snapshot=snapshot, verdicts=verdicts)
