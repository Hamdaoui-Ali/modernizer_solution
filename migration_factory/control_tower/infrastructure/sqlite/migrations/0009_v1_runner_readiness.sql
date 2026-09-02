-- V1-05: Runner JDK and Maven readiness checks
--
-- Records the results of JDK 11/17/21 and Maven readiness checks for runner
-- profiles. Each check is independently recorded so the Control Tower can
-- report per-tool readiness status.
--
-- The runner_readiness_checks table is append-only. A new row is inserted
-- each time a readiness check is performed for a (runner_profile_id,
-- runner_profile_version) tuple. The most recent row for each tuple
-- represents the current readiness state.
--
-- Invariants preserved:
--   * JDK paths come from backend-owned runner profile refs (java_home),
--     not from browser input.
--   * Request bodies cannot override tool refs.
--   * Boot 4 NOT selectable
--   * 3.5.14 NOT execution-relevant

CREATE TABLE v1_runner_readiness_checks (
    check_id TEXT PRIMARY KEY,
    runner_profile_id TEXT NOT NULL,
    runner_profile_version TEXT NOT NULL,
    jdk_11_ready INTEGER NOT NULL CHECK (jdk_11_ready IN (0, 1)),
    jdk_17_ready INTEGER NOT NULL CHECK (jdk_17_ready IN (0, 1)),
    jdk_21_ready INTEGER NOT NULL CHECK (jdk_21_ready IN (0, 1)),
    maven_ready INTEGER NOT NULL CHECK (maven_ready IN (0, 1)),
    jdk_11_path TEXT NOT NULL,
    jdk_17_path TEXT NOT NULL,
    jdk_21_path TEXT NOT NULL,
    maven_path TEXT NOT NULL,
    jdk_11_message TEXT,
    jdk_17_message TEXT,
    jdk_21_message TEXT,
    maven_message TEXT,
    checked_at TEXT NOT NULL,
    created_by TEXT NOT NULL
);

CREATE INDEX ix_v1_runner_readiness_checks_profile
ON v1_runner_readiness_checks(runner_profile_id, runner_profile_version, checked_at DESC);

CREATE TRIGGER v1_runner_readiness_checks_no_update
BEFORE UPDATE ON v1_runner_readiness_checks
BEGIN
    SELECT RAISE(ABORT, 'v1_runner_readiness_checks is append-only');
END;

CREATE TRIGGER v1_runner_readiness_checks_no_delete
BEFORE DELETE ON v1_runner_readiness_checks
BEGIN
    SELECT RAISE(ABORT, 'v1_runner_readiness_checks is append-only');
END;
