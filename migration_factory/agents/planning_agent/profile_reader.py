from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from migration_factory.agents.planning_agent.paths import get_ai_hub_profile_path


@dataclass(frozen=True)
class LoadedMigrationProfile:
    profile: dict[str, Any] = field(default_factory=dict)
    path: Path | None = None
    errors: list[str] = field(default_factory=list)
    ok: bool = True


def load_migration_profile(
    ai_hub_path: str | Path, profile_id: str
) -> LoadedMigrationProfile:
    profile_path = get_ai_hub_profile_path(ai_hub_path, profile_id)
    errors: list[str] = []

    if not profile_path.exists():
        return LoadedMigrationProfile(
            profile={},
            path=profile_path,
            errors=[f"Profile not found: {profile_path}"],
            ok=False,
        )

    try:
        with profile_path.open("r", encoding="utf-8") as file_obj:
            loaded = yaml.safe_load(file_obj)
    except (OSError, yaml.YAMLError) as exc:
        return LoadedMigrationProfile(
            profile={},
            path=profile_path,
            errors=[f"Profile YAML failed to load: {exc}"],
            ok=False,
        )

    if not isinstance(loaded, dict) or not loaded:
        return LoadedMigrationProfile(
            profile={},
            path=profile_path,
            errors=["Profile YAML is empty or not object."],
            ok=False,
        )

    for field_name in ("source", "target", "rules"):
        if field_name not in loaded:
            errors.append(f"Profile missing required top-level field: {field_name}")

    target = loaded.get("target")
    if not isinstance(target, dict):
        errors.append("Profile target must be object.")
    else:
        if not target.get("java"):
            errors.append("Profile target missing required value: java")
        if not target.get("spring_boot"):
            errors.append("Profile target missing required value: spring_boot")

        has_build_field = any(key in target for key in ("build", "build_tool", "buildTool"))
        if has_build_field:
            build_value = target.get("build") or target.get("build_tool") or target.get("buildTool")
            if not build_value:
                errors.append("Profile target build tool field present but empty.")

    return LoadedMigrationProfile(
        profile=loaded,
        path=profile_path,
        errors=errors,
        ok=not errors,
    )
