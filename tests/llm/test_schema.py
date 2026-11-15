import pytest
from pydantic import BaseModel, ConfigDict, Field

from pr_evidence_reviewer.domain.claims import ExtractionOutput
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
