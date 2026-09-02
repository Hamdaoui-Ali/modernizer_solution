"""V1-05: Runner JDK and Maven readiness validation service.

Performs fakeable readiness checks for Java 11/17/21 JDK installations and
Maven availability using backend-owned paths from the runner profile.
Request bodies cannot override tool refs — all paths come from the registered
runner profile, not from browser or API input.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text


# ---------------------------------------------------------------------------
# Readiness data types
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class JdkReadiness:
    """Result of checking a single JDK installation."""

    jdk_id: str
    jdk_path: str
    ready: bool
    expected_major: int
    message: str = ""


@dataclass(frozen=True, slots=True)
class MavenReadiness:
    """Result of checking a Maven installation."""

    executable_path: str
    ready: bool
    expected_version: str
    message: str = ""


@dataclass(frozen=True, slots=True)
class RunnerReadinessResult:
    """Aggregate readiness for a runner profile."""

    runner_profile_id: str
    runner_profile_version: str
    jdk_11: JdkReadiness
    jdk_17: JdkReadiness
    jdk_21: JdkReadiness
    maven: MavenReadiness
    all_ready: bool = False
    checked_at: str = ""

    @property
    def jdk_11_ready(self) -> bool:
        return self.jdk_11.ready

    @property
    def jdk_17_ready(self) -> bool:
        return self.jdk_17.ready

    @property
    def jdk_21_ready(self) -> bool:
        return self.jdk_21.ready

    @property
    def maven_ready(self) -> bool:
        return self.maven.ready


# ---------------------------------------------------------------------------
# Backend-owned tool reference provider
# ---------------------------------------------------------------------------


@dataclass
class ToolRefs:
    """Tool references extracted from the runner profile.

    All paths come from the registered runner profile, never from
    browser or API request bodies. This enforces the V1 invariant that
    browser payloads cannot choose raw executable paths or Maven goals.
    """

    jdk_11_home: str
    jdk_17_home: str
    jdk_21_home: str
    maven_executable: str
    maven_expected_version: str


def extract_tool_refs(runner_profile_payload: dict[str, Any]) -> ToolRefs:
    """Extract backend-owned tool references from a runner profile payload.

    All JDK and Maven paths come from the registered runner profile.
    There is no mechanism for request bodies to override these refs.
    """
    jdks: Sequence[dict[str, Any]] = runner_profile_payload.get("jdks", ()) or ()
    maven: dict[str, Any] = runner_profile_payload.get("maven", {}) or {}

    jdk_11_home = ""
    jdk_17_home = ""
    jdk_21_home = ""

    for jdk in jdks:
        jdk_id = jdk.get("jdk_id", "")
        java_home = jdk.get("java_home", "")
        if jdk_id == "java11":
            jdk_11_home = java_home
        elif jdk_id == "java17":
            jdk_17_home = java_home
        elif jdk_id == "java21":
            jdk_21_home = java_home

    return ToolRefs(
        jdk_11_home=jdk_11_home,
        jdk_17_home=jdk_17_home,
        jdk_21_home=jdk_21_home,
        maven_executable=maven.get("executable_path", ""),
        maven_expected_version=maven.get("expected_version", ""),
    )


# ---------------------------------------------------------------------------
# Pluggable checker protocol
# ---------------------------------------------------------------------------


class ReadinessChecker:
    """Pluggable readiness checker with fakeable subprocess calls.

    Override `check_java` and `check_maven_version` in tests to avoid
    actual subprocess calls.
    """

    def check_java(self, java_home: str) -> tuple[bool, str]:
        """Check whether a JDK at java_home responds with a version string.

        Returns (ready, message). Uses backend-owned paths only.
        """
        java_bin = _java_executable(java_home)
        if not java_bin:
            return False, f"java executable not found at {java_home}/bin/java"

        try:
            result = subprocess.run(
                [java_bin, "-version"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            output = result.stderr.strip() or result.stdout.strip()
            if result.returncode == 0 and output:
                return True, output.splitlines()[0] if output else "ok"
            return False, f"java -version returned code {result.returncode}: {output}"
        except FileNotFoundError:
            return False, f"java not found at {java_bin}"
        except subprocess.TimeoutExpired:
            return False, "java -version timed out after 10s"
        except OSError as exc:
            return False, f"java execution error: {exc}"

    def check_maven_version(self, mvn_path: str) -> tuple[bool, str]:
        """Check whether Maven at mvn_path responds with a version string.

        Returns (ready, message). Uses backend-owned paths only.
        """
        if not mvn_path:
            return False, "Maven executable path is empty"

        try:
            result = subprocess.run(
                [mvn_path, "--version"],
                capture_output=True,
                text=True,
                timeout=15,
            )
            output = result.stdout.strip() or result.stderr.strip()
            if result.returncode == 0 and output:
                return True, output.splitlines()[0] if output else "ok"
            return False, f"mvn --version returned code {result.returncode}: {output}"
        except FileNotFoundError:
            return False, f"Maven not found at {mvn_path}"
        except subprocess.TimeoutExpired:
            return False, "mvn --version timed out after 15s"
        except OSError as exc:
            return False, f"Maven execution error: {exc}"


def _java_executable(java_home: str) -> str:
    """Return the path to the java binary under java_home."""
    if not java_home:
        return ""
    if sys.platform == "win32":
        return f"{java_home}\\bin\\java.exe"
    return f"{java_home}/bin/java"


# ---------------------------------------------------------------------------
# Service
# ---------------------------------------------------------------------------


class RunnerJdkReadinessService:
    """Performs and records JDK/Maven readiness checks for runner profiles.

    Uses backend-owned tool refs extracted from the registered runner profile.
    Request bodies cannot override tool refs — all JDK and Maven paths are
    determined by the Control Tower from the registered profile.
    """

    def __init__(
        self,
        unit_of_work_factory: Callable,
        checker: ReadinessChecker | None = None,
        now_provider: Callable[[], str] | None = None,
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory
        self._checker = checker or ReadinessChecker()
        self._now_provider = now_provider or utc_now_text

    def check_runner_readiness(
        self,
        runner_profile_id: str,
        runner_profile_version: str,
        *,
        actor: str = "system",
    ) -> RunnerReadinessResult:
        """Check JDK 11/17/21 and Maven readiness for a runner profile.

        All tool paths come from the backend-owned runner profile registration,
        never from API request bodies. Returns independent readiness status for
        each tool, and persists the result.
        """
        with self._unit_of_work_factory() as uow:
            # Load the runner profile from the repository
            dto = uow.runner_profiles.get(runner_profile_id, runner_profile_version)
            if dto is None:
                raise ValueError(
                    f"Runner profile {runner_profile_id!r} version "
                    f"{runner_profile_version!r} not found"
                )

            payload = dto.payload if hasattr(dto, "payload") else dto
            if hasattr(payload, "model_dump"):
                payload = payload.model_dump(mode="python")

            tool_refs = extract_tool_refs(payload)

        # Perform readiness checks using backend-owned paths
        now = self._now_provider()

        jdk_11_result = self._check_single_jdk("java11", tool_refs.jdk_11_home, 11)
        jdk_17_result = self._check_single_jdk("java17", tool_refs.jdk_17_home, 17)
        jdk_21_result = self._check_single_jdk("java21", tool_refs.jdk_21_home, 21)
        maven_result = self._check_maven(tool_refs)

        all_ready = (
            jdk_11_result.ready
            and jdk_17_result.ready
            and jdk_21_result.ready
            and maven_result.ready
        )

        result = RunnerReadinessResult(
            runner_profile_id=runner_profile_id,
            runner_profile_version=runner_profile_version,
            jdk_11=jdk_11_result,
            jdk_17=jdk_17_result,
            jdk_21=jdk_21_result,
            maven=maven_result,
            all_ready=all_ready,
            checked_at=now,
        )

        # Persist the result (append-only)
        self._persist_readiness(result, actor)

        return result

    def _check_single_jdk(
        self,
        jdk_id: str,
        java_home: str,
        expected_major: int,
    ) -> JdkReadiness:
        if not java_home:
            return JdkReadiness(
                jdk_id=jdk_id,
                jdk_path="",
                ready=False,
                expected_major=expected_major,
                message="JDK path not defined in runner profile",
            )
        ready, message = self._checker.check_java(java_home)
        return JdkReadiness(
            jdk_id=jdk_id,
            jdk_path=java_home,
            ready=ready,
            expected_major=expected_major,
            message=message,
        )

    def _check_maven(self, tool_refs: ToolRefs) -> MavenReadiness:
        if not tool_refs.maven_executable:
            return MavenReadiness(
                executable_path="",
                ready=False,
                expected_version=tool_refs.maven_expected_version,
                message="Maven executable path is not defined in runner profile",
            )
        ready, message = self._checker.check_maven_version(tool_refs.maven_executable)
        return MavenReadiness(
            executable_path=tool_refs.maven_executable,
            ready=ready,
            expected_version=tool_refs.maven_expected_version,
            message=message,
        )

    def _persist_readiness(
        self,
        result: RunnerReadinessResult,
        actor: str,
    ) -> None:
        """Insert a readiness check result row into v1_runner_readiness_checks."""
        with self._unit_of_work_factory() as uow:
            connection = getattr(uow, "connection", None)
            if connection is None:
                return

            import hashlib as _hashlib
            import json as _json

            payload = {
                "runner_profile_id": result.runner_profile_id,
                "runner_profile_version": result.runner_profile_version,
                "jdk_11_ready": result.jdk_11.ready,
                "jdk_11_path": result.jdk_11.jdk_path,
                "jdk_11_message": result.jdk_11.message,
                "jdk_17_ready": result.jdk_17.ready,
                "jdk_17_path": result.jdk_17.jdk_path,
                "jdk_17_message": result.jdk_17.message,
                "jdk_21_ready": result.jdk_21.ready,
                "jdk_21_path": result.jdk_21.jdk_path,
                "jdk_21_message": result.jdk_21.message,
                "maven_ready": result.maven.ready,
                "maven_path": result.maven.executable_path,
                "maven_message": result.maven.message,
                "checked_at": result.checked_at,
            }
            _ = _hashlib.sha256(
                _json.dumps(payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
            ).hexdigest()

            connection.execute(
                """
                INSERT INTO v1_runner_readiness_checks (
                    check_id, runner_profile_id, runner_profile_version,
                    jdk_11_ready, jdk_17_ready, jdk_21_ready, maven_ready,
                    jdk_11_path, jdk_17_path, jdk_21_path, maven_path,
                    jdk_11_message, jdk_17_message, jdk_21_message, maven_message,
                    checked_at, created_by
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid4()),
                    result.runner_profile_id,
                    result.runner_profile_version,
                    1 if result.jdk_11.ready else 0,
                    1 if result.jdk_17.ready else 0,
                    1 if result.jdk_21.ready else 0,
                    1 if result.maven.ready else 0,
                    result.jdk_11.jdk_path,
                    result.jdk_17.jdk_path,
                    result.jdk_21.jdk_path,
                    result.maven.executable_path,
                    result.jdk_11.message[:500] if result.jdk_11.message else None,
                    result.jdk_17.message[:500] if result.jdk_17.message else None,
                    result.jdk_21.message[:500] if result.jdk_21.message else None,
                    result.maven.message[:500] if result.maven.message else None,
                    result.checked_at,
                    actor,
                ),
            )


class FakeReadinessChecker(ReadinessChecker):
    """Fake readiness checker for tests. Does not call subprocess."""

    def __init__(
        self,
        *,
        jdk_11_ready: bool = True,
        jdk_17_ready: bool = True,
        jdk_21_ready: bool = True,
        maven_ready: bool = True,
    ) -> None:
        self._jdk_11_ready = jdk_11_ready
        self._jdk_17_ready = jdk_17_ready
        self._jdk_21_ready = jdk_21_ready
        self._maven_ready = maven_ready

    def check_java(self, java_home: str) -> tuple[bool, str]:
        if "java-11" in java_home or "jdk-11" in java_home or "java11" in java_home:
            return (self._jdk_11_ready, "fake JDK 11 check")
        if "java-17" in java_home or "jdk-17" in java_home or "java17" in java_home:
            return (self._jdk_17_ready, "fake JDK 17 check")
        if "java-21" in java_home or "jdk-21" in java_home or "java21" in java_home:
            return (self._jdk_21_ready, "fake JDK 21 check")
        return (True, "fake JDK check")

    def check_maven_version(self, mvn_path: str) -> tuple[bool, str]:
        return (self._maven_ready, "fake Maven check")
