-- Repair Assistant processing lease ownership.
--
-- Adds processing_owner, lease start/expiry timestamps, and exact
-- response_message_id correlation for idempotent deterministic replay.
--
-- lease_expires_at uses the repository's existing TEXT timestamp
-- convention (ISO-8601 UTC).  Expired leases are recovered by the
-- next claimant carrying the same idempotency_key; no background
-- cleanup is required.
--
-- Also widens the status CHECK constraint to include the 'processing'
-- and 'revision_failed' states introduced by the lease lifecycle.

ALTER TABLE repair_assistant_messages ADD COLUMN processing_owner TEXT;
ALTER TABLE repair_assistant_messages ADD COLUMN processing_started_at TEXT;
ALTER TABLE repair_assistant_messages ADD COLUMN lease_expires_at TEXT;
ALTER TABLE repair_assistant_messages ADD COLUMN response_message_id TEXT;

-- Rebuild the table to widen the status CHECK constraint.
-- SQLite does not support ALTER … DROP CHECK, so we create a new
-- table, copy data, drop the old table, and rename.
-- The unique partial index is also re-created with the same semantics.

CREATE TABLE repair_assistant_messages_new (
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
    status TEXT CHECK (status IN ('processing', 'answered', 'clarification_required', 'revision_generating', 'revision_created', 'revision_failed', 'blocked', 'error')),
    created_at TEXT NOT NULL,
    idempotency_key TEXT,
    processing_owner TEXT,
    processing_started_at TEXT,
    lease_expires_at TEXT,
    response_message_id TEXT
);

INSERT INTO repair_assistant_messages_new SELECT * FROM repair_assistant_messages;

DROP TABLE repair_assistant_messages;

ALTER TABLE repair_assistant_messages_new RENAME TO repair_assistant_messages;

CREATE INDEX ix_repair_assistant_messages_scope
ON repair_assistant_messages(job_id, proposal_id, created_at);

CREATE UNIQUE INDEX ux_repair_assistant_messages_idempotency
ON repair_assistant_messages(idempotency_key)
WHERE idempotency_key IS NOT NULL;
