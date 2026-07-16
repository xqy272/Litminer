"""Small JSON-Schema subset used for dependency-free MCP validation."""

from __future__ import annotations

import re
from typing import Any

from litminer.contracts.errors import LitminerValidationError


_TYPE_MAP = {
    "object": dict,
    "array": list,
    "string": str,
    "integer": int,
    "number": (int, float),
    "boolean": bool,
}


def _matches_type(value: Any, expected: str) -> bool:
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    return isinstance(value, _TYPE_MAP[expected])


def _is_valid(data: Any, schema: dict[str, Any]) -> bool:
    try:
        validate_json_schema(data, schema)
        return True
    except LitminerValidationError:
        return False


def validate_json_schema(data: Any, schema: dict[str, Any], *, path: str = "arguments") -> None:
    expected_type = schema.get("type")
    if expected_type and expected_type in _TYPE_MAP and not _matches_type(data, expected_type):
        raise LitminerValidationError(f"{path} must be {expected_type}")

    if "enum" in schema and data not in schema["enum"]:
        raise LitminerValidationError(f"{path} must be one of: {', '.join(map(str, schema['enum']))}")

    if isinstance(data, (int, float)) and not isinstance(data, bool):
        if "minimum" in schema and data < schema["minimum"]:
            raise LitminerValidationError(f"{path} must be >= {schema['minimum']}")
        if "maximum" in schema and data > schema["maximum"]:
            raise LitminerValidationError(f"{path} must be <= {schema['maximum']}")

    if isinstance(data, str):
        if "minLength" in schema and len(data) < schema["minLength"]:
            raise LitminerValidationError(f"{path} must not be empty")
        pattern = schema.get("pattern")
        if isinstance(pattern, str) and re.search(pattern, data) is None:
            raise LitminerValidationError(f"{path} does not match the required format")

    if isinstance(data, list):
        if "minItems" in schema and len(data) < schema["minItems"]:
            raise LitminerValidationError(f"{path} must contain at least {schema['minItems']} item(s)")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(data):
                validate_json_schema(item, item_schema, path=f"{path}[{index}]")

    if isinstance(data, dict):
        required = schema.get("required", [])
        missing = [key for key in required if key not in data or data[key] in (None, "", [])]
        if missing:
            raise LitminerValidationError(f"{path} missing required field(s): {', '.join(missing)}")
        properties = schema.get("properties", {})
        for key, value in data.items():
            child = properties.get(key)
            if isinstance(child, dict):
                validate_json_schema(value, child, path=f"{path}.{key}")

    if "allOf" in schema:
        for child in schema["allOf"]:
            validate_json_schema(data, child, path=path)
    if "anyOf" in schema and not any(_is_valid(data, child) for child in schema["anyOf"]):
        raise LitminerValidationError(f"{path} does not satisfy any allowed input shape")
    if "oneOf" in schema:
        matches = sum(1 for child in schema["oneOf"] if _is_valid(data, child))
        if matches != 1:
            raise LitminerValidationError(f"{path} must satisfy exactly one allowed input shape")
    if "not" in schema and _is_valid(data, schema["not"]):
        raise LitminerValidationError(f"{path} contains a forbidden field combination")
