import json


def build_rewrite_plugin_plan(context, catalog):
    openrewrite = catalog.get("openrewrite", {})
    return {
        "schema_version": "1.0.0",
        "status": catalog["status"],
        "profile_id": catalog.get("profile_id"),
        "catalog_path": catalog.get("path"),
        "catalog_id": catalog.get("catalog_id"),
        "plugin": openrewrite.get("plugin"),
        "recipe_artifacts": openrewrite.get("recipe_artifacts", []),
        "active_recipes": openrewrite.get("active_recipes", []),
        "preview_goals": catalog.get("preview_goals", []),
        "selected_preview_goal": openrewrite.get("dry_run"),
        "apply_goals_forbidden": True,
        "transformer_guidance": (
            "Transformer Agent may add OpenRewrite plugin/config in migration workspace only. "
            "Analysis may only execute rewrite:dryRun."
        ),
        "openrewrite": openrewrite,
        "artifact_refs": {"self": "rewrite_plugin_plan.json"},
        "run_id": getattr(context, "run_id", "unknown"),
        "agent": "analysis_agent",
        "phase": "analysis",
    }


def write_rewrite_plugin_plan(path, context, catalog):
    plan = build_rewrite_plugin_plan(context, catalog)
    with open(path, "w", encoding="utf-8") as handle:
        json.dump(plan, handle, indent=4)
    return plan
