"""JSON Schema helpers for providers whose structured output has extra rules.

OpenAI structured outputs (used by the Codex CLI's `--output-schema`) run in strict mode: every object
must set `additionalProperties: false` and list every property as required. pydantic leaves fields
with defaults out of `required`, so those schemas are rejected as they are.
"""

import copy
from typing import Any


def strict_json_schema(schema: dict[str, Any]) -> dict[str, Any]:
    """Return a copy where every object is closed and requires all of its properties.

    This only narrows the schema: a reply that follows the strict copy still validates against the
    original pydantic model. Open-ended objects (`dict[str, ...]`) can't be expressed in strict mode,
    so they raise instead of being silently changed.
    """
    strict = copy.deepcopy(schema)
    _tighten(strict)
    return strict


def _tighten(node: object) -> None:
    if isinstance(node, list):
        for item in node:
            _tighten(item)
        return
    if not isinstance(node, dict):
        return
    properties = node.get("properties")
    if node.get("type") == "object" or isinstance(properties, dict):
        if node.get("additionalProperties", False) is not False:
            raise ValueError(
                "Strict structured output needs every object to set additionalProperties to "
                "false, so open-ended mappings can't be used in a model reply."
            )
        node["additionalProperties"] = False
        node["required"] = list(properties or {})
    for key, value in node.items():
        if key in _SCHEMA_MAPS and isinstance(value, dict):
            # Keys here are field or definition names; only the values are schemas.
            for subschema in value.values():
                _tighten(subschema)
        else:
            _tighten(value)


_SCHEMA_MAPS = frozenset({"properties", "$defs", "definitions"})
