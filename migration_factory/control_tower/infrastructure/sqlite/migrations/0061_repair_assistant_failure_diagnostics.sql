-- AMF-252: add diagnostic fields to repair_assistant_messages for failure tracking.
ALTER TABLE repair_assistant_messages ADD COLUMN failure_stage TEXT;
ALTER TABLE repair_assistant_messages ADD COLUMN failure_code TEXT;
ALTER TABLE repair_assistant_messages ADD COLUMN safe_failure_message TEXT;
ALTER TABLE repair_assistant_messages ADD COLUMN correlation_id TEXT;
