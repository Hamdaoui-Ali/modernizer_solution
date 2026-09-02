from __future__ import annotations

from collections.abc import Sequence
from typing import Any

JAVA_RUNTIME_VALIDATION_ONLY_PROFILE_IDS = frozenset(
    {
        "springboot-3.5-java17-to-java21",
    }
)

JAVA_RUNTIME_VALIDATION_ONLY_UNIT_IDS = ("baseline", "java-21-runtime-validation")


def _major_version(value: Any) -> int | None:
    text = str(value or "").strip()
    if not text:
        return None
    digits = []
    for char in text:
        if char.isdigit():
            digits.append(char)
        elif digits:
            break
    if not digits:
        return None
    return int("".join(digits))


def _normalise_unit_ids(unit_ids: Sequence[str] | None) -> tuple[str, ...]:
    if not unit_ids:
        return ()
    return tuple(str(unit_id) for unit_id in unit_ids if str(unit_id))


def is_java_runtime_validation_only(
    profile_id: str | None = None,
    unit_ids: Sequence[str] | None = None,
) -> bool:
    profile_text = str(profile_id or "").strip()
    if profile_text in JAVA_RUNTIME_VALIDATION_ONLY_PROFILE_IDS:
        return True
    return _normalise_unit_ids(unit_ids) == JAVA_RUNTIME_VALIDATION_ONLY_UNIT_IDS


def requires_jakarta_migration(
    *,
    source_boot_version: str = "",
    target_boot_version: str = "",
    profile_id: str | None = None,
    unit_ids: Sequence[str] | None = None,
) -> bool:
    if is_java_runtime_validation_only(profile_id=profile_id, unit_ids=unit_ids):
        return False

    source_major = _major_version(source_boot_version)
    target_major = _major_version(target_boot_version)
    if source_major is None or target_major is None:
        return False
    return target_major >= 3 and source_major < target_major


def should_openrewrite_impact_be_fatal(
    *,
    profile_id: str | None = None,
    unit_ids: Sequence[str] | None = None,
) -> bool:
    return not is_java_runtime_validation_only(profile_id=profile_id, unit_ids=unit_ids)
