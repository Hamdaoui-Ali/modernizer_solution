"""Focused tests for V1-17B: Validate action policy and checksums.

Tests cover:
- Parameter structure validation (malformed, missing fields)
- Policy validation (forbidden paths, unsafe Maven goals, shell metacharacters)
- Checksum verification (match, mismatch)
- Stale action detection (not found, wrong status)
- End-to-end request_action validation pipeline
"""

from __future__ import annotations

import json
import sqlite3

import pytest

from migration_factory.control_tower.application.actions import (
    ActionFormatError,
    ActionPolicyViolationError,
    ActionStaleError,
    ChecksumMismatchError,
    PrivilegedActionService,
)
from migration_factory.control_tower.domain.checksums import sha256_canonical_json


# ── Parameter structure validation ────────────────────────────────────


class TestValidateParametersStructure:
    """validate_parameters_structure rejects malformed action parameters."""

    def _create_service(self):
        """Minimal service for static method tests (no DB needed)."""
        return PrivilegedActionService(None)

    def test_maven_missing_goal(self) -> None:
        with pytest.raises(ActionFormatError, match="Missing required.*goal"):
            PrivilegedActionService.validate_parameters_structure(
                "maven", {"module": "core"}
            )

    def test_maven_goal_not_string(self) -> None:
        with pytest.raises(ActionFormatError, match="non-empty string"):
            PrivilegedActionService.validate_parameters_structure(
                "maven", {"goal": 123}
            )

    def test_maven_goal_empty_string(self) -> None:
        with pytest.raises(ActionFormatError, match="non-empty string"):
            PrivilegedActionService.validate_parameters_structure(
                "maven", {"goal": ""}
            )

    def test_maven_module_non_string(self) -> None:
        with pytest.raises(ActionFormatError, match="non-empty string"):
            PrivilegedActionService.validate_parameters_structure(
                "maven", {"goal": "compile", "module": 123}
            )

    def test_maven_valid_parameters(self) -> None:
        # Should not raise
        PrivilegedActionService.validate_parameters_structure(
            "maven", {"goal": "compile"}
        )
        PrivilegedActionService.validate_parameters_structure(
            "maven", {"goal": "compile", "module": "core"}
        )

    def test_write_missing_path(self) -> None:
        with pytest.raises(ActionFormatError, match="Missing required.*path"):
            PrivilegedActionService.validate_parameters_structure(
                "write", {"content": "data"}
            )

    def test_write_path_not_string(self) -> None:
        with pytest.raises(ActionFormatError, match="non-empty string"):
            PrivilegedActionService.validate_parameters_structure(
                "write", {"path": True, "content": "data"}
            )

    def test_write_path_empty_string(self) -> None:
        with pytest.raises(ActionFormatError, match="non-empty string"):
            PrivilegedActionService.validate_parameters_structure(
                "write", {"path": "", "content": "data"}
            )

    def test_write_missing_content(self) -> None:
        with pytest.raises(ActionFormatError, match="Missing required.*content"):
            PrivilegedActionService.validate_parameters_structure(
                "write", {"path": "src/main.java"}
            )

    def test_write_content_not_string(self) -> None:
        with pytest.raises(ActionFormatError, match="must be a string"):
            PrivilegedActionService.validate_parameters_structure(
                "write", {"path": "src/main.java", "content": 42}
            )

    def test_write_valid_parameters(self) -> None:
        PrivilegedActionService.validate_parameters_structure(
            "write", {"path": "src/main.java", "content": "public class A {}"}
        )


# ── Policy validation ─────────────────────────────────────────────────


class TestValidateActionParametersPolicy:
    """validate_action_parameters_policy rejects policy-violating payloads."""

    def test_write_forbidden_etc_path(self) -> None:
        with pytest.raises(ActionPolicyViolationError, match="forbidden"):
            PrivilegedActionService.validate_action_parameters_policy(
                "write",
                {"path": "/etc/passwd", "content": "hacked"},
            )

    def test_write_forbidden_pem_file(self) -> None:
        with pytest.raises(ActionPolicyViolationError, match="forbidden"):
            PrivilegedActionService.validate_action_parameters_policy(
                "write",
                {"path": "/home/user/secret.pem", "content": "hacked"},
            )

    def test_write_forbidden_env_file(self) -> None:
        with pytest.raises(ActionPolicyViolationError, match="forbidden"):
            PrivilegedActionService.validate_action_parameters_policy(
                "write",
                {"path": "/app/.env", "content": "hacked"},
            )

    def test_write_safe_project_path_allowed(self) -> None:
        PrivilegedActionService.validate_action_parameters_policy(
            "write",
            {"path": "/home/user/project/src/main.java", "content": "ok"},
        )

    def test_maven_safe_goal_allowed(self) -> None:
        for goal in ("compile", "test", "package", "clean install", "verify"):
            PrivilegedActionService.validate_action_parameters_policy(
                "maven", {"goal": goal}
            )

    def test_maven_unsafe_goal_rejected(self) -> None:
        with pytest.raises(ActionPolicyViolationError, match="Unsafe"):
            PrivilegedActionService.validate_action_parameters_policy(
                "maven", {"goal": "rm -rf /"}
            )

    def test_maven_plugin_goal_rejected(self) -> None:
        with pytest.raises(ActionPolicyViolationError, match="Unsafe"):
            PrivilegedActionService.validate_action_parameters_policy(
                "maven", {"goal": "dependency:tree"}
            )

    def test_shell_metacharacters_in_goal(self) -> None:
        with pytest.raises(ActionPolicyViolationError, match="shell"):
            PrivilegedActionService.validate_action_parameters_policy(
                "maven", {"goal": "compile; rm -rf"}
            )

    def test_shell_metacharacters_in_content(self) -> None:
        with pytest.raises(ActionPolicyViolationError, match="shell"):
            PrivilegedActionService.validate_action_parameters_policy(
                "write",
                {"path": "src/file.java", "content": "$(cat /etc/passwd)"},
            )

    def test_forbidden_path_in_content(self) -> None:
        with pytest.raises(ActionPolicyViolationError, match="forbidden"):
            PrivilegedActionService.validate_action_parameters_policy(
                "write",
                {"path": "src/file.java", "content": "path /etc/passwd"},
            )

    def test_safe_project_path_allowed_write(self) -> None:
        PrivilegedActionService.validate_action_parameters_policy(
            "write",
            {"path": "/home/user/project/target/file.java", "content": "safe"},
        )


# ── Checksum verification ────────────────────────────────────────────


class TestVerifyChecksum:
    """verify_checksum validates checksum integrity."""

    def test_checksum_match(self) -> None:
        params = {"goal": "compile"}
        chk = sha256_canonical_json(params)
        PrivilegedActionService.verify_checksum(
            action_id="pa-001",
            expected_checksum=chk,
            actual_checksum=chk,
        )

    def test_checksum_mismatch(self) -> None:
        with pytest.raises(ChecksumMismatchError, match="Checksum mismatch"):
            PrivilegedActionService.verify_checksum(
                action_id="pa-001",
                expected_checksum="abc",
                actual_checksum="def",
            )

    def test_verify_stored_parameters(self) -> None:
        params = {"path": "src/main.java", "content": "public class A {}"}
        stored_chk = sha256_canonical_json(params)
        computed_chk = PrivilegedActionService.compute_parameters_checksum(params)
        assert stored_chk == computed_chk

    def test_different_parameters_different_checksum(self) -> None:
        params_a = {"goal": "compile"}
        params_b = {"goal": "test"}
        chk_a = PrivilegedActionService.compute_parameters_checksum(params_a)
        chk_b = PrivilegedActionService.compute_parameters_checksum(params_b)
        assert chk_a != chk_b


# ── Stale action detection (integration with SQLite) ──────────────────


class TestValidateActionAvailable:
    """validate_action_available checks action exists and is pending."""

    def _create_service(self, tmp_path):
        db_path = tmp_path / "test_validate_available.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        with open(
            "migration_factory/control_tower/infrastructure/sqlite/migrations/"
            "0017_v1_privileged_actions.sql"
        ) as f:
            cur.executescript(f.read())
        with open(
            "migration_factory/control_tower/infrastructure/sqlite/migrations/"
            "0001_foundation.sql"
        ) as f:
            cur.executescript(f.read())
        conn.commit()

        from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
            SqliteControlTowerUnitOfWork,
        )

        def uow_factory():
            return SqliteControlTowerUnitOfWork(conn)

        service = PrivilegedActionService(uow_factory)
        return conn, service

    def _create_job(self, conn: sqlite3.Connection, job_id: str = "job-001") -> None:
        conn.execute(
            """INSERT OR IGNORE INTO migration_jobs (
                job_id, version, status, active_slot, last_event_sequence,
                runner_profile_id, runner_profile_version, pipeline_id,
                pipeline_version, target_proof_level, legacy_source_ref,
                output_root_ref, created_at, updated_at, created_by
            ) VALUES (?, 1, 'created', NULL, 0, 'rp-1', '1.0', 'pl-1', '1.0',
                      'none', 'legacy', 'output', '2026-06-12T00:00:00Z',
                      '2026-06-12T00:00:00Z', 'test')""",
            (job_id,),
        )
        conn.commit()

    def test_valid_pending_action(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        record = service.request_action(
            job_id="job-001",
            action_type="maven",
            parameters={"goal": "compile"},
        )
        validated = service.validate_action_available(record.action_id)
        assert validated.action_id == record.action_id
        assert validated.status == "pending"
        conn.close()

    def test_action_not_found(self, tmp_path) -> None:
        _, service = self._create_service(tmp_path)
        with pytest.raises(ActionStaleError, match="not found"):
            service.validate_action_available("nonexistent")

    def test_action_already_approved(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        record = service.request_action(
            job_id="job-001",
            action_type="maven",
            parameters={"goal": "compile"},
        )
        # Simulate an approved status by inserting a new record directly
        import json as _json
        conn.execute(
            """INSERT INTO v1_privileged_actions (
                action_id, job_id, action_type, action_version,
                parameters_json, parameters_checksum, status,
                requested_by, requested_at, approved_by, approved_at
            ) VALUES (?, ?, 'maven', '1.0', '{}', 'chk', 'approved',
                      'admin', '2026-06-12T00:00:00Z', 'reviewer', '2026-06-12T01:00:00Z')""",
            (f"{record.action_id}-approved", record.job_id),
        )
        conn.commit()

        with pytest.raises(ActionStaleError, match="approved"):
            service.validate_action_available(f"{record.action_id}-approved")
        conn.close()

    def test_action_rejected(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)
        conn.execute(
            """INSERT INTO v1_privileged_actions (
                action_id, job_id, action_type, action_version,
                parameters_json, parameters_checksum, status,
                requested_by, requested_at, rejected_by, rejected_reason
            ) VALUES ('pa-rejected', 'job-001', 'write', '1.0', '{}', 'chk',
                      'rejected', 'admin', '2026-06-12T00:00:00Z',
                      'reviewer', 'Policy violation')""",
        )
        conn.commit()

        with pytest.raises(ActionStaleError, match="rejected"):
            service.validate_action_available("pa-rejected")
        conn.close()


# ── Full request_action validation pipeline ──────────────────────────


class TestRequestActionValidations:
    """request_action integrates the full V1-17B validation pipeline."""

    def _create_service(self, tmp_path):
        db_path = tmp_path / "test_request_action_v17b.db"
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        cur = conn.cursor()
        with open(
            "migration_factory/control_tower/infrastructure/sqlite/migrations/"
            "0017_v1_privileged_actions.sql"
        ) as f:
            cur.executescript(f.read())
        with open(
            "migration_factory/control_tower/infrastructure/sqlite/migrations/"
            "0001_foundation.sql"
        ) as f:
            cur.executescript(f.read())
        conn.commit()

        from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import (
            SqliteControlTowerUnitOfWork,
        )

        def uow_factory():
            return SqliteControlTowerUnitOfWork(conn)

        service = PrivilegedActionService(uow_factory)
        return conn, service

    def _create_job(self, conn: sqlite3.Connection, job_id: str = "job-001") -> None:
        conn.execute(
            """INSERT OR IGNORE INTO migration_jobs (
                job_id, version, status, active_slot, last_event_sequence,
                runner_profile_id, runner_profile_version, pipeline_id,
                pipeline_version, target_proof_level, legacy_source_ref,
                output_root_ref, created_at, updated_at, created_by
            ) VALUES (?, 1, 'created', NULL, 0, 'rp-1', '1.0', 'pl-1', '1.0',
                      'none', 'legacy', 'output', '2026-06-12T00:00:00Z',
                      '2026-06-12T00:00:00Z', 'test')""",
            (job_id,),
        )
        conn.commit()

    def test_maven_with_forbidden_goal_rejected(self) -> None:
        """Unsafe Maven goals are rejected by validate_action_parameters_policy."""
        with pytest.raises(ActionPolicyViolationError, match="Unsafe"):
            PrivilegedActionService.validate_action_parameters_policy(
                "maven", {"goal": "rm -rf"}
            )

    def test_write_with_forbidden_path_rejected(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        with pytest.raises(ActionPolicyViolationError, match="forbidden"):
            service.request_action(
                job_id="job-001",
                action_type="write",
                parameters={"path": "/etc/passwd", "content": "hack"},
            )
        conn.close()

    def test_request_action_with_secret_goal(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        with pytest.raises(ActionPolicyViolationError, match="shell"):
            service.request_action(
                job_id="job-001",
                action_type="maven",
                parameters={"goal": "compile; cat /etc/shadow"},
            )
        conn.close()

    def test_write_forbidden_file_extension(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        with pytest.raises(ActionPolicyViolationError, match="forbidden"):
            service.request_action(
                job_id="job-001",
                action_type="write",
                parameters={
                    "path": "src/secret.pem",
                    "content": "fake key",
                },
            )
        conn.close()

    def test_valid_maven_request_succeeds(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        record = service.request_action(
            job_id="job-001",
            action_type="maven",
            parameters={"goal": "compile", "module": "core"},
        )
        assert record.status == "pending"
        assert record.action_type == "maven"
        conn.close()

    def test_valid_write_request_succeeds(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        record = service.request_action(
            job_id="job-001",
            action_type="write",
            parameters={
                "path": "src/main/java/App.java",
                "content": "public class App {}",
            },
        )
        assert record.status == "pending"
        assert record.action_type == "write"
        conn.close()

    def test_checksum_computed_on_request(self, tmp_path) -> None:
        conn, service = self._create_service(tmp_path)
        self._create_job(conn)

        params = {"goal": "compile", "module": "core"}
        record = service.request_action(
            job_id="job-001",
            action_type="maven",
            parameters=params,
        )
        expected_chk = sha256_canonical_json(params)
        assert record.parameters_checksum == expected_chk
        conn.close()
