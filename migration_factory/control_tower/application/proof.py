"""Deterministic proof gate computation for V1 pipeline.

Proof gates are deterministic checksums computed from the outputs of
each stage in the springboot-216-to-356-java21-three-stage pipeline:

- Gate 1: Stage 1 sandbox output checksum (Java 11 / Boot 2.7.18)
- Gate 2: Stage 2 sandbox output checksum (Java 17 / Boot 3.5.6)
- Gate 3: Stage 3 sandbox output checksum (Java 21 / Boot 3.5.6)

All three gates MUST be present and match for proof to be considered
complete. Model summaries CANNOT create or override proof gates.
Proof gates are computed from the stage chain ledger, never from
LLM output or browser payloads.

Browser payloads CANNOT choose:
- raw executable paths
- Maven goals or build commands
- arbitrary shell commands
- working directories
- model deployment IDs

LLM flows CANNOT execute commands, approve decisions, or write files
directly.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any
from uuid import uuid4 as _uuid4

from migration_factory.control_tower.application.ports import (
    ControlTowerUnitOfWork,
)
from migration_factory.control_tower.application.services import UnitOfWorkFactory
from migration_factory.control_tower.domain.checksums import (
    canonical_json_text,
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.domain.entities import (
    StageChainLedgerRecord,
    StageChainEventRecord,
    RunEventRecord,
    AuditRecord,
    V1ProofReportRecord,
    V1ProofReportGateRecord,
)
from migration_factory.control_tower.domain.states import TargetProofLevel
from migration_factory.control_tower.domain.errors import (
    NotFoundError,
)


class DeterministicProofGateService:
    """Compute deterministic proof gates from stage chain ledger entries.

    Proof gates are computed deterministically from the output checksums
    registered in the stage chain ledger. Model summaries CANNOT create
    or override proof gates.

    All three gates must be present for proof to be complete:
    - Gate 1 (Stage 1 sandbox) - required
    - Gate 2 (Stage 2 sandbox) - required
    - Gate 3 (Stage 3 sandbox) - required
    """

    PROOF_GATE_ALGORITHM = "sha256"

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def compute_proof_gates(
        self,
        job_id: str,
        computed_by: str = "system",
    ) -> dict[int, str]:
        """Compute deterministic proof gates for a job.

        Returns a dict mapping stage_index -> proof_gate_checksum.
        Raises NotFoundError if the job or stage ledger entries are missing.
        Raises ValueError if any stage gate cannot be computed.
        """
        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(job_id)
            if job is None:
                raise NotFoundError("migration job", job_id)

            ledger_entries = uow.stage_chain_ledger.list_for_job(job_id)

            gates: dict[int, str] = {}
            now = utc_now_text()

            # Compute gates for all three stages
            for stage_index in (1, 2, 3):
                entry = self._find_ledger_entry(ledger_entries, stage_index)

                if entry is None or entry.output_checksum is None:
                    raise ValueError(
                        f"Cannot compute proof gate for stage {stage_index}: "
                        "stage has no output checksum"
                    )

                gate = self._compute_gate(
                    job_id=job_id,
                    stage_index=stage_index,
                    output_checksum=entry.output_checksum,
                    output_artifact_id=entry.output_artifact_id,
                    input_checksum=entry.input_checksum,
                    chain_status=entry.chain_status,
                )
                gates[stage_index] = gate

                # Record gate computed event
                uow.stage_chain_ledger.insert_event(
                    StageChainEventRecord(
                        event_id=f"proof-gate-{job_id}-s{stage_index:04d}",
                        job_id=job_id,
                        stage_index=stage_index,
                        event_type="proof_gate_computed",
                        prior_status=None,
                        new_status="computed",
                        ledger_id=entry.ledger_id,
                        output_id=entry.output_artifact_id,
                        payload_json=canonical_json_text({
                            "stage_index": stage_index,
                            "output_checksum": entry.output_checksum,
                            "proof_gate": gate,
                            "proof_gate_algorithm": self.PROOF_GATE_ALGORITHM,
                            "input_checksum": entry.input_checksum,
                            "output_artifact_id": entry.output_artifact_id,
                        }),
                        payload_checksum=sha256_canonical_json({
                            "stage_index": stage_index,
                            "proof_gate": gate,
                            "output_checksum": entry.output_checksum,
                        }),
                        created_at=now,
                        created_by=computed_by,
                    )
                )

            # Record proof_gates_all_computed event when all three gates are done
            uow.stage_chain_ledger.insert_event(
                StageChainEventRecord(
                    event_id=f"proof-gate-{job_id}-all",
                    job_id=job_id,
                    stage_index=None,
                    event_type="proof_gates_all_computed",
                    prior_status=None,
                    new_status="all_computed",
                    ledger_id=None,
                    output_id=None,
                    payload_json=canonical_json_text({
                        "gate_count": len(gates),
                        "gates": {str(k): v for k, v in gates.items()},
                        "gate_algorithm": self.PROOF_GATE_ALGORITHM,
                        "target_proof_level": TargetProofLevel.BUILD_TEST_VERIFIED.value,
                    }),
                    payload_checksum=sha256_canonical_json({
                        "gate_count": len(gates),
                        "gates": {str(k): v for k, v in gates.items()},
                    }),
                    created_at=now,
                    created_by=computed_by,
                )
            )

            # Record audit event
            uow.audit_records.append_global_audit(
                audit_id=f"audit-proof-gates-{job_id}",
                actor_type="system",
                actor_id=computed_by,
                action="proof_gates_computed",
                payload_json=canonical_json_text({
                    "job_id": job_id,
                    "gates": {str(k): v for k, v in gates.items()},
                    "algorithm": self.PROOF_GATE_ALGORITHM,
                }),
                created_at=now,
            )

        return gates

    def verify_proof_gate(
        self,
        job_id: str,
        stage_index: int,
        expected_gate: str,
    ) -> bool:
        """Verify a specific proof gate for a given stage.

        Re-computes the gate from the ledger and checks against the
        expected value.
        """
        try:
            computed_gates = self.compute_proof_gates(job_id)
        except (NotFoundError, ValueError):
            return False

        if stage_index not in computed_gates:
            return False

        return computed_gates[stage_index] == expected_gate

    def get_gate_summary(
        self,
        job_id: str,
    ) -> dict[str, Any]:
        """Get a summary of all computed proof gates for a job.

        Returns gate information from ledger events, without re-computing.
        """
        with self._unit_of_work_factory() as uow:
            events = uow.stage_chain_ledger.list_events_for_job(job_id)
            ledger_entries = uow.stage_chain_ledger.list_for_job(job_id)

            gates: dict[str, Any] = {}
            all_computed = False

            for event in events:
                if event.event_type == "proof_gate_computed":
                    payload = json.loads(event.payload_json)
                    si = payload.get("stage_index")
                    gates[str(si)] = {
                        "stage_index": si,
                        "proof_gate": payload.get("proof_gate"),
                        "output_checksum": payload.get("output_checksum"),
                        "algorithm": payload.get("proof_gate_algorithm", "sha256"),
                        "computed_at": event.created_at,
                    }
                elif event.event_type == "proof_gates_all_computed":
                    all_computed = True

            return {
                "job_id": job_id,
                "gate_count": len(gates),
                "all_gates_computed": all_computed,
                "gates": gates,
                "required_gates": 3,
                "stages": [
                    {
                        "stage_index": e.stage_index,
                        "output_checksum": e.output_checksum,
                        "chain_status": e.chain_status,
                    }
                    for e in ledger_entries
                    if e.output_checksum is not None
                ],
            }

    def _find_ledger_entry(
        self,
        entries: tuple[StageChainLedgerRecord, ...],
        stage_index: int,
    ) -> StageChainLedgerRecord | None:
        """Find a ledger entry by stage index."""
        for entry in entries:
            if entry.stage_index == stage_index:
                return entry
        return None

    def _compute_gate(
        self,
        *,
        job_id: str,
        stage_index: int,
        output_checksum: str,
        output_artifact_id: str | None,
        input_checksum: str | None,
        chain_status: str,
    ) -> str:
        """Compute a deterministic proof gate hash.

        The gate is a SHA-256 hash of the concatenated deterministic
        inputs: job_id, stage_index, output_checksum, and a constant
        domain separator.
        """
        domain_separator = b"V1_PROOF_GATE_v1"
        gate_input = (
            domain_separator
            + job_id.encode("utf-8")
            + str(stage_index).encode("utf-8")
            + (output_checksum or "").encode("utf-8")
            + (output_artifact_id or "").encode("utf-8")
            + (input_checksum or "").encode("utf-8")
        )
        return hashlib.sha256(gate_input).hexdigest()


class FinalReportService:
    """Generate deterministic final report artifacts from proof gates.

    A final report is generated when all three deterministic proof gates
    are present and verified. The report is a deterministic summary of
    the proof gates, stage chain ledger entries, and pipeline metadata.

    Model summaries CANNOT create or override proof reports.
    Reports are computed from proof gates and stage chain data only.
    """

    def __init__(self, unit_of_work_factory: UnitOfWorkFactory) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._gate_service = DeterministicProofGateService(unit_of_work_factory)

    def generate_final_report(
        self,
        job_id: str,
        generated_by: str = "system",
    ) -> dict[str, Any]:
        """Generate a deterministic final report artifact.

        Returns the report dict. Raises ValueError if proof gates are
        not complete.
        """
        # Compute proof gates first (this opens its own UOW)
        gates = self._gate_service.compute_proof_gates(job_id, computed_by=generated_by)

        if len(gates) < 3:
            raise ValueError(
                f"Cannot generate final report: only {len(gates)} of 3 proof gates computed"
            )

        with self._unit_of_work_factory() as uow:
            job = uow.migration_jobs.get(job_id)
            if job is None:
                raise NotFoundError("migration job", job_id)

            now = utc_now_text()
            report_id = f"report-{_uuid4().hex[:12]}"

            # Get ledger entries for stage details
            ledger_entries = uow.stage_chain_ledger.list_for_job(job_id)

            # Build stage details
            stage_details = []
            for stage_index in (1, 2, 3):
                gate_checksum = gates.get(stage_index, "")
                entry = None
                for e in ledger_entries:
                    if e.stage_index == stage_index:
                        entry = e
                        break

                stage_details.append({
                    "stage_index": stage_index,
                    "output_checksum": entry.output_checksum if entry else None,
                    "proof_gate_checksum": gate_checksum,
                    "chain_status": entry.chain_status if entry else "unknown",
                    "input_checksum": entry.input_checksum if entry else None,
                })

            summary = {
                "job_id": job_id,
                "pipeline_id": "springboot-216-to-356-java21-three-stage",
                "stage_count": 3,
                "gate_count": len(gates),
                "proof_complete": True,
                "target_proof_level": TargetProofLevel.BUILD_TEST_VERIFIED.value,
                "stages": stage_details,
                "generated_at": now,
                "generated_by": generated_by,
            }

            summary_json_str = json.dumps(summary, sort_keys=True)
            report_checksum = sha256_canonical_json(summary)

            # Insert report
            report = V1ProofReportRecord(
                report_id=report_id,
                job_id=job_id,
                report_version=1,
                report_checksum=report_checksum,
                gate_count=len(gates),
                all_gates_present=1,
                proof_complete=1,
                target_proof_level=TargetProofLevel.BUILD_TEST_VERIFIED.value,
                pipeline_id="springboot-216-to-356-java21-three-stage",
                stage_count=3,
                summary_json=summary_json_str,
                generated_at=now,
                generated_by=generated_by,
            )
            uow.v1_proof_reports.insert(report)

            # Insert gate details
            for stage_detail in stage_details:
                report_gate_id = f"rpt-gate-{report_id}-s{stage_detail['stage_index']:04d}"
                gate = V1ProofReportGateRecord(
                    report_gate_id=report_gate_id,
                    report_id=report_id,
                    job_id=job_id,
                    stage_index=stage_detail["stage_index"],
                    output_checksum=stage_detail["output_checksum"] or "",
                    proof_gate_checksum=stage_detail["proof_gate_checksum"],
                    chain_status=stage_detail["chain_status"],
                )
                uow.v1_proof_report_gates.insert(gate)

            # Record audit event
            uow.audit_records.append_global_audit(
                audit_id=f"audit-proof-report-{_uuid4().hex[:12]}",
                actor_type="system",
                actor_id=generated_by,
                action="proof_report_generated",
                payload_json=canonical_json_text({
                    "job_id": job_id,
                    "report_id": report_id,
                    "report_checksum": report_checksum,
                    "gate_count": len(gates),
                    "proof_complete": True,
                }),
                created_at=now,
            )

        return summary

    def get_report(
        self,
        job_id: str,
    ) -> dict[str, Any] | None:
        """Get the most recent final report for a job."""
        with self._unit_of_work_factory() as uow:
            report = uow.v1_proof_reports.get_latest_for_job(job_id)
            if report is None:
                return None

            gates = uow.v1_proof_report_gates.list_for_report(report.report_id)

            return {
                "report_id": report.report_id,
                "job_id": report.job_id,
                "report_version": report.report_version,
                "report_checksum": report.report_checksum,
                "gate_count": report.gate_count,
                "all_gates_present": bool(report.all_gates_present),
                "proof_complete": bool(report.proof_complete),
                "target_proof_level": report.target_proof_level,
                "pipeline_id": report.pipeline_id,
                "summary": json.loads(report.summary_json) if report.summary_json else {},
                "gates": [
                    {
                        "stage_index": g.stage_index,
                        "output_checksum": g.output_checksum,
                        "proof_gate_checksum": g.proof_gate_checksum,
                        "chain_status": g.chain_status,
                    }
                    for g in gates
                ],
                "generated_at": report.generated_at,
                "generated_by": report.generated_by,
            }
