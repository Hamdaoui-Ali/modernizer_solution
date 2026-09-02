from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import jsonschema


SCHEMA_DIR = Path(__file__).resolve().parent / "schemas"


def load_schema(schema_name: str) -> dict[str, Any]:
    return json.loads((SCHEMA_DIR / schema_name).read_text(encoding="utf-8"))


def validate_against_schema(payload: Any, schema_name: str) -> tuple[str, ...]:
    validator = jsonschema.Draft7Validator(load_schema(schema_name))
    errors = sorted(validator.iter_errors(payload), key=lambda error: error.path)
    return tuple(_format_error(error) for error in errors)


def _format_error(error: jsonschema.ValidationError) -> str:
    path = ".".join(str(part) for part in error.path)
    location = path or "$"
    return f"{location}: {error.message}"
