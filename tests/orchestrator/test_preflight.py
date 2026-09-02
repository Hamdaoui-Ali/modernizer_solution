from pathlib import Path

import pytest

from migration_factory.orchestrator.preflight import (
    PreflightError,
    build_langgraph_config,
    validate_preflight,
)
from migration_factory.orchestrator.state import (
    FULL_SANDBOX_MIGRATION_MODE,
    build_initial_state,
)


def _write_profile(ai_hub_path: Path, profile_id: str = "java17") -> None:
    profiles_dir = ai_hub_path / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / f"{profile_id}.yaml").write_text(
        f"id: {profile_id}\n", encoding="utf-8"
    )


def _valid_state(tmp_path: Path):
    legacy_app_path = tmp_path / "legacy"
    modernized_app_path = tmp_path / "modernized"
    ai_hub_path = tmp_path / "ai-hub"
    legacy_app_path.mkdir()
    ai_hub_path.mkdir()
    _write_profile(ai_hub_path)

    return build_initial_state(
        run_id="run-001",
        legacy_app_path=str(legacy_app_path),
        modernized_app_path=str(modernized_app_path),
        ai_hub_path=str(ai_hub_path),
        profile_id="java17",
    )


def test_build_langgraph_config_uses_run_id_as_thread_id() -> None:
    assert build_langgraph_config("run-001") == {
        "configurable": {"thread_id": "run-001"}
    }


def test_preflight_rejects_empty_run_id(tmp_path: Path) -> None:
    state = _valid_state(tmp_path)
    state["run_id"] = ""

    with pytest.raises(PreflightError, match="run_id is required"):
        validate_preflight(state, build_langgraph_config("run-001"))


def test_preflight_rejects_invalid_mode(tmp_path: Path) -> None:
    state = _valid_state(tmp_path)
    state["mode"] = "transform"

    with pytest.raises(PreflightError, match="mode must be read_only_assessment"):
        validate_preflight(state, build_langgraph_config(state["run_id"]))


def test_preflight_rejects_missing_legacy_path(tmp_path: Path) -> None:
    state = _valid_state(tmp_path)
    state["legacy_app_path"] = str(tmp_path / "missing-legacy")

    with pytest.raises(PreflightError, match="legacy_app_path not found"):
        validate_preflight(state, build_langgraph_config(state["run_id"]))


def test_preflight_rejects_missing_ai_hub_path(tmp_path: Path) -> None:
    state = _valid_state(tmp_path)
    state["ai_hub_path"] = str(tmp_path / "missing-ai-hub")

    with pytest.raises(PreflightError, match="ai_hub_path not found"):
        validate_preflight(state, build_langgraph_config(state["run_id"]))


def test_preflight_rejects_missing_profile(tmp_path: Path) -> None:
    state = _valid_state(tmp_path)
    state["profile_id"] = "missing"

    with pytest.raises(PreflightError, match="AI Hub profile not found"):
        validate_preflight(state, build_langgraph_config(state["run_id"]))


@pytest.mark.parametrize(
    "config",
    [
        {},
        {"configurable": {}},
        {"configurable": {"thread_id": "other-run"}},
    ],
)
def test_preflight_rejects_missing_or_mismatched_thread_id(
    tmp_path: Path, config: dict
) -> None:
    state = _valid_state(tmp_path)

    with pytest.raises(PreflightError, match="thread_id must match run_id"):
        validate_preflight(state, config)


def test_preflight_accepts_valid_input_and_creates_modernized_path(
    tmp_path: Path,
) -> None:
    state = _valid_state(tmp_path)
    modernized_app_path = Path(state["modernized_app_path"])
    assert not modernized_app_path.exists()

    validate_preflight(state, build_langgraph_config(state["run_id"]))

    assert modernized_app_path.is_dir()


def test_preflight_rejects_full_sandbox_for_dry_run_only_profile(tmp_path: Path) -> None:
    legacy_app_path = tmp_path / "legacy"
    modernized_app_path = tmp_path / "modernized"
    ai_hub_path = tmp_path / "ai-hub"
    legacy_app_path.mkdir()
    profiles_dir = ai_hub_path / "profiles"
    profiles_dir.mkdir(parents=True, exist_ok=True)
    (profiles_dir / "java21.yaml").write_text(
        "\n".join(
            [
                "id: java21",
                "dry_run_only: true",
                "rules:",
                "  dry_run_only: true",
                "openrewrite:",
                "  apply_allowed: false",
                "",
            ]
        ),
        encoding="utf-8",
    )

    state = build_initial_state(
        run_id="run-001",
        legacy_app_path=str(legacy_app_path),
        modernized_app_path=str(modernized_app_path),
        ai_hub_path=str(ai_hub_path),
        profile_id="java21",
        mode=FULL_SANDBOX_MIGRATION_MODE,
    )

    with pytest.raises(
        PreflightError,
        match="profile java21 does not support mode full_sandbox_migration; use read_only_assessment instead",
    ):
        validate_preflight(state, build_langgraph_config(state["run_id"]))
