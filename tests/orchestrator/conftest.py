from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import pytest
from langgraph.checkpoint.memory import InMemorySaver

from migration_factory.orchestrator.artifact_validation import ArtifactValidationResult
from migration_factory.orchestrator.phase_services import PhaseServices
from migration_factory.orchestrator.state import MigrationState, build_initial_state


@pytest.fixture
def run_id(request: pytest.FixtureRequest) -> str:
    return f"{request.node.name}-{uuid4().hex[:8]}"


@pytest.fixture
def thread_id(run_id: str) -> str:
    return run_id


@pytest.fixture
def legacy_app_path(tmp_path: Path) -> Path:
    path = tmp_path / "legacy"
    (path / "src" / "main" / "java" / "example").mkdir(parents=True)
    (path / "src" / "main" / "java" / "example" / "App.java").write_text(
        "package example;\nclass App {}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def modernized_app_path(tmp_path: Path) -> Path:
    path = tmp_path / "modernized"
    (path / "src" / "main" / "java" / "example").mkdir(parents=True)
    (path / "src" / "main" / "java" / "example" / "Existing.java").write_text(
        "package example;\nclass Existing {}\n",
        encoding="utf-8",
    )
    return path


@pytest.fixture
def ai_hub_path(tmp_path: Path) -> Path:
    path = tmp_path / "ai-hub"
    profiles_dir = path / "profiles"
    profiles_dir.mkdir(parents=True)
    (profiles_dir / "java17.yaml").write_text("id: java17\n", encoding="utf-8")
    return path


@pytest.fixture
def initial_state(
    run_id: str,
    thread_id: str,
    legacy_app_path: Path,
    modernized_app_path: Path,
    ai_hub_path: Path,
) -> MigrationState:
    return build_initial_state(
        run_id=run_id,
        legacy_app_path=str(legacy_app_path),
        modernized_app_path=str(modernized_app_path),
        ai_hub_path=str(ai_hub_path),
        profile_id="java17",
        thread_id=thread_id,
    )


@pytest.fixture
def phase_calls() -> list[str]:
    return []


@pytest.fixture
def fake_successful_phase_services(phase_calls: list[str]) -> PhaseServices:
    def run_analysis_phase(state: MigrationState) -> MigrationState:
        phase_calls.append("analysis")
        return {"analysis_status": "PASS", "artifact_refs": {"analysis": "analysis.json"}}

    def run_planning_phase(state: MigrationState) -> MigrationState:
        phase_calls.append("planning")
        return {"planning_status": "PASS", "artifact_refs": {"planning": "planning.yaml"}}

    def run_assessment_phase(state: MigrationState) -> MigrationState:
        phase_calls.append("assessment")
        return {"assessment_status": "PASS", "artifact_refs": {"assessment": "assessment.json"}}

    return PhaseServices(
        run_analysis_phase=run_analysis_phase,
        run_planning_phase=run_planning_phase,
        run_assessment_phase=run_assessment_phase,
    )


@pytest.fixture
def failing_phase_services(phase_calls: list[str]) -> Callable[[str], PhaseServices]:
    def build(failing_phase: str) -> PhaseServices:
        def run_analysis_phase(state: MigrationState) -> MigrationState:
            phase_calls.append("analysis")
            return {"analysis_status": "FAIL" if failing_phase == "analysis" else "PASS"}

        def run_planning_phase(state: MigrationState) -> MigrationState:
            phase_calls.append("planning")
            return {"planning_status": "FAIL" if failing_phase == "planning" else "PASS"}

        def run_assessment_phase(state: MigrationState) -> MigrationState:
            phase_calls.append("assessment")
            return {"assessment_status": "FAIL" if failing_phase == "assessment" else "PASS"}

        return PhaseServices(
            run_analysis_phase=run_analysis_phase,
            run_planning_phase=run_planning_phase,
            run_assessment_phase=run_assessment_phase,
        )

    return build


@pytest.fixture
def valid_artifact_result() -> ArtifactValidationResult:
    return ArtifactValidationResult(
        valid=True,
        artifact_refs={"validated": "artifact.json"},
        blockers=[],
        warnings=[],
    )


@pytest.fixture
def invalid_artifact_result() -> ArtifactValidationResult:
    return ArtifactValidationResult(
        valid=False,
        artifact_refs={},
        blockers=["invalid artifacts"],
        warnings=[],
    )


@pytest.fixture
def fresh_checkpointer() -> InMemorySaver:
    return InMemorySaver()
