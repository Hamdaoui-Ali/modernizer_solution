import pytest

from rewrite_command_builder import build_rewrite_maven_command


def _catalog(goal):
    return {
        "plugin": "org.openrewrite.maven:rewrite-maven-plugin:5.40.0",
        "recipe_artifacts": "org.openrewrite.recipe:rewrite-spring:6.0.0",
        "active_recipes": "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0",
        "dry_run": goal,
    }


def test_allows_only_dryrun_like_goals():
    assert build_rewrite_maven_command(_catalog("dryRun"))[1].endswith(":dryRun")
    assert build_rewrite_maven_command(_catalog("rewrite:dryRun"))[1].endswith(":dryRun")
    for goal in ["dryRunNoFork", "rewrite:dryRunNoFork", "discover", "rewrite:discover"]:
        with pytest.raises(ValueError, match="Unsupported OpenRewrite goal"):
            build_rewrite_maven_command(_catalog(goal))


def test_rejects_apply_goals_and_aliases():
    blocked = ["rewrite:run", "run", "rewrite:runNoFork", "runNoFork"]
    for goal in blocked:
        with pytest.raises(ValueError, match="Forbidden OpenRewrite goal"):
            build_rewrite_maven_command(_catalog(goal))


def test_uses_catalog_recipes_and_artifacts_only():
    cmd = build_rewrite_maven_command(_catalog("rewrite:dryRun"))
    assert "-Drewrite.activeRecipes=org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_0" in cmd
    assert "-Drewrite.recipeArtifactCoordinates=org.openrewrite.recipe:rewrite-spring:6.0.0" in cmd
    assert "-Drewrite.failOnDryRunResults=false" in cmd
    assert all("UpgradeSpringBoot_" not in token or token.endswith("_3_0") for token in cmd)


def test_joins_yaml_catalog_lists_for_maven_properties():
    cmd = build_rewrite_maven_command(
        {
            "plugin": "org.openrewrite.maven:rewrite-maven-plugin:6.39.0",
            "recipe_artifacts": [
                "org.openrewrite.recipe:rewrite-spring:6.30.4",
                "org.openrewrite.recipe:rewrite-migrate-java:3.34.1",
            ],
            "active_recipes": [
                "org.openrewrite.java.migrate.UpgradeToJava17",
                "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5",
            ],
            "dry_run": "rewrite:dryRun",
        }
    )

    assert "-Drewrite.recipeArtifactCoordinates=org.openrewrite.recipe:rewrite-spring:6.30.4,org.openrewrite.recipe:rewrite-migrate-java:3.34.1" in cmd
    assert "-Drewrite.activeRecipes=org.openrewrite.java.migrate.UpgradeToJava17,org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5" in cmd


def test_analysis_preview_maven_args_are_inserted_before_rewrite_goal():
    catalog = _catalog("rewrite:dryRun")
    catalog["analysis_preview_maven_args"] = ["-Denforcer.skip=true"]

    cmd = build_rewrite_maven_command(catalog)

    assert cmd[1] == "-Denforcer.skip=true"
    assert cmd[2].endswith(":dryRun")
