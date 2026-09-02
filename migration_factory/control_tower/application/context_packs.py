"""Context pack manifest service for V1-11A.

Persists bounded, redacted context pack manifests with evidence
references, bounds, and checksums. This module stores manifests
only; bounded retrievers and redaction filtering belong to V1-11B
and V1-11C respectively.
"""

from __future__ import annotations

import json as _json
from uuid import uuid4

from migration_factory.control_tower.domain.entities import V1ContextPackManifestRecord
from migration_factory.control_tower.domain.checksums import utc_now_text, canonical_json_text, sha256_canonical_json
from migration_factory.control_tower.application.dto import ContextPackManifestDto
from migration_factory.control_tower.application.context_pack_redaction import (
    redact_context_pack_metadata,
)


class ContextPackManifestService:
    """Service for persisting and querying context pack manifests."""

    def __init__(self, unit_of_work_factory) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def persist_manifest(
        self,
        *,
        manifest_id: str | None = None,
        pack_type: str,
        pack_version: str,
        title: str,
        job_id: str | None = None,
        stage_run_id: str | None = None,
        description: str | None = None,
        evidence_refs_json: str | None = None,
        bounds_json: str | None = None,
        enrichment_metadata: dict[str, object] | None = None,
        redaction_policy: str | None = None,
        redacted_summary: str | None = None,
        model_profile_id: str | None = None,
        model_name: str | None = None,
        token_count: int | None = None,
        created_by: str = "system",
        correlation_id: str | None = None,
        causation_id: str | None = None,
    ) -> V1ContextPackManifestRecord:
        """Persist a context pack manifest record.

        Computes checksum over the manifest content automatically.
        If enrichment_metadata is provided, it is redacted and merged
        into bounds_json so downstream queries can access it.
        """
        if manifest_id is None:
            manifest_id = f"cp-{uuid4().hex}"

        now = utc_now_text()

        # Merge enrichment metadata into bounds_json if provided
        if enrichment_metadata:
            redacted_meta = redact_context_pack_metadata(enrichment_metadata)
            existing_bounds: dict[str, object] = {}
            if bounds_json:
                try:
                    existing_bounds = _json.loads(bounds_json)
                except (_json.JSONDecodeError, TypeError):
                    pass
            existing_bounds["_enrichment"] = redacted_meta
            bounds_json = _json.dumps(existing_bounds, separators=(",", ":"), sort_keys=True)

        # Build content dict for checksum computation
        content = {
            "manifest_id": manifest_id,
            "pack_type": pack_type,
            "pack_version": pack_version,
            "title": title,
            "job_id": job_id,
            "stage_run_id": stage_run_id,
            "description": description,
            "evidence_refs_json": evidence_refs_json,
            "bounds_json": bounds_json,
            "redaction_policy": redaction_policy,
            "redacted_summary": redacted_summary,
            "model_profile_id": model_profile_id,
            "model_name": model_name,
            "token_count": token_count,
        }
        # Remove None values for stable checksum
        clean_content = {k: v for k, v in content.items() if v is not None}
        checksum = sha256_canonical_json(clean_content)
        checksum_algorithm = "sha256"

        record = V1ContextPackManifestRecord(
            manifest_id=manifest_id,
            job_id=job_id,
            stage_run_id=stage_run_id,
            pack_type=pack_type,
            pack_version=pack_version,
            title=title,
            description=description,
            evidence_refs_json=evidence_refs_json,
            bounds_json=bounds_json,
            redaction_policy=redaction_policy,
            redacted_summary=redacted_summary,
            checksum_algorithm=checksum_algorithm,
            checksum=checksum,
            model_profile_id=model_profile_id,
            model_name=model_name,
            token_count=token_count,
            created_at=now,
            created_by=created_by,
            correlation_id=correlation_id,
            causation_id=causation_id,
        )

        with self._unit_of_work_factory() as uow:
            uow.v1_context_pack_manifests.insert(record)

            # Record audit event
            audit_payload = {
                "action": "context_pack_manifest_persisted",
                "manifest_id": manifest_id,
                "pack_type": pack_type,
                "pack_version": pack_version,
                "title": title,
                "checksum": checksum,
            }
            uow.audit_records.append_global_audit(
                audit_id=uuid4().hex,
                actor_type="system",
                actor_id=created_by,
                action="context_pack_manifest_persisted",
                payload_json=_json.dumps(audit_payload, separators=(",", ":"), sort_keys=True),
                created_at=now,
                correlation_id=correlation_id,
                causation_id=causation_id,
            )

        return record

    def get_manifest(self, manifest_id: str) -> V1ContextPackManifestRecord | None:
        with self._unit_of_work_factory() as uow:
            return uow.v1_context_pack_manifests.get(manifest_id)

    def list_manifests(self) -> tuple[V1ContextPackManifestRecord, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.v1_context_pack_manifests.list()

    def list_manifests_for_job(self, job_id: str) -> tuple[V1ContextPackManifestRecord, ...]:
        with self._unit_of_work_factory() as uow:
            return uow.v1_context_pack_manifests.list_for_job(job_id)

    def to_dto(self, record: V1ContextPackManifestRecord) -> ContextPackManifestDto:
        """Convert a domain record to a public DTO.

        Only redacted fields are exposed. Raw prompts, secrets,
        and deployment IDs are absent by construction since the
        domain record never stores them.
        """
        # Extract enrichment metadata from bounds_json if present
        enrichment_meta = self._extract_enrichment_from_bounds(record.bounds_json)
        return ContextPackManifestDto(
            manifest_id=record.manifest_id,
            pack_type=record.pack_type,
            pack_version=record.pack_version,
            title=record.title,
            description=record.description,
            evidence_refs_json=record.evidence_refs_json,
            bounds_json=record.bounds_json,
            redacted_summary=record.redacted_summary,
            checksum_algorithm=record.checksum_algorithm,
            checksum=record.checksum,
            model_profile_id=record.model_profile_id,
            model_name=record.model_name,
            token_count=record.token_count,
            created_at=record.created_at,
            created_by=record.created_by,
            enrichment_metadata=enrichment_meta,
        )

    @staticmethod
    def _extract_enrichment_from_bounds(bounds_json: str | None) -> dict[str, object]:
        """Extract enrichment metadata from bounds_json if present.

        Returns empty dict if no enrichment data exists (backward compatible).
        """
        if not bounds_json:
            return {}
        try:
            bounds = _json.loads(bounds_json)
            if isinstance(bounds, dict) and "_enrichment" in bounds:
                return dict(bounds["_enrichment"])
        except (_json.JSONDecodeError, TypeError):
            pass
        return {}
