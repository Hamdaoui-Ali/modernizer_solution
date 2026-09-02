-- Repair assistant conversation and revision intent persistence.
--
-- Tracks user-assistant chat scoped to a repair proposal, including
-- structured revision intents when the assistant requests a code change.

CREATE TABLE repair_assistant_messages (
    message_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    proposal_id TEXT NOT NULL,
    attempt_number INTEGER,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant')),
    message_text TEXT NOT NULL,
    action TEXT CHECK (action IN ('ANSWER_ONLY', 'REQUEST_REVISION', 'CLARIFICATION_REQUIRED')),
    revision_intent_json TEXT,
    base_diff_checksum TEXT NOT NULL,
    generated_proposal_id TEXT,
    status TEXT CHECK (status IN ('answered', 'clarification_required', 'revision_generating', 'revision_created', 'blocked', 'error')),
    created_at TEXT NOT NULL,
    idempotency_key TEXT
);

CREATE INDEX ix_repair_assistant_messages_scope
ON repair_assistant_messages(job_id, proposal_id, created_at);

CREATE UNIQUE INDEX ux_repair_assistant_messages_idempotency
ON repair_assistant_messages(idempotency_key)
WHERE idempotency_key IS NOT NULL;
