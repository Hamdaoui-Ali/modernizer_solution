"""Redaction filtering for context pack content (V1-11C).

Extends the V1-00D redaction baseline with context-pack-specific
filters that ensure evidence refs, bounds JSON, and manifest fields
do not expose raw secrets, env values, local paths, forbidden paths,
raw prompts, deployment IDs, logs, DBs, caches, or runtime artifacts.
"""

from __future__ import annotations

import json
from typing import Any

from migration_factory.control_tower.application.redaction import (
    contains_forbidden_path,
    is_forbidden_file,
    redact_absolute_paths,
    redact_deployment_identifiers,
    redact_env_assignments,
    redact_model_summary,
    redact_public_value,
    redact_raw_prompts,
    redact_secret_keys,
    redact_sensitive_env_vars,
)
from migration_factory.control_tower.application.retrievers import (
    EvidenceBoundsError,
    EvidenceRef,
)


# ── Forbidden evidence ref patterns ────────────────────────────────

# Path patterns that should never appear in evidence refs
FORBIDDEN_EVIDENCE_PATTERNS: tuple[str, ...] = (
    # Log files
    "logs/",
    "log/",
    ".log",
    # Database files
    ".db",
    ".sqlite",
    ".sqlite3",
    # Cache directories
    "__pycache__",
    ".cache",
    "cache/",
    "caches/",
    # Runtime artifacts
    "target/",
    "build/",
    "dist/",
    ".gradle",
    ".m2/repository",
    "node_modules",
    ".next",
    # IDE/editor files
    ".idea",
    ".vscode",
    ".vs/",
    # Git objects
    ".git/objects",
    ".git/refs",
    # OS special files
    ".DS_Store",
    "Thumbs.db",
    # Container/system metadata
    "Dockerfile",
    "docker-compose",
)


def contains_forbidden_evidence_pattern(path: str) -> bool:
    """Check if a path matches forbidden evidence patterns."""
    normalized = path.replace("\\", "/").lower()
    for pattern in FORBIDDEN_EVIDENCE_PATTERNS:
        if pattern.lower() in normalized:
            return True
    return False


# ── Evidence ref redaction ─────────────────────────────────────────


def redact_evidence_ref(ref: EvidenceRef) -> EvidenceRef:
    """Redact an evidence reference for context pack storage.

    Returns the ref unchanged if it's clean, or a redacted copy
    with paths replaced by placeholders.
    """
    redacted_path = redact_absolute_paths(ref.relative_path)
    redacted_path = _redact_runtime_artifacts(redacted_path)

    # Redact metadata JSON if present
    redacted_metadata = ref.metadata_json
    if redacted_metadata:
        try:
            meta = json.loads(redacted_metadata)
            meta = redact_public_value(meta)
            redacted_metadata = json.dumps(meta, separators=(",", ":"), sort_keys=True)
        except (json.JSONDecodeError, TypeError):
            redacted_metadata = redact_model_summary(redacted_metadata)

    return EvidenceRef(
        source_type=ref.source_type,
        source_id=ref.source_id,
        relative_path=redacted_path,
        content_type=ref.content_type,
        size_bytes=ref.size_bytes,
        checksum_algorithm=ref.checksum_algorithm,
        checksum=ref.checksum,
        metadata_json=redacted_metadata,
    )


def filter_evidence_refs(refs: tuple[EvidenceRef, ...]) -> tuple[EvidenceRef, ...]:
    """Filter and redact evidence refs for context pack storage.

    Removes refs that reference forbidden paths (logs, DBs, caches,
    runtime artifacts). Redacts remaining refs to remove absolute
    paths, env refs, and secrets.
    """
    filtered: list[EvidenceRef] = []

    for ref in refs:
        # Skip forbidden paths
        if _is_forbidden_evidence_ref(ref):
            continue

        # Redact the ref
        redacted = redact_evidence_ref(ref)
        filtered.append(redacted)

    return tuple(filtered)


def _is_forbidden_evidence_ref(ref: EvidenceRef) -> bool:
    """Check if an evidence ref should be excluded entirely."""
    path = ref.relative_path

    # Check forbidden path prefixes from V1-00D
    if contains_forbidden_path(path):
        return True

    # Check forbidden file types
    if is_forbidden_file(path):
        return True

    # Check forbidden evidence patterns (logs, DBs, caches, etc.)
    if contains_forbidden_evidence_pattern(path):
        return True

    return False


# ── Manifest field redaction ───────────────────────────────────────


def redact_manifest_field(value: str | None) -> str | None:
    """Redact a single manifest field value.

    Applies all redaction primitives. Returns None for empty strings,
    or the redacted value otherwise.
    """
    if not value:
        return value

    result = value
    result = redact_absolute_paths(result)
    result = redact_env_assignments(result)
    result = redact_sensitive_env_vars(result)
    result = redact_secret_keys(result)
    result = redact_deployment_identifiers(result)
    result = redact_raw_prompts(result)
    return result


def redact_bounds_json(bounds_json: str | None) -> str | None:
    """Redact bounds JSON for context pack storage.

    Strips any fields that contain raw paths, environment variable
    names, or secret-like values from the bounds configuration.
    """
    if not bounds_json:
        return None

    try:
        bounds = json.loads(bounds_json)
        redacted = _redact_bounds_dict(bounds)
        return json.dumps(redacted, separators=(",", ":"), sort_keys=True)
    except (json.JSONDecodeError, TypeError):
        return redact_manifest_field(bounds_json)


def _redact_bounds_dict(d: dict[str, Any]) -> dict[str, Any]:
    """Recursively redact a bounds dict."""
    result: dict[str, Any] = {}
    for key, value in d.items():
        key_lower = key.lower()

        # Remove path-related bounds that could leak structure
        if any(x in key_lower for x in ("path", "dir", "root", "prefix", "location")):
            if isinstance(value, str):
                result[key] = redact_absolute_paths(value)
            else:
                result[key] = value
        elif isinstance(value, dict):
            result[key] = _redact_bounds_dict(value)
        elif isinstance(value, str):
            result[key] = redact_public_value(value)
        else:
            result[key] = value
    return result


# ── Full context pack redaction pipeline ───────────────────────────


def redact_manifest_title(original: str) -> str:
    """Redact a manifest title.

    Titles should be safe by construction, but we still apply path
    and env redaction as a defense-in-depth measure.
    """
    return redact_absolute_paths(redact_env_assignments(original))


def redact_manifest_description(original: str | None) -> str | None:
    """Redact a manifest description."""
    return redact_manifest_field(original)


def redact_evidence_refs_json(evidence_refs_json: str | None) -> str | None:
    """Redact a persisted evidence_refs_json string.

    Parses the JSON, redacts each entry, and re-serializes.
    """
    if not evidence_refs_json:
        return None

    try:
        entries = json.loads(evidence_refs_json)
        if isinstance(entries, list):
            redacted_entries = []
            for entry in entries:
                if isinstance(entry, dict):
                    redacted_entries.append(redact_public_value(entry))
                else:
                    redacted_entries.append(entry)
            return json.dumps(redacted_entries, separators=(",", ":"), sort_keys=True)
        return redact_public_value(entries)
    except (json.JSONDecodeError, TypeError):
        return redact_manifest_field(evidence_refs_json)


# ── Context pack metadata redaction (F01) ─────────────────────────


def redact_context_pack_metadata(
    metadata: dict[str, object],
) -> dict[str, object]:
    """Redact context pack enrichment metadata before storage or prompt construction.

    Applies path, env, secret, and deployment redaction to string fields.
    Removes keys that reference raw paths, model deployments, or secrets.
    """
    redacted: dict[str, object] = {}
    for key, value in metadata.items():
        if value is None:
            redacted[key] = None
            continue
        if isinstance(value, str):
            redacted_val: str = value
            redacted_val = redact_absolute_paths(redacted_val)
            redacted_val = redact_env_assignments(redacted_val)
            redacted_val = redact_secret_keys(redacted_val)
            redacted_val = redact_deployment_identifiers(redacted_val)
            redacted_val = redact_raw_prompts(redacted_val)
            # Drop keys that are entirely redacted placeholders
            if redacted_val.startswith("[redacted-"):
                # Keep the key but with redacted value
                redacted[key] = redacted_val
            else:
                redacted[key] = redacted_val
        elif isinstance(value, (list, tuple)):
            redacted[key] = [
                redact_absolute_paths(str(v)) if isinstance(v, str) else v
                for v in value
            ]
        else:
            redacted[key] = value
    return redacted


def build_metadata_dict(pack: Any) -> dict[str, object]:
    """Extract enrichment metadata fields from a ContextPack into a dict."""
    fields = (
        "agent_name",
        "event_type",
        "stage_index",
        "profile_id",
        "command_id",
        "failure_type",
        "artifact_refs_used",
        "pom_summary_ref",
        "sandbox_binding_ref",
        "redaction_status",
    )
    result: dict[str, object] = {}
    for f in fields:
        val = getattr(pack, f, None)
        if val is not None and val != ():
            result[f] = val if not isinstance(val, tuple) else list(val)
    return result


# ── Internal helpers ───────────────────────────────────────────────


def _redact_runtime_artifacts(path: str) -> str:
    """Redact runtime artifact paths that are not safe for context packs."""
    if contains_forbidden_evidence_pattern(path):
        return "[redacted-runtime-artifact]"
    return path
