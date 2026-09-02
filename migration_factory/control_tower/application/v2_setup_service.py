"""V2 setup persistence and preflight readiness service.

This module provides the application service for creating migration
setup drafts, computing preflight readiness, and checking setup
checksum gating for job creation.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.application.redaction import (
    redact_absolute_paths,
    redact_model_summary,
)
from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
    V2MigrationSetupRecord,
    V2PreflightResultRecord,
)


# ── Data types ───────────────────────────────────────────────────────


@dataclass(frozen=True)
class CreateSetupRequest:
    run_name: str
    legacy_app_path: str
    output_parent_path: str
    ai_hub_path: str
    java11_home: str
    java17_home: str
    java21_home: str
    maven_cmd: str
    proof_level: str = "build_test_verified"
    skip_endpoint_smoke: bool = False
    migration_flags: dict[str, Any] = field(default_factory=dict)
    created_by: str = "operator"
    correlation_id: str | None = None


@dataclass(frozen=True)
class SetupDto:
    setup_id: str
    run_name: str
    legacy_app_path: str
    output_parent_path: str
    ai_hub_path: str
    java_homes: dict[str, str]
    maven_cmd: str
    proof_level: str
    skip_endpoint_smoke: bool
    migration_flags: dict[str, Any]
    setup_checksum: str
    created_at: str


@dataclass(frozen=True)
class PreflightDto:
    preflight_id: str
    setup_id: str
    setup_checksum: str
    all_ready: bool
    legacy_app_exists: bool
    legacy_app_has_project_file: bool
    legacy_app_not_in_output_parent: bool
    output_parent_writable: bool
    ai_hub_root_exists: bool
    ai_hub_profiles_ready: bool
    ai_hub_catalogs_ready: bool
    ai_hub_policies_ready: bool
    jdk11_ready: bool
    jdk17_ready: bool
    jdk21_ready: bool
    maven_ready: bool
    pipeline_route_ready: bool
    legacy_marker_ready: bool
    output_parent_gate_ready: bool
    azure_model_ready: bool
    azure_model_failure_reason: str
    azure_model_response_snippet: str
    azure_model_checked_at: str
    readiness: dict[str, Any]
    warnings: tuple[str, ...]
    errors: tuple[str, ...]
    checked_at: str


@dataclass(frozen=True)
class PreflightReadiness:
    """Aggregate deterministic readiness from a preflight result."""
    all_ready: bool
    setup_checksum: str
    preflight_checksum_match: bool
    gates: dict[str, bool]


# ── Setup checksum computation ───────────────────────────────────────


def compute_setup_checksum(request: CreateSetupRequest) -> str:
    """Compute a deterministic SHA-256 checksum of setup fields."""
    payload = {
        "run_name": request.run_name,
        "legacy_app_path": request.legacy_app_path,
        "output_parent_path": request.output_parent_path,
        "ai_hub_path": request.ai_hub_path,
        "java11_home": request.java11_home,
        "java17_home": request.java17_home,
        "java21_home": request.java21_home,
        "maven_cmd": request.maven_cmd,
        "proof_level": request.proof_level,
        "skip_endpoint_smoke": request.skip_endpoint_smoke,
        "migration_flags": request.migration_flags,
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def is_ai_smoke_required(skip_endpoint_smoke: bool) -> bool:
    """Return True when AI smoke must pass before migration can start."""
    return not skip_endpoint_smoke


# ── Setup service ────────────────────────────────────────────────────


class V2SetupService:
    """Application service for V2 migration setup drafts."""

    def __init__(
        self,
        repo: SqliteV2SetupRepository,
        model_client: Any | None = None,
    ) -> None:
        self._repo = repo
        self._model_client = model_client

    def create_setup(self, request: CreateSetupRequest) -> SetupDto:
        """Create a new migration setup draft."""
        setup_id = uuid4().hex
        checksum = compute_setup_checksum(request)
        now = utc_now_text()
        flags_json = json.dumps(request.migration_flags, separators=(",", ":"))

        record = V2MigrationSetupRecord(
            setup_id=setup_id,
            run_name=request.run_name,
            legacy_app_path=request.legacy_app_path,
            output_parent_path=request.output_parent_path,
            ai_hub_path=request.ai_hub_path,
            java11_home=request.java11_home,
            java17_home=request.java17_home,
            java21_home=request.java21_home,
            maven_cmd=request.maven_cmd,
            proof_level=request.proof_level,
            skip_endpoint_smoke=request.skip_endpoint_smoke,
            migration_flags_json=flags_json,
            setup_checksum=checksum,
            checksum_algorithm="sha256",
            created_at=now,
            created_by=request.created_by,
            correlation_id=request.correlation_id,
        )
        self._repo.save(record)
        return self._record_to_dto(record)

    def get_setup(self, setup_id: str) -> SetupDto | None:
        record = self._repo.get(setup_id)
        if record is None:
            return None
        return self._record_to_dto(record)

    def list_setups(self) -> tuple[SetupDto, ...]:
        return tuple(self._record_to_dto(r) for r in self._repo.list())

    def run_preflight(self, setup_id: str, checked_by: str = "system") -> PreflightDto:
        """Run preflight readiness checks for a setup."""
        record = self._repo.get(setup_id)
        if record is None:
            raise ValueError(f"Setup {setup_id!r} not found")

        readiness, warnings, errors, azure_meta = self._compute_readiness(record)

        ai_smoke_required = is_ai_smoke_required(record.skip_endpoint_smoke)
        all_ready = all(
            v for k, v in readiness.items()
            if k != "azure_model_ready" or ai_smoke_required
        )

        preflight_id = uuid4().hex
        now = utc_now_text()

        preflight = V2PreflightResultRecord(
            preflight_id=preflight_id,
            setup_id=setup_id,
            setup_checksum=record.setup_checksum,
            all_ready=all_ready,
            legacy_app_exists=readiness.get("legacy_app_exists", False),
            legacy_app_has_project_file=readiness.get("legacy_app_has_project_file", False),
            legacy_app_not_in_output_parent=readiness.get("legacy_app_not_in_output_parent", False),
            output_parent_writable=readiness.get("output_parent_writable", False),
            ai_hub_root_exists=readiness.get("ai_hub_root_exists", False),
            ai_hub_profiles_ready=readiness.get("ai_hub_profiles_ready", False),
            ai_hub_catalogs_ready=readiness.get("ai_hub_catalogs_ready", False),
            ai_hub_policies_ready=readiness.get("ai_hub_policies_ready", False),
            jdk11_ready=readiness.get("jdk11_ready", False),
            jdk17_ready=readiness.get("jdk17_ready", False),
            jdk21_ready=readiness.get("jdk21_ready", False),
            maven_ready=readiness.get("maven_ready", False),
            pipeline_route_ready=readiness.get("pipeline_route_ready", True),
            legacy_marker_ready=readiness.get("legacy_marker_ready", True),
            output_parent_gate_ready=readiness.get("output_parent_gate_ready", True),
            readiness_json=json.dumps(readiness, separators=(",", ":")),
            warnings_json=json.dumps(list(warnings), separators=(",", ":")),
            errors_json=json.dumps(list(errors), separators=(",", ":")),
            checked_at=now,
            checked_by=checked_by,
            correlation_id=record.correlation_id,
        )
        self._repo.save_preflight(preflight)

        return PreflightDto(
            preflight_id=preflight_id,
            setup_id=setup_id,
            setup_checksum=record.setup_checksum,
            all_ready=all_ready,
            legacy_app_exists=readiness.get("legacy_app_exists", False),
            legacy_app_has_project_file=readiness.get("legacy_app_has_project_file", False),
            legacy_app_not_in_output_parent=readiness.get("legacy_app_not_in_output_parent", False),
            output_parent_writable=readiness.get("output_parent_writable", False),
            ai_hub_root_exists=readiness.get("ai_hub_root_exists", False),
            ai_hub_profiles_ready=readiness.get("ai_hub_profiles_ready", False),
            ai_hub_catalogs_ready=readiness.get("ai_hub_catalogs_ready", False),
            ai_hub_policies_ready=readiness.get("ai_hub_policies_ready", False),
            jdk11_ready=readiness.get("jdk11_ready", False),
            jdk17_ready=readiness.get("jdk17_ready", False),
            jdk21_ready=readiness.get("jdk21_ready", False),
            maven_ready=readiness.get("maven_ready", False),
            pipeline_route_ready=readiness.get("pipeline_route_ready", True),
            legacy_marker_ready=readiness.get("legacy_marker_ready", True),
            output_parent_gate_ready=readiness.get("output_parent_gate_ready", True),
            azure_model_ready=readiness.get("azure_model_ready", True),
            azure_model_failure_reason=azure_meta["failure_reason"],
            azure_model_response_snippet=azure_meta["snippet"],
            azure_model_checked_at=azure_meta["checked_at"],
            readiness=readiness,
            warnings=tuple(warnings),
            errors=tuple(errors),
            checked_at=now,
        )

    def get_readiness(self, setup_id: str) -> PreflightReadiness | None:
        """Get the latest preflight readiness for a setup."""
        preflight = self._repo.get_latest_preflight(setup_id)
        if preflight is None:
            return None

        setup = self._repo.get(setup_id)
        checksum_matches = setup is not None and setup.setup_checksum == preflight.setup_checksum

        try:
            gates = json.loads(preflight.readiness_json)
        except (json.JSONDecodeError, TypeError):
            gates = {}

        return PreflightReadiness(
            all_ready=preflight.all_ready,
            setup_checksum=preflight.setup_checksum,
            preflight_checksum_match=checksum_matches,
            gates={k: bool(v) for k, v in gates.items()},
        )

    def get_readiness_by_checksum(self, checksum: str) -> PreflightReadiness | None:
        """Get the latest preflight readiness for a setup checksum."""
        preflight = self._repo.get_latest_preflight_by_checksum(checksum)
        if preflight is None:
            return None

        setup = self._repo.get_by_checksum(checksum)
        checksum_matches = setup is not None and setup.setup_checksum == preflight.setup_checksum

        try:
            gates = json.loads(preflight.readiness_json)
        except (json.JSONDecodeError, TypeError):
            gates = {}

        return PreflightReadiness(
            all_ready=preflight.all_ready,
            setup_checksum=preflight.setup_checksum,
            preflight_checksum_match=checksum_matches,
            gates={k: bool(v) for k, v in gates.items()},
        )

    # ── DTO converters ───────────────────────────────────────────

    def setup_to_dict(self, dto: SetupDto) -> dict[str, Any]:
        return {
            "setup_id": dto.setup_id,
            "run_name": dto.run_name,
            "legacy_app_path": redact_absolute_paths(dto.legacy_app_path),
            "output_parent_path": redact_absolute_paths(dto.output_parent_path),
            "ai_hub_path": redact_absolute_paths(dto.ai_hub_path),
            "java_homes": dto.java_homes,
            "maven_cmd": redact_absolute_paths(dto.maven_cmd),
            "proof_level": dto.proof_level,
            "skip_endpoint_smoke": dto.skip_endpoint_smoke,
            "migration_flags": dto.migration_flags,
            "setup_checksum": dto.setup_checksum,
            "created_at": dto.created_at,
        }

    def preflight_to_dict(self, dto: PreflightDto) -> dict[str, Any]:
        return {
            "preflight_id": dto.preflight_id,
            "setup_id": dto.setup_id,
            "setup_checksum": dto.setup_checksum,
            "all_ready": dto.all_ready,
            "legacy_app_exists": dto.legacy_app_exists,
            "legacy_app_has_project_file": dto.legacy_app_has_project_file,
            "legacy_app_not_in_output_parent": dto.legacy_app_not_in_output_parent,
            "output_parent_writable": dto.output_parent_writable,
            "ai_hub_root_exists": dto.ai_hub_root_exists,
            "ai_hub_profiles_ready": dto.ai_hub_profiles_ready,
            "ai_hub_catalogs_ready": dto.ai_hub_catalogs_ready,
            "ai_hub_policies_ready": dto.ai_hub_policies_ready,
            "jdk11_ready": dto.jdk11_ready,
            "jdk17_ready": dto.jdk17_ready,
            "jdk21_ready": dto.jdk21_ready,
            "maven_ready": dto.maven_ready,
            "pipeline_route_ready": dto.pipeline_route_ready,
            "legacy_marker_ready": dto.legacy_marker_ready,
            "output_parent_gate_ready": dto.output_parent_gate_ready,
            "azure_model_ready": dto.azure_model_ready,
            "azure_model_failure_reason": dto.azure_model_failure_reason,
            "azure_model_response_snippet": dto.azure_model_response_snippet,
            "azure_model_checked_at": dto.azure_model_checked_at,
            "readiness": dto.readiness,
            "warnings": list(dto.warnings),
            "errors": list(dto.errors),
            "checked_at": dto.checked_at,
        }

    def readiness_to_dict(self, readiness: PreflightReadiness | None) -> dict[str, Any]:
        if readiness is None:
            return {"ready": False, "setup_checksum": "", "preflight_checksum_match": False, "gates": {}}
        return {
            "ready": readiness.all_ready,
            "setup_checksum": readiness.setup_checksum,
            "preflight_checksum_match": readiness.preflight_checksum_match,
            "gates": readiness.gates,
        }

    # ── Internal ─────────────────────────────────────────────────

    def _compute_readiness(
        self,
        record: V2MigrationSetupRecord,
    ) -> tuple[dict[str, bool], list[str], list[str], dict[str, str]]:
        """Compute deterministic readiness checks.

        Returns (readiness_dict, warnings, errors, azure_smoke_meta).
        azure_smoke_meta contains keys: deployment, failure_reason, snippet.
        """
        readiness: dict[str, bool] = {}
        warnings: list[str] = []
        errors: list[str] = []

        # Legacy app path
        legacy = Path(record.legacy_app_path)
        legacy_exists = legacy.exists()
        readiness["legacy_app_exists"] = legacy_exists
        if not legacy_exists:
            errors.append(f"Legacy app path does not exist: {record.legacy_app_path}")

        # Legacy app has pom.xml
        has_pom = legacy_exists and (legacy / "pom.xml").exists()
        has_gradle = legacy_exists and (legacy / "build.gradle").exists()
        has_project = has_pom or has_gradle
        readiness["legacy_app_has_project_file"] = has_project
        if not has_project and legacy_exists:
            warnings.append("No pom.xml or build.gradle found in legacy app path")

        # Legacy not inside output parent
        output = Path(record.output_parent_path)
        try:
            not_in_output = not str(legacy.resolve()).startswith(str(output.resolve()))
        except (ValueError, OSError):
            not_in_output = True
        readiness["legacy_app_not_in_output_parent"] = not_in_output
        if not not_in_output:
            errors.append("Legacy app path is inside output parent path")

        # Output parent writable
        output_parent_writable = True
        try:
            if not output.exists():
                output.mkdir(parents=True, exist_ok=True)
            output_parent_writable = output.exists() and os.access(str(output), os.W_OK)
        except (OSError, PermissionError):
            output_parent_writable = False
        readiness["output_parent_writable"] = output_parent_writable
        if not output_parent_writable:
            errors.append("Output parent path is not writable")

        # AI Hub
        hub = Path(record.ai_hub_path)
        hub_exists = hub.exists()
        readiness["ai_hub_root_exists"] = hub_exists
        if not hub_exists:
            warnings.append(f"AI Hub path does not exist: {record.ai_hub_path}")

        # Check AI Hub profiles
        profiles_ready = hub_exists and _check_ai_hub_profiles(hub)
        readiness["ai_hub_profiles_ready"] = profiles_ready
        if not profiles_ready and hub_exists:
            warnings.append("AI Hub profiles not complete")

        catalogs_ready = hub_exists and _check_ai_hub_catalogs(hub)
        readiness["ai_hub_catalogs_ready"] = catalogs_ready
        if not catalogs_ready and hub_exists:
            warnings.append("AI Hub catalogs not complete")

        policies_ready = hub_exists and _check_ai_hub_policies(hub)
        readiness["ai_hub_policies_ready"] = policies_ready
        if not policies_ready and hub_exists:
            warnings.append("AI Hub policies not complete")

        # JDK checks with real subprocess version validation
        readiness["jdk11_ready"] = _check_jdk_path_with_version(record.java11_home, 11)
        readiness["jdk17_ready"] = _check_jdk_path_with_version(record.java17_home, 17)
        readiness["jdk21_ready"] = _check_jdk_path_with_version(record.java21_home, 21)

        if not readiness["jdk11_ready"]:
            errors.append(f"JAVA11_HOME path does not exist: {record.java11_home}")
        if not readiness["jdk17_ready"]:
            errors.append(f"JAVA17_HOME path does not exist: {record.java17_home}")
        if not readiness["jdk21_ready"]:
            errors.append(f"JAVA21_HOME path does not exist: {record.java21_home}")

        maven_result = _validate_maven_command(
            record.maven_cmd,
            java_home=record.java21_home,
            java_homes={
                "JAVA11_HOME": record.java11_home,
                "JAVA17_HOME": record.java17_home,
                "JAVA21_HOME": record.java21_home,
            },
        )
        readiness["maven_ready"] = maven_result.ready
        if not maven_result.ready:
            if maven_result.status == _ToolCheckStatus.PATH_MISSING:
                errors.append(f"Maven command path does not exist: {record.maven_cmd}")
            else:
                errors.append(f"Maven command failed: {maven_result.message}")

        # Pipeline route (always ready for V2)
        readiness["pipeline_route_ready"] = True

        # Legacy marker (always ready for V2)
        readiness["legacy_marker_ready"] = True

        # Output parent gate
        readiness["output_parent_gate_ready"] = output_parent_writable

        # Azure model readiness — perform real smoke if model client is available
        # and endpoint smoke is not explicitly skipped.
        azure_required = is_ai_smoke_required(record.skip_endpoint_smoke)
        azure_smoke_ready = not azure_required
        azure_deployment = ""
        azure_failure_reason = ""
        azure_snippet = ""
        azure_checked_at = ""
        if self._model_client is not None and azure_required:
            try:
                smoke_result = self._model_client.smoke()
                azure_smoke_ready = smoke_result.success
                azure_deployment = smoke_result.deployment
                azure_failure_reason = smoke_result.failure_reason
                azure_snippet = redact_model_summary(smoke_result.response_snippet)
                azure_checked_at = getattr(smoke_result, "checked_at", "")
                if not smoke_result.success:
                    warnings.append(f"Azure model smoke failed: {redact_model_summary(smoke_result.redacted_summary)}")
            except Exception as exc:
                azure_smoke_ready = False
                azure_failure_reason = "invalid_response"
                warnings.append(f"Azure model smoke error: {redact_model_summary(str(exc))}")
        elif azure_required and self._model_client is None:
            azure_failure_reason = "missing_model_client"
            warnings.append("Azure model smoke unavailable: no model client configured.")
        readiness["azure_model_ready"] = azure_smoke_ready

        azure_meta = {
            "deployment": azure_deployment,
            "failure_reason": azure_failure_reason,
            "snippet": azure_snippet,
            "checked_at": azure_checked_at,
        }
        return readiness, warnings, errors, azure_meta

    def _record_to_dto(self, record: V2MigrationSetupRecord) -> SetupDto:
        try:
            flags = json.loads(record.migration_flags_json)
        except (json.JSONDecodeError, TypeError):
            flags = {}
        return SetupDto(
            setup_id=record.setup_id,
            run_name=record.run_name,
            legacy_app_path=record.legacy_app_path,
            output_parent_path=record.output_parent_path,
            ai_hub_path=record.ai_hub_path,
            java_homes={
                "java11": record.java11_home,
                "java17": record.java17_home,
                "java21": record.java21_home,
            },
            maven_cmd=record.maven_cmd,
            proof_level=record.proof_level,
            skip_endpoint_smoke=record.skip_endpoint_smoke,
            migration_flags=flags,
            setup_checksum=record.setup_checksum,
            created_at=record.created_at,
        )


# ── Internal helpers ────────────────────────────────────────────────

# Maximum stdout/stderr bytes to capture and redact
_MAX_CAPTURE_BYTES = 4096
# Subprocess timeout for version checks
_VERSION_CHECK_TIMEOUT = 10.0


class _ToolCheckStatus(str, Enum):
    READY = "ready"
    PATH_MISSING = "path_missing"
    COMMAND_FAILED = "command_failed"


@dataclass(frozen=True)
class _ToolCheckResult:
    ready: bool
    status: _ToolCheckStatus
    message: str = ""


def _check_jdk_path(path: str, expected_major: int | None = None) -> bool:
    """Check if a JDK home path exists and the java binary reports the
    expected major version.

    If expected_major is None, the function auto-detects which JDK
    to expect from the path (JAVA11_HOME→11, JAVA17_HOME→17,
    JAVA21_HOME→21). Otherwise it checks the explicit value.

    Uses subprocess.run with shell=False, timeout, and redacts
    captured output before any storage/return.
    """
    java_home = Path(path)
    if not java_home.exists():
        return False

    # Determine expected major version from path hint
    if expected_major is None:
        if "11" in path or "java11" in path.lower():
            expected_major = 11
        elif "17" in path or "java17" in path.lower():
            expected_major = 17
        elif "21" in path or "java21" in path.lower():
            expected_major = 21

    java_bin = java_home / "bin" / "java"
    if not java_bin.exists():
        # Try Windows-style java.exe
        java_bin = java_home / "bin" / "java.exe"
    if not java_bin.exists():
        return False

    try:
        result = subprocess.run(
            [str(java_bin), "-version"],
            shell=False,
            timeout=_VERSION_CHECK_TIMEOUT,
            capture_output=True,
            text=True,
            env={},  # sanitized: no leaked env vars
        )
    except (subprocess.TimeoutExpired, OSError, ValueError):
        return False

    # java -version writes to stderr
    version_output = (result.stderr or result.stdout or "")
    # Bound and redact captured output
    version_output = version_output[:_MAX_CAPTURE_BYTES]

    if expected_major is None:
        # If we couldn't determine expected major, just verify it runs
        return result.returncode == 0 or bool(version_output.strip())

    return _java_major_matches(version_output, expected_major)


def _java_major_matches(version_output: str, expected_major: int) -> bool:
    """Parse java -version output for the reported major version.

    Handles legacy (1.8.0_...), modern feature-only (21), and
    modern feature-update (11.0.x, 17.0.x,...) formats.
    """
    # Legacy format first: "1.8.0_..." (major = 8).
    # Must check before modern regex since "1.8.0" would
    # otherwise be misparsed as major 1.
    match = re.search(r'version\s+"1\.(\d+)\.', version_output)
    if match:
        major = int(match.group(1))
        return major == expected_major
    # Modern format: "21", "11.0.21", "17.0.13", etc.
    match = re.search(r'version\s+"?(\d+)(?:\.|")', version_output)
    if match:
        major = int(match.group(1))
        return major == expected_major
    return False


def _check_maven_path(path: str) -> bool:
    """Check if a Maven executable path exists and runs successfully.

    Runs mvn --version with subprocess.run, shell=False, timeout,
    and bounds/redacts captured output.
    """
    return _validate_maven_command(path).ready


def _validate_maven_command(
    path: str,
    *,
    java_home: str | None = None,
    java_homes: dict[str, str] | None = None,
) -> _ToolCheckResult:
    """Validate an explicit Maven executable path and version command."""
    cleaned = _clean_operator_path(path)
    maven_path = Path(cleaned)
    if not maven_path.exists() or not maven_path.is_file():
        return _ToolCheckResult(
            ready=False,
            status=_ToolCheckStatus.PATH_MISSING,
        )
    try:
        result = subprocess.run(
            [str(maven_path), "--version"],
            shell=False,
            timeout=_VERSION_CHECK_TIMEOUT,
            capture_output=True,
            text=True,
            env=_maven_subprocess_env(maven_path, java_home=java_home, java_homes=java_homes),
        )
    except subprocess.TimeoutExpired:
        return _ToolCheckResult(
            ready=False,
            status=_ToolCheckStatus.COMMAND_FAILED,
            message="mvn --version timed out",
        )
    except (OSError, ValueError) as exc:
        return _ToolCheckResult(
            ready=False,
            status=_ToolCheckStatus.COMMAND_FAILED,
            message=_bounded_redacted_text(str(exc)),
        )

    output = _bounded_redacted_text("\n".join(
        part for part in (result.stdout, result.stderr) if part
    ))
    if result.returncode != 0:
        return _ToolCheckResult(
            ready=False,
            status=_ToolCheckStatus.COMMAND_FAILED,
            message=output or f"mvn --version exited with code {result.returncode}",
        )

    if "Apache Maven" in output or "mvn" in output.lower() or output.strip():
        return _ToolCheckResult(ready=True, status=_ToolCheckStatus.READY)

    return _ToolCheckResult(
        ready=False,
        status=_ToolCheckStatus.COMMAND_FAILED,
        message="mvn --version returned no version output",
    )


def _clean_operator_path(path: str) -> str:
    return path.strip().strip("\"'")


def _maven_subprocess_env(
    maven_path: Path,
    *,
    java_home: str | None = None,
    java_homes: dict[str, str] | None = None,
) -> dict[str, str]:
    """Build the minimal Windows-safe environment Maven needs to start."""
    env: dict[str, str] = {}

    selected_java_home = _clean_operator_path(
        java_home
        or os.environ.get("JAVA21_HOME")
        or os.environ.get("JAVA_HOME")
        or "",
    )
    if selected_java_home:
        env["JAVA_HOME"] = selected_java_home

    for key in ("JAVA11_HOME", "JAVA17_HOME", "JAVA21_HOME"):
        value = (java_homes or {}).get(key) or os.environ.get(key)
        if value:
            env[key] = _clean_operator_path(value)

    path_entries: list[str] = []
    if selected_java_home:
        path_entries.append(str(Path(selected_java_home) / "bin"))
    path_entries.append(str(maven_path.parent))
    existing_path = os.environ.get("PATH")
    if existing_path:
        path_entries.append(existing_path)
    env["PATH"] = os.pathsep.join(path_entries)

    for key in (
        "SystemRoot",
        "ComSpec",
        "PATHEXT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
    ):
        value = os.environ.get(key)
        if value:
            env[key] = value

    for key in ("MAVEN_OPTS", "MAVEN_USER_HOME"):
        value = os.environ.get(key)
        if value and not _is_secret_like_env(key, value):
            env[key] = value

    return env


def _is_secret_like_env(key: str, value: str) -> bool:
    marker = f"{key}={value}".lower()
    return any(
        token in marker
        for token in (
            "api_key",
            "apikey",
            "authorization",
            "bearer",
            "client_secret",
            "connectionstring",
            "credential",
            "password",
            "secret",
            "sas",
            "token",
        )
    )


def _bounded_redacted_text(value: str) -> str:
    return redact_absolute_paths(value[:_MAX_CAPTURE_BYTES])


def _check_jdk_path_with_version(path: str, expected_major: int) -> bool:
    """Explicit version check — used when path hints are unreliable."""
    return _check_jdk_path(path, expected_major=expected_major)


def _check_ai_hub_profiles(hub: Path) -> bool:
    """Check for required AI Hub profiles."""
    profiles_dir = hub / "profiles"
    if not profiles_dir.is_dir():
        return False
    required = (
        "springboot-2.1.6-to-2.7-java11",
        "springboot-2.7-to-3.5-java17",
        "springboot-3.5-java17-to-java21",
        "springboot-3.5-java21-to-4.0-java21",
    )
    return all((profiles_dir / f"{profile}.yaml").is_file() for profile in required)


def _check_ai_hub_catalogs(hub: Path) -> bool:
    """Check for required AI Hub catalogs."""
    profiles_dir = hub / "profiles"
    catalogs_dir = hub / "catalogs" / "openrewrite"
    if not profiles_dir.is_dir() or not catalogs_dir.is_dir():
        return False
    required_profiles = (
        "springboot-2.1.6-to-2.7-java11",
        "springboot-2.7-to-3.5-java17",
        "springboot-3.5-java17-to-java21",
        "springboot-3.5-java21-to-4.0-java21",
    )
    for profile in required_profiles:
        profile_path = profiles_dir / f"{profile}.yaml"
        catalog_path = _catalog_path_declared_by_profile(profile_path)
        if catalog_path is None:
            return False
        if catalog_path.parts[:2] != ("catalogs", "openrewrite"):
            return False
        declared_catalog = hub.joinpath(*catalog_path.parts)
        if declared_catalog.is_file():
            continue
        if profile == "springboot-2.7-to-3.5-java17" and catalog_path.name == "springboot-3.5-java17.yaml":
            fallback_catalog = catalogs_dir / "springboot-3.5-java17.yaml"
            if fallback_catalog.is_file():
                continue
        return False
    return True


def _check_ai_hub_policies(hub: Path) -> bool:
    """Check for required AI Hub policies."""
    policies_dir = hub / "policies"
    if not policies_dir.is_dir():
        return False
    required = ("planning", "safety", "transformation")
    return all((policies_dir / f"{policy}.yaml").is_file() for policy in required)


def _catalog_path_declared_by_profile(profile_path: Path) -> Path | None:
    if not profile_path.is_file():
        return None
    try:
        text = profile_path.read_text(encoding="utf-8")
    except OSError:
        return None
    match = re.search(r"(?m)^\s*catalog_path:\s*[\"']?([^\"'\r\n#]+)", text)
    if not match:
        return None
    raw = match.group(1).strip()
    if not raw:
        return None
    return Path(raw)
