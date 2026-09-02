ALTER TABLE command_executions ADD COLUMN stdout_artifact_id TEXT;
ALTER TABLE command_executions ADD COLUMN stderr_artifact_id TEXT;
ALTER TABLE command_executions ADD COLUMN result_artifact_id TEXT;
ALTER TABLE command_executions ADD COLUMN spool_artifact_id TEXT;
ALTER TABLE command_executions ADD COLUMN finalization_status TEXT DEFAULT 'PENDING';
ALTER TABLE command_executions ADD COLUMN finalized_at TEXT;

INSERT INTO event_types (event_type, description) VALUES
    ('command_finalized', 'Command terminal artifacts have been finalized and registered');
