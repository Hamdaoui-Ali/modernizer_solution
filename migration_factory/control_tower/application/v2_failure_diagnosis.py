"""V2 Automatic Failure Diagnosis (F02).

Creates governed LLM diagnosis and repair proposal objects when backend-owned
migration execution emits build_failed, test_failed, or transform_failed.

Responsibilities:
1. Accept backend failure events with job/stage/command context.
2. Idempotency: reject duplicate diagnoses for the same command+event_type.
3. Collect existing failure evidence via evidence_collector.
4. Classify failure via failure_classifier.
5. Build enriched ContextPack using F01 metadata fields.
6. Route through F03 EventPromptRouter to RepairProposal.
7. Validate model output with existing schema validation.
8. Persist diagnosis/proposal correlation.
9. Emit ai_diagnosis_created event.
10. Never apply patches, create approval cards, or bypass repair_loop.

Non-goals (inherited from architecture):
- New failure collector, classifier, repair schema, event stream, or frontend-only diagnosis.
- Patch apply, approval card creation, or legacy source mutation.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.application.v2_model_schemas import (
    ContextPack,
    ContextPackBuilder,
    validate_model_output,
    SCHEMA_REGISTRY,
)
from migration_factory.control_tower.application.v2_prompt_router import (
    EventPromptRouter,
)
from migration_factory.control_tower.application.v2_repair_flow import (
    V2RepairFlowService,
)


# â”€â”€ Diagnosis record â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


@dataclass(frozen=True)
class FailureDiagnosisRecord:
    """Correlated diagnosis record.

    Stored in-memory and/or serialized into ai_diagnosis_created event payload
    for audit. Keyed by (command_id, event_type) for idempotency.
    """
    diagnosis_id: str
    command_id: str
    event_type: str  # build_failed, test_failed, transform_failed
    failure_type: str  # from failure classifier
    context_pack_id: str
    context_pack_checksum: str
    repair_proposal_id: str | None
    model_invocation_id: str | None
    redaction_status: str
    created_at: str


# â”€â”€ Diagnosis service â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


class V2FailureDiagnosisService:
    """Automatic failure diagnosis service.

    Triggered by backend failure events. Routes through existing evidence
    collection, classification, context pack building, prompt routing,
    schema validation, and repair proposal persistence.

    The service is idempotent: the same (command_id, event_type) pair
    cannot create duplicate diagnosis records.

    Production callers must serialize access via an event loop or lock.
    """

    # Failure event types that trigger diagnosis
    TRIGGER_EVENT_TYPES = frozenset({
        "build_failed",
        "test_failed",
        "transform_failed",
    })

    def __init__(
        self,
        *,
        repair_flow: V2RepairFlowService | None = None,
        event_sink: Callable[[str, int | None, str, str, str, dict[str, Any] | None], None] | None = None,
        evidence_collector: Callable[..., tuple[dict[str, Any], Path, dict[str, Any]]] | None = None,
        run_dir_resolver: Callable[[str, str], str | None] | None = None,
    ) -> None:
        self._repair_flow = repair_flow or V2RepairFlowService()
        self._event_sink = event_sink
        self._evidence_collector = evidence_collector
        self._run_dir_resolver = run_dir_resolver

        # In-memory idempotency store: {(command_id, event_type): diagnosis_id}
        self._diagnoses: dict[tuple[str, str], FailureDiagnosisRecord] = {}

    def diagnose(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        event_type: str,
        payload: dict[str, Any] | None = None,
        profile_id: str | None = None,
        pom_summary_ref: str | None = None,
        sandbox_binding_ref: str | None = None,
    ) -> FailureDiagnosisRecord:
        """Create a diagnosis for a backend failure event.

        Idempotent: returns existing record if already diagnosed for this
        (command_id, event_type).

        Args:
            job_id: The job that owns the failed command.
            stage_index: The stage index where failure occurred.
            command_id: The failed command id.
            event_type: One of build_failed, test_failed, transform_failed.
            payload: The full failure event payload with build/test/transform
                     status and contract fields.

        Returns:
            A FailureDiagnosisRecord with correlation fields.

        Raises:
            ValueError: If event_type is not a trigger type, or if
                        required context is missing.
        """
        # 1. Validate trigger event type
        if event_type not in self.TRIGGER_EVENT_TYPES:
            raise ValueError(
                f"Event type {event_type!r} is not a diagnosis trigger. "
                f"Expected one of: {', '.join(sorted(self.TRIGGER_EVENT_TYPES))}"
            )

        # 2. Idempotency check
        existing = self._diagnoses.get((command_id, event_type))
        if existing is not None:
            return existing

        payload_data = payload or {}

        # 3. Determine build/test statuses from payload for evidence collector
        build_status = str(payload_data.get("build_status", ""))
        test_status = str(payload_data.get("test_status", ""))
        transform_status = str(payload_data.get("transform_status", ""))

        source_profile = str(payload_data.get("source_profile") or "")
        target_profile = str(payload_data.get("target_profile") or "")

        # 4. Optional: resolve run_dir and sandbox_path from payload
        #    (prod callers pass artifact_refs; test callers may omit)
        artifact_refs = payload_data.get("artifact_refs", {})
        if not isinstance(artifact_refs, dict):
            artifact_refs = {}

        # 5. Build failure summary from payload
        failure_summary = self._build_failure_summary(
            event_type=event_type,
            payload=payload_data,
        )

        # 6. Collect failure evidence (if collector provided)
        classification_result = None
        if self._evidence_collector:
            classification_result = self._collect_and_classify(
                command_id=command_id,
                event_type=event_type,
                payload=payload_data,
                build_status=build_status,
                test_status=test_status,
            )

        # 7. Build ContextPack with enrichment metadata (F01)
        failure_type = (
            classification_result.get("failure_type", "UNKNOWN")
            if classification_result
            else "UNKNOWN"
        )
        # Collect artifact refs from payload for context pack enrichment
        evidence_artifact_refs: tuple[str, ...] = ()
        raw_refs = payload_data.get("artifact_refs", {})
        if isinstance(raw_refs, dict):
            evidence_artifact_refs = tuple(
                str(v) for v in raw_refs.values() if v
            )[:10]
        pack = self._build_context_pack(
            event_type=event_type,
            stage_index=stage_index,
            command_id=command_id,
            failure_type=failure_type,
            failure_summary=failure_summary,
            classification=classification_result,
            redaction_status=(
                "evidence_redacted"
                if self._evidence_collector is not None
                else "evidence_collector_unavailable"
            ),
            pom_summary_ref=pom_summary_ref,
            sandbox_binding_ref=sandbox_binding_ref,
            profile_id=profile_id,
            artifact_refs_used=evidence_artifact_refs,
        )

        # 8. Route through EventPromptRouter to get ModelCallRequest
        route_payload = {
            "event_type": event_type,
            "stage_index": stage_index,
            "failure_summary": failure_summary,
            "evidence_refs": ", ".join(pack.evidence_refs),
            "command_id": command_id,
        }
        model_request = EventPromptRouter.route(
            event_type=event_type,
            pack=pack,
            payload=route_payload,
        )

        # 9. Validate the prompt_router output schema name exists
        schema_name = model_request.output_schema_name
        if schema_name not in SCHEMA_REGISTRY:
            raise ValueError(
                f"Schema {schema_name!r} resolved by prompt router is not registered"
            )

        # 10. Create the RepairProposal via repair flow.
        #     The proposal is a draft with evidence-based hypothesis.
        #     In production, LLM fills patch_summary and affected_paths
        #     via model-structured output after prompt routing.
        hypothesis = (
            classification_result.get("likely_root_cause", "Unknown failure")
            if classification_result
            else "Unknown failure"
        )
        proposal = self._repair_flow.create_proposal(
            command_id=command_id,
            failure_summary=failure_summary,
            hypothesis=hypothesis,
            patch_summary="Diagnosis pending model-generated repair proposal",
            affected_paths=(),
        )

        # 11. Validate proposal against RepairProposal schema for defensive consistency.
        #     In production, the real model output is validated after model call.
        proposal_dict = {
            "failure_hypothesis": hypothesis,
            "patch_summary": "Diagnosis pending model-generated repair proposal",
            "affected_paths": [],
            "validation_plan": "Run model diagnosis to produce validated repair proposal.",
        }
        validate_model_output("RepairProposal", proposal_dict)

        # 12. Build diagnosis record
        diagnosis = FailureDiagnosisRecord(
            diagnosis_id=uuid4().hex,
            command_id=command_id,
            event_type=event_type,
            failure_type=failure_type,
            context_pack_id=pack.pack_id,
            context_pack_checksum=pack.checksum,
            repair_proposal_id=proposal.proposal_id,
            model_invocation_id=f"model-{model_request.request_id[:12]}",
            redaction_status=(
                "evidence_redacted"
                if self._evidence_collector is not None
                else "evidence_collector_unavailable"
            ),
            created_at=utc_now_text(),
        )

        # 13. Store for idempotency
        self._diagnoses[(command_id, event_type)] = diagnosis

        # 14. Emit ai_diagnosis_created event
        self._emit_diagnosis_created(
            job_id=job_id,
            stage_index=stage_index,
            command_id=command_id,
            event_type=event_type,
            diagnosis=diagnosis,
            source_profile=source_profile,
            target_profile=target_profile,
        )

        return diagnosis

    def get_diagnosis(
        self,
        command_id: str,
        event_type: str,
    ) -> FailureDiagnosisRecord | None:
        """Retrieve an existing diagnosis record (idempotency lookup)."""
        return self._diagnoses.get((command_id, event_type))

    def list_diagnoses(self) -> tuple[FailureDiagnosisRecord, ...]:
        """List all in-memory diagnosis records."""
        return tuple(self._diagnoses.values())

    def clear(self) -> None:
        """Clear in-memory diagnoses (for testing)."""
        self._diagnoses.clear()

    # â”€â”€ Internal helpers â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    def _build_failure_summary(
        self,
        *,
        event_type: str,
        payload: dict[str, Any],
    ) -> str:
        """Build a human-readable failure summary from event payload."""
        build_status = str(payload.get("build_status", ""))
        test_status = str(payload.get("test_status", ""))
        transform_status = str(payload.get("transform_status", ""))
        message = str(payload.get("message", ""))
        stderr = str(payload.get("stderr", ""))[:200]
        stdout_tail = str(payload.get("stdout_tail", ""))[:200]

        parts: list[str] = []

        if event_type == "build_failed":
            parts.append(f"Build failed: {build_status}")
        elif event_type == "test_failed":
            parts.append(f"Test failed: {test_status}")
        elif event_type == "transform_failed":
            parts.append(f"Transform failed: {transform_status or build_status}")

        if message and message != parts[-1] if parts else False:
            parts.append(message[:200])
        if stderr:
            parts.append(f"stderr: {stderr}")
        if stdout_tail:
            parts.append(f"stdout: {stdout_tail}")

        return " | ".join(parts) if parts else f"{event_type} with no details"

    def _collect_and_classify(
        self,
        *,
        command_id: str,
        event_type: str,
        payload: dict[str, Any],
        build_status: str,
        test_status: str,
    ) -> dict[str, Any]:
        """Collect failure evidence and classify the failure.

        Uses existing evidence_collector.collect_failure_evidence() and
        failure_classifier.agent.classify_failure() when available.
        Falls back to minimal classification when collector is not configured.
        """
        if self._evidence_collector is None:
            return self._minimal_classification(
                event_type=event_type,
                build_status=build_status,
                test_status=test_status,
            )

        # Resolve run_dir from command_id if resolver available
        run_dir_str = None
        if self._run_dir_resolver:
            run_dir_str = self._run_dir_resolver(command_id, event_type)

        run_dir = Path(run_dir_str) if run_dir_str else Path("/tmp/unknown")

        # Extract artifact refs if available
        artifact_refs = payload.get("artifact_refs", {})
        if not isinstance(artifact_refs, dict):
            artifact_refs = {}

        sandbox_path = payload.get("sandbox_path", None)
        h2_report = payload.get("h2_startup_report", None)

        try:
            classification, _, _ = self._evidence_collector(
                run_id=command_id,
                run_dir=str(run_dir),
                sandbox_path=sandbox_path,
                artifact_refs=artifact_refs,
                build_status=build_status,
                test_status=test_status,
                h2_startup_report=h2_report,
            )
            return classification
        except Exception:
            return self._minimal_classification(
                event_type=event_type,
                build_status=build_status,
                test_status=test_status,
            )

    def _build_context_pack(
        self,
        *,
        event_type: str,
        stage_index: int,
        command_id: str,
        failure_type: str,
        failure_summary: str,
        classification: dict[str, Any] | None,
        redaction_status: str = "evidence_collector_unavailable",
        pom_summary_ref: str | None = None,
        sandbox_binding_ref: str | None = None,
        profile_id: str | None = None,
        artifact_refs_used: tuple[str, ...] = (),
    ) -> ContextPack:
        """Build an enriched ContextPack for the diagnosis.

        Passes F01 enrichment metadata fields so downstream services
        (prompt router, model client, cockpit) can use them.
        Evidence refs include the failure classification artifact path
        when available.
        """
        evidence_refs: list[str] = []

        if classification:
            evidence_refs.append(f"failure_type={classification.get('failure_type', 'UNKNOWN')}")
            evidence_refs.append(f"severity={classification.get('severity', 'UNKNOWN')}")
            if classification.get("evidence"):
                evidence_refs.extend(str(e) for e in classification["evidence"][:3])

        pack = ContextPackBuilder.build_context_pack(
            pack_type="repair_proposal",
            title=f"Diagnosis for {event_type}",
            description=failure_summary,
            evidence_refs=tuple(evidence_refs) if evidence_refs else ("no_evidence",),
            agent_name="v2-failure-diagnosis",
            event_type=event_type,
            stage_index=stage_index,
            command_id=command_id,
            failure_type=failure_type,
            redaction_status=redaction_status,
            pom_summary_ref=pom_summary_ref,
            sandbox_binding_ref=sandbox_binding_ref,
            profile_id=profile_id,
            artifact_refs_used=artifact_refs_used,
        )
        return pack

    def _emit_diagnosis_created(
        self,
        *,
        job_id: str,
        stage_index: int,
        command_id: str,
        event_type: str,
        diagnosis: FailureDiagnosisRecord,
        source_profile: str = "",
        target_profile: str = "",
    ) -> None:
        """Emit ai_diagnosis_created event via the configured event sink."""
        if self._event_sink is None:
            return

        event_payload = {
            "diagnosis_id": diagnosis.diagnosis_id,
            "context_pack_id": diagnosis.context_pack_id,
            "context_pack_checksum": diagnosis.context_pack_checksum,
            "command_id": diagnosis.command_id,
            "event_type": diagnosis.event_type,
            "failure_type": diagnosis.failure_type,
            "repair_proposal_id": diagnosis.repair_proposal_id,
            "model_invocation_id": diagnosis.model_invocation_id,
            "redaction_status": diagnosis.redaction_status,
        }
        if source_profile:
            event_payload["source_profile"] = source_profile
        if target_profile:
            event_payload["target_profile"] = target_profile
        self._event_sink(
            job_id=job_id,
            stage=stage_index,
            event_type="ai_diagnosis_created",
            status="completed",
            message=f"AI diagnosis created for {event_type} (command {command_id})",
            payload=event_payload,
        )

    @staticmethod
    def _minimal_classification(
        *,
        event_type: str,
        build_status: str,
        test_status: str,
    ) -> dict[str, Any]:
        """Create a fail-closed classification when evidence collector is unavailable.

        Without concrete evidence, this must NOT convert the raw event type into
        a justified concrete failure classification (e.g. BUILD_FAILED, TEST_FAILED).
        Fabricating certainty when evidence was unavailable contaminates retry cycles.

        When build_status contains a recognized BuildResultKind value, map it to
        the corresponding failure type so infrastructure/environment failures
        (e.g. TLS certificate trust) do not waste proposer/reviewer tokens.
        """
        build_status_lower = build_status.strip().lower()

        if build_status_lower == "repository_tls_failure":
            return {
                "failure_type": "ENVIRONMENT_FAILURE",
                "severity": "BLOCKER",
                "migration_blocker": True,
                "security_env_warning": False,
                "likely_root_cause": (
                    "Repository TLS certificate trust failure — "
                    "environment/infrastructure issue"
                ),
                "evidence": [],
                "recommended_next_step": (
                    "Verify TLS truststore and certificate configuration "
                    "on the build environment."
                ),
                "requires_human_review": True,
            }

        if build_status_lower == "dependency_error":
            return {
                "failure_type": "DEPENDENCY_CONFIGURATION_FAILURE",
                "severity": "BLOCKER",
                "migration_blocker": True,
                "security_env_warning": False,
                "likely_root_cause": (
                    "Maven dependency resolution failed — "
                    "check repository coordinates or dependency declarations."
                ),
                "evidence": [],
                "recommended_next_step": (
                    "Review dependency declarations in pom.xml for "
                    "correct coordinates, versions, and repository access."
                ),
                "requires_human_review": True,
            }

        if build_status_lower == "compilation_error":
            return {
                "failure_type": "APPLICATION_CODE_FAILURE",
                "severity": "BLOCKER",
                "migration_blocker": True,
                "security_env_warning": False,
                "likely_root_cause": (
                    "Java compilation error — "
                    "application source code failed to compile."
                ),
                "evidence": [],
                "recommended_next_step": (
                    "Review compiler error output and fix "
                    "source code compilation issues."
                ),
                "requires_human_review": True,
            }

        if test_status in {"TEST_FAILED", "TEST_ERROR"}:
            return {
                "failure_type": "TEST_FAILURE",
                "severity": "BLOCKER",
                "migration_blocker": True,
                "security_env_warning": False,
                "likely_root_cause": (
                    f"Test validation reported as failed ({test_status})."
                ),
                "evidence": [],
                "recommended_next_step": (
                    "Review test report and fix failing tests."
                ),
                "requires_human_review": True,
            }

        failure_type = "UNKNOWN_MIGRATION_FAILURE"
        severity = "UNKNOWN"

        if event_type == "build_failed":
            likely_root_cause = (
                f"Maven build reported as failed ({build_status or 'unknown status'}); "
                "evidence collector was unavailable — classification is UNKNOWN"
            )
        elif event_type == "test_failed":
            likely_root_cause = (
                f"Test validation reported as failed ({test_status or 'unknown status'}); "
                "evidence collector was unavailable — classification is UNKNOWN"
            )
        elif event_type == "transform_failed":
            likely_root_cause = (
                "Sandbox transform reported as failed; "
                "evidence collector was unavailable — classification is UNKNOWN"
            )
        else:
            likely_root_cause = (
                f"Event type {event_type!r} reported; "
                "evidence collector was unavailable — classification is UNKNOWN"
            )

        return {
            "failure_type": failure_type,
            "severity": severity,
            "migration_blocker": False,
            "security_env_warning": False,
            "likely_root_cause": likely_root_cause,
            "evidence": [],
            "recommended_next_step": "Rerun with evidence collector enabled to get concrete classification.",
            "requires_human_review": True,
        }

    # â”€â”€ Serialization â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

    @staticmethod
    def diagnosis_to_dict(diagnosis: FailureDiagnosisRecord) -> dict[str, Any]:
        """Convert a FailureDiagnosisRecord to a dict for API responses."""
        return {
            "diagnosis_id": diagnosis.diagnosis_id,
            "command_id": diagnosis.command_id,
            "event_type": diagnosis.event_type,
            "failure_type": diagnosis.failure_type,
            "context_pack_id": diagnosis.context_pack_id,
            "context_pack_checksum": diagnosis.context_pack_checksum,
            "repair_proposal_id": diagnosis.repair_proposal_id,
            "model_invocation_id": diagnosis.model_invocation_id,
            "redaction_status": diagnosis.redaction_status,
            "created_at": diagnosis.created_at,
        }

    @staticmethod
    def is_diagnosable_event(event_type: str) -> bool:
        """Check if an event type can trigger diagnosis."""
        return event_type in V2FailureDiagnosisService.TRIGGER_EVENT_TYPES


# â”€â”€ Orchestrator integration helper â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€


def create_orchestrator_diagnosis_callback(
    service: V2FailureDiagnosisService | None = None,
    *,
    repair_flow: Any | None = None,
    event_sink: Any | None = None,
    evidence_collector: Any | None = None,
    run_dir_resolver: Any | None = None,
    profile_id: str | None = None,
    pom_summary_ref: str | None = None,
    sandbox_binding_ref: str | None = None,
) -> Callable[[str, int, str, str, dict[str, Any]], None]:
    """Create a callback suitable for V2OrchestratorRunner(diagnosis_callback=...).

    The returned callback has the exact signature that
    V2OrchestratorRunner._maybe_diagnose expects:
        (job_id, stage_index, command_id, event_type, payload) -> None

    Usage:
        svc = V2FailureDiagnosisService(repair_flow=..., event_sink=...)
        runner = V2OrchestratorRunner(
            unit_of_work_factory=...,
            diagnosis_callback=create_orchestrator_diagnosis_callback(svc),
        )
    """
    if service is None:
        service = V2FailureDiagnosisService(
            repair_flow=repair_flow,
            event_sink=event_sink,
            evidence_collector=evidence_collector,
            run_dir_resolver=run_dir_resolver,
        )

    def callback(
        job_id: str,
        stage_index: int,
        command_id: str,
        event_type: str,
        payload: dict[str, Any],
    ) -> None:
        service.diagnose(  # type: ignore[union-attr]
            job_id=job_id,
            stage_index=stage_index,
            command_id=command_id,
            event_type=event_type,
            payload=payload,
            profile_id=profile_id,
            pom_summary_ref=pom_summary_ref,
            sandbox_binding_ref=sandbox_binding_ref,
        )

    return callback

