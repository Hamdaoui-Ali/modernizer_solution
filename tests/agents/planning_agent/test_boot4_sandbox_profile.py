from pathlib import Path

import yaml

from migration_factory.agents.planning_agent.profile_reader import load_migration_profile


REPO_ROOT = Path(__file__).resolve().parents[3]
AI_HUB = REPO_ROOT / "modernizer-solution-ai-hub"


def test_boot4_java21_sandbox_profile_loads_with_guardrails() -> None:
    loaded = load_migration_profile(AI_HUB, "springboot-2-java8-to-boot4-java21")

    assert loaded.ok
    profile = loaded.profile
    assert profile["strategy"] == "direct_openrewrite_sandbox"
    assert profile["risk_level"] == "high"
    assert profile["production_allowed"] is False
    assert profile["requires_human_approval"] is True
    assert profile["fallback_profile"] == "springboot-2-to-3-5-to-4-java21"


def test_boot4_catalog_emits_multiple_recipe_artifacts_and_active_recipes() -> None:
    profile = yaml.safe_load(
        (AI_HUB / "profiles" / "springboot-2-java8-to-boot4-java21.yaml").read_text(
            encoding="utf-8"
        )
    )
    catalog = yaml.safe_load((AI_HUB / profile["openrewrite"]["catalog_path"]).read_text(encoding="utf-8"))

    assert catalog["recipe_artifacts"] == [
        {
            "group_id": "org.openrewrite.recipe",
            "artifact_id": "rewrite-migrate-java",
            "version": "3.36.0",
        },
        {
            "group_id": "org.openrewrite.recipe",
            "artifact_id": "rewrite-spring",
            "version": "6.31.0",
        },
    ]
    assert catalog["active_recipes"] == [
        "org.openrewrite.java.migrate.UpgradeToJava21",
        "org.openrewrite.java.spring.boot4.UpgradeSpringBoot_4_0",
    ]


def test_boot4_profile_has_no_production_promotion_controls() -> None:
    profile = yaml.safe_load(
        (AI_HUB / "profiles" / "springboot-2-java8-to-boot4-java21.yaml").read_text(
            encoding="utf-8"
        )
    )

    forbidden_keys = {"create_pr", "deployment", "auto_merge", "production_promotion"}
    assert forbidden_keys.isdisjoint(profile.keys())
    assert profile["openrewrite"]["apply_allowed"] is False
    assert profile["rules"]["openrewrite_apply_allowed"] is False
