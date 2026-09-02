import json
from pathlib import Path

import jsonschema
import yaml

from migration_factory.agents.planning_agent.profile_reader import load_migration_profile


REPO_ROOT = Path(__file__).resolve().parents[3]
AI_HUB = REPO_ROOT / "modernizer-solution-ai-hub"
PROFILE_ID = "library-jakarta-java17-minimal"
EXPECTED_RECIPES = [
    "org.openrewrite.java.migrate.UpgradeBuildToJava17",
    "org.openrewrite.java.migrate.jakarta.JavaxAnnotationMigrationToJakartaAnnotation",
    "org.openrewrite.java.migrate.jakarta.JavaxXmlBindMigrationToJakartaXmlBind",
    "org.openrewrite.java.migrate.jakarta.JavaxPersistenceToJakartaPersistence",
    "org.openrewrite.java.migrate.jakarta.JavaxServletToJakartaServlet",
]


def _load_profile_and_catalog():
    profile = yaml.safe_load((AI_HUB / "profiles" / f"{PROFILE_ID}.yaml").read_text(encoding="utf-8"))
    catalog = yaml.safe_load((AI_HUB / profile["openrewrite"]["catalog_path"]).read_text(encoding="utf-8"))
    return profile, catalog


def test_minimal_library_profile_loads_and_matches_safety_guardrails() -> None:
    loaded = load_migration_profile(AI_HUB, PROFILE_ID)

    assert loaded.ok
    profile = loaded.profile
    schema = json.loads((AI_HUB / profile["schema"]).read_text(encoding="utf-8"))
    jsonschema.validate(profile, schema)

    assert profile["id"] == PROFILE_ID
    assert profile["strategy"] == "read_only_library_jakarta_java17_first"
    assert profile["risk_level"] == "experimental"
    assert profile["production_allowed"] is False
    assert profile["source"]["java"]["allowed_versions"] == ["11"]
    assert profile["source"]["spring_boot"]["allowed_version_prefixes"] == ["unknown", "2.1"]
    assert profile["source"]["build"]["allowed_tools"] == ["maven"]
    assert profile["target"]["java"] == "17"
    assert profile["target"]["spring_boot"] == "3.5.14"
    assert profile["requirements"]["human_approval_required"] is True
    assert profile["requirements"]["baseline_tests_required"] is False
    assert profile["requirements"]["dependency_graph_unavailable_fatal"] is False
    assert profile["openrewrite"]["preview_allowed"] is True
    assert profile["openrewrite"]["apply_allowed"] is False
    assert profile["rules"]["openrewrite_apply_allowed"] is False
    assert profile["rules"]["dry_run_only"] is True
    assert profile["rules"]["original_source_modification_allowed"] is False

    catalog_path = AI_HUB / profile["openrewrite"]["catalog_path"]
    assert catalog_path.is_file()


def test_minimal_library_catalog_uses_only_targeted_recipes_and_dry_run() -> None:
    profile, catalog = _load_profile_and_catalog()

    assert profile["openrewrite"]["recipe_artifacts"] == [
        "org.openrewrite.recipe:rewrite-migrate-java:3.35.0"
    ]
    assert profile["openrewrite"]["active_recipes"] == EXPECTED_RECIPES
    assert catalog["plugin"] == {
        "group_id": "org.openrewrite.maven",
        "artifact_id": "rewrite-maven-plugin",
        "version": "6.40.0",
    }
    assert catalog["recipe_artifacts"] == [
        {
            "group_id": "org.openrewrite.recipe",
            "artifact_id": "rewrite-migrate-java",
            "version": "3.35.0",
        }
    ]
    assert catalog["active_recipes"] == EXPECTED_RECIPES

    forbidden_recipes = {
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5",
        "org.openrewrite.java.migrate.jakarta.JakartaEE10",
    }
    assert forbidden_recipes.isdisjoint(profile["openrewrite"]["active_recipes"])
    assert forbidden_recipes.isdisjoint(catalog["active_recipes"])
    assert all(item["artifact_id"] != "rewrite-spring" for item in catalog["recipe_artifacts"])

    assert catalog["preview_goals"] == ["dryRun"]
    assert catalog["dry_run_only"] is True
    assert catalog["rewrite_run_allowed"] is False
    assert catalog["code_modification_allowed"] is False
    assert {"run", "runNoFork", "rewrite:run", "rewrite:runNoFork"}.issubset(
        set(catalog["forbidden_apply_goals"])
    )
    assert "rewrite-migrate-java recipe artifact may be Moderne Source Available License" in catalog["license_note"]


def test_minimal_library_catalog_documents_servlet_scope_regression_review() -> None:
    profile, catalog = _load_profile_and_catalog()

    review_text = "\n".join(
        [
            *profile["manual_review_rules"],
            *catalog["manual_review_rules"],
            *catalog["notes"],
        ]
    )
    assert "jakarta.servlet-api scope from provided to compile" in review_text
