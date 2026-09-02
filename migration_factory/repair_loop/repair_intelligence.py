"""Deterministic Maven/POM repair intelligence V1.

This module deliberately abstains from classifying or enriching non-POM
failures.  V1 is eligible only when the exact Maven artifact failure can be
bound to one unambiguous declaration in one authoritative sandbox POM.
"""

from __future__ import annotations

import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from migration_factory.control_tower.application.target_version_update import inspect_pom_for_coordinate
from migration_factory.repair_loop.failure_evidence import FailureEvidence
from migration_factory.repair_loop.failure_evidence import FailureSource
from migration_factory.repair_loop.repair_context import (
    RepairContextPack,
    find_relevant_build_context_files,
)

_ARTIFACT_RE = re.compile(
    r"(?:Could not find artifact|Could not resolve artifact)\s+"
    r"(?P<group>[A-Za-z0-9_.-]+):(?P<artifact>[A-Za-z0-9_.-]+):"
    r"(?P<type>[A-Za-z0-9_.-]+)(?::(?P<classifier>[A-Za-z0-9_.-]+))?:"
    r"(?P<version>[A-Za-z0-9][A-Za-z0-9_.+\-]*)",
    re.IGNORECASE,
)
_REPOSITORY_RE = re.compile(
    r"\bin\s+(?P<repository>[A-Za-z0-9_.-]+)\s*\((?P<url>https?://[^)\s]+)\)",
    re.IGNORECASE,
)
_NOT_FOUND_RE = re.compile(r"\b(?:could not find|not found)\b", re.IGNORECASE)


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request: Any, fp: Any, code: int, msg: str, headers: Any, newurl: str) -> Any:
        return None


def _evidence_universe(evidence: FailureEvidence) -> tuple[str, ...]:
    """Return every bounded evidence channel used by all V1 extractors."""
    values = [evidence.failure_summary, evidence.stdout_tail, evidence.stderr_tail, evidence.safe_log_preview]
    values.extend(str(value) for value in evidence.diagnostic_metadata.values())
    values.extend(error.message for error in evidence.compiler_errors)
    return tuple(value for value in values if value)


def _evidence_text(evidence: FailureEvidence) -> str:
    return "\n".join(_evidence_universe(evidence))


def _extract_coordinate(evidence: FailureEvidence) -> dict[str, str] | None:
    match = _ARTIFACT_RE.search(_evidence_text(evidence))
    if not match:
        return None
    data = match.groupdict()
    return {
        "group_id": data["group"],
        "artifact_id": data["artifact"],
        "type": data.get("type") or "jar",
        "classifier": data.get("classifier") or "",
        "requested_version": data["version"],
    }


def _is_exact_artifact_not_found(evidence: FailureEvidence, coordinate: dict[str, str] | None) -> bool:
    if coordinate is None:
        return False
    text = _evidence_text(evidence)
    return bool(_ARTIFACT_RE.search(text) and _NOT_FOUND_RE.search(text))


def _redact_repository_url(value: str) -> str:
    value = str(value or "").strip().rstrip("/.,")
    if not value:
        return ""
    try:
        parsed = urllib.parse.urlsplit(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            return ""
        hostname = parsed.hostname or ""
        host = f"[{hostname}]" if ":" in hostname and not hostname.startswith("[") else hostname
        if parsed.port:
            host += f":{parsed.port}"
        return urllib.parse.urlunsplit((parsed.scheme, host, parsed.path.rstrip("/"), "", ""))
    except ValueError:
        return ""


def _repository(evidence: FailureEvidence) -> tuple[str, str]:
    repository_id = ""
    repository_url = ""
    for value in _evidence_universe(evidence):
        match = _REPOSITORY_RE.search(value)
        if match:
            repository_id = match.group("repository")
            repository_url = match.group("url")
            break
    metadata = evidence.diagnostic_metadata
    repository_id = repository_id or next(
        (str(metadata[key]) for key in ("repository_id", "repo_id", "maven_repository_id") if metadata.get(key)), ""
    )
    repository_url = repository_url or next(
        (str(metadata[key]) for key in ("repository_url", "repo_url", "maven_repository_url") if metadata.get(key)), ""
    )
    return repository_id, _redact_repository_url(repository_url)


def _lookup_versions(repository_url: str, coordinate: dict[str, str]) -> dict[str, Any]:
    result: dict[str, Any] = {
        "lookup_status": "NOT_ATTEMPTED",
        "requested_version_status": "UNKNOWN",
        "available_versions": [],
    }
    if not repository_url:
        return result
    url = (
        f"{repository_url}/{coordinate['group_id'].replace('.', '/')}/"
        f"{coordinate['artifact_id']}/maven-metadata.xml"
    )
    try:
        opener = urllib.request.build_opener(_NoRedirectHandler)
        with opener.open(url, timeout=5) as response:
            root = ET.fromstring(response.read())
        versions = sorted({
            node.text.strip() for node in root.iter()
            if str(node.tag).rsplit("}", 1)[-1] == "version" and node.text and node.text.strip()
        })
        if not versions:
            result["lookup_status"] = "MALFORMED_METADATA"
            return result
        requested = coordinate["requested_version"]
        result.update(
            lookup_status="SUCCESS",
            requested_version_status="EXISTS" if requested in versions else "NOT_FOUND",
            available_versions=versions,
        )
    except urllib.error.HTTPError as exc:
        result["lookup_status"] = "AUTH_REQUIRED" if exc.code in {401, 403} else "HTTP_ERROR"
    except urllib.error.URLError as exc:
        reason = str(getattr(exc, "reason", exc)).lower()
        result["lookup_status"] = (
            "TLS_ERROR" if any(item in reason for item in ("certificate", "ssl", "tls"))
            else "TIMEOUT" if "timed out" in reason else "NETWORK_ERROR"
        )
    except TimeoutError:
        result["lookup_status"] = "TIMEOUT"
    except ET.ParseError:
        result["lookup_status"] = "MALFORMED_METADATA"
    except OSError:
        result["lookup_status"] = "NETWORK_ERROR"
    return result


def _authoritative_pom(
    *, context_pack: RepairContextPack, sandbox_path: str | Path | None,
) -> tuple[str, str, str] | None:
    complete = [
        context for context in context_pack.source_contexts
        if str(getattr(context, "path", "")).lower().endswith("pom.xml")
        and bool(getattr(context, "context_is_complete", False))
    ]
    if len(complete) == 1:
        context = complete[0]
        content = str(context.content)
        expected = str(getattr(context, "source_file_sha256", "") or getattr(context, "content_checksum", ""))
        if not expected or hashlib.sha256(content.encode("utf-8")).hexdigest() != expected:
            return None
        return str(context.path).replace("\\", "/"), str(context.content), str(
            expected
        )
    if not sandbox_path:
        return None
    candidates = find_relevant_build_context_files(
        sandbox_root=sandbox_path,
        working_directory=None,
        module="",
        tool="maven",
    )
    sandbox = Path(sandbox_path).resolve()
    discovered = {
        path.resolve() for path in sandbox.rglob("pom.xml")
        if path.is_file() and not path.is_symlink()
        and not any(part.lower() in {".git", ".migration", "target"} for part in path.relative_to(sandbox).parts)
    }
    poms = [sandbox / path for path in candidates if path.lower().endswith("pom.xml")]
    poms = list({path.resolve() for path in poms if path.exists()} | discovered)
    if len(poms) != 1 or not poms[0].is_file() or poms[0].is_symlink():
        return None
    pom = poms[0]
    raw = pom.read_bytes()
    content = raw.decode("utf-8", errors="replace")
    relative = pom.relative_to(sandbox).as_posix()
    return relative, content, hashlib.sha256(raw).hexdigest()


def run_repair_intelligence_preflight(
    *, failure_evidence: FailureEvidence, context_pack: RepairContextPack,
    sandbox_path: str | Path | None = None,
) -> dict[str, Any]:
    """Return compact POM facts, or ``{}`` when V1 eligibility is unproven."""
    coordinate = _extract_coordinate(failure_evidence)
    if not _is_exact_artifact_not_found(failure_evidence, coordinate):
        return {}
    pom = _authoritative_pom(context_pack=context_pack, sandbox_path=sandbox_path)
    if coordinate is None or pom is None:
        return {}
    pom_path, pom_text, pom_checksum = pom
    if failure_evidence.failure_source not in {FailureSource.BUILD, FailureSource.VALIDATION}:
        return {}
    declaration = inspect_pom_for_coordinate(
        pom_text, coordinate["group_id"], coordinate["artifact_id"],
        coordinate["type"], coordinate["classifier"],
    )
    if not declaration or declaration.get("status") != "MATCH":
        return {}
    repository_id, repository_url = _repository(failure_evidence)
    metadata = _lookup_versions(repository_url, coordinate)
    warnings = list(declaration.get("warnings") or [])
    if metadata["lookup_status"] != "SUCCESS":
        warnings.append("authoritative repository metadata was not available")
    return {
        "schema_version": "1.0.0",
        "eligibility": True,
        "failure_subtype": "MAVEN_ARTIFACT_NOT_FOUND",
        "coordinate": coordinate,
        "pom": {"path": pom_path, "sha256": pom_checksum},
        "declaration": {
            "status": declaration.get("status"),
            "kind": declaration.get("declaration_kind"),
            "raw_version": declaration.get("raw_version"),
            "resolved_version": declaration.get("resolved_version"),
            "version_source": declaration.get("version_source"),
            "property_name": declaration.get("property_name"),
            "property_value": declaration.get("property_value"),
            "shared_property_consumers": declaration.get("known_property_consumers", []),
        },
        "repository": {"id": repository_id, "url": repository_url},
        "metadata_lookup": metadata,
        "missing_evidence": [item for item in (
            "repository_url" if not repository_url else "",
            "requested_version_status" if metadata["requested_version_status"] == "UNKNOWN" else "",
        ) if item],
        "warnings": sorted(set(item for item in warnings if item)),
    }
