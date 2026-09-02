"""Tests for V2 approval, assistant, and repair repositories."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

import pytest

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
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


def _connection(tmp_path: Path, name: str = "test.sqlite3") -> sqlite3.Connection:
    conn = sqlite3.connect(
        tmp_path / name,
        check_same_thread=False,
        isolation_level=None,
        timeout=5.0,
    )
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    apply_pending_migrations(conn)
    return conn


# ── Approval repository tests ───────────────────────────────────────


class TestSqliteV2ApprovalRepository:

    def test_save_and_get_card(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ApprovalRepository(conn)
        now = utc_now_text()
        card = V2ApprovalDecisionRecord(
            card_id="card1", interrupt_id="int1", request_checksum="abc",
            stage_index=1, summary="Test", status="pending", created_at=now,
        )
        repo.save_card(card)
        loaded = repo.get_card("card1")
        assert loaded is not None
        assert loaded.card_id == "card1"
        assert loaded.status == "pending"

    def test_save_and_get_resume(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ApprovalRepository(conn)
        now = utc_now_text()
        card = V2ApprovalDecisionRecord(
            card_id="card2", interrupt_id="int2", request_checksum="abc",
            stage_index=1, summary="Test", status="pending", created_at=now,
        )
        repo.save_card(card)
        resume = V2ResumeCommandRecord(
            resume_id="res1", card_id="card2", decision="approved",
            job_id="job1", stage_index=1,
            command_json=json.dumps(["python", "-m", "resume"]),
            created_at=now,
        )
        repo.save_resume(resume)
        loaded = repo.get_resume("res1")
        assert loaded is not None
        assert loaded.decision == "approved"
        assert loaded.job_id == "job1"

    def test_update_card_status(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ApprovalRepository(conn)
        now = utc_now_text()
        card = V2ApprovalDecisionRecord(
            card_id="card3", interrupt_id="int3", request_checksum="abc",
            stage_index=1, summary="Test", status="pending", created_at=now,
        )
        repo.save_card(card)
        repo.update_card_status("card3", "approved")
        loaded = repo.get_card("card3")
        assert loaded is not None
        assert loaded.status == "approved"

    def test_list_cards_by_status(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        repo = SqliteV2ApprovalRepository(conn)
        now = utc_now_text()
        c1 = V2ApprovalDecisionRecord(
            card_id="c1", interrupt_id="i1", request_checksum="a",
            stage_index=1, summary="", status="pending", created_at=now,
        )
        c2 = V2ApprovalDecisionRecord(
            card_id="c2", interrupt_id="i2", request_checksum="b",
            stage_index=1, summary="", status="approved", created_at=now,
        )
        repo.save_card(c1)
        repo.save_card(c2)
        pending = repo.list_cards_by_status("pending")
        assert len(pending) == 1
        assert pending[0].card_id == "c1"

    def test_persistence_across_connections(self, tmp_path: Path) -> None:
        db_path = tmp_path / "persist.sqlite3"
        now = utc_now_text()

        conn1 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn1.row_factory = sqlite3.Row
        conn1.execute("PRAGMA foreign_keys = ON")
        apply_pending_migrations(conn1)
        repo1 = SqliteV2ApprovalRepository(conn1)
        card = V2ApprovalDecisionRecord(
            card_id="persist-card", interrupt_id="pi1", request_checksum="abc",
            stage_index=2, summary="Persist test", status="pending", created_at=now,
        )
        repo1.save_card(card)
        resume = V2ResumeCommandRecord(
            resume_id="persist-res", card_id="persist-card", decision="approved",
            job_id="pj1", stage_index=2,
            command_json=json.dumps(["resume"]),
            created_at=now,
        )
        repo1.save_resume(resume)
        conn1.close()

        conn2 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn2.row_factory = sqlite3.Row
        conn2.execute("PRAGMA foreign_keys = ON")
        repo2 = SqliteV2ApprovalRepository(conn2)
        assert repo2.get_card("persist-card") is not None
        assert repo2.get_resume("persist-res") is not None
        assert repo2.get_card("persist-card").status == "pending"
        conn2.close()


# ── Assistant repository tests ──────────────────────────────────────


class TestSqliteV2AssistantRepository:

    def test_save_and_get_message(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path, "asm.sqlite3")
        repo = SqliteV2AssistantRepository(conn)
        now = utc_now_text()
        msg = V2AssistantMessageRecord(
            message_id="m1", job_id="j1", role="user",
            content="Hello", correlation_id="cid1", created_at=now,
        )
        repo.save_message(msg)
        loaded = repo.get_message("m1")
        assert loaded is not None
        assert loaded.content == "Hello"
        assert loaded.role == "user"

    def test_list_messages(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path, "asm2.sqlite3")
        repo = SqliteV2AssistantRepository(conn)
        now = utc_now_text()
        m1 = V2AssistantMessageRecord(
            message_id="m1", job_id="j1", role="user", content="Hi",
            correlation_id=None, created_at=now,
        )
        m2 = V2AssistantMessageRecord(
            message_id="m2", job_id="j1", role="assistant", content="Hello!",
            correlation_id=None, created_at=now,
        )
        repo.save_message(m1)
        repo.save_message(m2)
        msgs = repo.list_messages("j1")
        assert len(msgs) == 2

    def test_save_and_get_draft(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path, "asm3.sqlite3")
        repo = SqliteV2AssistantRepository(conn)
        now = utc_now_text()
        draft = V2PendingActionDraftRecord(
            action_id="d1", job_id="j1", action_type="plan_instruction",
            reason="Test", stage_index=1, payload_checksum="abc",
            status="draft", created_at=now,
        )
        repo.save_draft(draft)
        loaded = repo.get_draft("d1")
        assert loaded is not None
        assert loaded.status == "draft"
        assert loaded.action_type == "plan_instruction"

    def test_update_draft_status(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path, "asm4.sqlite3")
        repo = SqliteV2AssistantRepository(conn)
        now = utc_now_text()
        draft = V2PendingActionDraftRecord(
            action_id="d2", job_id="j1", action_type="repair_instruction",
            reason="Test", stage_index=1, payload_checksum="abc",
            status="draft", created_at=now,
        )
        repo.save_draft(draft)
        repo.update_draft_status("d2", "submitted")
        loaded = repo.get_draft("d2")
        assert loaded.status == "submitted"

    def test_list_drafts(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path, "asm5.sqlite3")
        repo = SqliteV2AssistantRepository(conn)
        now = utc_now_text()
        d1 = V2PendingActionDraftRecord(
            action_id="d1", job_id="j1", action_type="type1", reason="r1",
            stage_index=1, payload_checksum="a", status="draft", created_at=now,
        )
        d2 = V2PendingActionDraftRecord(
            action_id="d2", job_id="j1", action_type="type2", reason="r2",
            stage_index=2, payload_checksum="b", status="draft", created_at=now,
        )
        repo.save_draft(d1)
        repo.save_draft(d2)
        drafts = repo.list_drafts("j1")
        assert len(drafts) == 2

    def test_message_persistence_across_connections(self, tmp_path: Path) -> None:
        db_path = tmp_path / "asm_persist.sqlite3"
        now = utc_now_text()

        conn1 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn1.row_factory = sqlite3.Row
        conn1.execute("PRAGMA foreign_keys = ON")
        apply_pending_migrations(conn1)
        repo1 = SqliteV2AssistantRepository(conn1)
        msg = V2AssistantMessageRecord(
            message_id="persist-msg", job_id="pj1", role="user",
            content="Persist test", correlation_id=None, created_at=now,
        )
        repo1.save_message(msg)
        conn1.close()

        conn2 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn2.row_factory = sqlite3.Row
        conn2.execute("PRAGMA foreign_keys = ON")
        repo2 = SqliteV2AssistantRepository(conn2)
        loaded = repo2.get_message("persist-msg")
        assert loaded is not None
        assert loaded.content == "Persist test"
        conn2.close()


# ── Repair repository tests ─────────────────────────────────────────


class TestSqliteV2RepairRepository:

    def test_save_and_get_proposal(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path, "rep.sqlite3")
        repo = SqliteV2RepairRepository(conn)
        now = utc_now_text()
        prop = V2RepairProposalRecord(
            proposal_id="p1", command_id="cmd1",
            failure_summary="Build failed", hypothesis="Null pointer",
            patch_summary="Add null check",
            affected_paths_json=json.dumps(["src/main.java"]),
            status="draft", approval_checksum=None, created_at=now,
        )
        repo.save_proposal(prop)
        loaded = repo.get_proposal("p1")
        assert loaded is not None
        assert loaded.status == "draft"
        assert "Null pointer" in loaded.hypothesis

    def test_update_proposal_status(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path, "rep2.sqlite3")
        repo = SqliteV2RepairRepository(conn)
        now = utc_now_text()
        prop = V2RepairProposalRecord(
            proposal_id="p2", command_id="cmd2",
            failure_summary="Failed", hypothesis="Bug",
            patch_summary="Fix", affected_paths_json="[]",
            status="draft", approval_checksum=None, created_at=now,
        )
        repo.save_proposal(prop)
        repo.update_proposal_status("p2", "approved", "checksum-abc")
        loaded = repo.get_proposal("p2")
        assert loaded.status == "approved"
        assert loaded.approval_checksum == "checksum-abc"

    def test_save_and_get_action(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path, "rep3.sqlite3")
        repo = SqliteV2RepairRepository(conn)
        now = utc_now_text()
        prop = V2RepairProposalRecord(
            proposal_id="p3", command_id="cmd3",
            failure_summary="Fail", hypothesis="Hyp",
            patch_summary="Patch", affected_paths_json="[]",
            status="approved", approval_checksum="chk", created_at=now,
        )
        repo.save_proposal(prop)
        action = V2SandboxActionRecord(
            action_id="a1", proposal_id="p3",
            target_path="src/main.java",
            patch_content="--- a\n+++ b\n@@ -1 +1 @@\n-old\n+new",
            status="applied", result_summary="Applied", created_at=now,
        )
        repo.save_action(action)
        loaded = repo.get_action("a1")
        assert loaded is not None
        assert loaded.status == "applied"
        assert "src/main.java" in loaded.target_path

    def test_list_proposals_by_command(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path, "rep4.sqlite3")
        repo = SqliteV2RepairRepository(conn)
        now = utc_now_text()
        p1 = V2RepairProposalRecord(
            proposal_id="p1", command_id="cmdX",
            failure_summary="A", hypothesis="H1",
            patch_summary="P1", affected_paths_json="[]",
            status="draft", approval_checksum=None, created_at=now,
        )
        p2 = V2RepairProposalRecord(
            proposal_id="p2", command_id="cmdX",
            failure_summary="B", hypothesis="H2",
            patch_summary="P2", affected_paths_json="[]",
            status="approved", approval_checksum="chk", created_at=now,
        )
        repo.save_proposal(p1)
        repo.save_proposal(p2)
        props = repo.list_proposals_by_command("cmdX")
        assert len(props) == 2

    def test_list_actions_by_proposal(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path, "rep5.sqlite3")
        repo = SqliteV2RepairRepository(conn)
        now = utc_now_text()
        prop = V2RepairProposalRecord(
            proposal_id="p10", command_id="cmd10",
            failure_summary="F", hypothesis="H",
            patch_summary="P", affected_paths_json="[]",
            status="approved", approval_checksum="c", created_at=now,
        )
        repo.save_proposal(prop)
        a1 = V2SandboxActionRecord(
            action_id="a1", proposal_id="p10",
            target_path="t1", patch_content="patch1",
            status="applied", result_summary="OK", created_at=now,
        )
        a2 = V2SandboxActionRecord(
            action_id="a2", proposal_id="p10",
            target_path="t2", patch_content="patch2",
            status="pending", result_summary="", created_at=now,
        )
        repo.save_action(a1)
        repo.save_action(a2)
        actions = repo.list_actions_by_proposal("p10")
        assert len(actions) == 2

    def test_proposal_persistence_across_connections(self, tmp_path: Path) -> None:
        db_path = tmp_path / "rep_persist.sqlite3"
        now = utc_now_text()

        conn1 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn1.row_factory = sqlite3.Row
        conn1.execute("PRAGMA foreign_keys = ON")
        apply_pending_migrations(conn1)
        repo1 = SqliteV2RepairRepository(conn1)
        prop = V2RepairProposalRecord(
            proposal_id="persist-prop", command_id="pcmd",
            failure_summary="Persist", hypothesis="Test",
            patch_summary="Check", affected_paths_json="[]",
            status="draft", approval_checksum=None, created_at=now,
        )
        repo1.save_proposal(prop)
        conn1.close()

        conn2 = sqlite3.connect(
            db_path, check_same_thread=False, isolation_level=None, timeout=5.0
        )
        conn2.row_factory = sqlite3.Row
        conn2.execute("PRAGMA foreign_keys = ON")
        repo2 = SqliteV2RepairRepository(conn2)
        loaded = repo2.get_proposal("persist-prop")
        assert loaded is not None
        assert loaded.failure_summary == "Persist"
        conn2.close()


# ── Migration trigger tests ─────────────────────────────────────────


class TestV2TriggerExistence:

    def test_0031_triggers_exist(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        triggers = [
            "v2_approval_decisions_no_delete",
            "v2_resume_commands_no_update",
            "v2_resume_commands_no_delete",
        ]
        for t in triggers:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
                (t,),
            ).fetchall()
            assert len(rows) >= 1, f"Trigger {t} not found"

    def test_0032_triggers_exist(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        triggers = [
            "v2_assistant_messages_no_update",
            "v2_assistant_messages_no_delete",
            "v2_pending_action_drafts_no_delete",
        ]
        for t in triggers:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
                (t,),
            ).fetchall()
            assert len(rows) >= 1, f"Trigger {t} not found"

    def test_0033_triggers_exist(self, tmp_path: Path) -> None:
        conn = _connection(tmp_path)
        triggers = [
            "v2_repair_proposals_no_delete",
            "v2_sandbox_actions_no_update",
            "v2_sandbox_actions_no_delete",
        ]
        for t in triggers:
            rows = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='trigger' AND name=?",
                (t,),
            ).fetchall()
            assert len(rows) >= 1, f"Trigger {t} not found"
