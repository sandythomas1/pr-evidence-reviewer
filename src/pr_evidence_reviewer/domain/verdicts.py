"""Verdicts: what the evidence says about each claim, with the lines and CI checks it rests on.

The `Proposed*` models are the reply the model must return. Every field is required and has no
default, and extra fields fail the request, so the same schema works with strict structured output
on every provider. Nothing in them is trusted until each citation is checked against the snapshot
or the checkout at the head commit.

Line numbers are capped at MAX_LINE_NUMBER so an absurd number fails the schema instead of reaching
the checker (threat T7). Quote length isn't a schema constraint: strict structured output on some
providers refuses `maxLength`, so the checker drops an overlong quote instead.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

Verdict = Literal["supported", "contradicted", "insufficient_evidence"]
ClaimStatus = Literal["supported", "contradicted", "insufficient_evidence", "analysis_failed"]

MAX_LINE_NUMBER = 10**7
MAX_QUOTE_CHARS = 500


class ProposedDiffCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(description="A changed file's path exactly as listed in the evidence.")
    start_line: int = Field(
        le=MAX_LINE_NUMBER, description="First head-side line number cited, as shown in the diff."
    )
    end_line: int = Field(
        le=MAX_LINE_NUMBER,
        description="Last head-side line number cited. Same as start_line for one.",
    )


class ProposedCodeCitation(BaseModel):
    """Lines of a file at the head commit, including code the pull request didn't change."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    path: str = Field(description="A repository file's path exactly as listed in the evidence.")
    start_line: int = Field(
        ge=1, le=MAX_LINE_NUMBER, description="First line number cited, as shown in the evidence."
    )
    end_line: int = Field(
        ge=1,
        le=MAX_LINE_NUMBER,
        description="Last line number cited. Same as start_line for one.",
    )
    commit: str = Field(
        description="The head commit's id, in full or its first 7 or more characters."
    )
    quote: str = Field(
        description=(
            f"Exact text from the cited lines, at most {MAX_QUOTE_CHARS} characters, "
            "or an empty string."
        )
    )


class ProposedCheckCitation(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    name: str = Field(description="A CI check's name exactly as listed in the evidence.")


class ProposedAssessment(BaseModel):
    """One claim's assessment exactly as the model returns it."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    claim_id: str = Field(description="The id of the claim being assessed, for example c1.")
    verdict: Verdict
    explanation: str = Field(description="What the evidence shows and what it can't show.")
    diff_citations: list[ProposedDiffCitation]
    code_citations: list[ProposedCodeCitation]
    check_citations: list[ProposedCheckCitation]
    missing_tests: list[str] = Field(
        description="Tests that would check the claim but aren't there."
    )
    edge_cases: list[str] = Field(description="Situations a reviewer should check by hand.")


class AssessmentOutput(BaseModel):
    """The whole reply the model must return. Extra fields fail the request."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    assessments: list[ProposedAssessment]


class DiffCitation(BaseModel, frozen=True):
    """Lines that exist on the head side of the fetched diff."""

    kind: Literal["diff"] = "diff"
    path: str
    start_line: int
    end_line: int


class CodeCitation(BaseModel, frozen=True):
    """Lines that exist in a text file of the checkout at the head commit."""

    kind: Literal["code"] = "code"
    path: str
    start_line: int
    end_line: int
    commit: str = Field(description="The full head commit id.")


class CheckCitation(BaseModel, frozen=True):
    """A CI check reported for the head commit, with the result GitHub gave for it."""

    kind: Literal["check"] = "check"
    name: str
    source: Literal["check_run", "status"]
    status: str
    conclusion: str | None


Citation = Annotated[DiffCitation | CodeCitation | CheckCitation, Field(discriminator="kind")]


class ClaimVerdict(BaseModel, frozen=True):
    claim_id: str
    status: ClaimStatus = Field(
        description="A verdict, or analysis_failed when the claim wasn't assessed. Never approval."
    )
    explanation: str
    citations: list[Citation] = Field(description="Only citations that checked out.")
    missing_tests: list[str] = Field(default_factory=list)
    edge_cases: list[str] = Field(default_factory=list)
    dropped_citations: int = Field(
        default=0,
        description="Citations the model gave that didn't check out against the evidence.",
    )
    notes: list[str] = Field(default_factory=list)


class VerdictSet(BaseModel, frozen=True):
    head_sha: str = Field(description="The commit every citation refers to.")
    verdicts: list[ClaimVerdict] = Field(description="One per claim, in the claims' order.")
    dropped_citations: int = 0
    notes: list[str] = Field(default_factory=list)
    scope_limitations: list[str] = Field(
        default_factory=list, description="Evidence the model didn't see. Gaps, never approval."
    )
    provider: str | None = None
    model: str | None = None
    packet_hash: str | None = Field(
        default=None, description="Content hash of the evidence packet the verdicts rest on."
    )
