ALTER TABLE command_executions ADD COLUMN stdout_offset INTEGER NOT NULL DEFAULT 0;
ALTER TABLE command_executions ADD COLUMN stderr_offset INTEGER NOT NULL DEFAULT 0;
ALTER TABLE command_executions ADD COLUMN output_limit_exceeded INTEGER NOT NULL DEFAULT 0;

INSERT INTO event_types (event_type, description) VALUES
    ('command_output_available', 'Command output bytes are available at given offsets'),
    ('output_limit_exceeded', 'Command output exceeded configured byte limit');
