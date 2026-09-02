"""Focused tests: V1-05 Validate runner JDK readiness.

Verifies that JDK 11/17/21 and Maven readiness checks are independently
reported, request bodies cannot override tool refs, and results are
persisted in v1_runner_readiness_checks.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from migration_factory.control_tower.application.runner_readiness import (
    RunnerJdkReadinessService,
    FakeReadinessChecker,
    ReadinessChecker,
    extract_tool_refs,
    ToolRefs,
    JdkReadiness,
    MavenReadiness,
    RunnerReadinessResult,
)
from migration_factory.control_tower.infrastructure.sqlite.connection import connect_control_tower
from migration_factory.control_tower.infrastructure.sqlite.migrations import apply_pending_migrations
from migration_factory.control_tower.infrastructure.sqlite.unit_of_work import SqliteUnitOfWork
from tests.control_tower._helpers import (
    canonical_json,
    sha256_json,
    runner_profile_payload,
    seed_runner_profile,
)
from tests.control_tower.v1_fixtures import make_v1_runner_profile


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _migrated_connection(tmp_path: Path) -> sqlite3.Connection:
    connection = connect_control_tower(tmp_path / "control_tower.sqlite3")
    apply_pending_migrations(connection)
    return connection


def _seed_v1_runner(connection: sqlite3.Connection) -> None:
    """Insert the V1 runner profile into the database."""
    payload = make_v1_runner_profile()
    connection.execute(
        """
        INSERT INTO runner_profiles (
            runner_profile_id, runner_profile_version, display_name, schema_version,
            payload_json, payload_checksum, created_at, created_by
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            payload["runner_profile_id"],
            payload["runner_profile_version"],
            payload["display_name"],
            payload["schema_version"],
            canonical_json(payload),
            sha256_json(payload),
            "2026-06-12T00:00:00.000000Z",
            "tester",
        ),
    )


def _service(
    connection: sqlite3.Connection,
    *,
    jdk_11_ready: bool = True,
    jdk_17_ready: bool = True,
    jdk_21_ready: bool = True,
    maven_ready: bool = True,
) -> RunnerJdkReadinessService:
    return RunnerJdkReadinessService(
        lambda: SqliteUnitOfWork(connection),
        checker=FakeReadinessChecker(
            jdk_11_ready=jdk_11_ready,
            jdk_17_ready=jdk_17_ready,
            jdk_21_ready=jdk_21_ready,
            maven_ready=maven_ready,
        ),
    )


def _count_readiness_rows(connection: sqlite3.Connection) -> int:
    return connection.execute(
        "SELECT COUNT(*) FROM v1_runner_readiness_checks"
    ).fetchone()[0]


# ===================================================================
# criterion-1: Java 11/17/21 and Maven checks independently reported
# ===================================================================


class TestIndependentReadinessReporting:
    """Each tool's readiness must be independently reported."""

    def test_all_ready_returns_true(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _seed_v1_runner(connection)
            svc = _service(connection, jdk_11_ready=True, jdk_17_ready=True, jdk_21_ready=True, maven_ready=True)
            result = svc.check_runner_readiness("runner-v1", "2026.06")
            assert result.all_ready is True
            assert result.jdk_11.ready is True
            assert result.jdk_17.ready is True
            assert result.jdk_21.ready is True
            assert result.maven.ready is True
            assert result.jdk_11.jdk_path == "/usr/lib/jvm/java-11-openjdk"
            assert result.jdk_17.jdk_path == "/usr/lib/jvm/java-17-openjdk"
            assert result.jdk_21.jdk_path == "/usr/lib/jvm/java-21-openjdk"
            assert result.maven.executable_path == "/usr/share/maven/bin/mvn"
        finally:
            connection.close()

    def test_jdk_11_failure_independent(self, tmp_path: Path) -> None:
        """JDK 11 failure must not affect JDK 17/21 or Maven reporting."""
        connection = _migrated_connection(tmp_path)
        try:
            _seed_v1_runner(connection)
            svc = _service(connection, jdk_11_ready=False, jdk_17_ready=True, jdk_21_ready=True, maven_ready=True)
            result = svc.check_runner_readiness("runner-v1", "2026.06")
            assert result.all_ready is False  # not all ready
            assert result.jdk_11.ready is False
            assert result.jdk_17.ready is True
            assert result.jdk_21.ready is True
            assert result.maven.ready is True
        finally:
            connection.close()

    def test_jdk_17_failure_independent(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _seed_v1_runner(connection)
            svc = _service(connection, jdk_11_ready=True, jdk_17_ready=False, jdk_21_ready=True, maven_ready=True)
            result = svc.check_runner_readiness("runner-v1", "2026.06")
            assert result.all_ready is False
            assert result.jdk_11.ready is True
            assert result.jdk_17.ready is False
            assert result.jdk_21.ready is True
            assert result.maven.ready is True
        finally:
            connection.close()

    def test_jdk_21_failure_independent(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _seed_v1_runner(connection)
            svc = _service(connection, jdk_11_ready=True, jdk_17_ready=True, jdk_21_ready=False, maven_ready=True)
            result = svc.check_runner_readiness("runner-v1", "2026.06")
            assert result.all_ready is False
            assert result.jdk_11.ready is True
            assert result.jdk_17.ready is True
            assert result.jdk_21.ready is False
            assert result.maven.ready is True
        finally:
            connection.close()

    def test_maven_failure_independent(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _seed_v1_runner(connection)
            svc = _service(connection, jdk_11_ready=True, jdk_17_ready=True, jdk_21_ready=True, maven_ready=False)
            result = svc.check_runner_readiness("runner-v1", "2026.06")
            assert result.all_ready is False
            assert result.jdk_11.ready is True
            assert result.jdk_17.ready is True
            assert result.jdk_21.ready is True
            assert result.maven.ready is False
        finally:
            connection.close()

    def test_all_failures_independent(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _seed_v1_runner(connection)
            svc = _service(connection, jdk_11_ready=False, jdk_17_ready=False, jdk_21_ready=False, maven_ready=False)
            result = svc.check_runner_readiness("runner-v1", "2026.06")
            assert result.all_ready is False
            assert result.jdk_11.ready is False
            assert result.jdk_17.ready is False
            assert result.jdk_21.ready is False
            assert result.maven.ready is False
        finally:
            connection.close()


# ===================================================================
# criterion-2: Request bodies cannot override tool refs
# ===================================================================


class TestToolRefsAreBackendOwned:
    """Request bodies must not be able to override JDK/Maven tool references.

    All tool refs come from the registered runner profile, not from API
    request bodies. The API endpoint takes only a runner_profile_id and
    version — no JDK paths, Maven paths, or tool selection.
    """

    def test_extract_tool_refs_uses_registered_profile(self) -> None:
        """extract_tool_refs must derive paths from runner profile payload."""
        payload = make_v1_runner_profile()
        refs = extract_tool_refs(payload)
        assert refs.jdk_11_home == "/usr/lib/jvm/java-11-openjdk"
        assert refs.jdk_17_home == "/usr/lib/jvm/java-17-openjdk"
        assert refs.jdk_21_home == "/usr/lib/jvm/java-21-openjdk"
        assert refs.maven_executable == "/usr/share/maven/bin/mvn"
        assert refs.maven_expected_version == "3.9.9"

    def test_endpoint_path_does_not_accept_tool_refs(self, tmp_path: Path) -> None:
        """The endpoint only takes profile_id/version — no tool overrides.

        This is a schema/contract test confirming no mechanism exists for
        request bodies to inject tool paths.
        """
        connection = _migrated_connection(tmp_path)
        try:
            _seed_v1_runner(connection)
            svc = _service(connection)
            # The service only accepts runner_profile_id and version.
            # No tool refs can be passed via the check_runner_readiness signature.
            result = svc.check_runner_readiness("runner-v1", "2026.06")
            # All refs come from the seeded profile
            assert result.jdk_11.jdk_path == "/usr/lib/jvm/java-11-openjdk"
            assert result.jdk_17.jdk_path == "/usr/lib/jvm/java-17-openjdk"
            assert result.jdk_21.jdk_path == "/usr/lib/jvm/java-21-openjdk"
            assert result.maven.executable_path == "/usr/share/maven/bin/mvn"
        finally:
            connection.close()

    def test_extracted_refs_ignores_extra_keys(self) -> None:
        """Extra keys in the profile payload must not change extracted refs."""
        payload = make_v1_runner_profile()
        # Add extra keys that a malicious request might try
        payload["override_jdk_11_home"] = "/fake/java11"
        payload["override_maven_path"] = "/fake/mvn"
        refs = extract_tool_refs(payload)
        assert refs.jdk_11_home == "/usr/lib/jvm/java-11-openjdk"  # unchanged
        assert refs.maven_executable == "/usr/share/maven/bin/mvn"  # unchanged

    def test_extract_tool_refs_no_jdk_matches(self) -> None:
        """When no JDK jdk_id matches, paths must be empty."""
        payload = make_v1_runner_profile()
        payload["jdks"] = (
            {
                "jdk_id": "corretto-11",
                "java_home": "/usr/lib/jvm/corretto-11",
                "expected_major": 11,
                "role": "source",
            },
        )
        refs = extract_tool_refs(payload)
        assert refs.jdk_11_home == ""  # No jdk_id "java11"
        assert refs.jdk_17_home == ""
        assert refs.jdk_21_home == ""

    def test_extract_tool_refs_empty_profile(self) -> None:
        """Empty/missing fields must not crash and return empty refs."""
        refs = extract_tool_refs({})
        assert refs.jdk_11_home == ""
        assert refs.jdk_17_home == ""
        assert refs.jdk_21_home == ""
        assert refs.maven_executable == ""


# ===================================================================
# criterion-3: Persistence in v1_runner_readiness_checks
# ===================================================================


class TestReadinessPersistence:
    """Readiness check results must be persisted in the database."""

    def test_persists_one_row_per_check(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _seed_v1_runner(connection)
            svc = _service(connection)
            svc.check_runner_readiness("runner-v1", "2026.06")
            assert _count_readiness_rows(connection) == 1
        finally:
            connection.close()

    def test_persists_multiple_checks(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _seed_v1_runner(connection)
            svc = _service(connection)
            svc.check_runner_readiness("runner-v1", "2026.06")
            svc.check_runner_readiness("runner-v1", "2026.06")
            svc.check_runner_readiness("runner-v1", "2026.06")
            assert _count_readiness_rows(connection) == 3  # append-only
        finally:
            connection.close()

    def test_persists_all_fields(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            _seed_v1_runner(connection)
            svc = _service(connection, jdk_11_ready=True, jdk_17_ready=False, jdk_21_ready=True, maven_ready=False)
            svc.check_runner_readiness("runner-v1", "2026.06")

            row = connection.execute(
                """
                SELECT jdk_11_ready, jdk_17_ready, jdk_21_ready, maven_ready,
                       jdk_11_path, jdk_17_path, jdk_21_path, maven_path,
                       runner_profile_id, runner_profile_version
                FROM v1_runner_readiness_checks
                """
            ).fetchone()
            assert row is not None
            assert row["jdk_11_ready"] == 1
            assert row["jdk_17_ready"] == 0
            assert row["jdk_21_ready"] == 1
            assert row["maven_ready"] == 0
            assert row["jdk_11_path"] == "/usr/lib/jvm/java-11-openjdk"
            assert row["jdk_17_path"] == "/usr/lib/jvm/java-17-openjdk"
            assert row["jdk_21_path"] == "/usr/lib/jvm/java-21-openjdk"
            assert row["maven_path"] == "/usr/share/maven/bin/mvn"
            assert row["runner_profile_id"] == "runner-v1"
            assert row["runner_profile_version"] == "2026.06"
        finally:
            connection.close()


# ===================================================================
# criterion-4: Profile not found raises ValueError
# ===================================================================


class TestProfileNotFound:
    """Attempting to check readiness for a non-existent profile must fail."""

    def test_raises_value_error_for_missing_profile(self, tmp_path: Path) -> None:
        connection = _migrated_connection(tmp_path)
        try:
            svc = _service(connection)
            with pytest.raises(ValueError, match="not found"):
                svc.check_runner_readiness("non-existent", "1.0.0")
        finally:
            connection.close()


# ===================================================================
# criterion-5: FakeReadinessChecker contract
# ===================================================================


class TestFakeReadinessChecker:
    """Verify the fake checker produces expected results."""

    def test_fake_checker_defaults_to_true(self) -> None:
        checker = FakeReadinessChecker()
        ready_11, msg_11 = checker.check_java("/usr/lib/jvm/java-11-openjdk")
        ready_17, msg_17 = checker.check_java("/usr/lib/jvm/java-17-openjdk")
        ready_21, msg_21 = checker.check_java("/usr/lib/jvm/java-21-openjdk")
        ready_mvn, msg_mvn = checker.check_maven_version("/usr/share/maven/bin/mvn")
        assert ready_11 is True
        assert ready_17 is True
        assert ready_21 is True
        assert ready_mvn is True
        assert "fake" in msg_11
        assert "fake" in msg_mvn

    def test_fake_checker_custom_values(self) -> None:
        checker = FakeReadinessChecker(jdk_11_ready=False, maven_ready=False)
        ready_11, _ = checker.check_java("/usr/lib/jvm/java-11-openjdk")
        ready_17, _ = checker.check_java("/usr/lib/jvm/java-17-openjdk")
        ready_mvn, _ = checker.check_maven_version("/usr/share/maven/bin/mvn")
        assert ready_11 is False
        assert ready_17 is True  # unaffected
        assert ready_mvn is False


# ===================================================================
# criterion-6: Invariant tests
# ===================================================================


class TestV1Invariants:
    """V1 invariants must be preserved by readiness checks."""

    def test_no_boot4_in_tool_refs(self) -> None:
        """Boot 4 must not be selectable or appear in readiness paths."""
        payload = make_v1_runner_profile()
        # The V1 runner profile has no reference to Boot 4
        assert "4." not in str(payload.get("maven", {}))

    def test_3514_not_execution_relevant(self) -> None:
        """3.5.14 must not appear in execution paths."""
        payload = make_v1_runner_profile()
        # No 3.5.14 references in runner profile
        assert "3.5.14" not in str(payload)

    def test_no_raw_paths_in_request_params(self) -> None:
        """The service signature must not accept raw executable paths."""
        import inspect
        sig = inspect.signature(RunnerJdkReadinessService.check_runner_readiness)
        params = list(sig.parameters.keys())
        # The method should only accept runner_profile_id, runner_profile_version, and actor
        assert "runner_profile_id" in params
        assert "runner_profile_version" in params
        # It must NOT accept any path/jdk/maven parameters from callers
        assert "java_home" not in params
        assert "maven_path" not in params
        assert "jdk_path" not in params
        assert "tool_refs" not in params
