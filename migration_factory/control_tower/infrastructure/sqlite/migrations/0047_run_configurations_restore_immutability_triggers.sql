-- 0047_run_configurations_restore_immutability_triggers.sql
-- Re-create immutable run_configurations triggers after the 0044 table rebuild.

CREATE TRIGGER IF NOT EXISTS run_configurations_no_update
BEFORE UPDATE ON run_configurations
BEGIN
    SELECT RAISE(ABORT, 'run_configurations are immutable');
END;

CREATE TRIGGER IF NOT EXISTS run_configurations_no_delete
BEFORE DELETE ON run_configurations
BEGIN
    SELECT RAISE(ABORT, 'run_configurations are immutable');
END;
