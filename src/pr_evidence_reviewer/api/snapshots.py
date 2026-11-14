"""POST /api/snapshots: the PR version a review is pinned to."""

from typing import Annotated, Protocol

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from pr_evidence_reviewer.api.errors import ErrorResponse
from pr_evidence_reviewer.domain.pull_request import (
    MAX_URL_LENGTH,
    PullRequestRef,
    PullRequestSnapshot,
    parse_pull_request_url,
)


class SnapshotSource(Protocol):
    async def fetch_snapshot(self, ref: PullRequestRef) -> PullRequestSnapshot: ...


class SnapshotRequest(BaseModel):
    pr_url: str = Field(min_length=1, max_length=MAX_URL_LENGTH)


def get_snapshot_source(request: Request) -> SnapshotSource:
    source: SnapshotSource = request.app.state.snapshot_source
    return source


SnapshotSourceDep = Annotated[SnapshotSource, Depends(get_snapshot_source)]

ERROR_RESPONSES: dict[int | str, dict[str, object]] = {
    status: {"model": ErrorResponse} for status in (401, 404, 422, 429, 502)
}

router = APIRouter()


@router.post("/api/snapshots", response_model=PullRequestSnapshot, responses=ERROR_RESPONSES)
async def create_snapshot(body: SnapshotRequest, source: SnapshotSourceDep) -> PullRequestSnapshot:
    return await source.fetch_snapshot(parse_pull_request_url(body.pr_url))
