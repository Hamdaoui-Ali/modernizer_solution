"""F14 — Stage 3 POM dependency change domain models.

All public responses must use to_public_dict() for redaction.
No raw sandbox paths, tokens, or secrets in public representations.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Allowed operations ─────────────────────────────────────────────

ALLOWED_POM_OPERATIONS = frozenset({
    "update_property_version",
    "update_dependency_version",
    "remove_dependency_version",
    "update_plugin_version",
    "change_dependency_coordinates",
    "add_dependency",
    "remove_dependency",
    "add_or_update_dependency_management_entry",
})

# Operations that have real executor support (patcher can apply them)
APPLY_CAPABLE_POM_OPERATIONS = frozenset({
    "update_property_version",
    "update_dependency_version",
    "remove_dependency_version",
    "update_plugin_version",
})

# Operations that are recognized but not apply-capable in F14
PROPOSAL_ONLY_POM_OPERATIONS = frozenset({
    "change_dependency_coordinates",
    "add_dependency",
    "remove_dependency",
    "add_or_update_dependency_management_entry",
})


class PomChangeStatus(str, Enum):
    PROPOSED = "proposed"
    APPLIED_PENDING_VALIDATION = "applied_pending_validation"
    VALIDATION_RUNNING = "validation_running"
    VALIDATED_PASSED = "validated_passed"
    VALIDATED_FAILED = "validated_failed"
    REPAIR_APPLIED = "repair_applied"
    ROLLED_BACK = "rolled_back"


class PomValidationStatus(str, Enum):
    RUNNING = "running"
    PASSED = "passed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"


class PomRepairPlanStatus(str, Enum):
    PROPOSED = "proposed"
    APPLIED = "applied"
    EXPIRED = "expired"


POM_VALIDATION_FAILURE_CLASSIFICATIONS = frozenset({
    "dependency_resolution_failure",
    "bom_conflict",
    "jakarta_javax_mismatch",
    "hibernate_api_break",
    "plugin_failure",
    "compilation_failure",
    "test_failure",
    "unknown_build_failure",
    "evidence_insufficient",
})


# ── Dependency target identity ─────────────────────────────────────

@dataclass(frozen=True)
class PomChangeTarget:
    """Identifies what to change."""
    kind: str  # "dependency" | "property" | "plugin" | "dependency_management" | "parent" | "bom"
    group_id: str | None = None
    artifact_id: str | None = None
    property_name: str | None = None
    plugin_group_id: str | None = None
    plugin_artifact_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"kind": self.kind}
        if self.group_id is not None:
            d["group_id"] = self.group_id
        if self.artifact_id is not None:
            d["artifact_id"] = self.artifact_id
        if self.property_name is not None:
            d["property_name"] = self.property_name
        if self.plugin_group_id is not None:
            d["plugin_group_id"] = self.plugin_group_id
        if self.plugin_artifact_id is not None:
            d["plugin_artifact_id"] = self.plugin_artifact_id
        return d


# ── Patch operation detail ─────────────────────────────────────────

@dataclass(frozen=True)
class PomPatchOperation:
    """Single patch operation on a POM."""
    operation: str  # From ALLOWED_POM_OPERATIONS
    target: PomChangeTarget
    current_version: str
    requested_version: str
    before_xml_excerpt: str
    after_xml_excerpt: str


# ── Server-validated change plan ───────────────────────────────────

@dataclass(frozen=True)
class PomChangePlan:
    """Server-validated change plan."""
    intent: str  # "apply_dependency_change"
    stage: int  # Must be 3
    operation: str
    target: PomChangeTarget
    requested_version: str
    risk: str  # "low" | "medium" | "high" | "blocked" | "evidence_insufficient"
    control_mode: str  # From DependencyControlMode enum
    requires_validation: bool
    evidence: tuple[str, ...]
    rationale: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "intent": self.intent,
            "stage": self.stage,
            "operation": self.operation,
            "target": self.target.to_dict(),
            "requested_version": self.requested_version,
            "risk": self.risk,
            "control_mode": self.control_mode,
            "requires_validation": self.requires_validation,
            "evidence": list(self.evidence),
            "rationale": self.rationale,
        }


# ── Change proposal (read-only, no write) ──────────────────────────

@dataclass(frozen=True)
class PomChangeProposal:
    """Read-only proposal (no file written yet)."""
    proposal_id: str
    server_validated_plan_preview: dict[str, Any]
    risk: str
    can_apply: bool
    warnings: tuple[str, ...]
    applied: bool  # Always False for proposals
    control_mode: str
    created_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "server_validated_plan_preview": self.server_validated_plan_preview,
            "risk": self.risk,
            "can_apply": self.can_apply,
            "warnings": list(self.warnings),
            "applied": self.applied,
            "control_mode": self.control_mode,
            "created_at": self.created_at,
        }


# ── Apply result (after backend writes) ────────────────────────────

@dataclass(frozen=True)
class PomApplyResult:
    """Result after backend applies a POM change.

    Returns `applied_pending_validation` immediately — never blocks on build.
    """
    change_id: str
    status: str  # "applied_pending_validation"
    operation: str
    target_desc: str
    before_version: str
    after_version: str
    before_checksum: str
    after_checksum: str
    diff_summary: str
    validation_id: str | None
    rollback_available: bool
    idempotency_key: str | None
    created_at: str
    message: str

    def to_public_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "change_id": self.change_id,
            "status": self.status,
            "operation": self.operation,
            "target_desc": self.target_desc,
            "before_version": self.before_version,
            "after_version": self.after_version,
            "before_checksum": self.before_checksum,
            "after_checksum": self.after_checksum,
            "diff_summary": self.diff_summary,
            "validation_id": self.validation_id,
            "rollback_available": self.rollback_available,
            "idempotency_key": self.idempotency_key,
            "created_at": self.created_at,
            "message": self.message,
        }
        return d


# ── Validation failure diagnosis ───────────────────────────────────

@dataclass(frozen=True)
class PomValidationFailureDiagnosis:
    """Classified build/test failure from log evidence only."""
    failure_classification: str
    failed_phase: str
    exit_code: int
    log_excerpt: str  # Redacted, bounded excerpt
    log_ref: str  # Reference to full log artifact, never raw content
    root_cause: str
    evidence_sufficient: bool
    missing_evidence: tuple[str, ...]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "failure_classification": self.failure_classification,
            "failed_phase": self.failed_phase,
            "exit_code": self.exit_code,
            "log_excerpt": self.log_excerpt,
            "log_ref": self.log_ref,
            "root_cause": self.root_cause,
            "evidence_sufficient": self.evidence_sufficient,
            "missing_evidence": list(self.missing_evidence),
        }


# ── Repair plan ────────────────────────────────────────────────────

@dataclass(frozen=True)
class PomRepairPlan:
    """Repair plan after failed validation."""
    repair_plan_id: str
    change_id: str
    summary: str
    detailed_steps: tuple[str, ...]
    confidence: str  # "low" | "medium" | "high"
    evidence_sources: tuple[str, ...]
    actions_available: tuple[str, ...]  # "apply_repair", "rollback", "show_logs"
    created_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "repair_plan_id": self.repair_plan_id,
            "change_id": self.change_id,
            "summary": self.summary,
            "detailed_steps": list(self.detailed_steps),
            "confidence": self.confidence,
            "evidence_sources": list(self.evidence_sources),
            "actions_available": list(self.actions_available),
            "created_at": self.created_at,
        }


# ── Validation run ─────────────────────────────────────────────────

@dataclass(frozen=True)
class PomValidationRun:
    """Validation run result."""
    validation_id: str
    change_id: str
    status: str  # "running" | "passed" | "failed"
    command: str
    build_status: str
    test_status: str
    exit_code: int | None
    duration_ms: int | None
    log_ref: str | None  # Reference, never raw content in API
    test_log_ref: str | None
    diagnosis: PomValidationFailureDiagnosis | None
    repair_plan: PomRepairPlan | None
    created_at: str
    completed_at: str | None

    def to_public_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "validation_id": self.validation_id,
            "change_id": self.change_id,
            "status": self.status,
            "command": self.command,
            "build_status": self.build_status,
            "test_status": self.test_status,
            "exit_code": self.exit_code,
            "duration_ms": self.duration_ms,
            "log_ref": self.log_ref,
            "test_log_ref": self.test_log_ref,
            "diagnosis": self.diagnosis.to_public_dict() if self.diagnosis else None,
            "repair_plan": self.repair_plan.to_public_dict() if self.repair_plan else None,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
        }
        return d


# ── Rollback result ────────────────────────────────────────────────

@dataclass(frozen=True)
class PomRollbackResult:
    """Rollback result."""
    change_id: str
    rollback_id: str
    status: str  # "rolled_back"
    checksum_restored: bool
    validation_triggered: bool
    validation_id: str | None
    created_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "rollback_id": self.rollback_id,
            "status": self.status,
            "checksum_restored": self.checksum_restored,
            "validation_triggered": self.validation_triggered,
            "validation_id": self.validation_id,
            "created_at": self.created_at,
        }


# ── Baseline detection ─────────────────────────────────────────────

@dataclass(frozen=True)
class PomBaseline:
    """Detected Stage 3 baseline from root POM evidence."""
    java_version: str
    spring_boot_version: str
    spring_boot_version_location: str
    detected_from: tuple[str, ...]

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "java_version": self.java_version,
            "spring_boot_version": self.spring_boot_version,
            "spring_boot_version_location": self.spring_boot_version_location,
            "detected_from": list(self.detected_from),
        }


# ── Dependency finding ─────────────────────────────────────────────

@dataclass(frozen=True)
class PomDependencyFinding:
    """Single dependency finding in review."""
    dependency_name: str
    current_version: str
    source_location: str
    bucket: str
    control_mode: str
    risk: str
    recommended_action: str
    can_apply_now: bool
    reason: str
    evidence_source: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "dependency_name": self.dependency_name,
            "current_version": self.current_version,
            "source_location": self.source_location,
            "bucket": self.bucket,
            "control_mode": self.control_mode,
            "risk": self.risk,
            "recommended_action": self.recommended_action,
            "can_apply_now": self.can_apply_now,
            "reason": self.reason,
            "evidence_source": self.evidence_source,
        }


# ── Full dependency review ─────────────────────────────────────────

@dataclass(frozen=True)
class PomDependencyReview:
    """Full Stage 3 dependency review."""
    job_id: str
    stage: int
    baseline: PomBaseline
    buckets: dict[str, list[PomDependencyFinding]]
    findings: tuple[PomDependencyFinding, ...]
    evidence_loaded: tuple[str, ...]
    evidence_missing: tuple[str, ...]
    warnings: tuple[str, ...]
    created_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "job_id": self.job_id,
            "stage": self.stage,
            "baseline": self.baseline.to_public_dict(),
            "buckets": {k: [f.to_public_dict() for f in v] for k, v in self.buckets.items()},
            "findings": [f.to_public_dict() for f in self.findings],
            "evidence_loaded": list(self.evidence_loaded),
            "evidence_missing": list(self.evidence_missing),
            "warnings": list(self.warnings),
            "created_at": self.created_at,
        }


# ── POM view (redacted) ────────────────────────────────────────────

@dataclass(frozen=True)
class PomView:
    """Redacted POM view for public display."""
    job_id: str
    stage: int
    exists: bool
    content: str
    truncated: bool
    content_type: str
    redaction_applied: bool
    detected_baseline: PomBaseline | None
    reason: str | None

    def to_public_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {
            "job_id": self.job_id,
            "stage": self.stage,
            "exists": self.exists,
            "content": self.content,
            "truncated": self.truncated,
            "content_type": self.content_type,
            "redaction_applied": self.redaction_applied,
            "detected_baseline": self.detected_baseline.to_public_dict() if self.detected_baseline else None,
        }
        if self.reason:
            d["reason"] = self.reason
        return d


# ── Change record summary (public-safe) ────────────────────────────

@dataclass(frozen=True)
class PomChangeRecordSummary:
    """Public-safe change record summary."""
    change_id: str
    operation: str
    target_desc: str
    before_version: str
    after_version: str
    before_checksum: str
    after_checksum: str
    diff_summary: str
    status: str
    validation_id: str | None
    rollback_id: str | None
    created_at: str

    def to_public_dict(self) -> dict[str, Any]:
        return {
            "change_id": self.change_id,
            "operation": self.operation,
            "target_desc": self.target_desc,
            "before_version": self.before_version,
            "after_version": self.after_version,
            "before_checksum": self.before_checksum,
            "after_checksum": self.after_checksum,
            "diff_summary": self.diff_summary,
            "status": self.status,
            "validation_id": self.validation_id,
            "rollback_id": self.rollback_id,
            "created_at": self.created_at,
        }


# ── Persisted change record (internal) ─────────────────────────────

@dataclass(frozen=True)
class PomChangeRecord:
    """Persisted POM change record (internal, not exposed directly)."""
    change_id: str
    proposal_id: str | None
    job_id: str
    stage_index: int
    operation: str
    target_json: str  # JSON serialized target
    requested_version: str
    before_content_ref: str  # Reference to stored before-content
    after_content_ref: str  # Reference to stored after-content
    before_checksum: str
    after_checksum: str
    diff_unified: str
    status: str
    validation_id: str | None
    rollback_id: str | None
    idempotency_key: str | None
    executor: str
    created_at: str
    updated_at: str

    def to_summary(self) -> PomChangeRecordSummary:
        target = json.loads(self.target_json)
        kind = target.get("kind", "unknown")
        gid = target.get("group_id", "")
        aid = target.get("artifact_id", "")
        pn = target.get("property_name", "")
        if kind == "property":
            target_desc = f"property:{pn}"
        elif kind == "plugin":
            pg = target.get("plugin_group_id", "") or gid
            pa = target.get("plugin_artifact_id", "") or aid
            target_desc = f"plugin:{pg}:{pa}" if pg and pa else f"plugin:{gid}:{aid}"
        elif gid and aid:
            target_desc = f"{gid}:{aid}"
        elif pn:
            target_desc = f"property:{pn}"
        else:
            target_desc = kind
        return PomChangeRecordSummary(
            change_id=self.change_id,
            operation=self.operation,
            target_desc=target_desc,
            before_version="",
            after_version=self.requested_version,
            before_checksum=self.before_checksum,
            after_checksum=self.after_checksum,
            diff_summary=f"{self.operation}: {target_desc} @ {self.requested_version}",
            status=self.status,
            validation_id=self.validation_id,
            rollback_id=self.rollback_id,
            created_at=self.created_at,
        )


# ── Request DTOs ───────────────────────────────────────────────────

@dataclass(frozen=True)
class PomProposeRequest:
    """User request to propose a POM change."""
    user_request: str
    idempotency_key: str | None = None


@dataclass(frozen=True)
class PomApplyRequest:
    """Request to apply a POM change. Backend validates server-side."""
    proposal_id: str | None = None
    user_request: str | None = None
    idempotency_key: str | None = None
    # plan_preview is advisory/debug only; never trusted as write authority
    plan_preview: dict[str, Any] | None = None


@dataclass(frozen=True)
class PomRepairApplyRequest:
    """Request to apply a repair plan."""
    repair_plan_id: str
    idempotency_key: str | None = None


@dataclass(frozen=True)
class PomRollbackRequest:
    """Request to rollback a POM change."""
    change_id: str
    idempotency_key: str | None = None
