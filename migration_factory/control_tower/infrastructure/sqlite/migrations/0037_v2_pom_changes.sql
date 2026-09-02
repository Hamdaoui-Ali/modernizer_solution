-- 0037 V2 POM change proposals, changes, validations, and repair plans (F14)
-- Immutable after merge.

CREATE TABLE IF NOT EXISTS v2_pom_change_proposals (
    proposal_id       TEXT PRIMARY KEY,
    job_id            TEXT NOT NULL,
    stage_index       INTEGER NOT NULL DEFAULT 3,
    user_request      TEXT NOT NULL,
    server_plan_json  TEXT NOT NULL,
    risk              TEXT NOT NULL,
    can_apply         INTEGER NOT NULL DEFAULT 0,
    control_mode      TEXT NOT NULL,
    expected_checksum TEXT,
    expires_at        TEXT,
    status            TEXT NOT NULL DEFAULT 'active',
    created_at        TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES v2_jobs (job_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v2_pom_changes (
    change_id          TEXT PRIMARY KEY,
    proposal_id        TEXT,
    job_id             TEXT NOT NULL,
    stage_index        INTEGER NOT NULL DEFAULT 3,
    operation          TEXT NOT NULL,
    target_json        TEXT NOT NULL,
    requested_version  TEXT NOT NULL,
    before_checksum    TEXT NOT NULL,
    after_checksum     TEXT NOT NULL,
    before_content_ref TEXT NOT NULL,
    after_content_ref  TEXT NOT NULL,
    diff_unified       TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'applied_pending_validation',
    validation_id      TEXT,
    rollback_id        TEXT,
    idempotency_key    TEXT,
    executor           TEXT NOT NULL DEFAULT 'pom_span_patch',
    created_at         TEXT NOT NULL,
    updated_at         TEXT NOT NULL,
    FOREIGN KEY (job_id) REFERENCES v2_jobs (job_id) ON DELETE CASCADE,
    FOREIGN KEY (proposal_id) REFERENCES v2_pom_change_proposals (proposal_id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_v2_pom_changes_idempotency
    ON v2_pom_changes (job_id, idempotency_key)
    WHERE idempotency_key IS NOT NULL;

CREATE TABLE IF NOT EXISTS v2_pom_validations (
    validation_id          TEXT PRIMARY KEY,
    change_id             TEXT NOT NULL,
    job_id                TEXT NOT NULL,
    stage_index           INTEGER NOT NULL DEFAULT 3,
    command               TEXT NOT NULL,
    status                TEXT NOT NULL DEFAULT 'running',
    exit_code             INTEGER,
    duration_ms           INTEGER,
    log_ref               TEXT,
    test_log_ref          TEXT,
    failure_classification TEXT,
    diagnosis_json        TEXT,
    created_at            TEXT NOT NULL,
    completed_at          TEXT,
    FOREIGN KEY (job_id) REFERENCES v2_jobs (job_id) ON DELETE CASCADE,
    FOREIGN KEY (change_id) REFERENCES v2_pom_changes (change_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS v2_pom_repair_plans (
    repair_plan_id     TEXT PRIMARY KEY,
    validation_id      TEXT NOT NULL,
    change_id          TEXT NOT NULL,
    summary            TEXT NOT NULL,
    steps_json         TEXT NOT NULL,
    confidence         TEXT NOT NULL,
    evidence_refs_json TEXT NOT NULL,
    status             TEXT NOT NULL DEFAULT 'proposed',
    created_at         TEXT NOT NULL,
    FOREIGN KEY (validation_id) REFERENCES v2_pom_validations (validation_id) ON DELETE CASCADE,
    FOREIGN KEY (change_id) REFERENCES v2_pom_changes (change_id) ON DELETE CASCADE
);
