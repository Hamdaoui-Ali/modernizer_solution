from migration_factory.agents.planning_agent.unit_builder import build_migration_units


def test_build_migration_units_has_deterministic_ids_in_exact_order() -> None:
    units = build_migration_units()
    assert [unit.id for unit in units] == [
        "baseline",
        "java-17",
        "spring-boot-3-5-14",
        "jakarta",
        "dependency-cleanup",
        "existing-test-migration",
    ]


def test_build_migration_units_for_boot4_java21_profile() -> None:
    units = build_migration_units(
        {"target": {"java": "21", "spring_boot": "4.0.0", "build": "maven"}}
    )

    assert [unit.id for unit in units] == [
        "baseline",
        "java-21",
        "spring-boot-4-0",
        "jakarta",
        "dependency-cleanup",
        "existing-test-migration",
    ]
    assert units[1].goal == "Upgrade project runtime and build configuration to Java 21."
    assert units[2].goal == "Upgrade Spring Boot dependencies and plugins to 4.0."


def test_each_unit_has_required_fields_and_assist_policy_separate_from_tools() -> None:
    units = build_migration_units()

    for unit in units:
        assert isinstance(unit.id, str) and unit.id
        assert isinstance(unit.goal, str) and unit.goal
        assert isinstance(unit.writes_source, bool)
        assert isinstance(unit.tools, tuple) and unit.tools
        assert all(isinstance(tool, str) and tool for tool in unit.tools)
        assert isinstance(unit.validation, tuple) and unit.validation
        assert isinstance(unit.expected_artifacts, tuple) and unit.expected_artifacts
        assert isinstance(unit.rollback_strategy, str) and unit.rollback_strategy
        assert isinstance(unit.blocking_gate, str) and unit.blocking_gate
        assert unit.required in {"yes", "auto"}

        # assist policy is a first-class field on the unit, not embedded in tools.
        assert unit.assist_policy is not None


def test_existing_test_migration_unit_is_auto_required() -> None:
    units = build_migration_units()
    existing_test_unit = next(unit for unit in units if unit.id == "existing-test-migration")
    assert existing_test_unit.required == "auto"


def test_unit_tools_exclude_copilot_llm_and_model_names() -> None:
    units = build_migration_units()
    banned_tokens = {
        "copilot",
        "llm",
        "model",
        "gpt",
        "claude",
        "gemini",
        "llama",
        "mistral",
    }

    for unit in units:
        for tool in unit.tools:
            lower_tool = tool.lower()
            assert not any(token in lower_tool for token in banned_tokens)
