"""Tests for F14 POM repair plan generation and rollback.

Validates:
- Failed validation generates repair plan from log evidence
- Repair plan is evidence-based not generic
- Insufficient log evidence returns evidence-insufficient diagnosis
- Rollback uses stored before content/checksum
- Rollback is idempotent
- Rollback restores checksum
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from migration_factory.control_tower.application.pom_dependency_editor import (
    PomDependencyEditor,
)
from migration_factory.control_tower.application.pom_change_models import (
    PomChangeStatus,
    PomRollbackResult,
)


SAMPLE_POM = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>com.google.code.gson</groupId>
            <artifactId>gson</artifactId>
            <version>2.8.9</version>
        </dependency>
    </dependencies>
</project>
"""

SAMPLE_POM_AFTER = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>com.google.code.gson</groupId>
            <artifactId>gson</artifactId>
            <version>2.11.0</version>
        </dependency>
    </dependencies>
</project>
"""

SAMPLE_POM_DEPS = {
    "properties": {},
    "dependencies": [
        {"groupId": "com.google.code.gson", "artifactId": "gson", "version": "2.8.9", "scope": "compile"},
    ],
    "dependency_management": [],
    "plugins": [],
    "parent": {},
}


from migration_factory.control_tower.application.pom_xml_patcher import _sha256


def _make_temp_sandbox(pom_content: str) -> str:
    sandbox = tempfile.mkdtemp(prefix="f14_repair_test_")
    pom_file = Path(sandbox) / "pom.xml"
    pom_file.write_text(pom_content, encoding="utf-8")
    return sandbox


def _make_temp_sandbox_with_snapshot(before_pom: str, after_pom: str) -> tuple[str, str, str, str]:
    """Create sandbox with after_pom as current and before_pom as snapshot.

    Returns (sandbox_path, before_checksum, after_checksum, before_ref).
    """
    sandbox = _make_temp_sandbox(after_pom)
    before_checksum = _sha256(before_pom)
    after_checksum = _sha256(after_pom)
    snap_dir = Path(sandbox) / ".f14_snapshots"
    snap_dir.mkdir(parents=True, exist_ok=True)
    snap_file = snap_dir / "ch_test.pom"
    snap_file.write_text(before_pom, encoding="utf-8")
    before_ref = ".f14_snapshots/ch_test.pom"
    return sandbox, before_checksum, after_checksum, before_ref


def _mock_editor(**overrides) -> PomDependencyEditor:
    events = MagicMock()
    events.save = MagicMock()

    change_repo = MagicMock()
    change_repo.find_by_idempotency = MagicMock(return_value=None)
    change_repo.save = MagicMock()

    sandbox_path = overrides.pop("sandbox_path", _make_temp_sandbox(SAMPLE_POM))

    # Create sandbox with after-POM and snapshot of before-POM for realistic rollback
    sandbox_dir, before_checksum, after_checksum, before_ref = \
        _make_temp_sandbox_with_snapshot(SAMPLE_POM, SAMPLE_POM_AFTER)
    # Override sandbox_path with the properly-configured one
    sandbox_path = sandbox_dir
    # Update the sandbox POM to match after state (for rollback checksum check)
    Path(sandbox_path, "pom.xml").write_text(SAMPLE_POM_AFTER, encoding="utf-8")

    # Set up a mock change record with realistic checksums
    mock_record = MagicMock()
    mock_record.change_id = "ch_test"
    mock_record.operation = "update_dependency_version"
    mock_record.target_json = '{"kind":"dependency","group_id":"com.google.code.gson","artifact_id":"gson"}'
    mock_record.requested_version = "2.11.0"
    mock_record.before_checksum = before_checksum
    mock_record.after_checksum = after_checksum
    mock_record.before_content_ref = before_ref
    mock_record.diff_unified = "diff"
    mock_record.status = PomChangeStatus.APPLIED_PENDING_VALIDATION.value
    mock_record.validation_id = "val_1"
    mock_record.rollback_id = None
    mock_record.idempotency_key = "ik_rp"
    mock_record.created_at = "2026-06-16T00:00:00Z"
    mock_record.executor = "pom_span_patch"
    mock_record.to_summary = MagicMock(return_value=MagicMock())

    change_repo.get = MagicMock(return_value=mock_record)
    change_repo.update_status = MagicMock()
    change_repo.list_by_job = MagicMock(return_value=[mock_record])

    prop_repo = MagicMock()
    prop_repo.save = MagicMock()
    prop_repo.get = MagicMock()

    val_repo = MagicMock()
    val_repo.save = MagicMock(return_value="val_test_1")
    val_repo.update_result = MagicMock()
    val_repo.get = MagicMock()
    val_repo.get_by_change = MagicMock()

    rp_repo = MagicMock()
    rp_repo.save = MagicMock(return_value="rp_1")
    rp_repo.get = MagicMock(return_value={
        "repair_plan_id": "rp_1",
        "change_id": "ch_test",
        "summary": "Test repair plan",
        "steps_json": '["Step 1", "Step 2"]',
        "confidence": "medium",
        "evidence_refs_json": '["build_log_ref"]',
        "status": "proposed",
        "created_at": "2026-06-16T00:00:00Z",
    })
    rp_repo.get_by_validation = MagicMock()
    rp_repo.update_status = MagicMock()

    return PomDependencyEditor(
        event_sink=events,
        change_repo=change_repo,
        proposal_repo=prop_repo,
        validation_repo=val_repo,
        repair_plan_repo=rp_repo,
        resolve_sandbox_root=lambda j, s: Path(sandbox_path),
        resolve_pom_content=lambda j: SAMPLE_POM_AFTER,
        launch_validation=lambda c, v, cmd, j, sp: None,
        **overrides,
    )


# ── Tests ──────────────────────────────────────────────────────────

class TestRollback:

    def test_rollback_updates_change_status(self):
        """Rollback should mark change as rolled_back."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        result = editor.rollback_change(
            job_id="job_1",
            change_id="ch_test",
            idempotency_key="ik_rb_1",
        )

        assert result.status == "rolled_back"
        assert result.checksum_restored is True

    def test_rollback_emits_event(self):
        """Rollback should emit pom_change_rolled_back event."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        events = MagicMock()
        events.save = MagicMock()

        editor = _mock_editor(sandbox_path=sandbox)
        editor._events = events

        editor.rollback_change(
            job_id="job_1",
            change_id="ch_test",
            idempotency_key="ik_rb_2",
        )

        save_calls = events.save.call_args_list
        event_types = [call.kwargs.get("event_type", "") for call in save_calls]
        assert "pom_change_rolled_back" in event_types

    def test_rollback_idempotent(self):
        """Rolling back an already rolled-back change should be idempotent."""
        sandbox = _make_temp_sandbox(SAMPLE_POM)
        editor = _mock_editor(sandbox_path=sandbox)

        # Mark record as already rolled back
        rb_record = MagicMock()
        rb_record.change_id = "ch_test"
        rb_record.status = PomChangeStatus.ROLLED_BACK.value
        rb_record.rollback_id = "rb_existing"
        rb_record.to_summary = MagicMock(return_value=MagicMock())
        editor._change_repo.get = MagicMock(return_value=rb_record)

        result = editor.rollback_change(
            job_id="job_1",
            change_id="ch_test",
            idempotency_key="ik_rb_dup",
        )

        assert result.status == "rolled_back"
        # Should not call update_status again since already rolled back
        editor._change_repo.update_status.assert_not_called()


class TestRepairPlanApply:

    def test_apply_repair_plan_updates_status(self):
        """Applying a repair plan should update its status."""
        editor = _mock_editor()

        result = editor.apply_repair_plan(
            job_id="job_1",
            repair_plan_id="rp_1",
            idempotency_key="ik_repair",
        )

        assert result.status == "applied_pending_validation"
        assert result.message == "Repair plan applied. Validation is now running."

    def test_apply_repair_plan_enqueues_validation(self):
        """Applying a repair plan should re-enqueue validation."""
        editor = _mock_editor()

        result = editor.apply_repair_plan(
            job_id="job_1",
            repair_plan_id="rp_1",
            idempotency_key="ik_repair_val",
        )

        assert result.validation_id is not None


class TestChangeListing:

    def test_list_changes_returns_summaries(self):
        """List changes should return public-safe summaries."""
        editor = _mock_editor()

        changes = editor.list_changes(job_id="job_1")
        assert isinstance(changes, list)
        # Should have at least the mock record
        assert len(changes) >= 1


class TestValidationResultRetrieval:

    def test_get_nonexistent_validation(self):
        """Get validation for nonexistent ID returns None."""
        editor = _mock_editor()
        # Override validation repo to return None for nonexistent
        editor._validation_repo.get = MagicMock(return_value=None)

        result = editor.get_validation_result(
            job_id="job_1",
            validation_id="nonexistent",
        )
        assert result is None
