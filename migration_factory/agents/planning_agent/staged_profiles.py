from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


STAGED_BOOT_21_TO_35_JAVA17 = (
    "springboot-2.1.6-to-2.7-java11",
    "springboot-2.7-to-3.5-java17",
    "springboot-3.5-java17-to-java21",
)


@dataclass(frozen=True)
class StagedProfile:
    id: str
    stage: str
    purpose: str
    required: bool
    source: dict[str, Any]
    target: dict[str, Any]


def plan_boot_216_to_boot35_stages(
    ai_hub_path: str | Path,
    *,
    include_java21_validation: bool = False,
) -> tuple[StagedProfile, ...]:
    profile_ids = (
        STAGED_BOOT_21_TO_35_JAVA17
        if include_java21_validation
        else STAGED_BOOT_21_TO_35_JAVA17[:2]
    )
    return tuple(
        _to_staged_profile(_load_profile(ai_hub_path, profile_id), required=index < 2)
        for index, profile_id in enumerate(profile_ids)
    )


def _load_profile(ai_hub_path: str | Path, profile_id: str) -> dict[str, Any]:
    path = Path(ai_hub_path) / "profiles" / f"{profile_id}.yaml"
    payload = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    if not isinstance(payload, dict):
        raise ValueError(f"Profile must be a YAML mapping: {path}")
    return payload


def _to_staged_profile(profile: dict[str, Any], *, required: bool) -> StagedProfile:
    return StagedProfile(
        id=str(profile.get("id") or ""),
        stage=str(profile.get("stage") or ""),
        purpose=str(profile.get("purpose") or profile.get("description") or ""),
        required=required,
        source=dict(profile.get("source") or {}),
        target=dict(profile.get("target") or {}),
    )
