"""V2 worker stage execution — backend-owned Stage 1 command manifest."""

from __future__ import annotations

import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text
from migration_factory.control_tower.infrastructure.sqlite.v2_setup_repository import (
    SqliteV2SetupRepository,
    V2MigrationSetupRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_command_repository import (
    SqliteV2CommandRepository,
    V2StageCommandRecord,
)
from migration_factory.control_tower.infrastructure.sqlite.v2_job_repository import (
    SqliteV2JobRepository,
)
from migration_factory.control_tower.infrastructure.sqlite.repositories import (
    SqliteRunConfigurationRepository,
)
from migration_factory.control_tower.application.v2_profile_runtime import (
    resolve_catalog_for_runtime_profile,
    ensure_runtime_profile_available,
    resolve_execution_jdk_id_for_runtime_profile,
    resolve_execution_jdk_env_var_for_runtime_profile,
    resolve_runtime_profile_for_run_configuration,
)


STAGE_JDK_MAP = {
    1: {"jdk_id": "java11", "env_var": "JAVA11_HOME", "expected_major": 11},
    2: {"jdk_id": "java17", "env_var": "JAVA17_HOME", "expected_major": 17},
    3: {"jdk_id": "java21", "env_var": "JAVA21_HOME", "expected_major": 21},
    4: {"jdk_id": "java21", "env_var": "JAVA21_HOME", "expected_major": 21},
}

PIPELINE_ID = "springboot-216-to-400-java21-four-stage"
RUNNER_MODULE = "migration_factory.orchestrator.runner"
RESUME_MODULE = "migration_factory.orchestrator.resume"


@dataclass(frozen=True)
class V2StageCommandResult:
    command_id: str
    job_id: str
    stage_index: int
    manifest_checksum: str
    argv: tuple[str, ...]
    created_at: str
    route_step_index: int = 1
    runtime_profile: str = ""
    catalog: str = ""
    execution_jdk: str = ""


class V2WorkerStageService:
    """Build Stage 1 command manifests from V2 setup data.

    The manifest argv/env are always backend-owned. Browser payloads
    cannot supply argv or env values.
    """

    def __init__(
        self,
        setup_repo: SqliteV2SetupRepository,
        command_repo: SqliteV2CommandRepository | None = None,
        job_repo: SqliteV2JobRepository | None = None,
        run_config_repo: SqliteRunConfigurationRepository | None = None,
    ) -> None:
        self._setup_repo = setup_repo
        self._command_repo = command_repo
        self._job_repo = job_repo
        self._run_config_repo = run_config_repo

    def build_stage1_manifest(
        self,
        job_id: str,
        setup_id: str,
        run_id: str | None = None,
    ) -> V2StageCommandResult:
        """Build a Stage 1 command manifest from a V2 setup.

        Does NOT start any process. Only builds and persists the manifest.
        """
        setup = self._setup_repo.get(setup_id)
        if setup is None:
            raise ValueError(f"Setup {setup_id!r} not found")
        if self._job_repo is not None:
            job = self._job_repo.get(job_id)
            if job is None:
                raise ValueError(f"Job {job_id!r} not found")
            if job.setup_id != setup_id:
                raise ValueError(
                    "Stage 1 start setup_id must match the persisted job's setup_id"
                )

        if self._run_config_repo is None:
            raise ValueError(
                "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: run configuration repository is unavailable"
            )
        run_configuration = self._run_config_repo.get_for_job(job_id)
        if run_configuration is None:
            raise ValueError(
                "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: persisted run configuration not found for job "
                f"{job_id!r}"
            )
        runtime_profile = resolve_runtime_profile_for_run_configuration(run_configuration)
        ensure_runtime_profile_available(setup.ai_hub_path, runtime_profile)
        catalog = resolve_catalog_for_runtime_profile(runtime_profile)
        execution_jdk = resolve_execution_jdk_id_for_runtime_profile(runtime_profile)

        command_id = uuid4().hex
        now = utc_now_text()
        effective_run_id = run_id or f"v2-{job_id[:8]}"

        # Build backend-owned argv/env for Stage 1 from the selected route profile.
        jdk_env_var = resolve_execution_jdk_env_var_for_runtime_profile(runtime_profile)
        jdk_home = _get_jdk_home(setup, jdk_env_var)
        path_prepend = str(Path(jdk_home) / "bin")

        argv = (
            sys.executable,
            "-m",
            RUNNER_MODULE,
            "--run-id", effective_run_id,
            "--legacy", setup.legacy_app_path,
            "--modernized", setup.output_parent_path,
            "--ai-hub", setup.ai_hub_path,
            "--profile", runtime_profile,
            "--mode", "full_sandbox_migration",
        )

        # Persist to database
        if self._command_repo is not None:
            env_manifest = {
                "JAVA_HOME": jdk_home,
                "JAVA11_HOME": setup.java11_home,
                "JAVA17_HOME": setup.java17_home,
                "JAVA21_HOME": setup.java21_home,
                "MAVEN_CMD": setup.maven_cmd,
                "PATH_PREPEND": path_prepend,
                "ROUTE_STEP_INDEX": "1",
                "ROUTE_STEP_RUNTIME_PROFILE": runtime_profile,
                "ROUTE_STEP_CATALOG": catalog,
                "ROUTE_STEP_EXECUTION_JDK": execution_jdk,
            }
            record = V2StageCommandRecord(
                command_id=command_id,
                job_id=job_id,
                stage_index=1,
                manifest_checksum="v2-stage1",  # Simplified for now
                argv_json=json.dumps(list(argv), separators=(",", ":")),
                env_json=json.dumps(env_manifest, separators=(",", ":")),
                status="manifest_ready",
                created_at=now,
                updated_at=now,
                result_json=None,
            )
            self._command_repo.save(record)

        return V2StageCommandResult(
            command_id=command_id,
            job_id=job_id,
            stage_index=1,
            manifest_checksum="v2-stage1",  # Simplified for now
            argv=argv,
            created_at=now,
            route_step_index=1,
            runtime_profile=runtime_profile,
            catalog=catalog,
            execution_jdk=execution_jdk,
        )

    def get_command(self, command_id: str) -> V2StageCommandResult | None:
        """Retrieve a persisted command by ID."""
        if self._command_repo is None:
            return None
        record = self._command_repo.get(command_id)
        if record is None:
            return None
        try:
            argv = tuple(json.loads(record.argv_json))
        except (json.JSONDecodeError, TypeError):
            argv = ()
        return V2StageCommandResult(
            command_id=record.command_id,
            job_id=record.job_id,
            stage_index=record.stage_index,
            manifest_checksum=record.manifest_checksum,
            argv=argv,
            created_at=record.created_at,
            route_step_index=1,
            runtime_profile="",
            catalog="",
            execution_jdk="",
        )

    def result_to_dict(self, result: V2StageCommandResult) -> dict[str, Any]:
        return {
            "command_id": result.command_id,
            "job_id": result.job_id,
            "stage_index": result.stage_index,
            "manifest_checksum": result.manifest_checksum,
            "argv": list(result.argv),
            "created_at": result.created_at,
            "route_step_index": result.route_step_index,
            "runtime_profile": result.runtime_profile,
            "catalog": result.catalog,
            "execution_jdk": result.execution_jdk,
        }


def _get_jdk_home(setup: V2MigrationSetupRecord, env_var: str) -> str:
    mapping = {
        "JAVA11_HOME": setup.java11_home,
        "JAVA17_HOME": setup.java17_home,
        "JAVA21_HOME": setup.java21_home,
    }
    jdk_home = mapping.get(env_var, "")
    if not jdk_home:
        raise ValueError(
            f"Required JDK home {env_var!r} is missing for the selected runtime profile"
        )
    return jdk_home
