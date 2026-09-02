"""V2 structured output schemas and context pack builder."""

from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.application.redaction import (
    redact_absolute_paths,
)


REQUIRED_SCHEMAS = (
    "PlanProposal",
    "RepairProposal",
    "RepairPrimaryOutput",
    "RepairReviewerOutput",
    "ReviewerCritique",
    "SafeDiffLine",
    "SafeDiffHunk",
    "SafeDiffFile",
    "SafeDiffPreview",
    "ReviewerVerdictProjection",
    "FilesChangedSummary",
    "ReviewedDiffProposal",
    "ActionRequest",
    "AssistantAnswer",
    "RepairAssistantIntent",
    "GateActionRequest",
    "AssistantGateAnswer",
)

TOKEN_BUDGETS = {
    "plan_proposal": {"input": 24000, "output": 6000},
    "plan_revision": {"input": 18000, "output": 5000},
    "repair_proposal": {"input": 40000, "output": 20000},
    "reviewer_critique": {"input": 16000, "output": 4000},
    "assistant_answer": {"input": 8000, "output": 2000},
    "action_request": {"input": 6000, "output": 1500},
}


# ── Structured output schemas (strict) ──────────────────────────────

PLAN_PROPOSAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["summary", "stage_impacts", "risks", "approval_checksum"],
    "properties": {
        "summary": {"type": "string"},
        "stage_impacts": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["stage_index", "impact"],
                "properties": {
                    "stage_index": {"type": "integer", "minimum": 1, "maximum": 3},
                    "impact": {"type": "string"},
                },
            },
        },
        "risks": {"type": "array", "items": {"type": "string"}},
        "approval_checksum": {"type": "string"},
    },
}

REPAIR_PROPOSAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["failure_hypothesis", "patch_summary", "affected_paths", "validation_plan"],
    "properties": {
        "failure_hypothesis": {"type": "string"},
        "patch_summary": {"type": "string"},
        "affected_paths": {"type": "array", "items": {"type": "string"}},
        "validation_plan": {"type": "string"},
        "rollback_note": {"type": "string"},
    },
}

REPAIR_PRIMARY_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "root_cause",
        "fix_strategy",
        "changed_files",
        "proposed_diff",
        "proposed_edits",
        "deterministic_rule_id",
        "risk",
        "confidence",
        "rationale",
        "no_fix_reason",
    ],
    "properties": {
        "root_cause": {"type": "string"},
        "fix_strategy": {"type": "string"},
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "proposed_diff": {
            "type": "string",
            "description": (
                "Legacy raw Git-style unified diff. Leave empty when proposed_edits is supplied; "
                "the backend will generate the authoritative diff."
            ),
        },
        "proposed_edits": {
            "type": "array",
            "description": "Preferred bounded source replacements; the backend generates Git syntax.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "expected_source_sha256", "exact_old_text", "exact_new_text"],
                "properties": {
                    "path": {"type": "string"},
                    "expected_source_sha256": {"type": "string"},
                    "exact_old_text": {"type": "string"},
                    "exact_new_text": {"type": "string"},
                },
            },
        },
        "deterministic_rule_id": {"type": ["string", "null"]},
        "risk": {"type": ["string", "null"], "enum": ["LOW", "MEDIUM", "HIGH", None]},
        "confidence": {"type": "number"},
        "rationale": {"type": "string"},
        "no_fix_reason": {"type": ["string", "null"]},
    },
}

REPAIR_REVIEWER_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "decision",
        "proposed_diff",
        "proposed_edits",
        "changed_files",
        "review_notes",
        "notes",
        "risks",
        "confidence",
        "policy_concerns",
        "reviewed_context_checksum",
        "reviewed_primary_output_checksum",
        "reviewed_diff_checksum",
    ],
    "properties": {
        "decision": {"type": "string", "enum": ["accept", "revise", "reject"]},
        "proposed_diff": {"type": "string"},
        "proposed_edits": {
            "type": "array",
            "description": "Preferred bounded source replacements; the backend generates Git syntax.",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["path", "expected_source_sha256", "exact_old_text", "exact_new_text"],
                "properties": {
                    "path": {"type": "string"},
                    "expected_source_sha256": {"type": "string"},
                    "exact_old_text": {"type": "string"},
                    "exact_new_text": {"type": "string"},
                },
            },
        },
        "changed_files": {"type": "array", "items": {"type": "string"}},
        "review_notes": {"type": "array", "items": {"type": "string"}},
        "notes": {"type": "array", "items": {"type": "string"}},
        "risks": {"type": "array", "items": {"type": "string"}},
        "confidence": {"type": "number"},
        "policy_concerns": {"type": "array", "items": {"type": "string"}},
        "reviewed_context_checksum": {"type": "string"},
        "reviewed_primary_output_checksum": {"type": "string"},
        "reviewed_diff_checksum": {"type": "string"},
    },
}

REVIEWER_CRITIQUE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "reasoning", "missing_evidence", "unsafe_assumptions"],
    "properties": {
        "decision": {"type": "string", "enum": ["accept", "revise", "reject"]},
        "reasoning": {"type": "string"},
        "missing_evidence": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
        },
        "unsafe_assumptions": {
            "type": "array",
            "items": {"type": "string"},
            "minItems": 0,
        },
    },
}

# F05: Allowed action types — strict enum for ActionRequest schema
F05_ALLOWED_ACTION_TYPES = (
    "explain_failure",
    "diagnose_failure",
    "propose_repair",
    "revise_repair_proposal",
    "propose_pom_patch",
    "request_reviewer_critique",
    "prepare_approval_request",
    "prepare_sandbox_repair",
    "request_validation_rerun_after_apply",
    # F14: POM dependency change actions
    "apply_dependency_change",
    "rollback_pom_change",
    "explain_validation_result",
    "apply_repair_plan_action",
    # F15: Gate action types (job061)
    "continue_from_gate",
    "request_reanalysis",
    "request_plan_revision",
    "approve_from_gate",
    "reject_from_gate",
    "explain_gate_evidence",
    "show_gate_available_actions",
)

# F05: Explicitly blocked action types — reject at service boundary
F05_EXPLICITLY_BLOCKED_ACTION_TYPES = (
    "execute_command_directly",
    "write_file_directly",
    "approve_decision",
    "modify_legacy_source",
    "override_failed_proof",
    "choose_random_sandbox",
)

ACTION_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action_type", "reason", "stage_index", "payload_checksum"],
    "properties": {
        "action_type": {
            "type": "string",
            "enum": list(F05_ALLOWED_ACTION_TYPES),
        },
        "reason": {"type": "string"},
        "stage_index": {"type": "integer", "minimum": 1, "maximum": 3},
        "payload_checksum": {"type": "string"},
        # F05: Revision steering fields
        "source_proposal_id": {"type": "string"},
        "failed_command_id": {"type": "string"},
        "revision_instruction": {"type": "string"},
        "context_pack_checksum": {"type": "string"},
        "revision_of": {"type": "string"},
        "revision_number": {"type": "integer", "minimum": 1},
        "allowed_scope": {"type": "string", "enum": ["any", "pom_only"]},
    },
}

ASSISTANT_ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["answer"],
    "properties": {
        "answer": {"type": "string"},
        "evidence_refs": {"type": "array", "items": {"type": "string"}},
        "follow_up_action": {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "action_type": {"type": "string"},
                "reason": {"type": "string"},
            },
        },
    },
}

REPAIR_ASSISTANT_INTENT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["action", "assistant_message", "tool", "arguments", "requires_clarification"],
    "properties": {
        "action": {"type": "string", "enum": ["ANSWER_ONLY", "REQUEST_REVISION", "CLARIFICATION_REQUIRED"]},
        "assistant_message": {"type": "string"},
        "tool": {"type": ["string", "null"]},
        "arguments": {
            "type": "object",
            "additionalProperties": False,
            "required": ["user_instruction", "resolved_instruction", "constraints", "target_files"],
            "properties": {
                "user_instruction": {"type": "string"},
                "resolved_instruction": {"type": "string"},
                "constraints": {"type": "array", "items": {"type": "string"}},
                "target_files": {"type": "array", "items": {"type": "string"}},
            },
        },
        "requires_clarification": {"type": "boolean"},
    },
}

# F15: Gate action request schema (job061)
# Separate from ACTION_REQUEST_SCHEMA to keep strict typing for gate actions.
# Gate actions must always include the gate_id and expected_gate_checksum.
F15_GATE_ALLOWED_ACTION_TYPES = frozenset({
    "continue_from_gate",
    "request_reanalysis",
    "request_plan_revision",
    "approve_from_gate",
    "reject_from_gate",
    "explain_gate_evidence",
    "show_gate_available_actions",
})


GATE_ACTION_REQUEST_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "action_type", "gate_id", "expected_gate_checksum",
        "idempotency_key", "request_checksum", "reason",
    ],
    "properties": {
        "action_type": {
            "type": "string",
            "enum": sorted(F15_GATE_ALLOWED_ACTION_TYPES),
        },
        "gate_id": {"type": "string"},
        "expected_gate_checksum": {"type": "string"},
        "idempotency_key": {"type": "string"},
        "request_checksum": {"type": "string"},
        "reason": {"type": "string"},
        "stage_index": {"type": "integer", "minimum": 1, "maximum": 3},
        # Chatbot can supply reanalysis/revision notes
        "user_feedback": {"type": "string"},
        "revision_instructions": {"type": "string"},
        # Correlation for audit trail
        "correlation_id": {"type": "string"},
        "causation_id": {"type": "string"},
    },
}


# F15: AssistantGateAnswer schema — gate-aware explanation (jobs 064-067)
ASSISTANT_GATE_ANSWER_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["gate_id", "gate_phase", "answer"],
    "properties": {
        "gate_id": {"type": "string"},
        "gate_phase": {
            "type": "string",
            "enum": [
                "analysis_review", "planning_review",
                "approval_review", "repair_review",
                "stage_completion_review",
            ],
        },
        "answer": {"type": "string"},
        "evidence_summary": {"type": "string"},
        "available_actions": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["action", "label"],
                "properties": {
                    "action": {"type": "string"},
                    "label": {"type": "string"},
                    "description": {"type": "string"},
                    "blocked": {"type": "boolean"},
                    "block_reason": {"type": "string"},
                },
            },
        },
        "warnings": {"type": "array", "items": {"type": "string"}},
        "decision_required": {"type": "boolean"},
        "gate_checksum": {"type": "string"},
        "stage_index": {"type": "integer", "minimum": 1, "maximum": 3},
    },
}

SAFE_DIFF_LINE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["kind", "text", "redacted"],
    "properties": {
        "kind": {"type": "string", "enum": ["context", "addition", "deletion"]},
        "old_line_number": {"type": ["integer", "null"], "minimum": 0},
        "new_line_number": {"type": ["integer", "null"], "minimum": 0},
        "text": {"type": "string"},
        "redacted": {"type": "boolean"},
    },
}

SAFE_DIFF_HUNK_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["old_start", "old_lines", "new_start", "new_lines", "lines"],
    "properties": {
        "old_start": {"type": "integer", "minimum": 0},
        "old_lines": {"type": "integer", "minimum": 0},
        "new_start": {"type": "integer", "minimum": 0},
        "new_lines": {"type": "integer", "minimum": 0},
        "section_header": {"type": ["string", "null"]},
        "lines": {"type": "array", "items": SAFE_DIFF_LINE_SCHEMA},
    },
}

SAFE_DIFF_FILE_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "change_type", "additions", "deletions", "hunks", "truncated"],
    "properties": {
        "path": {"type": "string"},
        "change_type": {"type": "string", "enum": ["added", "modified", "deleted", "renamed", "binary"]},
        "additions": {"type": "integer", "minimum": 0},
        "deletions": {"type": "integer", "minimum": 0},
        "hunks": {"type": "array", "items": SAFE_DIFF_HUNK_SCHEMA},
        "truncated": {"type": "boolean"},
    },
}

SAFE_DIFF_PREVIEW_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["proposal_id", "diff_ref", "diff_checksum", "files", "total_additions", "total_deletions", "truncated", "redactions"],
    "properties": {
        "proposal_id": {"type": "string"},
        "diff_ref": {"type": ["string", "null"]},
        "diff_checksum": {"type": "string"},
        "files": {"type": "array", "items": SAFE_DIFF_FILE_SCHEMA},
        "total_additions": {"type": "integer", "minimum": 0},
        "total_deletions": {"type": "integer", "minimum": 0},
        "truncated": {"type": "boolean"},
        "redactions": {"type": "array", "items": {"type": "string"}},
    },
}

FILES_CHANGED_SUMMARY_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["path", "change_type", "additions", "deletions"],
    "properties": {
        "path": {"type": "string"},
        "change_type": {"type": "string", "enum": ["added", "modified", "deleted", "renamed", "binary"]},
        "additions": {"type": "integer", "minimum": 0},
        "deletions": {"type": "integer", "minimum": 0},
    },
}

REVIEWER_VERDICT_PROJECTION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["decision", "missing_evidence", "unsafe_assumptions"],
    "properties": {
        "reviewer_verdict_id": {"type": ["string", "null"]},
        "decision": {"type": "string", "enum": ["accept", "revise", "reject", "unknown"]},
        "reasoning": {"type": ["string", "null"]},
        "missing_evidence": {"type": "array", "items": {"type": "string"}},
        "unsafe_assumptions": {"type": "array", "items": {"type": "string"}},
        "model_invocation_id": {"type": ["string", "null"]},
        "output_checksum": {"type": ["string", "null"]},
    },
}

REVIEWED_DIFF_PROPOSAL_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": [
        "proposal_id",
        "status",
        "failure_summary",
        "diff_ref",
        "diff_checksum",
        "safe_diff_preview",
        "reviewer_verdict",
        "files_changed",
        "required_validation",
        "allowed_actions",
        "redactions",
    ],
    "properties": {
        "proposal_id": {"type": "string"},
        "job_id": {"type": ["string", "null"]},
        "command_id": {"type": ["string", "null"]},
        "gate_id": {"type": ["string", "null"]},
        "route_step_index": {"type": ["integer", "null"], "minimum": 0},
        "stage_index": {"type": ["integer", "null"], "minimum": 0},
        "status": {"type": "string"},
        "attempt_number": {"type": ["integer", "null"], "minimum": 0},
        "revision_number": {"type": ["integer", "null"], "minimum": 0},
        "failure_summary": {"type": "string"},
        "diagnosis_ref": {"type": ["string", "null"]},
        "repair_plan_ref": {"type": ["string", "null"]},
        "diff_ref": {"type": ["string", "null"]},
        "diff_checksum": {"type": "string"},
        "safe_diff_preview": SAFE_DIFF_PREVIEW_SCHEMA,
        "reviewer_verdict": REVIEWER_VERDICT_PROJECTION_SCHEMA,
        "files_changed": {"type": "array", "items": FILES_CHANGED_SUMMARY_SCHEMA},
        "risk": {"type": ["string", "null"]},
        "required_validation": {"type": "array", "items": {"type": "string"}},
        "allowed_actions": {"type": "array", "items": {"type": "string"}},
        "redactions": {"type": "array", "items": {"type": "string"}},
    },
}


SCHEMA_REGISTRY = {
    "PlanProposal": PLAN_PROPOSAL_SCHEMA,
    "RepairProposal": REPAIR_PROPOSAL_SCHEMA,
    "RepairPrimaryOutput": REPAIR_PRIMARY_OUTPUT_SCHEMA,
    "RepairReviewerOutput": REPAIR_REVIEWER_OUTPUT_SCHEMA,
    "ReviewerCritique": REVIEWER_CRITIQUE_SCHEMA,
    "SafeDiffLine": SAFE_DIFF_LINE_SCHEMA,
    "SafeDiffHunk": SAFE_DIFF_HUNK_SCHEMA,
    "SafeDiffFile": SAFE_DIFF_FILE_SCHEMA,
    "SafeDiffPreview": SAFE_DIFF_PREVIEW_SCHEMA,
    "ReviewerVerdictProjection": REVIEWER_VERDICT_PROJECTION_SCHEMA,
    "FilesChangedSummary": FILES_CHANGED_SUMMARY_SCHEMA,
    "ReviewedDiffProposal": REVIEWED_DIFF_PROPOSAL_SCHEMA,
    "ActionRequest": ACTION_REQUEST_SCHEMA,
    "AssistantAnswer": ASSISTANT_ANSWER_SCHEMA,
    "RepairAssistantIntent": REPAIR_ASSISTANT_INTENT_SCHEMA,
    "GateActionRequest": GATE_ACTION_REQUEST_SCHEMA,
    "AssistantGateAnswer": ASSISTANT_GATE_ANSWER_SCHEMA,
}


# ── Schema validation ────────────────────────────────────────────────


class SchemaValidationError(Exception):
    """Raised when data does not match the expected schema."""


def validate_against_schema(schema_name: str, data: Any) -> None:
    """Convenience wrapper to validate data against a registered schema.

    Args:
        schema_name: One of the REQUIRED_SCHEMAS names.
        data: The data dict to validate.

    Raises:
        SchemaValidationError: If validation fails.
        ValueError: If schema_name is unknown.
    """
    SchemaValidator.validate(schema_name, data)


def normalize_model_output(schema_name: str, data: Any) -> Any:
    """Normalize only aliases proven compatible with governed contracts."""
    if schema_name == "RepairReviewerOutput" and isinstance(data, dict):
        normalized = dict(data)
        if "notes" not in normalized and "review_notes" in normalized:
            normalized["notes"] = normalized["review_notes"]
        elif "review_notes" not in normalized and "notes" in normalized:
            normalized["review_notes"] = normalized["notes"]
        return normalized
    return data


def validate_model_output(schema_name: str, data: Any) -> dict[str, Any]:
    """Validate model output at the service boundary.

    Call this on every structured model output before it enters
    the V2 runtime. Fails closed on any validation error; does
    not fall back to free-form execution.

    Args:
        schema_name: One of REQUIRED_SCHEMAS.
        data: The model output dict to validate.

    Returns:
        The validated data dict (unchanged).

    Raises:
        SchemaValidationError: If the model output violates the schema.
        ValueError: If schema_name is unknown.
    """
    normalized = normalize_model_output(schema_name, data)
    validate_against_schema(schema_name, normalized)
    return normalized


class SchemaValidator:
    """Lightweight JSON Schema validator for V2 model schemas.

    Validates data against the registered schemas without requiring
    an external jsonschema library. Covers the subset of JSON Schema
    used by the V2 structured output schemas.
    """

    @staticmethod
    def validate(schema_name: str, data: Any) -> None:
        """Validate data against the named schema.

        Args:
            schema_name: One of the REQUIRED_SCHEMAS names.
            data: The data dict to validate.

        Raises:
            SchemaValidationError: If validation fails.
            ValueError: If schema_name is unknown.
        """
        schema = SCHEMA_REGISTRY.get(schema_name)
        if schema is None:
            raise ValueError(f"Unknown schema: {schema_name!r}")

        SchemaValidator._validate_value(data, schema, [schema_name])

    @staticmethod
    def _validate_value(value: Any, schema: dict[str, Any], path: list[str]) -> None:
        """Validate a single value against a schema fragment."""
        if not isinstance(schema, dict):
            return

        schema_type = schema.get("type")
        schema_types = schema_type if isinstance(schema_type, list) else [schema_type] if schema_type is not None else []

        if value is None:
            if "null" in schema_types:
                return
            if schema_type is not None:
                raise SchemaValidationError(
                    f"Expected non-null value at {'.'.join(path)!r}, got null"
                )
            return

        # Check additionalProperties
        if schema.get("additionalProperties") is False and isinstance(value, dict):
            allowed = set(schema.get("properties", {}).keys())
            for key in value:
                if key not in allowed:
                    raise SchemaValidationError(
                        f"Unexpected property {'.'.join(path + [key])!r}. "
                        f"Allowed: {sorted(allowed)}"
                    )

        # Check type constraints
        if "object" in schema_types and not isinstance(value, dict):
            raise SchemaValidationError(
                f"Expected object at {'.'.join(path)!r}, got {type(value).__name__}"
            )
        if "array" in schema_types and not isinstance(value, (list, tuple)):
            raise SchemaValidationError(
                f"Expected array at {'.'.join(path)!r}, got {type(value).__name__}"
            )
        if "string" in schema_types and not isinstance(value, str):
            raise SchemaValidationError(
                f"Expected string at {'.'.join(path)!r}, got {type(value).__name__}"
            )
        if "integer" in schema_types and not isinstance(value, int):
            raise SchemaValidationError(
                f"Expected integer at {'.'.join(path)!r}, got {type(value).__name__}"
            )
        if "number" in schema_types and not isinstance(value, (int, float)):
            raise SchemaValidationError(
                f"Expected number at {'.'.join(path)!r}, got {type(value).__name__}"
            )
        if "string" in schema_types and isinstance(value, str):
            min_length = schema.get("minLength")
            if min_length is not None and len(value) < int(min_length):
                raise SchemaValidationError(
                    f"String at {'.'.join(path)!r} is shorter than minLength {min_length}"
                )
            max_length = schema.get("maxLength")
            if max_length is not None and len(value) > int(max_length):
                raise SchemaValidationError(
                    f"String at {'.'.join(path)!r} is longer than maxLength {max_length}"
                )
            pattern = schema.get("pattern")
            if pattern and re.search(str(pattern), value) is None:
                raise SchemaValidationError(
                    f"String at {'.'.join(path)!r} does not match pattern {pattern!r}"
                )

        # Check required fields
        if "object" in schema_types and isinstance(value, dict):
            required = schema.get("required", [])
            for field in required:
                if field not in value:
                    raise SchemaValidationError(
                        f"Missing required field {'.'.join(path + [field])!r}"
                    )

        # Check enum constraint
        enum_values = schema.get("enum")
        if enum_values is not None and value not in enum_values:
            raise SchemaValidationError(
                f"Value {value!r} at {'.'.join(path)!r} is not one of {enum_values}"
            )

        # Check numeric constraints
        if isinstance(value, (int, float)):
            minimum = schema.get("minimum")
            maximum = schema.get("maximum")
            if minimum is not None and value < minimum:
                raise SchemaValidationError(
                    f"Value {value} at {'.'.join(path)!r} is less than minimum {minimum}"
                )
            if maximum is not None and value > maximum:
                raise SchemaValidationError(
                    f"Value {value} at {'.'.join(path)!r} is greater than maximum {maximum}"
                )

        # Check array items
        if "array" in schema_types and isinstance(value, (list, tuple)):
            items_schema = schema.get("items")
            if items_schema:
                for i, item in enumerate(value):
                    SchemaValidator._validate_value(
                        item, items_schema, path + [str(i)]
                    )

        # Check property values
        if "object" in schema_types and isinstance(value, dict):
            properties = schema.get("properties", {})
            for key, prop_schema in properties.items():
                if key in value:
                    SchemaValidator._validate_value(
                        value[key], prop_schema, path + [key]
                    )


# ── Context pack ────────────────────────────────────────────────────

@dataclass(frozen=True)
class ContextPack:
    """Bounded context pack for model calls with optional enrichment metadata.

    Metadata fields enrich the pack for diagnosis/proposal/review flows.
    All metadata is optional — old packs remain readable.
    """

    pack_id: str
    pack_type: str
    title: str
    description: str
    evidence_refs: tuple[str, ...]
    token_budget_input: int
    token_budget_output: int
    checksum: str
    created_at: str
    # ── Optional enrichment metadata (F01) ────────────────────────────
    agent_name: str | None = None
    event_type: str | None = None
    stage_index: int | None = None
    profile_id: str | None = None
    command_id: str | None = None
    failure_type: str | None = None
    artifact_refs_used: tuple[str, ...] = ()
    pom_summary_ref: str | None = None
    sandbox_binding_ref: str | None = None
    redaction_status: str | None = None


class ContextPackBuilder:
    """Build bounded context packs for model calls."""

    @staticmethod
    def build_context_pack(
        pack_type: str,
        title: str,
        description: str,
        evidence_refs: tuple[str, ...],
        *,
        agent_name: str | None = None,
        event_type: str | None = None,
        stage_index: int | None = None,
        profile_id: str | None = None,
        command_id: str | None = None,
        failure_type: str | None = None,
        artifact_refs_used: tuple[str, ...] = (),
        pom_summary_ref: str | None = None,
        sandbox_binding_ref: str | None = None,
        redaction_status: str | None = None,
    ) -> ContextPack:
        budgets = TOKEN_BUDGETS.get(pack_type, {"input": 8000, "output": 2000})
        now = utc_now_text()
        pack_id = uuid4().hex

        return ContextPack(
            pack_id=pack_id,
            pack_type=pack_type,
            title=title,
            description=description,
            evidence_refs=evidence_refs,
            token_budget_input=budgets["input"],
            token_budget_output=budgets["output"],
            checksum=f"cp-{pack_id[:8]}",
            created_at=now,
            agent_name=agent_name,
            event_type=event_type,
            stage_index=stage_index,
            profile_id=profile_id,
            command_id=command_id,
            failure_type=failure_type,
            artifact_refs_used=artifact_refs_used,
            pom_summary_ref=pom_summary_ref,
            sandbox_binding_ref=sandbox_binding_ref,
            redaction_status=redaction_status,
        )

    @staticmethod
    def pack_to_dict(pack: ContextPack) -> dict[str, Any]:
        result = {
            "pack_id": pack.pack_id,
            "pack_type": pack.pack_type,
            "title": pack.title,
            "description": redact_absolute_paths(pack.description),
            "evidence_refs": list(pack.evidence_refs),
            "token_budget_input": pack.token_budget_input,
            "token_budget_output": pack.token_budget_output,
            "checksum": pack.checksum,
            "created_at": pack.created_at,
        }
        # Include non-empty metadata fields for backward compatibility
        if pack.agent_name is not None:
            result["agent_name"] = pack.agent_name
        if pack.event_type is not None:
            result["event_type"] = pack.event_type
        if pack.stage_index is not None:
            result["stage_index"] = pack.stage_index
        if pack.profile_id is not None:
            result["profile_id"] = pack.profile_id
        if pack.command_id is not None:
            result["command_id"] = pack.command_id
        if pack.failure_type is not None:
            result["failure_type"] = pack.failure_type
        if pack.artifact_refs_used:
            result["artifact_refs_used"] = list(pack.artifact_refs_used)
        if pack.pom_summary_ref is not None:
            result["pom_summary_ref"] = pack.pom_summary_ref
        if pack.sandbox_binding_ref is not None:
            result["sandbox_binding_ref"] = pack.sandbox_binding_ref
        if pack.redaction_status is not None:
            result["redaction_status"] = pack.redaction_status
        return result

    @staticmethod
    def schema_to_dict(schema_name: str) -> dict[str, Any] | None:
        schema = SCHEMA_REGISTRY.get(schema_name)
        if schema is None:
            return None
        return {
            "schema_name": schema_name,
            "schema": schema,
            "checksum": f"schema-{schema_name.lower()}",
        }
