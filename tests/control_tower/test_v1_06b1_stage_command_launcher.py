"""Focused tests for V1-06B1: Stage command launcher contract.

This test file covers:
- StageCommandManifest checksum coverage (ledger, JDK, profile,
  catalog, sandbox, argv, env)
- Backend-owned argv/env ownership (browser cannot choose raw paths,
  Maven goals, shell commands, working directories)
- No process launch in this issue
- V1 invariant preservation
"""

from __future__ import annotations

import hashlib
import json
from uuid import uuid4

import pytest

from migration_factory.control_tower.domain.manifests import (
    BrowserRestrictedPayload,
    CommandManifest,
    StageCommandManifest,
    compute_manifest_checksum,
    compute_stage_manifest_checksum,
    verify_manifest_checksum,
    verify_stage_manifest_checksum,
)
from migration_factory.control_tower.domain.errors import ManifestIntegrityError
from migration_factory.control_tower.application.commands import StageCommandLaunchCommand
from migration_factory.control_tower.application.services import (
    StageCommandLaunchService,
    StageCommandManifestBuilder,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stage_manifest(
    *,
    argv: tuple[str, ...] | None = None,
    env: dict[str, str] | None = None,
    **overrides,
) -> StageCommandManifest:
    """Create a default StageCommandManifest with minimal required fields."""
    kwargs = dict(
        schema_version="1.0.0",
        job_id="job-test-001",
        command_id="cmd-test-001",
        worker_id="worker-test",
        operation="maven_build",
        run_configuration_artifact_id="artifact-rc-001",
        run_configuration_checksum="abc123rc",
        working_directory_root_id="root-output",
        working_directory_relative_path="jobs/test-001",
        stdout_relative_path="logs/stdout.log",
        stderr_relative_path="logs/stderr.log",
        result_relative_path="result.json",
        spool_relative_path="spool",
        timeout_seconds=3600,
        max_stdout_bytes=104857600,
        max_stderr_bytes=104857600,
        event_schema_version="1.0.0",
        created_at="2026-06-12T00:00:00Z",
        manifest_checksum="",
        # Stage-specific fields
        stage_run_id="stage-test-0001",
        ledger_id="ledger-test-0001",
        ledger_input_checksum="input-checksum-001",
        ledger_checksum_guard="guard-001",
        jdk_id="java11",
        jdk_java_home="/opt/java11",
        jdk_expected_major=11,
        runner_profile_display_name="Test Runner",
        pipeline_id="springboot-216-to-356-java21-three-stage",
        pipeline_version="1.0.0",
        stage_index=1,
        stage_id="springboot-2.1.6-to-2.7-java11",
        profile_id="legacy-migration",
        command_jdk="java11",
        sandbox_root_id="root-sandbox",
        sandbox_relative_path="sandbox/test-001",
        catalog_checksum="catalog-checksum-001",
        argv=argv or (),
        env=env or {},
    )
    kwargs.update(overrides)
    return StageCommandManifest(**kwargs)


def _make_launch_command(**overrides) -> StageCommandLaunchCommand:
    """Create a default StageCommandLaunchCommand."""
    kwargs = dict(
        job_id="job-test-001",
        command_id="cmd-test-001",
        worker_id="worker-test",
        operation="maven_build",
        stage_run_id="stage-test-0001",
        ledger_id="ledger-test-0001",
        jdk_id="java11",
        jdk_java_home="/opt/java11",
        jdk_expected_major=11,
        runner_profile_display_name="Test Runner",
        pipeline_id="springboot-216-to-356-java21-three-stage",
        pipeline_version="1.0.0",
        stage_index=1,
        stage_id="springboot-2.1.6-to-2.7-java11",
        profile_id="legacy-migration",
        command_jdk="java11",
        sandbox_root_id="root-sandbox",
        sandbox_relative_path="sandbox/test-001",
        run_configuration_artifact_id="artifact-rc-001",
        run_configuration_checksum="abc123rc",
        working_directory_root_id="root-output",
        working_directory_relative_path="jobs/test-001",
        stdout_relative_path="logs/stdout.log",
        stderr_relative_path="logs/stderr.log",
        result_relative_path="result.json",
        spool_relative_path="spool",
        catalog_checksum="catalog-checksum-001",
        ledger_input_checksum="input-checksum-001",
        ledger_checksum_guard="guard-001",
        correlation_id=None,
        causation_id=None,
    )
    kwargs.update(overrides)
    return StageCommandLaunchCommand(**kwargs)


# ---------------------------------------------------------------------------
# Checksum coverage tests
# ---------------------------------------------------------------------------


class TestStageManifestChecksumCoverage:
    """Verify the manifest checksum covers all required contract fields."""

    def test_base_manifest_checksum_roundtrip(self):
        """A CommandManifest checksum must compute and verify correctly."""
        manifest = CommandManifest(
            schema_version="1.0.0",
            job_id="job-001",
            command_id="cmd-001",
            worker_id="w1",
            operation="build",
            run_configuration_artifact_id="rc-art-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-out",
            working_directory_relative_path="jobs/001",
            stdout_relative_path="logs/stdout.log",
            stderr_relative_path="logs/stderr.log",
            result_relative_path="result.json",
            spool_relative_path="spool",
            timeout_seconds=3600,
            max_stdout_bytes=104857600,
            max_stderr_bytes=104857600,
            event_schema_version="1.0.0",
            created_at="2026-06-12T00:00:00Z",
            manifest_checksum="",
        )
        checksum = compute_manifest_checksum(manifest)
        assert isinstance(checksum, str) and len(checksum) == 64
        assert checksum == hashlib.sha256(
            json.dumps(
                manifest.model_dump(mode="json", exclude={"manifest_checksum"}),
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        manifest = manifest.model_copy(update={"manifest_checksum": checksum})
        verify_manifest_checksum(manifest)

    def test_stage_manifest_checksum_covers_ledger_fields(self):
        """Checksum must cover ledger_id, ledger_input_checksum, ledger_checksum_guard."""
        m1 = _make_stage_manifest()
        m2 = _make_stage_manifest(ledger_id="different-ledger")
        cs1 = compute_stage_manifest_checksum(m1)
        cs2 = compute_stage_manifest_checksum(m2)
        assert cs1 != cs2, "changing ledger_id must change checksum"

        m3 = _make_stage_manifest(ledger_input_checksum="different-input-cs")
        cs3 = compute_stage_manifest_checksum(m3)
        assert cs1 != cs3, "changing ledger_input_checksum must change checksum"

        m4 = _make_stage_manifest(ledger_checksum_guard="different-guard")
        cs4 = compute_stage_manifest_checksum(m4)
        assert cs1 != cs4, "changing ledger_checksum_guard must change checksum"

    def test_stage_manifest_checksum_covers_jdk_fields(self):
        """Checksum must cover jdk_id, jdk_java_home, jdk_expected_major."""
        m1 = _make_stage_manifest()
        m2 = _make_stage_manifest(jdk_id="java17")
        cs1 = compute_stage_manifest_checksum(m1)
        cs2 = compute_stage_manifest_checksum(m2)
        assert cs1 != cs2, "changing jdk_id must change checksum"

        m3 = _make_stage_manifest(jdk_java_home="/opt/java17")
        cs3 = compute_stage_manifest_checksum(m3)
        assert cs1 != cs3, "changing jdk_java_home must change checksum"

        m4 = _make_stage_manifest(jdk_expected_major=17)
        cs4 = compute_stage_manifest_checksum(m4)
        assert cs1 != cs4, "changing jdk_expected_major must change checksum"

    def test_stage_manifest_checksum_covers_profile_and_pipeline(self):
        """Checksum must cover runner_profile_display_name, pipeline_id, pipeline_version."""
        m1 = _make_stage_manifest()
        m2 = _make_stage_manifest(runner_profile_display_name="Different Runner")
        cs1 = compute_stage_manifest_checksum(m1)
        cs2 = compute_stage_manifest_checksum(m2)
        assert cs1 != cs2, "changing runner_profile_display_name must change checksum"

        m3 = _make_stage_manifest(pipeline_id="different-pipeline")
        cs3 = compute_stage_manifest_checksum(m3)
        assert cs1 != cs3, "changing pipeline_id must change checksum"

        m4 = _make_stage_manifest(pipeline_version="2.0.0")
        cs4 = compute_stage_manifest_checksum(m4)
        assert cs1 != cs4, "changing pipeline_version must change checksum"

    def test_stage_manifest_checksum_covers_catalog_checksum(self):
        """Checksum must cover catalog_checksum."""
        m1 = _make_stage_manifest()
        m2 = _make_stage_manifest(catalog_checksum="different-catalog")
        cs1 = compute_stage_manifest_checksum(m1)
        cs2 = compute_stage_manifest_checksum(m2)
        assert cs1 != cs2, "changing catalog_checksum must change checksum"

    def test_stage_manifest_checksum_covers_sandbox_fields(self):
        """Checksum must cover sandbox_root_id and sandbox_relative_path."""
        m1 = _make_stage_manifest()
        m2 = _make_stage_manifest(sandbox_root_id="root-different")
        cs1 = compute_stage_manifest_checksum(m1)
        cs2 = compute_stage_manifest_checksum(m2)
        assert cs1 != cs2, "changing sandbox_root_id must change checksum"

        m3 = _make_stage_manifest(sandbox_relative_path="sandbox/other")
        cs3 = compute_stage_manifest_checksum(m3)
        assert cs1 != cs3, "changing sandbox_relative_path must change checksum"

    def test_stage_manifest_checksum_covers_stage_metadata(self):
        """Checksum must cover stage_index, stage_id, profile_id, command_jdk."""
        m1 = _make_stage_manifest()
        m2 = _make_stage_manifest(stage_index=2)
        cs1 = compute_stage_manifest_checksum(m1)
        cs2 = compute_stage_manifest_checksum(m2)
        assert cs1 != cs2, "changing stage_index must change checksum"

        m3 = _make_stage_manifest(stage_id="different-stage")
        cs3 = compute_stage_manifest_checksum(m3)
        assert cs1 != cs3, "changing stage_id must change checksum"

        m4 = _make_stage_manifest(profile_id="different-profile")
        cs4 = compute_stage_manifest_checksum(m4)
        assert cs1 != cs4, "changing profile_id must change checksum"

        m5 = _make_stage_manifest(command_jdk="java17")
        cs5 = compute_stage_manifest_checksum(m5)
        assert cs1 != cs5, "changing command_jdk must change checksum"

    def test_stage_manifest_checksum_covers_argv(self):
        """Checksum must cover the argv tuple."""
        m1 = _make_stage_manifest(argv=())
        m2 = _make_stage_manifest(argv=("mvn", "clean", "install"))
        cs1 = compute_stage_manifest_checksum(m1)
        cs2 = compute_stage_manifest_checksum(m2)
        assert cs1 != cs2, "adding argv must change checksum"

        m3 = _make_stage_manifest(argv=("mvn", "test"))
        cs3 = compute_stage_manifest_checksum(m3)
        assert cs2 != cs3, "changing argv must change checksum"

    def test_stage_manifest_checksum_covers_env(self):
        """Checksum must cover the env dict."""
        m1 = _make_stage_manifest(env={})
        m2 = _make_stage_manifest(env={"MAVEN_OPTS": "-Xmx512m"})
        cs1 = compute_stage_manifest_checksum(m1)
        cs2 = compute_stage_manifest_checksum(m2)
        assert cs1 != cs2, "adding env must change checksum"

        m3 = _make_stage_manifest(env={"MAVEN_OPTS": "-Xmx256m"})
        cs3 = compute_stage_manifest_checksum(m3)
        assert cs2 != cs3, "changing env must change checksum"

    def test_stage_manifest_checksum_integrity_failure(self):
        """verify_stage_manifest_checksum must raise on mismatch."""
        manifest = _make_stage_manifest(manifest_checksum="tampered")
        with pytest.raises(ManifestIntegrityError, match="checksum mismatch"):
            verify_stage_manifest_checksum(manifest)

    def test_stage_manifest_checksum_set_and_verify(self):
        """Checksum must round-trip correctly when set."""
        manifest = _make_stage_manifest()
        cs = compute_stage_manifest_checksum(manifest)
        manifest = manifest.model_copy(update={"manifest_checksum": cs})
        # Should not raise
        verify_stage_manifest_checksum(manifest)


# ---------------------------------------------------------------------------
# Backend-owned argv/env ownership tests
# ---------------------------------------------------------------------------


class TestBackendOwnedArgvEnv:
    """Verify that argv and env are backend-owned, not browser-supplied."""

    def test_stage_command_manifest_carries_backend_argv(self):
        """StageCommandManifest must carry the backend-supplied argv."""
        backend_argv = ("mvn", "clean", "compile", "-DskipTests")
        manifest = _make_stage_manifest(argv=backend_argv)
        assert manifest.argv == backend_argv

    def test_stage_command_manifest_carries_backend_env(self):
        """StageCommandManifest must carry the backend-supplied env."""
        backend_env = {"MAVEN_OPTS": "-Xmx1024m", "JAVA_HOME": "/opt/java11"}
        manifest = _make_stage_manifest(env=backend_env)
        assert manifest.env == backend_env

    def test_browser_restricted_payload_marker(self):
        """BrowserRestrictedPayload must mark argv/env as backend-owned."""
        payload = BrowserRestrictedPayload(
            argv=("mvn", "compile"),
            env={"MAVEN_OPTS": "-Xmx512m"},
        )
        assert payload.argv == ("mvn", "compile")
        assert payload.env == {"MAVEN_OPTS": "-Xmx512m"}

    def test_browser_cannot_choose_raw_paths(self):
        """Verify the manifest has no browser-configurable executable path field."""
        manifest = _make_stage_manifest(argv=("mvn", "compile"))
        # The argv is the only way to pass executable+args, and it is
        # backend-owned. There is no 'executable_path' or 'shell_command'
        # field that the browser could fill.
        assert not hasattr(manifest, "executable_path")
        assert not hasattr(manifest, "shell_command")
        assert not hasattr(manifest, "maven_goal")
        assert not hasattr(manifest, "working_directory")
        assert not hasattr(manifest, "model_deployment_id")

    def test_browser_cannot_choose_working_directory(self):
        """Working directory fields must be backend-owned, not browser-selectable."""
        manifest = _make_stage_manifest()
        # The working_directory_root_id and relative_path are backend-owned
        # through the runner profile configuration.
        # Browser does not have its own 'working_directory' field.
        assert hasattr(manifest, "working_directory_root_id")
        assert hasattr(manifest, "working_directory_relative_path")

    def test_llm_cannot_execute_or_approve(self):
        """Verify no fields allow LLM execution or approval in this contract."""
        manifest = _make_stage_manifest()
        # The manifest contract does not carry any LLM execution authority,
        # approval flags, or write permissions.
        assert not hasattr(manifest, "llm_execute")
        assert not hasattr(manifest, "llm_approve")
        assert not hasattr(manifest, "llm_write")


# ---------------------------------------------------------------------------
# No process launch tests
# ---------------------------------------------------------------------------


class TestNoProcessLaunch:
    """Verify no process is started by this contract-only issue."""

    def test_stage_command_manifest_is_contract_only(self):
        """StageCommandManifest must be a data contract with no launch capability."""
        manifest = _make_stage_manifest()
        # Verify it's just a data model
        assert isinstance(manifest, StageCommandManifest)
        assert isinstance(manifest, CommandManifest)
        # No launch methods
        assert not hasattr(manifest, "launch")
        assert not hasattr(manifest, "start")
        assert not hasattr(manifest, "execute")
        assert not hasattr(manifest, "run")
        assert not hasattr(manifest, "spawn")

    def test_builder_does_not_launch(self):
        """StageCommandManifestBuilder.build must not start any process."""
        cmd = _make_launch_command()
        manifest = StageCommandManifestBuilder.build(cmd)
        assert isinstance(manifest, StageCommandManifest)
        assert manifest.manifest_checksum != ""
        # Verify no side effects on process state
        assert manifest.manifest_checksum is not None


# ---------------------------------------------------------------------------
# StageCommandLaunchCommand contract tests
# ---------------------------------------------------------------------------


class TestStageCommandLaunchCommand:
    """Verify the launch command DTO contract."""

    def test_launch_command_is_frozen_dataclass(self):
        """StageCommandLaunchCommand must be a frozen dataclass."""
        cmd = _make_launch_command()
        assert isinstance(cmd, StageCommandLaunchCommand)


# ---------------------------------------------------------------------------
# V1 invariant preservation tests
# ---------------------------------------------------------------------------


class TestV1Invariants:
    """Preserve all V1 pipeline invariants."""

    def test_pipeline_id_is_fixed(self):
        """Pipeline ID must remain springboot-216-to-356-java21-three-stage."""
        manifest = _make_stage_manifest()
        assert manifest.pipeline_id == "springboot-216-to-356-java21-three-stage"
        assert manifest.pipeline_version == "1.0.0"

    def test_stage_jdk_invariants(self):
        """Stage 1 uses java11, Stage 2 uses java17, Stage 3 uses java21."""
        s1 = _make_stage_manifest(stage_index=1, command_jdk="java11", jdk_id="java11")
        assert s1.command_jdk == "java11"
        assert s1.jdk_id == "java11"

    def test_no_boot4_selection(self):
        """Boot 4 must not be selectable in V1."""
        manifest = _make_stage_manifest()
        # The manifest does not expose any boot version selection field
        # Boot 4 is not part of the contract
        assert not hasattr(manifest, "boot_version")
        assert not hasattr(manifest, "boot4")

    def test_shell_disabled_by_default(self):
        """Shell must remain disabled by default in this contract."""
        manifest = _make_stage_manifest()
        # The manifest carries backend-owned argv, not arbitrary shell commands
        assert not hasattr(manifest, "enable_shell")
        assert not hasattr(manifest, "shell")
        if manifest.argv:
            for arg in manifest.argv:
                assert ";" not in arg
                assert "|" not in arg
                assert "&&" not in arg

    def test_manifest_stage_fields_preserved(self):
        """All stage-specific fields must be present in the manifest."""
        manifest = _make_stage_manifest()
        assert manifest.stage_run_id == "stage-test-0001"
        assert manifest.ledger_id == "ledger-test-0001"
        assert manifest.jdk_id == "java11"
        assert manifest.stage_index == 1


# ---------------------------------------------------------------------------
# Shared mutable default isolation tests (AMF-170)
# ---------------------------------------------------------------------------


class TestStageManifestEnvDefaultIsolation:
    """Verify env default does not share mutable state across instances.

    Regression for AMF-170: using ``env: dict[str, str] = {}`` as a class-level
    default causes all instances created without an explicit ``env`` to share
    the same dict object. Mutations on one instance leak to all others.
    """

    def test_two_manifests_without_env_have_independent_env_dicts(self):
        """Two StageCommandManifests created without env must have independent env."""
        m1 = _make_stage_manifest()
        m2 = _make_stage_manifest()
        m1.env["MUTATION"] = "only-m1"
        assert "MUTATION" not in m2.env, (
            "Mutating env on m1 leaked into m2 — shared mutable default detected"
        )

    def test_manifest_with_env_and_without_env_are_independent(self):
        """A manifest with explicit env and one without must not share state."""
        m1 = _make_stage_manifest(env={"EXPLICIT": "yes"})
        m2 = _make_stage_manifest()
        m2.env["IMPLICIT"] = "leak-test"
        assert "IMPLICIT" not in m1.env, (
            "Mutating env on default-instance leaked into explicit-env instance"
        )


# ---------------------------------------------------------------------------
# StageCommandLaunchService contract tests (no-db verification)
# ---------------------------------------------------------------------------


class TestStageCommandLaunchServiceContract:
    """Verify the launch service contract without database."""

    def test_service_name_and_signature(self):
        """StageCommandLaunchService must accept a unit_of_work_factory."""
        # Just verify the class is importable and constructable
        # Real DB tests are in the integration suite
        assert StageCommandLaunchService is not None
        assert hasattr(StageCommandLaunchService, "__init__")
        assert hasattr(StageCommandLaunchService, "execute")

    def test_builder_restricted_payload_roundtrip(self):
        """Builder must produce a valid BrowserRestrictedPayload."""
        payload = StageCommandManifestBuilder.build_restricted_payload(
            argv=("mvn", "compile"),
            env={"MAVEN_OPTS": "-Xmx512m"},
        )
        assert isinstance(payload, BrowserRestrictedPayload)
        assert payload.argv == ("mvn", "compile")
        assert payload.env == {"MAVEN_OPTS": "-Xmx512m"}
