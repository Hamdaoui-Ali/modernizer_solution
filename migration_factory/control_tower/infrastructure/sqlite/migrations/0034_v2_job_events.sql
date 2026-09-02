-- V2 cockpit durable event/evidence stream.
--
-- Append-only events provide the Cockpit with replayable stage, command,
-- evidence, and proof updates. Payloads are redacted before insertion by
-- application code.

CREATE TABLE v2_job_events (
    event_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage INTEGER CHECK (stage IS NULL OR stage IN (1, 2, 3)),
    type TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    payload_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    sequence INTEGER NOT NULL UNIQUE
);

CREATE INDEX ix_v2_job_events_job_sequence
ON v2_job_events(job_id, sequence);

CREATE INDEX ix_v2_job_events_job_type
ON v2_job_events(job_id, type);

CREATE TRIGGER v2_job_events_no_update
BEFORE UPDATE ON v2_job_events
BEGIN
    SELECT RAISE(ABORT, 'v2_job_events is append-only');
END;

CREATE TRIGGER v2_job_events_no_delete
BEFORE DELETE ON v2_job_events
BEGIN
    SELECT RAISE(ABORT, 'v2_job_events is append-only');
END;
