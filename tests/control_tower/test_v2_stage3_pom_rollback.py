"""Tests for F14 POM rollback with real content restoration.

Validates:
- Rollback restores actual pom.xml file content
- Rollback verifies restored checksum
- Rollback refuses if current checksum does not match after_checksum
- Rollback is idempotent for already-rolled-back changes
- Rollback event emitted only after real restore
- Public rollback response has no raw sandbox paths
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from migration_factory.control_tower.application.pom_dependency_editor import (
    PomDependencyEditor,
    _sha256,
)
from migration_factory.control_tower.application.pom_change_models import (
    PomChangeStatus,
    PomRollbackResult,
)


# ── Sample POM ─────────────────────────────────────────────────────

SAMPLE_POM_BEFORE = """<?xml version="1.0" encoding="UTF-8"?>
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

SAMPLE_POM_DIFFERENT = """<?xml version="1.0" encoding="UTF-8"?>
<project>
    <dependencies>
        <dependency>
            <groupId>com.google.code.gson</groupId>
            <artifactId>gson</artifactId>
            <version>2.10.0</version>
        </dependency>
    </dependencies>
</project>
"""


# ── Helpers ────────────────────────────────────────────────────────

def _make_temp_sandbox(pom_content: str) -> str:
    sandbox = tempfile.mkdtemp(prefix="f14_rollback_test_")
    pom_file = Path(sandbox) / "pom.xml"
    pom_file.write_text(pom_content, encoding="utf-8")
    return sandbox


def _build_editor_with_sandbox(
    sandbox_path: str,
    change_record=None,
    after_pom: str = SAMPLE_POM_AFTER,
) -> PomDependencyEditor:
    """Build an editor with a real sandbox, snapshot, and mock repos."""

    # Resolve sandbox
    def resolve_sandbox(job_id: str, stage: int) -> Path | None:
        return Path(sandbox_path)

    # Resolve POM content from sandbox
    def resolve_pom(job_id: str) -> str:
        pom_file = Path(sandbox_path) / "pom.xml"
        if pom_file.exists():
            return pom_file.read_text(encoding="utf-8")
        return ""

    events = MagicMock()
    events.save = MagicMock()

    change_repo = MagicMock()
    change_repo.get = MagicMock(return_value=change_record)
    change_repo.update_status = MagicMock()
    change_repo.find_by_idempotency = MagicMock(return_value=None)

    proposal_repo = MagicMock()
    validation_repo = MagicMock()
    repair_plan_repo = MagicMock()

    return PomDependencyEditor(
        event_sink=events,
        change_repo=change_repo,
        proposal_repo=proposal_repo,
        validation_repo=validation_repo,
        repair_plan_repo=repair_plan_repo,
        resolve_sandbox_root=resolve_sandbox,
        resolve_pom_content=resolve_pom,
    )


def _make_change_record(
    change_id: str = "ch_test_rb",
    status: str = PomChangeStatus.APPLIED_PENDING_VALIDATION.value,
    before_content_ref: str = "",
    before_checksum: str = "",
    after_checksum: str = "",
) -> MagicMock:
    record = MagicMock()
    record.change_id = change_id
    record.operation = "update_dependency_version"
    record.target_json = '{"kind":"dependency","group_id":"com.google.code.gson","artifact_id":"gson"}'
    record.requested_version = "2.11.0"
    record.before_content_ref = before_content_ref
    record.before_checksum = before_checksum
    record.after_checksum = after_checksum
    record.diff_unified = "diff"
    record.status = status
    record.validation_id = "val_1"
    record.rollback_id = None
    record.idempotency_key = "ik_rb_test"
    record.created_at = "2026-06-16T00:00:00Z"
    record.executor = "pom_span_patch"
    record.to_summary = MagicMock(return_value=MagicMock())
    return record


# ── Tests ──────────────────────────────────────────────────────────

class TestRollbackContentRestoration:

    def test_rollback_restores_actual_pom_content(self):
        """Rollback must write before-content back to the sandbox pom.xml."""
        sandbox = _make_temp_sandbox(SAMPLE_POM_AFTER)
        before_checksum = _sha256(SAMPLE_POM_BEFORE)
        after_checksum = _sha256(SAMPLE_POM_AFTER)

        # Save before-content snapshot in sandbox
        snap_dir = Path(sandbox) / ".f14_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        snap_file = snap_dir / "ch_test_rb.pom"
        snap_file.write_text(SAMPLE_POM_BEFORE, encoding="utf-8")
        before_ref = ".f14_snapshots/ch_test_rb.pom"

        record = _make_change_record(
            change_id="ch_test_rb",
            before_content_ref=before_ref,
            before_checksum=before_checksum,
            after_checksum=after_checksum,
        )

        editor = _build_editor_with_sandbox(sandbox, change_record=record, after_pom=SAMPLE_POM_AFTER)

        result = editor.rollback_change(
            job_id="job_1",
            change_id="ch_test_rb",
            idempotency_key="ik_rb_restore",
        )

        assert result.status == "rolled_back"
        assert result.checksum_restored is True

        # Verify pom.xml was actually restored
        pom_file = Path(sandbox) / "pom.xml"
        restored_content = pom_file.read_text()
        assert "2.8.9" in restored_content
        assert "2.11.0" not in restored_content
        assert _sha256(restored_content) == before_checksum

    def test_rollback_verifies_restored_checksum(self):
        """Rollback must verify the restored content matches before_checksum."""
        sandbox = _make_temp_sandbox(SAMPLE_POM_AFTER)
        before_checksum = _sha256(SAMPLE_POM_BEFORE)
        after_checksum = _sha256(SAMPLE_POM_AFTER)

        # Save snapshot
        snap_dir = Path(sandbox) / ".f14_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "ch_test_rb2.pom").write_text(SAMPLE_POM_BEFORE, encoding="utf-8")
        before_ref = ".f14_snapshots/ch_test_rb2.pom"

        record = _make_change_record(
            change_id="ch_test_rb2",
            before_content_ref=before_ref,
            before_checksum=before_checksum,
            after_checksum=after_checksum,
        )

        editor = _build_editor_with_sandbox(sandbox, change_record=record)

        result = editor.rollback_change(
            job_id="job_1",
            change_id="ch_test_rb2",
            idempotency_key="ik_rb_verify",
        )

        assert result.checksum_restored is True
        # The checksum_restored flag is only True when actual file was restored
        pom_file = Path(sandbox) / "pom.xml"
        restored_checksum = _sha256(pom_file.read_text())
        assert restored_checksum == before_checksum

    def test_rollback_refuses_when_checksum_differs(self):
        """Rollback must refuse if current checksum does not match after_checksum."""
        sandbox = _make_temp_sandbox(SAMPLE_POM_DIFFERENT)  # Different from SAMPLE_POM_AFTER
        before_checksum = _sha256(SAMPLE_POM_BEFORE)
        after_checksum = _sha256(SAMPLE_POM_AFTER)  # Expected checksum

        # Save snapshot
        snap_dir = Path(sandbox) / ".f14_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "ch_test_rb3.pom").write_text(SAMPLE_POM_BEFORE, encoding="utf-8")
        before_ref = ".f14_snapshots/ch_test_rb3.pom"

        record = _make_change_record(
            change_id="ch_test_rb3",
            before_content_ref=before_ref,
            before_checksum=before_checksum,
            after_checksum=after_checksum,
        )

        editor = _build_editor_with_sandbox(sandbox, change_record=record)

        result = editor.rollback_change(
            job_id="job_1",
            change_id="ch_test_rb3",
            idempotency_key="ik_rb_conflict",
        )

        # Should refuse — current checksum does not match expected after_checksum
        assert result.checksum_restored is False
        assert result.status in ("checksum_conflict", "error")

    def test_rollback_idempotent_when_already_rolled_back(self):
        """Rollback must be idempotent for already-rolled-back changes."""
        sandbox = _make_temp_sandbox(SAMPLE_POM_AFTER)
        before_checksum = _sha256(SAMPLE_POM_BEFORE)
        after_checksum = _sha256(SAMPLE_POM_AFTER)

        # Save snapshot
        snap_dir = Path(sandbox) / ".f14_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "ch_test_rb4.pom").write_text(SAMPLE_POM_BEFORE, encoding="utf-8")

        record = _make_change_record(
            change_id="ch_test_rb4",
            status=PomChangeStatus.ROLLED_BACK.value,
            before_content_ref=".f14_snapshots/ch_test_rb4.pom",
            before_checksum=before_checksum,
            after_checksum=after_checksum,
        )
        record.rollback_id = "rb_existing"

        editor = _build_editor_with_sandbox(sandbox, change_record=record)

        result = editor.rollback_change(
            job_id="job_1",
            change_id="ch_test_rb4",
            idempotency_key="ik_rb_idempotent",
        )

        assert result.status == "rolled_back"
        assert result.checksum_restored is True
        # Should NOT call update_status again
        editor._change_repo.update_status.assert_not_called()

    def test_rollback_emits_event_only_after_real_restore(self):
        """Rollback must only emit event after actual file restoration."""
        sandbox = _make_temp_sandbox(SAMPLE_POM_AFTER)
        before_checksum = _sha256(SAMPLE_POM_BEFORE)
        after_checksum = _sha256(SAMPLE_POM_AFTER)

        # Save snapshot
        snap_dir = Path(sandbox) / ".f14_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "ch_test_rb5.pom").write_text(SAMPLE_POM_BEFORE, encoding="utf-8")

        record = _make_change_record(
            change_id="ch_test_rb5",
            before_content_ref=".f14_snapshots/ch_test_rb5.pom",
            before_checksum=before_checksum,
            after_checksum=after_checksum,
        )

        events = MagicMock()
        events.save = MagicMock()

        editor = _build_editor_with_sandbox(sandbox, change_record=record)
        editor._events = events

        editor.rollback_change(
            job_id="job_1",
            change_id="ch_test_rb5",
            idempotency_key="ik_rb_event",
        )

        # Verify event was emitted
        save_calls = events.save.call_args_list
        event_types = [call.kwargs.get("event_type", "") for call in save_calls]
        assert "pom_change_rolled_back" in event_types

        # Verify checksum_restored is True in the event payload
        for call in save_calls:
            if call.kwargs.get("event_type") == "pom_change_rolled_back":
                payload = call.kwargs.get("payload", {})
                assert payload.get("checksum_restored") is True

    def test_public_rollback_response_no_raw_paths(self):
        """Rollback result must not expose raw sandbox paths."""
        sandbox = _make_temp_sandbox(SAMPLE_POM_AFTER)
        before_checksum = _sha256(SAMPLE_POM_BEFORE)
        after_checksum = _sha256(SAMPLE_POM_AFTER)

        snap_dir = Path(sandbox) / ".f14_snapshots"
        snap_dir.mkdir(parents=True, exist_ok=True)
        (snap_dir / "ch_test_rb6.pom").write_text(SAMPLE_POM_BEFORE, encoding="utf-8")

        record = _make_change_record(
            change_id="ch_test_rb6",
            before_content_ref=".f14_snapshots/ch_test_rb6.pom",
            before_checksum=before_checksum,
            after_checksum=after_checksum,
        )

        editor = _build_editor_with_sandbox(sandbox, change_record=record)

        result = editor.rollback_change(
            job_id="job_1",
            change_id="ch_test_rb6",
            idempotency_key="ik_rb_public",
        )

        public = result.to_public_dict()
        # No absolute paths
        for key, value in public.items():
            if isinstance(value, str):
                assert sandbox not in value, f"Sandbox path leaked in key '{key}': {value}"
                assert "/tmp/" not in value, f"Temp path leaked in key '{key}': {value}"

    def test_rollback_no_change_repo_returns_error(self):
        """Rollback without change repo returns error result."""
        editor = PomDependencyEditor(
            change_repo=None,
            resolve_sandbox_root=lambda j, s: None,
        )
        result = editor.rollback_change(
            job_id="job_1",
            change_id="ch_test",
            idempotency_key="ik_rb",
        )
        assert result.status == "error"
        assert result.checksum_restored is False

    def test_rollback_with_nonexistent_record(self):
        """Rollback for a nonexistent change returns error."""
        sandbox = _make_temp_sandbox(SAMPLE_POM_AFTER)
        editor = _build_editor_with_sandbox(sandbox, change_record=None)

        result = editor.rollback_change(
            job_id="job_1",
            change_id="nonexistent",
            idempotency_key="ik_rb",
        )
        assert result.status == "error"

    def test_rollback_skips_already_rolled_back_changes(self):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_v2_assistant_answer,
        )

        editor = MagicMock()
        editor.list_changes.return_value = [
            MagicMock(
                change_id="ch_old",
                status=PomChangeStatus.ROLLED_BACK.value,
                rollback_id="rb_old",
                created_at="2026-06-16T00:02:00Z",
            ),
            MagicMock(
                change_id="ch_active",
                status=PomChangeStatus.APPLIED_PENDING_VALIDATION.value,
                rollback_id=None,
                created_at="2026-06-16T00:01:00Z",
            ),
        ]
        editor.rollback_change.return_value = PomRollbackResult(
            change_id="ch_active",
            rollback_id="rb_active",
            status="rolled_back",
            checksum_restored=True,
            validation_triggered=False,
            validation_id=None,
            created_at="2026-06-16T00:03:00Z",
        )
        event = MagicMock(job_id="job_1")

        with patch(
            "migration_factory.control_tower.adapters.fastapi.app._build_pom_dependency_editor",
            return_value=editor,
        ):
            answer = _build_v2_assistant_answer(
                question="Rollback latest Stage 3 POM change",
                events=(event,),
                approvals=(),
                commands=(),
            )

        editor.rollback_change.assert_called_once()
        assert editor.rollback_change.call_args.args[1] == "ch_active"
        assert "ch_active" in answer

    def test_rollback_no_active_change_returns_clear_message(self):
        from migration_factory.control_tower.adapters.fastapi.app import (
            _build_v2_assistant_answer,
        )

        editor = MagicMock()
        editor.list_changes.return_value = [
            MagicMock(
                change_id="ch_old",
                status=PomChangeStatus.ROLLED_BACK.value,
                rollback_id="rb_old",
                created_at="2026-06-16T00:02:00Z",
            )
        ]
        event = MagicMock(job_id="job_1")

        with patch(
            "migration_factory.control_tower.adapters.fastapi.app._build_pom_dependency_editor",
            return_value=editor,
        ):
            answer = _build_v2_assistant_answer(
                question="Rollback latest Stage 3 POM change",
                events=(event,),
                approvals=(),
                commands=(),
            )

        editor.rollback_change.assert_not_called()
        assert answer == "No applied Stage 3 POM change found to rollback."
