-- Scoped idempotency: replace global idempotency_key uniqueness
-- with (job_id, proposal_id, idempotency_key) scope so two distinct
-- proposals cannot collide on the same key.
--
-- Also adds a covering index for lease renewal and ownership
-- verification queries.

DROP INDEX IF EXISTS ux_repair_assistant_messages_idempotency;

CREATE UNIQUE INDEX ux_repair_assistant_messages_scoped_idempotency
ON repair_assistant_messages(job_id, proposal_id, idempotency_key)
WHERE idempotency_key IS NOT NULL;

CREATE INDEX ix_repair_assistant_messages_lease_lookup
ON repair_assistant_messages(idempotency_key, job_id, proposal_id, status, processing_owner);
