from pathlib import Path

import yaml

from migration_factory.agents.planning_agent.profile_reader import load_migration_profile


REPO_ROOT = Path(__file__).resolve().parents[3]
AI_HUB = REPO_ROOT / "modernizer-solution-ai-hub"


def test_boot4_stage_profile_loads_with_expected_target_and_guardrails() -> None:
    loaded = load_migration_profile(AI_HUB, "springboot-3.5-java21-to-4.0-java21")
    assert loaded.ok
    profile = loaded.profile
    assert profile["stage"] == "D"
    assert profile["stage_order"] == 4
    assert profile["risk_level"] == "high"
    assert profile["production_allowed"] is False
    assert profile["rules"]["human_approval_required"] is True
    assert profile["rules"]["openrewrite_apply_allowed"] is True
    assert profile["source"]["java"]["allowed_versions"] == ["21"]
    assert profile["source"]["spring_boot"]["allowed_version_prefixes"] == ["3.5"]
    assert profile["target"]["java"] == "21"
    assert profile["target"]["spring_boot"] == "4.0.0"


def test_boot4_stage_catalog_uses_expected_plugin_and_recipe() -> None:
    profile = yaml.safe_load(
        (AI_HUB / "profiles" / "springboot-3.5-java21-to-4.0-java21.yaml").read_text(
            encoding="utf-8"
        )
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
        }
    ]
    assert catalog["active_recipes"] == [
        "org.openrewrite.java.spring.boot4.UpgradeSpringBoot_4_0"
    ]


def test_boot4_stage_profile_generates_boot_only_unit_order_when_java_stays_21() -> None:
    profile = yaml.safe_load(
        (AI_HUB / "profiles" / "springboot-3.5-java21-to-4.0-java21.yaml").read_text(
            encoding="utf-8"
        )
    )

    from migration_factory.agents.planning_agent.unit_builder import build_migration_units

    units = build_migration_units(profile)

    assert [unit.id for unit in units] == [
        "baseline",
        "spring-boot-4-0",
        "jakarta",
        "dependency-cleanup",
        "existing-test-migration",
    ]
