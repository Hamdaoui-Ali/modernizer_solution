"""Regression tests for repair revision semantics."""

from __future__ import annotations

import sqlite3
from pathlib import Path

from migration_factory.control_tower.application.v2_gate_action_service import (
    V2GateActionService,
)
from migration_factory.control_tower.application.v2_phase_gate_service import (
    CreateGateRequest,
    V2PhaseGateService,
)
from migration_factory.control_tower.application.v2_repair_gate_service import (
    V2RepairGateService,
)
from migration_factory.control_tower.infrastructure.sqlite.migrations import (
    apply_pending_migrations,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)


def _connection(tmp_path: Path, name: str) -> sqlite3.Connection:
    conn = sqlite3.connect(
        str(tmp_path / name),
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    apply_pending_migrations(conn)
    return conn


def _setup(tmp_path: Path) -> tuple[V2PhaseGateService, SqlitePhaseGateRepository, SqliteGateDecisionRepository, SqliteArtifactRevisionRepository, V2GateActionService, V2RepairGateService]:
    conn = _connection(tmp_path, "repair_revision.sqlite3")
    gate_repo = SqlitePhaseGateRepository(conn)
    decision_repo = SqliteGateDecisionRepository(conn)
    revision_repo = SqliteArtifactRevisionRepository(conn)
    gate_service = V2PhaseGateService(gate_repo)
    action_service = V2GateActionService(
        gate_repo,
        decision_repo,
        gate_service,
        revision_repo=revision_repo,
    )
    repair_gate_service = V2RepairGateService(
        gate_service=gate_service,
        gate_action_service=action_service,
    )
    return gate_service, gate_repo, decision_repo, revision_repo, action_service, repair_gate_service


def test_request_repair_revision_persists_repair_revision_kind(tmp_path: Path) -> None:
    gate_service, gate_repo, decision_repo, revision_repo, action_service, repair_gate_service = _setup(tmp_path)
    gate_result = gate_service.create_gate(
        CreateGateRequest(
            job_id="job-abc",
            gate_phase="repair_review",
            stage_index=1,
            source_artifact_checksum="sha256:repair",
            source_artifact_refs=("diagnosis:1",),
        )
    )
    assert gate_result.status == "created"

    result = repair_gate_service.request_repair_revision(
        gate_id=gate_result.gate_id,
        job_id="job-abc",
        decided_by="human-1",
        proposal_id="proposal-1",
        user_feedback="Use a narrower patch scope",
    )

    assert result.status == "executed"
    assert result.result_gate_id is not None
    old_gate = gate_repo.get(gate_result.gate_id)
    assert old_gate is not None
    assert old_gate.gate_status == "resolved"
    new_gate = gate_repo.get(result.result_gate_id)
    assert new_gate is not None
    assert new_gate.gate_status == "open"

    revisions = revision_repo.list_by_job_and_stage("job-abc", 1)
    assert revisions
    assert revisions[0].revision_kind == "repair"
    assert "proposal-1" in revisions[0].artifact_refs_json
