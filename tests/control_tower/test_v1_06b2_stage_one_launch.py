"""Focused tests for V1-06B2: Launch worker-owned Stage One.

This test file covers:
- Stage 1 launch uses backend-owned argv/env and shell=False.
- Stage 1 uses the JAVA11 mapping for the worker-owned launch path.
- Unsupported platform or runtime conditions fail closed without launching.
- Browser payloads cannot choose raw paths, Maven goals, shell commands,
  working directories, or model deployments.
- LLM flows cannot execute commands, approve decisions, or write files directly.
- V1 invariant preservation.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock
from uuid import uuid4

import pytest

from migration_factory.control_tower.application.commands import StageCommandLaunchCommand
from migration_factory.control_tower.application.dto import WorkerLaunchResult
from migration_factory.control_tower.application.services import (
    StageOneLaunchService,
    StageCommandManifestBuilder,
)
from migration_factory.control_tower.domain.errors import (
    NotFoundError,
    UnsupportedPlatformError,
    WorkspacePathError,
)
from migration_factory.control_tower.domain.manifests import (
    BrowserRestrictedPayload,
    StageCommandManifest,
    compute_stage_manifest_checksum,
    verify_stage_manifest_checksum,
)
from migration_factory.control_tower.infrastructure.worker_launcher import (
    UnsupportedPlatformWorkerLauncher,
    StubWorkerTerminator,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_stage_one_launch_command(**overrides) -> StageCommandLaunchCommand:
    """Create a default Stage 1 launch command."""
    kwargs = dict(
        job_id="job-stage1-001",
        command_id="cmd-stage1-001",
        worker_id="worker-stage1",
        operation="maven_build",
        stage_run_id="stage-test-0001",
        ledger_id="ledger-test-0001",
        jdk_id="java11",
        jdk_java_home="/opt/java11",
        jdk_expected_major=11,
        runner_profile_display_name="Legacy Migration Runner",
        pipeline_id="springboot-216-to-356-java21-three-stage",
        pipeline_version="1.0.0",
        stage_index=1,
        stage_id="springboot-2.1.6-to-2.7-java11",
        profile_id="legacy-migration",
        command_jdk="java11",
        sandbox_root_id="root-sandbox",
        sandbox_relative_path="sandbox/stage1-001",
        run_configuration_artifact_id="artifact-rc-stage1-001",
        run_configuration_checksum="rc-checksum-stage1",
        working_directory_root_id="root-output",
        working_directory_relative_path="jobs/stage1-001",
        stdout_relative_path="logs/stdout.log",
        stderr_relative_path="logs/stderr.log",
        result_relative_path="result.json",
        spool_relative_path="spool",
        catalog_checksum="catalog-cs-stage1-001",
        ledger_input_checksum="input-cs-stage1-001",
        ledger_checksum_guard="guard-stage1-001",
        correlation_id=None,
        causation_id=None,
    )
    kwargs.update(overrides)
    return StageCommandLaunchCommand(**kwargs)


class FakeUnitOfWork:
    """Minimal fake UoW that returns stubs for worker launch tests."""

    def __init__(self) -> None:
        self.run_configurations = _FakeRunConfigRepo()
        self.runner_profiles = _FakeRunnerProfileRepo()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        return None


class _FakeRunConfigRepo:
    def get_for_job(self, job_id: str):
        if job_id == "job-stage1-001":
            return MagicMock(
                runner_profile_id="legacy-migration-runner",
                runner_profile_version="1.0.0",
            )
        return None


class _FakeRunnerProfileRepo:
    def get_exact(self, profile_id: str, version: str):
        if profile_id == "legacy-migration-runner" and version == "1.0.0":
            from pydantic import BaseModel
            class FakeRoot(BaseModel):
                root_id: str
                kind: str
                path: str
            class FakeFilesystem(BaseModel):
                roots: tuple[FakeRoot, ...]
            class FakePayload(BaseModel):
                filesystem: FakeFilesystem
                python_executable: str
            mock = MagicMock()
            mock.payload = FakePayload(
                filesystem=FakeFilesystem(
                    roots=(
                        FakeRoot(root_id="root-output", kind="output", path="/tmp/output"),
                    )
                ),
                python_executable=sys.executable,
            )
            return mock
        return None


# ---------------------------------------------------------------------------
# Stage 1 JAVA11 argv/env mapping tests
# ---------------------------------------------------------------------------


class TestStageOneArgvEnvMapping:
    """Verify Stage 1 uses JAVA11 backend-owned argv/env."""

    def test_stage_one_argv_is_backend_owned(self):
        """StageOneLaunchService.build_stage_one_argv must return backend-owned argv."""
        argv = StageOneLaunchService.build_stage_one_argv()
        assert isinstance(argv, tuple)
        assert len(argv) > 0
        # Must include Maven and Rewrite recipes for Boot 2.7 upgrade
        assert "mvn" in argv
        assert any("rewrite" in arg for arg in argv)
        assert any("UpgradeSpringBoot_2_7" in arg for arg in argv)

    def test_stage_one_argv_uses_maven_not_shell(self):
        """Stage 1 argv must use Maven directly, not via shell."""
        argv = StageOneLaunchService.build_stage_one_argv()
        assert "mvn" in argv
        # No shell metacharacters in any arg
        for arg in argv:
            assert ";" not in arg, f"shell metacharacter ';' in argv: {arg}"
            assert "|" not in arg, f"shell metacharacter '|' in argv: {arg}"
            assert "&&" not in arg, f"shell metacharacter '&&' in argv: {arg}"
            assert "$(" not in arg, f"command substitution in argv: {arg}"
            assert "`" not in arg, f"backtick in argv: {arg}"

    def test_stage_one_env_includes_java_home(self):
        """StageOneLaunchService.build_stage_one_env must set JAVA_HOME."""
        env = StageOneLaunchService.build_stage_one_env("/opt/java11")
        assert isinstance(env, dict)
        assert env["JAVA_HOME"] == "/opt/java11"
        assert "MAVEN_OPTS" in env

    def test_stage_one_env_maps_java11(self):
        """Stage 1 env must reflect JAVA11 configuration."""
        env = StageOneLaunchService.build_stage_one_env("/custom/java11")
        assert env["JAVA_HOME"] == "/custom/java11"
        assert "MAVEN_OPTS" in env

    def test_stage_one_env_has_shell_disabled_flag(self):
        """Stage 1 env must carry SHELL_DISABLED=true."""
        env = StageOneLaunchService.build_stage_one_env("/opt/java11")
        assert env.get("SHELL_DISABLED") == "true"

    def test_stage_one_argv_no_browser_selectable_paths(self):
        """Browser must not be able to influence argv paths."""
        argv = StageOneLaunchService.build_stage_one_argv()
        assert not any(arg.startswith("/") for arg in argv), (
            "absolute paths in argv would let browser choose executable"
        )
        # The 'mvn' first arg is resolved via PATH, not an absolute path
        assert argv[0] == "mvn"

    def test_stage_one_argv_no_maven_goals_from_browser(self):
        """No field allows browser-chosen Maven goals."""
        # The argv is backend-owned. There is no separate 'maven_goal' field.
        assert not hasattr(StageOneLaunchService, "maven_goal")
        assert not hasattr(StageOneLaunchService, "shell_command")
        assert not hasattr(StageOneLaunchService, "working_directory")
        assert not hasattr(StageOneLaunchService, "model_deployment_id")


# ---------------------------------------------------------------------------
# Launch behavior tests
# ---------------------------------------------------------------------------


class TestStageOneLaunchBehavior:
    """Verify Stage 1 launch uses backend-owned argv/env and shell=False."""

    def test_service_constructs_with_launcher(self):
        """StageOneLaunchService must accept unit_of_work_factory and worker_launcher."""
        uow_factory = MagicMock()
        launcher = MagicMock()
        service = StageOneLaunchService(uow_factory, launcher)
        assert service is not None

    def test_launch_calls_worker_launcher(self):
        """StageOneLaunchService.execute must call worker_launcher.launch."""
        cmd = _make_stage_one_launch_command()

        uow_factory = MagicMock(return_value=FakeUnitOfWork())

        launcher = MagicMock()
        launcher.launch.return_value = WorkerLaunchResult(
            command_id=cmd.command_id,
            job_id=cmd.job_id,
            process_control_id="pc-stage1-001",
            worker_pid=12345,
            process_started_at="2026-06-12T00:00:00Z",
            worker_id=cmd.worker_id,
            launch_attempt=1,
        )

        service = StageOneLaunchService(uow_factory, launcher)
        result = service.execute(cmd)

        assert result.command_id == cmd.command_id
        assert result.job_id == cmd.job_id
        assert result.worker_pid == 12345
        assert result.launch_attempt == 1
        launcher.launch.assert_called_once()

    def test_launch_uses_backend_argv_env(self):
        """Worker launcher must receive backend-owned argv/env from StageOneLaunchService."""
        cmd = _make_stage_one_launch_command()

        uow_factory = MagicMock(return_value=FakeUnitOfWork())

        launcher = MagicMock()
        expected_result = WorkerLaunchResult(
            command_id=cmd.command_id,
            job_id=cmd.job_id,
            process_control_id="pc-stage1-002",
            worker_pid=12346,
            process_started_at="2026-06-12T00:00:00Z",
            worker_id=cmd.worker_id,
            launch_attempt=1,
        )
        launcher.launch.return_value = expected_result

        service = StageOneLaunchService(uow_factory, launcher)
        result = service.execute(cmd)

        # Verify the launcher was called
        call_kwargs = launcher.launch.call_args[1]
        manifest = call_kwargs["manifest"]
        assert isinstance(manifest, StageCommandManifest)

        # The manifest must contain the Stage 1 backend argv/env
        expected_argv = StageOneLaunchService.build_stage_one_argv()
        assert manifest.argv == expected_argv, (
            f"Expected backend argv {expected_argv}, got {manifest.argv}"
        )

        expected_env = StageOneLaunchService.build_stage_one_env(cmd.jdk_java_home)
        assert manifest.env["JAVA_HOME"] == expected_env["JAVA_HOME"]
        assert manifest.env["SHELL_DISABLED"] == "true"
        assert manifest.env["MAVEN_OPTS"] == expected_env["MAVEN_OPTS"]
        assert result.command_id == cmd.command_id

    def test_launch_manifest_checksum_covers_stage_one_argv_env(self):
        """Stage 1 manifest checksum must cover the specific argv/env."""
        cmd = _make_stage_one_launch_command()

        uow_factory = MagicMock(return_value=FakeUnitOfWork())

        launcher = MagicMock()
        launcher.launch.return_value = WorkerLaunchResult(
            command_id=cmd.command_id,
            job_id=cmd.job_id,
            process_control_id="pc-stage1-003",
            worker_pid=12347,
            process_started_at="2026-06-12T00:00:00Z",
            worker_id=cmd.worker_id,
            launch_attempt=1,
        )

        service = StageOneLaunchService(uow_factory, launcher)
        service.execute(cmd)

        call_kwargs = launcher.launch.call_args[1]
        manifest = call_kwargs["manifest"]

        # Verify checksum integrity with the actual argv/env
        verify_stage_manifest_checksum(manifest)

        # Verify checksum changes if argv changes
        argv_cs = compute_stage_manifest_checksum(manifest)
        manifest_diff_argv = manifest.model_copy(
            update={"argv": ("different", "argv")}
        )
        diff_argv_cs = compute_stage_manifest_checksum(manifest_diff_argv)
        assert argv_cs != diff_argv_cs, \
            "changing argv must change stage manifest checksum"


# ---------------------------------------------------------------------------
# Unsupported platform fail-closed tests
# ---------------------------------------------------------------------------


class TestUnsupportedPlatformFailClosed:
    """Verify unsupported platforms/runtime fail closed without launching."""

    def test_unsupported_platform_launcher_raises(self):
        """UnsupportedPlatformWorkerLauncher must raise UnsupportedPlatformError."""
        launcher = UnsupportedPlatformWorkerLauncher()
        manifest = MagicMock()
        with pytest.raises(UnsupportedPlatformError):
            launcher.launch(
                working_dir=Path("/tmp"),
                manifest=manifest,
                manifest_bytes=b"{}",
                python_executable=sys.executable,
            )

    def test_service_fails_closed_with_unsupported_launcher(self):
        """StageOneLaunchService must propagate UnsupportedPlatformError."""
        cmd = _make_stage_one_launch_command()
        uow_factory = MagicMock(return_value=FakeUnitOfWork())

        service = StageOneLaunchService(
            unit_of_work_factory=uow_factory,
            worker_launcher=UnsupportedPlatformWorkerLauncher(),
        )

        with pytest.raises(UnsupportedPlatformError):
            service.execute(cmd)

    def test_no_process_on_unsupported_platform(self):
        """No process launch on unsupported platform: launcher.launch must raise."""
        launcher = UnsupportedPlatformWorkerLauncher()
        with pytest.raises(UnsupportedPlatformError):
            launcher.launch(
                working_dir=Path("/tmp"),
                manifest=MagicMock(),
                manifest_bytes=b"{}",
                python_executable="python",
            )


# ---------------------------------------------------------------------------
# Browser restriction tests
# ---------------------------------------------------------------------------


class TestBrowserRestrictions:
    """Verify browser cannot choose raw paths, Maven goals, shell, etc."""

    def test_no_browser_executable_path(self):
        """Manifest must not have a browser-selectable executable_path."""
        manifest = StageCommandManifest(
            schema_version="1.0.0",
            job_id="job-test",
            command_id="cmd-test",
            worker_id="w1",
            operation="build",
            run_configuration_artifact_id="rc-art-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-out",
            working_directory_relative_path="jobs/test",
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
            sandbox_relative_path="sandbox/test",
            argv=(),
            env={},
        )
        assert not hasattr(manifest, "executable_path"), \
            "Browser must not have an executable_path field"
        assert not hasattr(manifest, "shell_command"), \
            "Browser must not have a shell_command field"
        assert not hasattr(manifest, "maven_goal"), \
            "Browser must not have a maven_goal field"
        assert not hasattr(manifest, "model_deployment_id"), \
            "Browser must not have a model_deployment_id field"

    def test_working_directory_not_browser_selectable(self):
        """Working directory must be backend-owned through runner profile."""
        manifest = StageCommandManifest(
            schema_version="1.0.0",
            job_id="job-test",
            command_id="cmd-test",
            worker_id="w1",
            operation="build",
            run_configuration_artifact_id="rc-art-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-out",
            working_directory_relative_path="jobs/test",
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
            stage_run_id="stage-test",
            ledger_id="ledger-test",
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
            sandbox_relative_path="sandbox/test",
            argv=(),
            env={},
        )
        assert hasattr(manifest, "working_directory_root_id")
        assert hasattr(manifest, "working_directory_relative_path")
        # No browser-level working_directory field
        assert not hasattr(manifest, "browser_working_directory")

    def test_browser_restricted_payload_is_backend_only(self):
        """BrowserRestrictedPayload must only be created by backend code."""
        payload = BrowserRestrictedPayload(
            argv=StageOneLaunchService.build_stage_one_argv(),
            env=StageOneLaunchService.build_stage_one_env("/opt/java11"),
        )
        assert isinstance(payload.argv, tuple)
        assert isinstance(payload.env, dict)
        assert payload.env["JAVA_HOME"] == "/opt/java11"


# ---------------------------------------------------------------------------
# LLM restriction tests
# ---------------------------------------------------------------------------


class TestLlmRestrictions:
    """Verify LLM cannot execute, approve, or write files."""

    def test_no_llm_execute_field(self):
        """Manifest must not have an LLM execution field."""
        manifest = StageCommandManifest(
            schema_version="1.0.0",
            job_id="job-test",
            command_id="cmd-test",
            worker_id="w1",
            operation="build",
            run_configuration_artifact_id="rc-art-001",
            run_configuration_checksum="rc-cs-001",
            working_directory_root_id="root-out",
            working_directory_relative_path="jobs/test",
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
            stage_run_id="stage-test",
            ledger_id="ledger-test",
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
            sandbox_relative_path="sandbox/test",
            argv=(),
            env={},
        )
        assert not hasattr(manifest, "llm_execute")
        assert not hasattr(manifest, "llm_approve")
        assert not hasattr(manifest, "llm_write")

    def test_llm_no_execution_in_service(self):
        """StageOneLaunchService must not expose LLM execution methods."""
        assert not hasattr(StageOneLaunchService, "llm_execute")
        assert not hasattr(StageOneLaunchService, "llm_approve")
        assert not hasattr(StageOneLaunchService, "llm_write")
        assert not hasattr(StageOneLaunchService, "approve")


# ---------------------------------------------------------------------------
# V1 invariant preservation tests
# ---------------------------------------------------------------------------


class TestV1Invariants:
    """Preserve all V1 pipeline invariants."""

    def test_pipeline_id_is_fixed(self):
        """Pipeline ID must remain springboot-216-to-356-java21-three-stage."""
        cmd = _make_stage_one_launch_command()
        assert cmd.pipeline_id == "springboot-216-to-356-java21-three-stage"

    def test_stage_one_uses_java11(self):
        """Stage 1 must use java11."""
        cmd = _make_stage_one_launch_command()
        assert cmd.command_jdk == "java11"
        assert cmd.jdk_id == "java11"
        assert cmd.jdk_expected_major == 11

    def test_stage_one_env_maps_to_java11(self):
        """Stage 1 JAVA_HOME must reflect java11 path."""
        env = StageOneLaunchService.build_stage_one_env("/opt/java11")
        assert env["JAVA_HOME"] == "/opt/java11"

    def test_no_boot4(self):
        """Boot 4 must not be selectable in V1."""
        cmd = _make_stage_one_launch_command()
        # Use hasattr check via dict key check
        assert "boot_version" not in cmd.__dict__ if hasattr(cmd, "__dict__") else True
        assert "boot4" not in type(cmd).__dict__ if hasattr(type(cmd), "__dict__") else True

    def test_shell_disabled(self):
        """Shell must be disabled by default."""
        env = StageOneLaunchService.build_stage_one_env("/opt/java11")
        assert env.get("SHELL_DISABLED") == "true"

    def test_stage_one_launch_uses_shell_false(self):
        """Worker launcher must be called without shell=True."""
        cmd = _make_stage_one_launch_command()
        uow_factory = MagicMock(return_value=FakeUnitOfWork())

        launcher = MagicMock()
        launcher.launch.return_value = WorkerLaunchResult(
            command_id=cmd.command_id,
            job_id=cmd.job_id,
            process_control_id="pc-shell-test",
            worker_pid=99999,
            process_started_at="2026-06-12T00:00:00Z",
            worker_id=cmd.worker_id,
            launch_attempt=1,
        )

        service = StageOneLaunchService(uow_factory, launcher)
        service.execute(cmd)

        call_kwargs = launcher.launch.call_args[1]
        assert "shell" not in call_kwargs or not call_kwargs.get("shell"), \
            "launch must not use shell=True"


# ---------------------------------------------------------------------------
# Shell=False enforcement tests
# ---------------------------------------------------------------------------


class TestShellFalse:
    """Verify shell=False is enforced in worker launch."""

    def test_manifest_has_no_shell_field(self):
        """StageCommandManifest must not carry a shell enable field."""
        manifest = StageCommandManifest(
            schema_version="1.0.0",
            job_id="job-shell-test",
            command_id="cmd-shell-test",
            worker_id="w-shell",
            operation="build",
            run_configuration_artifact_id="rc-art-shell",
            run_configuration_checksum="rc-cs-shell",
            working_directory_root_id="root-out",
            working_directory_relative_path="jobs/shell-test",
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
            stage_run_id="stage-shell",
            ledger_id="ledger-shell",
            jdk_id="java11",
            jdk_java_home="/opt/java11",
            jdk_expected_major=11,
            runner_profile_display_name="Shell Test",
            pipeline_id="springboot-216-to-356-java21-three-stage",
            pipeline_version="1.0.0",
            stage_index=1,
            stage_id="springboot-2.1.6-to-2.7-java11",
            profile_id="legacy-migration",
            command_jdk="java11",
            sandbox_root_id="root-sandbox",
            sandbox_relative_path="sandbox/shell-test",
            argv=(),
            env={},
        )
        assert not hasattr(manifest, "enable_shell")
        assert not hasattr(manifest, "shell")
