"""Command manifest model and checksum verification."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from migration_factory.control_tower.domain.errors import ManifestIntegrityError
from pydantic import Field

from migration_factory.control_tower.schemas.common import StrictModel


class CommandManifest(StrictModel):
    """Base command manifest contract."""
    schema_version: str
    job_id: str
    command_id: str
    worker_id: str
    operation: str
    run_configuration_artifact_id: str
    run_configuration_checksum: str
    working_directory_root_id: str
    working_directory_relative_path: str
    stdout_relative_path: str
    stderr_relative_path: str
    result_relative_path: str
    spool_relative_path: str
    timeout_seconds: int
    max_stdout_bytes: int
    max_stderr_bytes: int
    event_schema_version: str
    created_at: str
    manifest_checksum: str = ""

    def compute_and_set_checksum(self) -> str:
        """Compute and set the manifest checksum in-place."""
        d = _manifest_dict_without_checksum(self)
        checksum = hashlib.sha256(_canonical_json_bytes(d)).hexdigest()
        object.__setattr__(self, "manifest_checksum", checksum)
        return checksum


class StageCommandManifest(CommandManifest):
    """Stage-specific command manifest with full checksum coverage.

    Extends the base CommandManifest with stage-scoped references that are
    hashed into the manifest_checksum: the stage ledger entry, the JDK
    configuration, the runner profile, the pipeline catalog, the route-step
    projection, the sandbox workspace, and the backend-owned argv/env payload.

    All argv and env values are backend-owned. The browser never chooses
    raw paths, Maven goals, shell commands, working directories, or model
    deployment IDs. This contract-only type does not launch any process.
    """

    stage_run_id: str
    ledger_id: str
    ledger_input_checksum: str | None = None
    ledger_checksum_guard: str | None = None
    jdk_id: str
    jdk_java_home: str
    jdk_expected_major: int
    runner_profile_display_name: str
    pipeline_id: str
    pipeline_version: str
    stage_index: int
    stage_id: str
    profile_id: str
    route_step_index: int = 1
    source_profile: str = ""
    target_profile: str = ""
    runtime_profile: str = ""
    catalog: str = ""
    command_jdk: str = ""
    execution_jdk: str = ""
    sandbox_root_id: str = ""
    sandbox_relative_path: str = ""
    catalog_checksum: str | None = None
    approval_gate_id: str = ""
    artifact_refs: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    argv: tuple[str, ...] = ()
    env: dict[str, str] = Field(default_factory=dict)


class BrowserRestrictedPayload:
    """Marker: backend-owned argv/env payload that the browser cannot modify.

    Browser payloads CANNOT choose:
    - raw executable paths
    - Maven goals or build commands
    - arbitrary shell commands
    - working directories
    - model deployment IDs

    LLM flows CANNOT execute commands, approve decisions, or write files
    directly.
    """

    def __init__(self, argv: tuple[str, ...], env: dict[str, str]) -> None:
        self.argv = argv
        self.env = env


def _manifest_dict_without_checksum(manifest: CommandManifest) -> dict[str, Any]:
    d = manifest.model_dump(mode="json")
    d.pop("manifest_checksum", None)
    return d


def _canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    ).encode("utf-8")


def compute_manifest_checksum(manifest: CommandManifest) -> str:
    d = _manifest_dict_without_checksum(manifest)
    return hashlib.sha256(_canonical_json_bytes(d)).hexdigest()


def compute_stage_manifest_checksum(manifest: StageCommandManifest) -> str:
    """Compute checksum over all stage manifest fields including argv/env.

    Checksum coverage includes:
    - base CommandManifest fields
    - stage references: ledger_id, ledger_input_checksum, ledger_checksum_guard
    - JDK config: jdk_id, jdk_java_home, jdk_expected_major
    - profile/pipeline: runner_profile_display_name, pipeline_id, pipeline_version
    - stage metadata: stage_index, stage_id, profile_id, route_step_index,
      source_profile, target_profile, runtime_profile, catalog, command_jdk,
      execution_jdk, approval_gate_id, artifact_refs, evidence_refs
    - sandbox workspace: sandbox_root_id, sandbox_relative_path
    - catalog: catalog_checksum
    - backend-owned argv and env
    """
    d = manifest.model_dump(mode="json")
    d.pop("manifest_checksum", None)
    return hashlib.sha256(_canonical_json_bytes(d)).hexdigest()


def verify_stage_manifest_checksum(manifest: StageCommandManifest) -> None:
    """Verify the stage manifest checksum covers all contract fields."""
    d = manifest.model_dump(mode="json")
    d.pop("manifest_checksum", None)
    expected = hashlib.sha256(_canonical_json_bytes(d)).hexdigest()
    if manifest.manifest_checksum != expected:
        raise ManifestIntegrityError(
            f"Stage manifest checksum mismatch for command {manifest.command_id!r}: "
            f"stored {manifest.manifest_checksum}, computed {expected}"
        )


def verify_manifest_checksum(manifest: CommandManifest) -> None:
    d = _manifest_dict_without_checksum(manifest)
    expected = hashlib.sha256(_canonical_json_bytes(d)).hexdigest()
    if manifest.manifest_checksum != expected:
        raise ManifestIntegrityError(
            f"Manifest checksum mismatch for command {manifest.command_id!r}: "
            f"stored {manifest.manifest_checksum}, computed {expected}"
        )
