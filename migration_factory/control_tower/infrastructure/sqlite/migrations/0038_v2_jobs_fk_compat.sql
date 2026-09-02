-- 0038 Compatibility parent table for F14 POM change foreign keys.
-- 0037 references v2_jobs(job_id), while the canonical V2 job table is
-- v2_migration_jobs. Keep this minimal parent table in sync append-only.

CREATE TABLE IF NOT EXISTS v2_jobs (
    job_id TEXT PRIMARY KEY
);

INSERT OR IGNORE INTO v2_jobs (job_id)
SELECT job_id FROM v2_migration_jobs;

CREATE TRIGGER IF NOT EXISTS v2_migration_jobs_v2_jobs_insert
AFTER INSERT ON v2_migration_jobs
BEGIN
    INSERT OR IGNORE INTO v2_jobs (job_id) VALUES (NEW.job_id);
END;
