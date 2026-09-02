from migration_factory.maven import resolve_maven_executable

_ALLOWED_GOALS = {"dryRun"}
_FORBIDDEN_GOALS = {"run", "runNoFork"}


def _parse_plugin(plugin):
    parts = plugin.split(":")
    if len(parts) < 3:
        raise ValueError("Invalid plugin coordinate, expected groupId:artifactId:version")
    return parts[0], parts[1], parts[2]


def _extract_goal(raw_goal):
    goal = str(raw_goal or "").strip()
    if ":" in goal:
        goal = goal.split(":")[-1]
    return goal


def build_rewrite_maven_command(catalog):
    plugin = catalog["plugin"]
    artifacts = catalog["recipe_artifacts"]
    recipes = catalog["active_recipes"]
    goal = _extract_goal(catalog["dry_run"])

    if goal in _FORBIDDEN_GOALS:
        raise ValueError(f"Forbidden OpenRewrite goal: {goal}")
    if goal not in _ALLOWED_GOALS:
        raise ValueError(f"Unsupported OpenRewrite goal: {goal}")

    group_id, artifact_id, version = _parse_plugin(plugin)
    if isinstance(artifacts, list):
        artifacts = ",".join(artifacts)
    if isinstance(recipes, list):
        recipes = ",".join(recipes)
    preview_maven_args = [str(arg) for arg in catalog.get("analysis_preview_maven_args", [])]

    maven_executable = resolve_maven_executable()
    cmd = [
        maven_executable,
        *preview_maven_args,
        f"{group_id}:{artifact_id}:{version}:{goal}",
        f"-Drewrite.activeRecipes={recipes}",
        f"-Drewrite.recipeArtifactCoordinates={artifacts}",
        "-Drewrite.failOnDryRunResults=false",
    ]
    return cmd
