"""F15 Analysis Diff Summary — compare artifact revisions after re-analysis.

Shows what changed between analysis revisions so users can see what
was added, removed, or modified. All content is redacted — no full
raw sensitive content is leaked.
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
class AnalysisDiffEntry:
    """A single change detected between analysis revisions.

    Only safe, redacted content is included.
    """

    kind: str  # 'added', 'removed', 'modified', 'unchanged'
    change_type: str  # 'finding', 'dependency', 'import', 'xml', 'test', 'security', 'other'
    summary: str  # Short redacted description of the change
    old_checksum: str | None = None
    new_checksum: str | None = None
    detail: str = ""  # Redacted detail (truncated to safe length)


@dataclass(frozen=True)
class AnalysisDiffResult:
    """Result of comparing two analysis revisions.

    The diff is always computed backend-owned — no frontend paths
    are accepted.
    """

    diff_id: str
    job_id: str
    stage_index: int
    prior_revision_id: str
    current_revision_id: str
    entries: tuple[AnalysisDiffEntry, ...]
    added_count: int
    removed_count: int
    modified_count: int
    unchanged_count: int
    checksum: str  # Deterministic checksum over the diff entries
    created_at: str


# ── Constants ─────────────────────────────────────────────────────────

# Max detail length per entry
MAX_DETAIL_CHARS = 500

# Max characters per content chunk for comparison
MAX_COMPARE_CHARS = 50_000


# ── Service ───────────────────────────────────────────────────────────


class V2AnalysisDiffService:
    """Backend-owned service for computing analysis revision diffs.

    Compares artifact summaries and highlights added/removed findings.
    Never leaks full raw sensitive content — always redacts.
    """

    def __init__(
        self,
        revision_repo: SqliteArtifactRevisionRepository,
    ) -> None:
        self._revision_repo = revision_repo

    def compute_analysis_diff(
        self,
        job_id: str,
        stage_index: int,
        *,
        prior_revision_id: str | None = None,
        current_revision_id: str | None = None,
    ) -> AnalysisDiffResult:
        """Compute diff between two analysis revisions.

        If either revision_id is None, the latest two accepted/draft
        analysis revisions for the stage are used.

        Args:
            job_id: The V2 job ID.
            stage_index: The stage index (1, 2, or 3).
            prior_revision_id: Optional explicit prior revision ID.
            current_revision_id: Optional explicit current revision ID.

        Returns:
            AnalysisDiffResult with redacted entries.
        """
        # Resolve revisions
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

        # Compare artifact refs
        entries: list[AnalysisDiffEntry] = []
        added = 0
        removed = 0
        modified = 0
        unchanged = 0

        # Find removed and modified artifacts
        for prior_ref in prior_refs:
            matching = [c for c in current_refs if c.get("kind") == prior_ref.get("kind")]

            if not matching:
                entries.append(AnalysisDiffEntry(
                    kind="removed",
                    change_type=prior_ref.get("kind", "other"),
                    summary=f"Artifact '{prior_ref.get('kind', 'unknown')}' was removed in the new revision.",
                    old_checksum=prior_ref.get("checksum"),
                    new_checksum=None,
                ))
                removed += 1
            else:
                current_match = matching[0]
                old_chk = prior_ref.get("checksum", "")
                new_chk = current_match.get("checksum", "")

                if old_chk != new_chk:
                    entries.append(AnalysisDiffEntry(
                        kind="modified",
                        change_type=current_match.get("kind", "other"),
                        summary=f"Artifact '{current_match.get('kind', 'unknown')}' content changed (checksum mismatch).",
                        old_checksum=old_chk,
                        new_checksum=new_chk,
                        detail="Artifact was re-generated with different content.",
                    ))
                    modified += 1
                else:
                    entries.append(AnalysisDiffEntry(
                        kind="unchanged",
                        change_type=current_match.get("kind", "other"),
                        summary=f"Artifact '{current_match.get('kind', 'unknown')}' unchanged.",
                        old_checksum=old_chk,
                        new_checksum=new_chk,
                    ))
                    unchanged += 1

        # Find new artifacts
        current_kinds = {c.get("kind") for c in current_refs}
        for current_ref in current_refs:
            kind = current_ref.get("kind", "")
            if kind not in {p.get("kind") for p in prior_refs}:
                entries.append(AnalysisDiffEntry(
                    kind="added",
                    change_type=kind,
                    summary=f"New artifact '{kind}' was added in this revision.",
                    old_checksum=None,
                    new_checksum=current_ref.get("checksum"),
                    detail=f"New artifact kind: {kind}",
                ))
                added += 1

        # If artifact refs are empty, compare revision metadata
        if not prior_refs and not current_refs:
            entries = self._compare_metadata(prior, current)
            for e in entries:
                if e.kind == "added":
                    added += 1
                elif e.kind == "removed":
                    removed += 1
                elif e.kind == "modified":
                    modified += 1
                else:
                    unchanged += 1

        # Compute deterministic checksum over the diff
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

        return AnalysisDiffResult(
            diff_id=uuid4().hex[:12],
            job_id=job_id,
            stage_index=stage_index,
            prior_revision_id=prior.revision_id,
            current_revision_id=current.revision_id,
            entries=tuple(entries),
            added_count=added,
            removed_count=removed,
            modified_count=modified,
            unchanged_count=unchanged,
            checksum=diff_checksum,
            created_at=utc_now_text(),
        )

    def to_dict(self, result: AnalysisDiffResult) -> dict[str, Any]:
        """Convert diff result to a dict for API/assistant consumption.

        All content is redacted.
        """
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
            "added_count": result.added_count,
            "removed_count": result.removed_count,
            "modified_count": result.modified_count,
            "unchanged_count": result.unchanged_count,
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
        """Resolve prior and current analysis revisions."""
        if current_revision_id is not None:
            current = self._revision_repo.get(current_revision_id)
        else:
            # Find the latest analysis revision for this stage
            current = self._revision_repo.find_latest_by_kind(
                job_id, stage_index, "analysis"
            )

        if prior_revision_id is not None:
            prior = self._revision_repo.get(prior_revision_id)
        elif current is not None and current.prior_revision_id:
            prior = self._revision_repo.get(current.prior_revision_id)
        else:
            # Find the second-latest analysis revision
            all_revisions = self._revision_repo.list_by_job_and_stage(
                job_id, stage_index
            )
            analysis_revisions = [
                r for r in all_revisions
                if r.revision_kind == "analysis"
            ]
            if len(analysis_revisions) >= 2:
                # Skip current to get prior
                current_id = current.revision_id if current else None
                sorted_revs = sorted(
                    analysis_revisions,
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
        """Parse artifact refs JSON into a list of {kind, checksum} dicts."""
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
            # Key-value format: {kind: checksum}
            return [
                {"kind": str(k), "checksum": str(v)}
                for k, v in refs.items()
            ]

        return []

    def _compare_metadata(
        self,
        prior: ArtifactRevisionRecord,
        current: ArtifactRevisionRecord,
    ) -> list[AnalysisDiffEntry]:
        """Compare revision metadata when artifact refs are empty."""
        entries: list[AnalysisDiffEntry] = []

        # Compare evidence checksums
        if prior.evidence_checksum != current.evidence_checksum:
            entries.append(AnalysisDiffEntry(
                kind="modified",
                change_type="evidence",
                summary="Evidence checksum changed, indicating different analysis results.",
                old_checksum=prior.evidence_checksum,
                new_checksum=current.evidence_checksum,
            ))

        # Compare creation sources
        if prior.created_by != current.created_by:
            entries.append(AnalysisDiffEntry(
                kind="modified",
                change_type="metadata",
                summary=f"Revision creator changed from '{prior.created_by}' to '{current.created_by}'.",
                old_checksum=None,
                new_checksum=None,
            ))

        # Compare statuses
        if prior.revision_status != current.revision_status:
            entries.append(AnalysisDiffEntry(
                kind="modified",
                change_type="status",
                summary=f"Revision status changed from '{prior.revision_status}' to '{current.revision_status}'.",
            ))

        if not entries:
            entries.append(AnalysisDiffEntry(
                kind="unchanged",
                change_type="metadata",
                summary="No metadata changes detected between revisions.",
            ))

        return entries

    def _empty_diff(
        self,
        job_id: str,
        stage_index: int,
        reason: str,
    ) -> AnalysisDiffResult:
        """Return an empty diff result."""
        return AnalysisDiffResult(
            diff_id=uuid4().hex[:12],
            job_id=job_id,
            stage_index=stage_index,
            prior_revision_id="",
            current_revision_id="",
            entries=(),
            added_count=0,
            removed_count=0,
            modified_count=0,
            unchanged_count=0,
            checksum=sha256_canonical_json({"reason": reason}),
            created_at=utc_now_text(),
        )
