import json
from pathlib import Path

import jsonschema
import yaml

from migration_factory.agents.planning_agent.profile_reader import load_migration_profile


REPO_ROOT = Path(__file__).resolve().parents[3]
AI_HUB = REPO_ROOT / "modernizer-solution-ai-hub"
PROFILE_ID = "springboot-2.1-to-3.5-java17-library-experimental"


def test_library_experimental_profile_loads_and_matches_guardrails() -> None:
    loaded = load_migration_profile(AI_HUB, PROFILE_ID)

    assert loaded.ok
    profile = loaded.profile
    schema = json.loads((AI_HUB / profile["schema"]).read_text(encoding="utf-8"))
    jsonschema.validate(profile, schema)

    assert profile["production_allowed"] is False
    assert profile["source"]["java"]["allowed_versions"] == ["11"]
    assert profile["source"]["spring_boot"]["allowed_version_prefixes"] == ["2.1", "unknown"]
    assert profile["target"]["java"] == "17"
    assert profile["target"]["spring_boot"] == "3.5.14"
    assert profile["requirements"]["human_approval_required"] is True
    assert profile["rules"]["human_approval_required"] is True
    assert profile["openrewrite"]["preview_allowed"] is True
    assert profile["openrewrite"]["apply_allowed"] is False
    assert profile["rules"]["openrewrite_preview_allowed"] is True
    assert profile["rules"]["openrewrite_apply_allowed"] is False

    catalog_path = AI_HUB / profile["openrewrite"]["catalog_path"]
    assert catalog_path.is_file()

    assert "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5" in profile["openrewrite"]["active_recipes"]
    assert "org.openrewrite.java.migrate.jakarta.JakartaEE10" in profile["openrewrite"]["active_recipes"]


def test_library_experimental_openrewrite_catalog_is_preview_only() -> None:
    profile = yaml.safe_load(
        (AI_HUB / "profiles" / f"{PROFILE_ID}.yaml").read_text(encoding="utf-8")
    )
    catalog = yaml.safe_load((AI_HUB / profile["openrewrite"]["catalog_path"]).read_text(encoding="utf-8"))

    assert catalog["plugin"] == {
        "group_id": "org.openrewrite.maven",
        "artifact_id": "rewrite-maven-plugin",
        "version": "6.40.0",
    }
    assert catalog["recipe_artifacts"] == [
        {
            "group_id": "org.openrewrite.recipe",
            "artifact_id": "rewrite-spring",
            "version": "6.31.0",
        },
        {
            "group_id": "org.openrewrite.recipe",
            "artifact_id": "rewrite-migrate-java",
            "version": "3.35.0",
        },
    ]
    assert catalog["active_recipes"] == [
        "org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5",
        "org.openrewrite.java.migrate.jakarta.JakartaEE10",
    ]
    assert catalog["preview_goals"] == ["dryRun"]
    assert "rewrite:run" in catalog["forbidden_apply_goals"]
    assert catalog["dry_run_only"] is True
    assert catalog["rewrite_run_allowed"] is False
    assert catalog["code_modification_allowed"] is False
    assert "legal review required before production use" in catalog["license_note"]
