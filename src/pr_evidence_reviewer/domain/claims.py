"""Claims: what a PR says it does, in the author's own words, before any of it is checked."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

ClaimKind = Literal["behavior", "test", "performance", "security", "refactor", "docs", "other"]


class ExtractedClaim(BaseModel):
    """One claim exactly as the model returns it. Never trusted until its quote is checked."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    quote: str = Field(description="The exact words from the PR text that make this claim.")
    statement: str = Field(description="The claim restated as one plain, checkable sentence.")
    kind: ClaimKind


class ExtractionOutput(BaseModel):
    """The whole reply the model must return. Extra fields fail the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claims: list[ExtractedClaim]


class Claim(BaseModel, frozen=True):
    id: str = Field(description="Stable within one extraction: c1, c2, ...")
    quote: str = Field(description="Verbatim text from the PR title or description.")
    statement: str
    kind: ClaimKind


class ClaimSet(BaseModel, frozen=True):
    claims: list[Claim]
    dropped_count: int = Field(
        default=0, description="Claims the model returned whose quote isn't in the PR text."
    )
    notes: list[str] = Field(default_factory=list)
    scope_limitations: list[str] = Field(
        default_factory=list, description="Parts of the PR text that weren't read."
    )
    provider: str | None = None
    model: str | None = None
