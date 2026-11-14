"""The provider interface every model adapter implements.

A request names a pydantic model as its output type. Its JSON Schema is what the provider is asked to
follow, and the same model validates the reply on our side, so a provider that ignores the schema
produces an error, never a partial result.
"""

import json
from dataclasses import dataclass
from typing import Any, Protocol

from pydantic import BaseModel, ValidationError

from pr_evidence_reviewer.errors import ProviderInvalidOutputError


@dataclass(frozen=True)
class StructuredRequest[T: BaseModel]:
    system_prompt: str
    user_content: str
    output_type: type[T]

    def json_schema(self) -> dict[str, Any]:
        return self.output_type.model_json_schema()

    def json_schema_text(self) -> str:
        return json.dumps(self.json_schema(), separators=(",", ":"))


class Usage(BaseModel, frozen=True):
    input_tokens: int | None = None
    output_tokens: int | None = None
    cost_usd: float | None = None


@dataclass(frozen=True)
class StructuredResult[T: BaseModel]:
    output: T
    provider: str
    model: str | None
    usage: Usage


class ProviderStatus(BaseModel, frozen=True):
    id: str
    name: str
    available: bool
    detail: str


class LLMProvider(Protocol):
    id: str
    name: str

    def status(self) -> ProviderStatus: ...

    async def complete_structured[T: BaseModel](
        self, request: StructuredRequest[T]
    ) -> StructuredResult[T]: ...


def validate_output[T: BaseModel](raw: object, output_type: type[T], provider_name: str) -> T:
    """Validate a provider's reply against the requested schema, or fail the whole request."""
    try:
        return output_type.model_validate(raw)
    except ValidationError as error:
        raise ProviderInvalidOutputError(
            f"{provider_name} returned a reply that didn't match the expected format. Try again."
        ) from error
