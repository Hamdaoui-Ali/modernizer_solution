from pathlib import Path
import json

import jsonschema
import yaml

from migration_factory.agents.analysis_agent.analysis_agent.rewrite_catalog_loader import load_rewrite_catalog
from migration_factory.agents.planning_agent.artifact_reader import LoadedAnalysisArtifacts
from migration_factory.agents.planning_agent.profile_compatibility import validate_profile_compatibility
from migration_factory.agents.planning_agent.profile_reader import load_migration_profile
from migration_factory.agents.planning_agent.staged_profiles import plan_boot_216_to_boot35_stages
from migration_factory.agents.planning_agent.unit_builder import build_migration_units
from migration_factory.agents.planning_agent.output_validator import ALLOWED_UNIT_ORDERS


REPO_ROOT = Path(__file__).resolve().parents[3]
AI_HUB = REPO_ROOT / "modernizer-solution-ai-hub"


class DummyContext:
    def __init__(self, legacy: Path, modernized: Path, profile: str):
        self.legacy_app_path = str(legacy)
        self.modernized_app_path = str(modernized)
        self.ai_hub_path = str(AI_HUB)
        self.profile = profile


def test_boot216_stage_a_profile_is_java11_to_boot27_compatible() -> None:
    loaded = load_migration_profile(AI_HUB, "springboot-2.1.6-to-2.7-java11")
    schema = json.loads((AI_HUB / loaded.profile["schema"]).read_text(encoding="utf-8"))

    assert loaded.ok
    jsonschema.validate(loaded.profile, schema)
    result = validate_profile_compatibility(
        LoadedAnalysisArtifacts(
            required={
                "analysis_report.json": {
                    "source_stack": {
                        "java": "11",
                        "spring_boot": "2.1.6.RELEASE",
                        "build_tool": "maven",
                    }
                },
                "dependency_graph.json": {},
                "test_inventory.json": {},
            }
        ),
        loaded,
    )

    assert result.ok
    assert result.target_stack.java == "11"
    assert result.target_stack.spring_boot == "2.7"


def test_staged_profile_planning_orders_required_and_optional_stages() -> None:
    stages = plan_boot_216_to_boot35_stages(AI_HUB, include_java21_validation=True)

    assert [stage.id for stage in stages] == [
        "springboot-2.1.6-to-2.7-java11",
        "springboot-2.7-to-3.5-java17",
        "springboot-3.5-java17-to-java21",
    ]
    assert [stage.stage for stage in stages] == ["A", "B", "C"]
    assert [stage.required for stage in stages] == [True, True, False]
    assert stages[2].target["java"] == "21"
    assert stages[2].target["spring_boot"].startswith("3.5")


def test_stage_a_units_do_not_jump_to_java17_or_jakarta() -> None:
    profile = yaml.safe_load(
        (AI_HUB / "profiles" / "springboot-2.1.6-to-2.7-java11.yaml").read_text(encoding="utf-8")
    )
    units = build_migration_units(profile)

    assert [unit.id for unit in units] == [
        "baseline",
        "spring-boot-2-7",
        "dependency-cleanup",
        "existing-test-migration",
    ]
    assert all(unit.id != "jakarta" for unit in units)
    assert all(unit.id != "java-17" for unit in units)
    assert tuple(unit.id for unit in units) in ALLOWED_UNIT_ORDERS


def test_java21_stage_is_validation_only_and_preview_only(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()

    profile = yaml.safe_load(
        (AI_HUB / "profiles" / "springboot-3.5-java17-to-java21.yaml").read_text(encoding="utf-8")
    )
    units = build_migration_units(profile)
    catalog = load_rewrite_catalog(DummyContext(legacy, modernized, profile["id"]))

    assert [unit.id for unit in units] == ["baseline", "java-21-runtime-validation"]
    assert tuple(unit.id for unit in units) in ALLOWED_UNIT_ORDERS
    assert units[0].writes_source is False
    assert units[1].writes_source is True
    assert profile["openrewrite"]["apply_allowed"] is True
    assert profile["openrewrite"]["apply_goal"] == "runNoFork"
    assert profile["target_jdk_home_env"] == "JAVA21_HOME"
    assert catalog["status"] == "USED"
    assert catalog["openrewrite"]["active_recipes"] == ["org.openrewrite.java.migrate.UpgradeToJava21"]
    assert catalog["openrewrite"]["plugin"] == "org.openrewrite.maven:rewrite-maven-plugin:6.40.0"
    assert catalog["openrewrite"]["recipe_artifacts"] == [
        "org.openrewrite.recipe:rewrite-migrate-java:3.35.0"
    ]


def test_stage_b_boot_356_units_match_allowed_order() -> None:
    profile = yaml.safe_load(
        (AI_HUB / "profiles" / "springboot-2.7-to-3.5-java17.yaml").read_text(encoding="utf-8")
    )

    units = build_migration_units(profile)

    assert [unit.id for unit in units] == [
        "baseline",
        "java-17",
        "spring-boot-3-5",
        "jakarta",
        "dependency-cleanup",
        "existing-test-migration",
    ]
    assert tuple(unit.id for unit in units) in ALLOWED_UNIT_ORDERS
    assert profile["openrewrite"]["post_apply_patches"] == [
        {
            "type": "spring_boot_version",
            "old_value": "3.5.14",
            "new_value": "3.5.6",
        }
    ]


def test_stage_a_catalog_does_not_target_boot4(tmp_path: Path) -> None:
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    legacy.mkdir()
    modernized.mkdir()

    result = load_rewrite_catalog(DummyContext(legacy, modernized, "springboot-2.1.6-to-2.7-java11"))

    assert result["status"] == "USED"
    assert result["openrewrite"]["active_recipes"] == [
        "org.openrewrite.java.spring.boot2.UpgradeSpringBoot_2_7"
    ]
    assert all("boot4" not in recipe.lower() for recipe in result["openrewrite"]["active_recipes"])
