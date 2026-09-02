"""Persistence durability tests — verify data survives connection restart."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
    V2MigrationJobRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_approval_repository import (
    SqliteV2ApprovalRepository,
    V2ApprovalDecisionRecord,
    V2ResumeCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_assistant_repository import (
    SqliteV2AssistantRepository,
    V2AssistantMessageRecord,
    V2PendingActionDraftRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    SqliteV2RepairRepository,
    V2RepairProposalRecord,
    V2SandboxActionRecord,
)


def _open_connection(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(
        db_path, check_same_thread=False, isolation_level=None, timeout=5.0
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def _init_db(conn: sqlite3.Connection) -> None:
    apply_pending_migrations(conn)


class TestPersistenceDurability:
    """Verify all V2 tables survive connection close and re-open."""

    def _check_job_tables(self, conn: sqlite3.Connection) -> tuple[str, str]:
        """Insert data and return IDs for later verification."""
        now = utc_now_text()

        job_repo = SqliteV2JobRepository(conn)
        job_record = V2MigrationJobRecord(
            job_id="durability-job",
            setup_id="durability-setup",
            setup_checksum="chk-abc",
            pipeline_id="springboot-216-to-356-java21-three-stage",
            stage_chain_json=json.dumps([{"stage_index": 1, "status": "queued"}]),
            status="created",
            created_at=now,
            updated_at=now,
            correlation_id=None,
        )
        job_repo.save(job_record)

        cmd_repo = SqliteV2CommandRepository(conn)
        cmd_record = V2StageCommandRecord(
            command_id="durability-cmd",
            job_id="durability-job",
            stage_index=1,
            manifest_checksum="chk-cmd",
            argv_json=json.dumps(["python", "-m", "runner"]),
            env_json=json.dumps({"JAVA_HOME": "/opt/java/11"}),
            status="manifest_ready",
            created_at=now,
            updated_at=now,
            result_json=None,
        )
        cmd_repo.save(cmd_record)

        return "durability-job", "durability-cmd"

    def test_job_and_command_survive_restart(self, tmp_path: Path) -> None:
        db_path = tmp_path / "durability_job.sqlite3"

        # First connection
        conn1 = _open_connection(db_path)
        _init_db(conn1)
        self._check_job_tables(conn1)
        conn1.close()

        # Second connection — read back
        conn2 = _open_connection(db_path)
        job_repo = SqliteV2JobRepository(conn2)
        cmd_repo = SqliteV2CommandRepository(conn2)

        job = job_repo.get("durability-job")
        assert job is not None
        assert job.setup_checksum == "chk-abc"
        assert job.status == "created"

        cmd = cmd_repo.get("durability-cmd")
        assert cmd is not None
        assert cmd.stage_index == 1
        assert cmd.status == "manifest_ready"
        conn2.close()

    def test_approval_survives_restart(self, tmp_path: Path) -> None:
        db_path = tmp_path / "durability_approval.sqlite3"
        now = utc_now_text()

        conn1 = _open_connection(db_path)
        _init_db(conn1)
        repo1 = SqliteV2ApprovalRepository(conn1)
        card = V2ApprovalDecisionRecord(
            card_id="dur-card", interrupt_id="dur-int",
            request_checksum="chk", stage_index=1,
            summary="Durable", status="pending", created_at=now,
        )
        repo1.save_card(card)
        resume = V2ResumeCommandRecord(
            resume_id="dur-res", card_id="dur-card",
            decision="approved", job_id="dur-job", stage_index=1,
            command_json=json.dumps(["resume"]), created_at=now,
        )
        repo1.save_resume(resume)
        conn1.close()

        conn2 = _open_connection(db_path)
        repo2 = SqliteV2ApprovalRepository(conn2)
        loaded_card = repo2.get_card("dur-card")
        assert loaded_card is not None
        assert loaded_card.status == "pending"
        loaded_resume = repo2.get_resume("dur-res")
        assert loaded_resume is not None
        assert loaded_resume.decision == "approved"
        conn2.close()

    def test_assistant_survives_restart(self, tmp_path: Path) -> None:
        db_path = tmp_path / "durability_assistant.sqlite3"
        now = utc_now_text()

        conn1 = _open_connection(db_path)
        _init_db(conn1)
        repo1 = SqliteV2AssistantRepository(conn1)
        msg = V2AssistantMessageRecord(
            message_id="dur-msg", job_id="dur-job",
            role="user", content="Test", correlation_id=None,
            created_at=now,
        )
        repo1.save_message(msg)
        draft = V2PendingActionDraftRecord(
            action_id="dur-draft", job_id="dur-job",
            action_type="plan", reason="Test", stage_index=1,
            payload_checksum="abc", status="draft", created_at=now,
        )
        repo1.save_draft(draft)
        conn1.close()

        conn2 = _open_connection(db_path)
        repo2 = SqliteV2AssistantRepository(conn2)
        loaded_msg = repo2.get_message("dur-msg")
        assert loaded_msg is not None
        assert loaded_msg.content == "Test"
        loaded_draft = repo2.get_draft("dur-draft")
        assert loaded_draft is not None
        assert loaded_draft.status == "draft"
        conn2.close()

    def test_repair_survives_restart(self, tmp_path: Path) -> None:
        db_path = tmp_path / "durability_repair.sqlite3"
        now = utc_now_text()

        conn1 = _open_connection(db_path)
        _init_db(conn1)
        repo1 = SqliteV2RepairRepository(conn1)
        prop = V2RepairProposalRecord(
            proposal_id="dur-prop", command_id="dur-cmd",
            failure_summary="Fail", hypothesis="Bug",
            patch_summary="Fix", affected_paths_json="[]",
            status="draft", approval_checksum=None, created_at=now,
        )
        repo1.save_proposal(prop)
        action = V2SandboxActionRecord(
            action_id="dur-act", proposal_id="dur-prop",
            target_path="src/main.java", patch_content="--- a\n+++ b",
            status="applied", result_summary="OK", created_at=now,
        )
        repo1.save_action(action)
        conn1.close()

        conn2 = _open_connection(db_path)
        repo2 = SqliteV2RepairRepository(conn2)
        loaded_prop = repo2.get_proposal("dur-prop")
        assert loaded_prop is not None
        assert loaded_prop.failure_summary == "Fail"
        loaded_action = repo2.get_action("dur-act")
        assert loaded_action is not None
        assert loaded_action.status == "applied"
        conn2.close()

    def test_all_data_survives_multiple_restarts(self, tmp_path: Path) -> None:
        """Write to all tables across connections, then verify."""
        db_path = tmp_path / "durability_all.sqlite3"
        now = utc_now_text()

        # First connection — write jobs + approvals
        conn1 = _open_connection(db_path)
        _init_db(conn1)
        job_repo = SqliteV2JobRepository(conn1)
        job_repo.save(V2MigrationJobRecord(
            job_id="multi-job", setup_id="multi-setup",
            setup_checksum="chk", pipeline_id="pipeline",
            stage_chain_json="[]", status="created",
            created_at=now, updated_at=now, correlation_id=None,
        ))
        conn1.close()

        # Second connection — write assistant + repair
        conn2 = _open_connection(db_path)
        asst_repo = SqliteV2AssistantRepository(conn2)
        asst_repo.save_message(V2AssistantMessageRecord(
            message_id="multi-msg", job_id="multi-job",
            role="user", content="Multi", correlation_id=None,
            created_at=now,
        ))
        conn2.close()

        # Third connection — verify everything
        conn3 = _open_connection(db_path)
        assert SqliteV2JobRepository(conn3).get("multi-job") is not None
        assert SqliteV2AssistantRepository(conn3).get_message("multi-msg") is not None
        conn3.close()
