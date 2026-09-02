-- 0044_run_configurations_relax_fk.sql
-- Relax the foreign key from run_configurations to migration_jobs
-- so V2 migration jobs can persist run configurations without
-- needing a V1 migration_jobs row.
-- V1 jobs always create migration_jobs before run_configurations.

-- SQLite does not support ALTER TABLE DROP FOREIGN KEY,
-- so we recreate the table without the migration_jobs FK.

CREATE TABLE run_configurations_new (
    run_configuration_id TEXT PRIMARY KEY,
    job_id              TEXT NOT NULL UNIQUE,

    schema_version          TEXT NOT NULL,
    runner_profile_id       TEXT NOT NULL,
    runner_profile_version  TEXT NOT NULL,
    pipeline_id             TEXT NOT NULL,
    pipeline_version        TEXT NOT NULL,

    target_proof_level  TEXT NOT NULL,
    enabled_gates_json  TEXT NOT NULL,
    policy_json         TEXT NOT NULL,
    payload_json        TEXT NOT NULL,
    payload_checksum    TEXT NOT NULL,
    created_at          TEXT NOT NULL,

    FOREIGN KEY (runner_profile_id, runner_profile_version)
        REFERENCES runner_profiles(
            runner_profile_id,
            runner_profile_version
        )
        ON DELETE RESTRICT,

    FOREIGN KEY (pipeline_id, pipeline_version)
        REFERENCES pipeline_definitions(
            pipeline_id,
            pipeline_version
        )
        ON DELETE RESTRICT
);

INSERT INTO run_configurations_new
SELECT
    run_configuration_id,
    job_id,
    schema_version,
    runner_profile_id,
    runner_profile_version,
    pipeline_id,
    pipeline_version,
    target_proof_level,
    enabled_gates_json,
    policy_json,
    payload_json,
    payload_checksum,
    created_at
FROM run_configurations;

DROP TABLE run_configurations;

ALTER TABLE run_configurations_new RENAME TO run_configurations;
