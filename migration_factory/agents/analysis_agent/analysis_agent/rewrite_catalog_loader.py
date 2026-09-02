import json
from pathlib import Path

import yaml


_REQUIRED_KEYS = (
    "openrewrite.plugin",
    "openrewrite.recipe_artifacts",
    "openrewrite.active_recipes",
    "openrewrite.dry_run",
)

_ONLY_ANALYSIS_GOAL = "rewrite:dryRun"


def _catalog_candidates(context):
    modernized = Path(getattr(context, "modernized_app_path", ""))
    legacy = Path(getattr(context, "legacy_app_path", ""))
    return [
        modernized / ".migration" / "ai_hub_profile.json",
        modernized / ".migration" / "catalog.json",
        modernized / "ai_hub_profile.json",
        legacy / ".migration" / "ai_hub_profile.json",
        legacy / "ai_hub_profile.json",
    ]


def _coord(payload):
    try:
        return f"{payload['group_id']}:{payload['artifact_id']}:{payload['version']}"
    except KeyError as exc:
        raise ValueError(f"Catalog missing required coordinate field: {exc.args[0]}") from exc


def _as_rewrite_goal(goal):
    value = str(goal or "").strip()
    if not value:
        return value
    return value if ":" in value else f"rewrite:{value}"


def _as_string_list(values):
    if not values:
        return []
    if not isinstance(values, list):
        raise ValueError("analysis_preview_maven_args must be a list")
    return [str(value) for value in values]


def _dedupe(values):
    seen = set()
    result = []
    for value in values:
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def _is_apply_goal(goal):
    return _as_rewrite_goal(goal) in {"rewrite:run", "rewrite:runNoFork"}


def _load_yaml(path):
    with path.open("r", encoding="utf-8") as handle:
        return yaml.safe_load(handle) or {}


def _load_ai_hub_catalog(context):
    ai_hub = getattr(context, "ai_hub_path", None)
    profile_id = getattr(context, "profile", None)
    if not ai_hub and not profile_id:
        return None
    if not ai_hub or not profile_id:
        return {
            "status": "FAILED",
            "errors": ["Both --ai-hub and --profile are required to load an AI Hub catalog"],
            "path": None,
        }

    hub_path = Path(ai_hub)
    if not hub_path.is_dir():
        return {
            "status": "FAILED",
            "errors": [f"AI Hub path not found: {hub_path}"],
            "path": None,
            "profile_id": profile_id,
        }

    profile_path = hub_path / "profiles" / f"{profile_id}.yaml"
    if not profile_path.is_file():
        return {
            "status": "FAILED",
            "errors": [f"AI Hub profile not found: {profile_path}"],
            "path": None,
            "profile_id": profile_id,
        }

    try:
        profile = _load_yaml(profile_path)
        catalog_rel = profile["openrewrite"]["catalog_path"]
    except KeyError as exc:
        return {
            "status": "FAILED",
            "errors": [f"Profile missing openrewrite.catalog_path: {exc.args[0]}"],
            "path": str(profile_path),
            "profile_id": profile_id,
        }

    catalog_path = (hub_path / catalog_rel).resolve()
    hub_root = hub_path.resolve()
    if not (catalog_path == hub_root or hub_root in catalog_path.parents):
        return {
            "status": "FAILED",
            "errors": [f"Catalog path escapes AI Hub: {catalog_rel}"],
            "path": str(catalog_path),
            "profile_id": profile_id,
        }
    if not catalog_path.is_file():
        return {
            "status": "FAILED",
            "errors": [f"OpenRewrite catalog not found: {catalog_path}"],
            "path": str(catalog_path),
            "profile_id": profile_id,
        }

    try:
        catalog = _load_yaml(catalog_path)
        preview_goals = [_as_rewrite_goal(goal) for goal in catalog.get("preview_goals", [])]
        apply_preview_goals = sorted(goal for goal in preview_goals if _is_apply_goal(goal))
        if apply_preview_goals:
            return {
                "status": "FAILED",
                "errors": [f"Forbidden OpenRewrite goal in preview_goals: {', '.join(apply_preview_goals)}"],
                "path": str(catalog_path),
                "profile_id": profile_id,
                "catalog_id": catalog.get("id"),
                "preview_goals": preview_goals,
            }
        selected_goal = _ONLY_ANALYSIS_GOAL
        if selected_goal not in preview_goals:
            return {
                "status": "FAILED",
                "errors": [f"Catalog does not allow required Analysis goal: {selected_goal}"],
                "path": str(catalog_path),
                "profile_id": profile_id,
                "catalog_id": catalog.get("id"),
                "preview_goals": preview_goals,
            }

        apply_goals = {_as_rewrite_goal(goal) for goal in catalog.get("forbidden_apply_goals", [])}
        blocked = sorted(goal for goal in apply_goals if goal != selected_goal)

        preview_maven_args = _dedupe([
            *_as_string_list(catalog.get("analysis_preview_maven_args")),
            *_as_string_list(profile.get("openrewrite", {}).get("analysis_preview_maven_args")),
        ])

        return {
            "status": "USED",
            "path": str(catalog_path),
            "profile_id": str(profile.get("id") or profile_id),
            "catalog_id": str(catalog["id"]),
            "preview_goals": preview_goals,
            "forbidden_apply_goals": blocked,
            "openrewrite": {
                "plugin": _coord(catalog["plugin"]),
                "recipe_artifacts": [_coord(item) for item in catalog.get("recipe_artifacts", [])],
                "active_recipes": [str(item) for item in catalog.get("active_recipes", [])],
                "dry_run": selected_goal,
                "analysis_preview_maven_args": preview_maven_args,
            },
        }
    except (KeyError, TypeError, ValueError) as exc:
        return {
            "status": "FAILED",
            "errors": [f"Invalid OpenRewrite catalog: {exc}"],
            "path": str(catalog_path),
            "profile_id": profile_id,
        }


def load_rewrite_catalog(context):
    ai_hub_catalog = _load_ai_hub_catalog(context)
    if ai_hub_catalog is not None:
        return ai_hub_catalog

    for candidate in _catalog_candidates(context):
        if not candidate.is_file():
            continue

        with candidate.open("r", encoding="utf-8") as handle:
            payload = json.load(handle)

        missing = [key for key in _REQUIRED_KEYS if key not in payload]
        if missing:
            return {
                "status": "FAILED",
                "errors": [f"Catalog missing required fields: {', '.join(missing)}"],
                "path": str(candidate),
            }

        dry_run_goal = str(payload["openrewrite.dry_run"])
        if _is_apply_goal(dry_run_goal):
            return {
                "status": "FAILED",
                "errors": [f"Forbidden OpenRewrite goal: {_as_rewrite_goal(dry_run_goal)}"],
                "path": str(candidate),
                "profile_id": None,
                "catalog_id": None,
                "preview_goals": [_as_rewrite_goal(dry_run_goal)],
            }
        if _as_rewrite_goal(dry_run_goal) != _ONLY_ANALYSIS_GOAL:
            return {
                "status": "FAILED",
                "errors": [f"Unsupported OpenRewrite goal: {_as_rewrite_goal(dry_run_goal)}"],
                "path": str(candidate),
                "profile_id": None,
                "catalog_id": None,
                "preview_goals": [_as_rewrite_goal(dry_run_goal)],
            }

        return {
            "status": "USED",
            "path": str(candidate),
            "profile_id": None,
            "catalog_id": None,
            "preview_goals": [_as_rewrite_goal(dry_run_goal)],
            "openrewrite": {
                "plugin": str(payload["openrewrite.plugin"]),
                "recipe_artifacts": str(payload["openrewrite.recipe_artifacts"]),
                "active_recipes": str(payload["openrewrite.active_recipes"]),
                "dry_run": _as_rewrite_goal(dry_run_goal),
                "analysis_preview_maven_args": _as_string_list(
                    payload.get("openrewrite.analysis_preview_maven_args")
                ),
            },
        }

    return {
        "status": "SKIPPED",
        "errors": ["OpenRewrite catalog/profile not found"],
        "path": None,
    }
