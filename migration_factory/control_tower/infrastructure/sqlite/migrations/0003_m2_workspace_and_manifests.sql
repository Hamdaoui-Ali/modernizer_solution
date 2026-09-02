CREATE TRIGGER artifacts_no_update
BEFORE UPDATE ON artifacts
BEGIN
    SELECT RAISE(ABORT, 'artifacts are immutable');
END;

CREATE TRIGGER artifacts_no_delete
BEFORE DELETE ON artifacts
BEGIN
    SELECT RAISE(ABORT, 'artifacts are immutable');
END;

CREATE TRIGGER run_configurations_no_update
BEFORE UPDATE ON run_configurations
BEGIN
    SELECT RAISE(ABORT, 'run_configurations are immutable');
END;

CREATE TRIGGER run_configurations_no_delete
BEFORE DELETE ON run_configurations
BEGIN
    SELECT RAISE(ABORT, 'run_configurations are immutable');
END;

CREATE TRIGGER run_events_no_update
BEFORE UPDATE ON run_events
BEGIN
    SELECT RAISE(ABORT, 'run_events are immutable');
END;

CREATE TRIGGER run_events_no_delete
BEFORE DELETE ON run_events
BEGIN
    SELECT RAISE(ABORT, 'run_events are immutable');
END;

ALTER TABLE command_executions ADD COLUMN command_manifest_artifact_id TEXT;
ALTER TABLE command_executions ADD COLUMN working_directory_root_id TEXT;
ALTER TABLE command_executions ADD COLUMN working_directory_relative_path TEXT;
ALTER TABLE command_executions ADD COLUMN worker_id TEXT;
ALTER TABLE command_executions ADD COLUMN launch_attempt INTEGER;
