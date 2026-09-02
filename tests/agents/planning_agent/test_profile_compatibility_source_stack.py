from migration_factory.agents.planning_agent.artifact_reader import LoadedAnalysisArtifacts
from migration_factory.agents.planning_agent.profile_compatibility import (
    validate_profile_compatibility,
)
from migration_factory.agents.planning_agent.profile_reader import LoadedMigrationProfile


def test_real_analysis_source_stack_shape_is_used_for_planning() -> None:
    artifacts = LoadedAnalysisArtifacts(
        required={
            "analysis_report.json": {
                "source_stack": {
                    "java": "11",
                    "spring_boot": "2.7.18",
                },
            },
            "dependency_graph.json": {
                "available": False,
                "warning": "Maven dependency tree unavailable.",
            },
            "test_inventory.json": {},
        },
    )
    profile = LoadedMigrationProfile(
        profile={
            "source": {},
            "target": {
                "java": "17",
                "spring_boot": "3.5.14",
                "build": "maven",
            },
            "rules": {},
        }
    )

    result = validate_profile_compatibility(artifacts, profile)

    assert result.ok
    assert result.source_stack.build_tool == "maven"
    assert result.source_stack.java == "11"
    assert result.source_stack.spring_boot == "2.7"
    assert "Source build tool unknown from analysis artifacts." not in result.warnings
    assert "Source Java version missing or unknown in analysis artifacts." not in result.warnings
    assert "Source Spring Boot version missing or unknown in analysis artifacts." not in result.warnings


def test_boot4_java21_profile_accepts_java8_boot2_and_warns() -> None:
    artifacts = LoadedAnalysisArtifacts(
        required={
            "analysis_report.json": {
                "source_stack": {
                    "java": "8",
                    "spring_boot": "2.7.18",
                    "build_tool": "maven",
                },
            },
            "dependency_graph.json": {},
            "test_inventory.json": {},
        },
    )
    profile = LoadedMigrationProfile(
        profile={
            "source": {
                "java": {"allowed_versions": ["8"]},
                "spring_boot": {"allowed_version_prefixes": ["2"]},
                "build": {"allowed_tools": ["maven"]},
            },
            "target": {
                "java": "21",
                "spring_boot": "4.0.0",
                "spring_framework": "7.x",
                "build": "maven",
            },
            "fallback_profile": "springboot-2-to-3-5-to-4-java21",
            "rules": {},
        }
    )

    result = validate_profile_compatibility(artifacts, profile)

    assert result.ok
    assert result.target_stack.java == "21"
    assert result.target_stack.spring_boot == "4.0"
    assert result.target_stack.spring_framework == "7.x"
    assert any("Spring Framework 7" in warning for warning in result.warnings)
    assert any("Fallback profile" in warning for warning in result.warnings)
