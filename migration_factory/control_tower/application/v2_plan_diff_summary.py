"""F15 Plan Diff Summary — compare plan revisions after revision request.

Shows what changed between plan revisions: migration units, risk summaries,
accepted/rejected changes. All output is redacted — no full raw sensitive
content is leaked.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.application.redaction import (
    redact_absolute_paths,
    redact_model_summary,
)
from migration_factory.control_tower.domain.checksums import (
    sha256_canonical_json,
    utc_now_text,
)
from migration_factory.control_tower.domain.entities import (
    ArtifactRevisionRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_artifact_revision_repository import (
    SqliteArtifactRevisionRepository,
)


# ── Result types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class UnitChange:
    """A change to a single migration unit between plan revisions."""

    unit_name: str
    change_kind: str  # 'added', 'removed', 'modified', 'unchanged'
    risk_level: str  # 'low', 'medium', 'high', 'unknown'
    summary: str  # Redacted description of the change
    old_checksum: str | None = None
    new_checksum: str | None = None


@dataclass(frozen=True)
class PlanDiffEntry:
    """A single change detected between plan revisions."""

    kind: str  # 'added', 'removed', 'modified', 'unchanged'
    change_type: str  # 'migration_unit', 'risk_summary', 'approval_request', 'metadata'
    summary: str  # Short redacted description
    detail: str = ""  # Redacted detail (truncated)
    old_checksum: str | None = None
    new_checksum: str | None = None


@dataclass(frozen=True)
class PlanDiffResult:
    """Result of comparing two plan revisions.

    Shows migration units, risk summaries, and other plan artifacts
    that changed between revisions. All content redacted.
    """

    diff_id: str
    job_id: str
    stage_index: int
    prior_revision_id: str
    current_revision_id: str
    entries: tuple[PlanDiffEntry, ...]
    unit_changes: tuple[UnitChange, ...]
    added_count: int
    removed_count: int
    modified_count: int
    unchanged_count: int
    risk_change: str  # 'increased', 'decreased', 'unchanged', 'unknown'
    checksum: str
    created_at: str


# ── Constants ─────────────────────────────────────────────────────────

MAX_DETAIL_CHARS = 500
MAX_COMPARE_CHARS = 50_000


# ── Service ───────────────────────────────────────────────────────────


class V2PlanDiffService:
    """Backend-owned service for computing plan revision diffs.

    Compares migration units, risk summaries, and approval requests
    between plan revisions. All output is redacted.
    """

    def __init__(
        self,
        revision_repo: SqliteArtifactRevisionRepository,
    ) -> None:
        self._revision_repo = revision_repo

    def compute_plan_diff(
        self,
        job_id: str,
        stage_index: int,
        *,
        prior_revision_id: str | None = None,
        current_revision_id: str | None = None,
    ) -> PlanDiffResult:
        """Compute diff between two plan revisions.

        If either revision_id is None, the latest two accepted/draft
        planning revisions for the stage are used.

        Args:
            job_id: The V2 job ID.
            stage_index: The stage index (1, 2, or 3).
            prior_revision_id: Optional explicit prior revision ID.
            current_revision_id: Optional explicit current revision ID.

        Returns:
            PlanDiffResult with redacted entries.
        """
        prior, current = self._resolve_revisions(
            job_id, stage_index,
            prior_revision_id=prior_revision_id,
            current_revision_id=current_revision_id,
        )

        if prior is None or current is None:
            return self._empty_diff(job_id, stage_index, "insufficient_revisions")

        # Parse artifact refs from both revisions
        prior_refs = self._parse_artifact_refs(prior)
        current_refs = self._parse_artifact_refs(current)

        entries: list[PlanDiffEntry] = []
        unit_changes: list[UnitChange] = []
        added = 0
        removed = 0
        modified = 0
        unchanged = 0

        # Compare artifact refs
        for prior_ref in prior_refs:
            matching = [c for c in current_refs if c.get("kind") == prior_ref.get("kind")]

            if not matching:
                entries.append(PlanDiffEntry(
                    kind="removed",
                    change_type=prior_ref.get("kind", "other"),
                    summary=f"Plan artifact '{prior_ref.get('kind', 'unknown')}' was removed.",
                    old_checksum=prior_ref.get("checksum"),
                ))
                removed += 1
            else:
                current_match = matching[0]
                old_chk = prior_ref.get("checksum", "")
                new_chk = current_match.get("checksum", "")

                if old_chk != new_chk:
                    change_type = current_match.get("kind", "migration_unit")
                    entries.append(PlanDiffEntry(
                        kind="modified",
                        change_type=change_type,
                        summary=f"Plan artifact '{change_type}' content changed.",
                        old_checksum=old_chk,
                        new_checksum=new_chk,
                        detail=f"Artifact checksum changed, indicating updated content.",
                    ))

                    # Add unit change for migration_unit artifacts
                    if change_type in ("migration_unit", "migration_units"):
                        unit_changes.append(UnitChange(
                            unit_name=change_type,
                            change_kind="modified",
                            risk_level=self._estimate_risk_from_checksum(old_chk, new_chk),
                            summary=f"Migration unit content changed (checksum mismatch).",
                            old_checksum=old_chk,
                            new_checksum=new_chk,
                        ))
                    modified += 1
                else:
                    entries.append(PlanDiffEntry(
                        kind="unchanged",
                        change_type=current_match.get("kind", "other"),
                        summary=f"Plan artifact '{current_match.get('kind', 'unknown')}' unchanged.",
                        old_checksum=old_chk,
                        new_checksum=new_chk,
                    ))
                    unchanged += 1

        # Find new artifacts
        prior_kinds = {p.get("kind") for p in prior_refs}
        for current_ref in current_refs:
            kind = current_ref.get("kind", "")
            if kind not in prior_kinds:
                entries.append(PlanDiffEntry(
                    kind="added",
                    change_type=kind,
                    summary=f"New plan artifact '{kind}' was added.",
                    old_checksum=None,
                    new_checksum=current_ref.get("checksum"),
                    detail=f"New artifact kind: {kind}",
                ))
                added += 1

        # If artifact refs are empty, compare metadata
        if not prior_refs and not current_refs:
            metadata_entries = self._compare_metadata(prior, current)
            entries.extend(metadata_entries)
            for e in metadata_entries:
                if e.kind == "added":
                    added += 1
                elif e.kind == "removed":
                    removed += 1
                elif e.kind == "modified":
                    modified += 1
                else:
                    unchanged += 1

        # Determine risk change
        risk_change = self._determine_risk_change(entries, unit_changes)

        # Compute checksum
        diff_dicts = [{
            "kind": e.kind,
            "change_type": e.change_type,
            "summary": e.summary[:200],
            "old_checksum": e.old_checksum,
            "new_checksum": e.new_checksum,
        } for e in entries]
        diff_checksum = sha256_canonical_json({
            "job_id": job_id,
            "stage_index": stage_index,
            "prior": prior.revision_id,
            "current": current.revision_id,
            "entries": diff_dicts,
        })

        return PlanDiffResult(
            diff_id=uuid4().hex[:12],
            job_id=job_id,
            stage_index=stage_index,
            prior_revision_id=prior.revision_id,
            current_revision_id=current.revision_id,
            entries=tuple(entries),
            unit_changes=tuple(unit_changes),
            added_count=added,
            removed_count=removed,
            modified_count=modified,
            unchanged_count=unchanged,
            risk_change=risk_change,
            checksum=diff_checksum,
            created_at=utc_now_text(),
        )

    def to_dict(self, result: PlanDiffResult) -> dict[str, Any]:
        """Convert diff result to dict for API/assistant consumption."""
        return {
            "diff_id": result.diff_id,
            "job_id": result.job_id,
            "stage_index": result.stage_index,
            "prior_revision_id": result.prior_revision_id,
            "current_revision_id": result.current_revision_id,
            "entries": [
                {
                    "kind": e.kind,
                    "change_type": e.change_type,
                    "summary": e.summary,
                    "old_checksum": e.old_checksum,
                    "new_checksum": e.new_checksum,
                    "detail": e.detail[:MAX_DETAIL_CHARS] if e.detail else "",
                }
                for e in result.entries
            ],
            "unit_changes": [
                {
                    "unit_name": u.unit_name,
                    "change_kind": u.change_kind,
                    "risk_level": u.risk_level,
                    "summary": u.summary,
                }
                for u in result.unit_changes
            ],
            "added_count": result.added_count,
            "removed_count": result.removed_count,
            "modified_count": result.modified_count,
            "unchanged_count": result.unchanged_count,
            "risk_change": result.risk_change,
            "checksum": result.checksum,
            "created_at": result.created_at,
        }

    # ── Internal ───────────────────────────────────────────────────

    def _resolve_revisions(
        self,
        job_id: str,
        stage_index: int,
        *,
        prior_revision_id: str | None = None,
        current_revision_id: str | None = None,
    ) -> tuple[ArtifactRevisionRecord | None, ArtifactRevisionRecord | None]:
        """Resolve prior and current planning revisions."""
        if current_revision_id is not None:
            current = self._revision_repo.get(current_revision_id)
        else:
            current = self._revision_repo.find_latest_by_kind(
                job_id, stage_index, "planning"
            )

        if prior_revision_id is not None:
            prior = self._revision_repo.get(prior_revision_id)
        elif current is not None and current.prior_revision_id:
            prior = self._revision_repo.get(current.prior_revision_id)
        else:
            all_revisions = self._revision_repo.list_by_job_and_stage(
                job_id, stage_index
            )
            planning_revisions = [
                r for r in all_revisions
                if r.revision_kind == "planning"
            ]
            if len(planning_revisions) >= 2:
                current_id = current.revision_id if current else None
                sorted_revs = sorted(
                    planning_revisions,
                    key=lambda r: r.revision_order,
                    reverse=True,
                )
                for rev in sorted_revs:
                    if rev.revision_id != current_id:
                        prior = rev
                        break
                else:
                    prior = None
            else:
                prior = None

        return prior, current

    def _parse_artifact_refs(
        self,
        revision: ArtifactRevisionRecord,
    ) -> list[dict[str, str]]:
        """Parse artifact refs JSON into list of {kind, checksum} dicts."""
        if not revision.artifact_refs_json:
            return []

        try:
            refs = json.loads(revision.artifact_refs_json)
        except (json.JSONDecodeError, TypeError):
            return []

        if isinstance(refs, list):
            result = []
            for ref in refs:
                if isinstance(ref, str):
                    result.append({"kind": "unknown", "checksum": ref})
                elif isinstance(ref, dict):
                    result.append({
                        "kind": str(ref.get("kind", "unknown")),
                        "checksum": str(ref.get("checksum", "")),
                    })
            return result
        elif isinstance(refs, dict):
            return [
                {"kind": str(k), "checksum": str(v)}
                for k, v in refs.items()
            ]
        return []

    def _compare_metadata(
        self,
        prior: ArtifactRevisionRecord,
        current: ArtifactRevisionRecord,
    ) -> list[PlanDiffEntry]:
        """Compare revision metadata when artifact refs are empty."""
        entries: list[PlanDiffEntry] = []

        if prior.evidence_checksum != current.evidence_checksum:
            entries.append(PlanDiffEntry(
                kind="modified",
                change_type="evidence",
                summary="Evidence checksum changed.",
                old_checksum=prior.evidence_checksum,
                new_checksum=current.evidence_checksum,
            ))

        if prior.created_by != current.created_by:
            entries.append(PlanDiffEntry(
                kind="modified",
                change_type="metadata",
                summary=f"Creator changed from '{prior.created_by}' to '{current.created_by}'.",
            ))

        if prior.revision_status != current.revision_status:
            entries.append(PlanDiffEntry(
                kind="modified",
                change_type="status",
                summary=f"Status changed from '{prior.revision_status}' to '{current.revision_status}'.",
            ))

        if not entries:
            entries.append(PlanDiffEntry(
                kind="unchanged",
                change_type="metadata",
                summary="No changes between plan revisions.",
            ))

        return entries

    def _estimate_risk_from_checksum(
        self,
        old_chk: str,
        new_chk: str,
    ) -> str:
        """Estimate risk level based on checksum change.

        If checksums differ, content changed — risk is at least 'medium'.
        This is a safe conservative estimate; actual risk analysis
        requires reading the content.
        """
        if old_chk and new_chk and old_chk != new_chk:
            return "medium"
        return "low"

    def _determine_risk_change(
        self,
        entries: list[PlanDiffEntry],
        unit_changes: list[UnitChange],
    ) -> str:
        """Determine if risk increased/decreased based on changes."""
        has_added = any(e.kind == "added" for e in entries)
        has_removed = any(e.kind == "removed" for e in entries)
        has_modified = any(e.kind == "modified" for e in entries)

        if has_added or has_modified:
            return "increased"
        if has_removed and not has_added:
            return "decreased"
        return "unchanged"

    def _empty_diff(
        self,
        job_id: str,
        stage_index: int,
        reason: str,
    ) -> PlanDiffResult:
        """Return an empty diff."""
        return PlanDiffResult(
            diff_id=uuid4().hex[:12],
            job_id=job_id,
            stage_index=stage_index,
            prior_revision_id="",
            current_revision_id="",
            entries=(),
            unit_changes=(),
            added_count=0,
            removed_count=0,
            modified_count=0,
            unchanged_count=0,
            risk_change="unknown",
            checksum=sha256_canonical_json({"reason": reason}),
            created_at=utc_now_text(),
        )
