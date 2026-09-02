ALTER TABLE command_executions ADD COLUMN process_control_id TEXT;
ALTER TABLE command_executions ADD COLUMN worker_pid INTEGER;
ALTER TABLE command_executions ADD COLUMN process_started_at TEXT;

INSERT INTO event_types (event_type, description) VALUES
    ('command_starting', 'Controlled diagnostic worker is starting'),
    ('command_running', 'Controlled diagnostic worker is running');
