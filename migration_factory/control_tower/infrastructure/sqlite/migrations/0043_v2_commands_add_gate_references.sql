-- F15-JOB-046: Add gate_id and decision_id to v2_stage_commands
--
-- Enables tracing commands back to the gate decision that triggered them.
-- Both columns are nullable for backward compatibility with existing commands.

ALTER TABLE v2_stage_commands ADD COLUMN gate_id TEXT;
ALTER TABLE v2_stage_commands ADD COLUMN decision_id TEXT;
