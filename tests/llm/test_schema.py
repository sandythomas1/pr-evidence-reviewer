import pytest
from pydantic import BaseModel, ConfigDict, Field

from pr_evidence_reviewer.domain.claims import ExtractionOutput
from pr_evidence_reviewer.domain.verdicts import MAX_LINE_NUMBER, AssessmentOutput
from pr_evidence_reviewer.llm.schema import strict_json_schema


class Inner(BaseModel):
    note: str | None = None


class Outer(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    count: int = Field(default=0, ge=0)
    inner: Inner
    items: list[Inner]


def test_extraction_output_is_already_strict() -> None:
    schema = ExtractionOutput.model_json_schema()

    assert strict_json_schema(schema) == schema


def test_every_object_closes_and_requires_all_its_properties() -> None:
    strict = strict_json_schema(Outer.model_json_schema())

    assert strict["additionalProperties"] is False
    assert strict["required"] == ["name", "count", "inner", "items"]
    inner = strict["$defs"]["Inner"]
    assert inner["additionalProperties"] is False
    assert inner["required"] == ["note"]


def test_keeps_defaults_and_constraints() -> None:
    strict = strict_json_schema(Outer.model_json_schema())

    assert strict["properties"]["count"] == {
        "default": 0,
        "minimum": 0,
        "title": "Count",
        "type": "integer",
    }


def test_does_not_change_the_input() -> None:
    schema = Outer.model_json_schema()
    before = repr(schema)

    strict_json_schema(schema)

    assert repr(schema) == before


def test_a_strict_reply_still_validates_against_the_model() -> None:
    reply = {"name": "a", "count": 1, "inner": {"note": None}, "items": [{"note": "b"}]}

    assert Outer.model_validate(reply).inner == Inner()


class Awkward(BaseModel):
    type: str
    properties: list[str] = []


def test_fields_named_like_schema_keywords_are_still_fields() -> None:
    strict = strict_json_schema(Awkward.model_json_schema())

    assert strict["required"] == ["type", "properties"]
    assert strict["properties"]["properties"]["type"] == "array"
    assert "additionalProperties" not in strict["properties"]


class Mapping(BaseModel):
    scores: dict[str, int]


def test_open_ended_objects_are_refused() -> None:
    with pytest.raises(ValueError, match="additionalProperties"):
        strict_json_schema(Mapping.model_json_schema())


# The assessment reply, as each provider receives it: Claude Code and Gemini get the model's own
# schema, Codex gets the strict copy. All three must be the same, strict-compatible schema.
REFUSED_BY_STRICT_MODE = frozenset({"default", "maxLength", "minLength"})


def _keywords(node: object) -> set[str]:
    if isinstance(node, list):
        return set().union(*(_keywords(item) for item in node)) if node else set()
    if not isinstance(node, dict):
        return set()
    found: set[str] = set()
    for key, value in node.items():
        if key in {"properties", "$defs"} and isinstance(value, dict):
            found |= set().union(*(_keywords(sub) for sub in value.values())) if value else set()
        else:
            found.add(key)
            found |= _keywords(value)
    return found


def test_the_assessment_schema_is_the_same_for_every_provider() -> None:
    schema = AssessmentOutput.model_json_schema()

    assert strict_json_schema(schema) == schema
    assert not _keywords(schema) & REFUSED_BY_STRICT_MODE


def test_the_assessment_schema_requires_code_citations_with_bounded_lines() -> None:
    schema = strict_json_schema(AssessmentOutput.model_json_schema())
    assessment = schema["$defs"]["ProposedAssessment"]
    code = schema["$defs"]["ProposedCodeCitation"]
    diff = schema["$defs"]["ProposedDiffCitation"]

    assert "code_citations" in assessment["required"]
    assert code["required"] == ["path", "start_line", "end_line", "commit", "quote"]
    assert code["additionalProperties"] is False
    for field in ("start_line", "end_line"):
        assert (code["properties"][field]["minimum"], code["properties"][field]["maximum"]) == (
            1,
            MAX_LINE_NUMBER,
        )
        assert diff["properties"][field]["maximum"] == MAX_LINE_NUMBER
