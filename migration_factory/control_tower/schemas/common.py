"""Shared helpers for strict Control Tower schemas."""

from __future__ import annotations

import re
from typing import Annotated, Iterable

from pydantic import BaseModel, ConfigDict, StringConstraints


class StrictModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        strict=True,
    )


NonEmptyString = Annotated[str, StringConstraints(min_length=1)]

_SECRET_LIKE_PATTERN = re.compile(
    r"bearer\s+|api[_-]?key\s*=|password\s*=|token\s*=|client[_-]?secret\s*=|"
    r"access[_-]?token\s*=|refresh[_-]?token\s*=|begin private key",
    re.IGNORECASE,
)


def require_non_empty_string(value: str, field_name: str) -> str:
    if not value.strip():
        raise ValueError(f"{field_name} must not be empty")
    return value


def reject_secret_like_value(value: str, field_name: str) -> str:
    if _SECRET_LIKE_PATTERN.search(value):
        raise ValueError(f"{field_name} contains a secret-like value")
    return value


def ensure_unique_ids(items: Iterable[object], attribute: str, label: str) -> None:
    seen: set[str] = set()
    duplicates: set[str] = set()

    for item in items:
        item_id = getattr(item, attribute)
        if item_id in seen:
            duplicates.add(item_id)
        seen.add(item_id)

    if duplicates:
        duplicate_list = ", ".join(sorted(duplicates))
        raise ValueError(f"duplicate {label} values are not allowed: {duplicate_list}")
