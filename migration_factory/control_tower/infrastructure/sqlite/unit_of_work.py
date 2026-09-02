"""SQLite unit of work for Control Tower application services."""

from __future__ import annotations

import sqlite3
import threading

from migration_factory.control_tower.infrastructure.sqlite.repositories import (
    SqliteArtifactRepository,
    SqliteAuditRecordRepository,
    SqliteCommandExecutionRepository,
    SqliteIdempotencyRepository,
    SqliteMigrationJobRepository,
    SqlitePipelineDefinitionRepository,
    SqliteRunConfigurationRepository,
    SqliteRunEventRepository,
    SqliteRunnerProfileRepository,
    SqliteStageChainLedgerRepository,
    SqliteStageRunRepository,
    SqliteV1ContextPackManifestRepository,
    SqliteV1FakeRepairProposalRepository,
    SqliteV1ModelInvocationRepository,
    SqliteV1PatchApplicationRepository,
    SqliteV1PatchMavenValidationRepository,
    SqliteV1PatchPolicyValidationRepository,
    SqliteV1PatchRollbackRepository,
    SqliteV1PlanAmendmentRepository,
    SqliteV1PlanReviewDecisionRepository,
    SqliteV1RepairClassificationRepository,
    SqliteV1PlanRevisionRepository,
    SqliteV1PrivilegedActionDecisionRepository,
    SqliteV1PrivilegedActionExecutionRepository,
    SqliteV1PrivilegedActionRepository,
    SqliteV1ProofReportGateRepository,
    SqliteV1ProofReportRepository,
    SqliteV1SandboxSnapshotRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v1_model_profile_repository import (
    SqliteV1ModelProfileEventRepository,
    SqliteV1ModelProfileRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v1_approval_repository import (
    SqliteV1ApprovalRepository,
    SqliteV1ApprovalResumeRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_azure_health_repository import (
    SqliteV2AzureHealthRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_approval_repository import (
    SqliteV2ApprovalRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_assistant_repository import (
    SqliteV2AssistantRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.repair_assistant_repository import (
    SqliteRepairAssistantRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_repair_repository import (
    SqliteV2RepairRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_event_repository import (
    SqliteV2JobEventRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_reviewer_repository import (
    SqliteV2ReviewerRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_pom_change_repository import (
    SqlitePomChangeProposalRepository,
    SqlitePomChangeRepository,
    SqlitePomValidationRepository,
    SqlitePomRepairPlanRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_phase_gate_repository import (
    SqlitePhaseGateRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_llm_invocation_repository import (
    SqliteV2LLMInvocationRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_gate_decision_repository import (
    SqliteGateDecisionRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
)


_WAL_CONFIGURED_CONNECTIONS: set[int] = set()
_WAL_CONFIG_LOCK = threading.Lock()


class SqliteControlTowerUnitOfWork:
    def __init__(
        self,
        connection: sqlite3.Connection,
        *,
        close_connection: bool = False,
        transaction_mode: str = "write",
    ) -> None:
        self.connection = connection
        self._close_connection = close_connection
        if transaction_mode not in {"read", "write"}:
            raise ValueError("transaction_mode must be 'read' or 'write'")
        self.transaction_mode = transaction_mode
        self.connection.execute("PRAGMA busy_timeout = 5000")
        self._enable_wal_if_file_backed()
        self.runner_profiles = SqliteRunnerProfileRepository(connection)
        self.pipeline_definitions = SqlitePipelineDefinitionRepository(connection)
        self.migration_jobs = SqliteMigrationJobRepository(connection)
        self.run_configurations = SqliteRunConfigurationRepository(connection)
        self.stage_runs = SqliteStageRunRepository(connection)
        self.run_events = SqliteRunEventRepository(connection)
        self.artifacts = SqliteArtifactRepository(connection)
        self.audit_records = SqliteAuditRecordRepository(connection)
        self.command_executions = SqliteCommandExecutionRepository(connection)
        self.idempotency_records = SqliteIdempotencyRepository(connection)
        self.stage_chain_ledger = SqliteStageChainLedgerRepository(connection)
        self.v1_model_profiles = SqliteV1ModelProfileRepository(connection)
        self.v1_model_profile_events = SqliteV1ModelProfileEventRepository(connection)
        self.v1_approvals = SqliteV1ApprovalRepository(connection)
        self.v1_approval_resume = SqliteV1ApprovalResumeRepository(connection)
        self.v1_model_invocations = SqliteV1ModelInvocationRepository(connection)
        self.v1_context_pack_manifests = SqliteV1ContextPackManifestRepository(connection)
        self.v1_privileged_actions = SqliteV1PrivilegedActionRepository(connection)
        self.v1_plan_amendments = SqliteV1PlanAmendmentRepository(connection)
        self.v1_plan_revisions = SqliteV1PlanRevisionRepository(connection)
        self.v1_plan_review_decisions = SqliteV1PlanReviewDecisionRepository(connection)
        self.v1_repair_classifications = SqliteV1RepairClassificationRepository(connection)
        self.v1_fake_repair_proposals = SqliteV1FakeRepairProposalRepository(connection)
        self.v1_privileged_action_decisions = SqliteV1PrivilegedActionDecisionRepository(connection)
        self.v1_privileged_action_executions = SqliteV1PrivilegedActionExecutionRepository(connection)
        self.v1_patch_policy_validations = SqliteV1PatchPolicyValidationRepository(connection)
        self.v1_sandbox_snapshots = SqliteV1SandboxSnapshotRepository(connection)
        self.v1_patch_applications = SqliteV1PatchApplicationRepository(connection)
        self.v1_patch_maven_validations = SqliteV1PatchMavenValidationRepository(connection)
        self.v1_patch_rollbacks = SqliteV1PatchRollbackRepository(connection)
        self.v2_setups = SqliteV2SetupRepository(connection)
        self.v2_azure_health = SqliteV2AzureHealthRepository(connection)
        self.v2_jobs = SqliteV2JobRepository(connection)
        self.v2_commands = SqliteV2CommandRepository(connection)
        self.v2_approvals = SqliteV2ApprovalRepository(connection)
        self.v2_assistant = SqliteV2AssistantRepository(connection)
        self.v2_repairs = SqliteV2RepairRepository(connection)
        self.v2_events = SqliteV2JobEventRepository(connection)
        self.v2_reviewer = SqliteV2ReviewerRepository(connection)
        self.v2_pom_proposals = SqlitePomChangeProposalRepository(connection)
        self.v2_pom_changes = SqlitePomChangeRepository(connection)
        self.v2_pom_validations = SqlitePomValidationRepository(connection)
        self.v2_pom_repair_plans = SqlitePomRepairPlanRepository(connection)
        self.v1_proof_reports = SqliteV1ProofReportRepository(connection)
        self.v1_proof_report_gates = SqliteV1ProofReportGateRepository(connection)
        self.phase_gates = SqlitePhaseGateRepository(connection)
        self.gate_decisions = SqliteGateDecisionRepository(connection)
        self.artifact_revisions = SqliteArtifactRevisionRepository(connection)
        self.v2_llm_invocations = SqliteV2LLMInvocationRepository(connection)
        self.repair_assistant = SqliteRepairAssistantRepository(connection)

    def __enter__(self) -> "SqliteControlTowerUnitOfWork":
        if self.transaction_mode == "write":
            self.connection.execute("BEGIN IMMEDIATE")
        return self

    def __exit__(self, exc_type, exc, tb) -> bool | None:
        if exc_type is None and self.connection.in_transaction:
            self.connection.execute("COMMIT")
        elif self.connection.in_transaction:
            self.connection.execute("ROLLBACK")
        if self._close_connection:
            self.connection.close()
        return None

    def _enable_wal_if_file_backed(self) -> None:
        connection_id = id(self.connection)
        with _WAL_CONFIG_LOCK:
            if connection_id in _WAL_CONFIGURED_CONNECTIONS:
                return
        try:
            row = self.connection.execute("PRAGMA database_list").fetchone()
            database_path = str(row["file"] if isinstance(row, sqlite3.Row) else row[2])
        except (IndexError, KeyError, TypeError, sqlite3.DatabaseError):
            return
        if not database_path or database_path == ":memory:":
            return
        try:
            self.connection.execute("PRAGMA journal_mode = WAL")
        except sqlite3.DatabaseError:
            return
        with _WAL_CONFIG_LOCK:
            _WAL_CONFIGURED_CONNECTIONS.add(connection_id)


SqliteUnitOfWork = SqliteControlTowerUnitOfWork
