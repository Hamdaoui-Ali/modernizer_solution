from pathlib import Path

from migration_factory.orchestrator.state import (
    APPROVAL_DECISION_VALUES,
    APPROVAL_STATUS_VALUES,
    CopilotConfigError,
    PHASE_STATUS_VALUES,
    READ_ONLY_ASSESSMENT_MODE,
    apply_copilot_config,
    build_initial_state,
    parse_copilot_config_from_env,
)


def test_initial_state_has_required_read_only_assessment_fields(tmp_path: Path) -> None:
    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
        ai_hub_path=str(tmp_path / "ai-hub"),
        profile_id="java-17",
        thread_id="thread-001",
    )

    assert state["mode"] == READ_ONLY_ASSESSMENT_MODE
    assert state["run_id"] == "run-001"
    assert state["legacy_app_path"] == str(tmp_path / "legacy")
    assert state["modernized_app_path"] == str(tmp_path / "modernized")
    assert state["ai_hub_path"] == str(tmp_path / "ai-hub")
    assert state["profile_id"] == "java-17"
    assert state["thread_id"] == "thread-001"
    assert state["current_unit"] == ""
    assert state["stop_reason"] is None
    assert state["blockers"] == []
    assert state["warnings"] == []
    assert state["errors"] == []
    assert state["artifact_refs"] == {}


def test_initial_state_statuses_and_artifact_flags_are_defaults(tmp_path: Path) -> None:
    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
    )

    assert PHASE_STATUS_VALUES == {"PENDING", "RUNNING", "PASS", "FAIL", "SKIPPED"}
    assert APPROVAL_STATUS_VALUES == {"PENDING", "INTERRUPTED", "COMPLETED", "FAILED"}
    assert state["analysis_status"] == "PENDING"
    assert state["planning_status"] == "PENDING"
    assert state["assessment_status"] == "PENDING"
    assert state["orchestration_status"] == "PENDING"
    assert state["approval_status"] == "PENDING"
    assert state["approval_decision"] is None
    assert state["analysis_artifacts_valid"] is False
    assert state["planning_artifacts_valid"] is False
    assert state["assessment_artifacts_valid"] is False
    assert state["orchestration_artifacts_valid"] is False


def test_approval_decision_values_are_exact() -> None:
    assert APPROVAL_DECISION_VALUES == {"approved", "rejected", "replan_required"}


def test_initial_state_derives_json_safe_path_strings(tmp_path: Path) -> None:
    modernized_app_path = tmp_path / "modernized"
    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(modernized_app_path),
    )

    run_dir = modernized_app_path / ".migration" / "runs" / "run-001"
    assert state["run_dir"] == str(run_dir)
    assert state["analysis_dir"] == str(run_dir / "analysis")
    assert state["planning_dir"] == str(run_dir / "planning")
    assert state["assessment_dir"] == str(run_dir / "assessment")
    assert state["orchestration_dir"] == str(run_dir / "orchestration")
    assert all(isinstance(state[key], str) for key in (
        "run_dir",
        "analysis_dir",
        "planning_dir",
        "assessment_dir",
        "orchestration_dir",
    ))


def test_initial_state_has_no_transformation_status(tmp_path: Path) -> None:
    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
    )

    assert "transformation_status" not in state


def test_initial_state_has_default_copilot_config(tmp_path: Path) -> None:
    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
    )

    assert state["copilot_enabled"] is False
    assert state["copilot_assist_mode"] == "off"
    assert state["copilot_report_enabled"] is False
    assert state["copilot_provider"] == "copilot_cli"
    assert state["copilot_model"] == "gpt-5-mini"
    assert state["copilot_timeout_seconds"] == 300
    assert state["copilot_phase_statuses"] == {}
    assert state["copilot_artifact_refs"] == {}
    assert state["copilot_warnings"] == []
    assert state["copilot_errors"] == []
    assert state["copilot_fallback_used"] is False


def test_copilot_config_supports_env_overrides() -> None:
    config = parse_copilot_config_from_env(
        {
            "AI_MIGRATION_COPILOT_ASSIST": "always",
            "AI_MIGRATION_ENABLE_COPILOT_REPORT": "false",
            "AI_MIGRATION_COPILOT_PROVIDER": "sdk",
            "AI_MIGRATION_COPILOT_MODEL": "gpt-5",
            "AI_MIGRATION_COPILOT_TIMEOUT_SECONDS": "45",
        }
    )

    assert config["copilot_enabled"] is True
    assert config["copilot_assist_mode"] == "always"
    assert config["copilot_report_enabled"] is False
    assert config["copilot_provider"] == "sdk"
    assert config["copilot_model"] == "gpt-5"
    assert config["copilot_timeout_seconds"] == 45


def test_copilot_off_assist_mode_disables_copilot() -> None:
    config = parse_copilot_config_from_env({"AI_MIGRATION_COPILOT_ASSIST": "off"})

    assert config["copilot_enabled"] is False
    assert config["copilot_assist_mode"] == "off"


def test_invalid_copilot_assist_mode_fails_validation() -> None:
    try:
        parse_copilot_config_from_env({"AI_MIGRATION_COPILOT_ASSIST": "sometimes"})
    except CopilotConfigError as exc:
        assert "AI_MIGRATION_COPILOT_ASSIST" in str(exc)
    else:
        raise AssertionError("invalid Copilot assist mode should fail validation")


def test_invalid_copilot_provider_fails_validation() -> None:
    try:
        parse_copilot_config_from_env({"AI_MIGRATION_COPILOT_PROVIDER": "unknown"})
    except CopilotConfigError as exc:
        assert "AI_MIGRATION_COPILOT_PROVIDER" in str(exc)
    else:
        raise AssertionError("invalid Copilot provider should fail validation")


def test_copilot_cli_provider_is_valid() -> None:
    config = parse_copilot_config_from_env({"AI_MIGRATION_COPILOT_PROVIDER": "copilot_cli"})

    assert config["copilot_provider"] == "copilot_cli"


def test_invalid_copilot_timeout_fails_validation() -> None:
    for timeout in ("0", "-1", "not-a-number"):
        try:
            parse_copilot_config_from_env({"AI_MIGRATION_COPILOT_TIMEOUT_SECONDS": timeout})
        except CopilotConfigError as exc:
            assert "AI_MIGRATION_COPILOT_TIMEOUT_SECONDS" in str(exc)
        else:
            raise AssertionError(f"invalid Copilot timeout should fail validation: {timeout}")


def test_copilot_config_does_not_mutate_official_status_or_issue_fields(tmp_path: Path) -> None:
    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(tmp_path / "legacy"),
        modernized_app_path=str(tmp_path / "modernized"),
    )
    state["analysis_status"] = "PASS"
    state["planning_status"] = "FAIL"
    state["assessment_status"] = "SKIPPED"
    state["orchestration_status"] = "RUNNING"
    state["blockers"] = ["deterministic blocker"]
    state["errors"] = ["deterministic error"]
    state["warnings"] = ["deterministic warning"]

    updated = apply_copilot_config(
        state,
        {
            "AI_MIGRATION_COPILOT_ASSIST": "always",
            "AI_MIGRATION_COPILOT_PROVIDER": "deterministic",
            "AI_MIGRATION_COPILOT_TIMEOUT_SECONDS": "60",
        },
    )

    assert updated["analysis_status"] == "PASS"
    assert updated["planning_status"] == "FAIL"
    assert updated["assessment_status"] == "SKIPPED"
    assert updated["orchestration_status"] == "RUNNING"
    assert updated["blockers"] == ["deterministic blocker"]
    assert updated["errors"] == ["deterministic error"]
    assert updated["warnings"] == ["deterministic warning"]
    assert updated["copilot_errors"] == []
    assert updated["copilot_warnings"] == []
