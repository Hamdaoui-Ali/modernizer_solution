-- Backend-owned per-job approval mode settings.
--
-- v2_migration_jobs remains append-only. Mutable operator settings live in
-- this job-scoped table and default to manual approval when absent.

CREATE TABLE v2_job_approval_settings (
    job_id TEXT PRIMARY KEY,
    auto_approval_enabled INTEGER NOT NULL DEFAULT 0 CHECK (auto_approval_enabled IN (0, 1)),
    updated_at TEXT NOT NULL,
    updated_by TEXT NOT NULL DEFAULT 'system',
    FOREIGN KEY (job_id) REFERENCES v2_migration_jobs(job_id) ON DELETE CASCADE
);
