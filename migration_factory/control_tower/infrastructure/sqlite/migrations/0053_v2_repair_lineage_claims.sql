-- AMF-252: immutable lineage references and short-lived Apply/continuation state.
-- All columns are nullable for legacy proposal compatibility; new records must
-- populate the fields relevant to their lifecycle state.
ALTER TABLE v2_repair_proposals ADD COLUMN lineage_manifest_ref TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN lineage_manifest_checksum TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN validation_context_ref TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN validation_context_checksum TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN apply_idempotency_key TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN apply_claim_status TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN apply_claim_version INTEGER;
ALTER TABLE v2_repair_proposals ADD COLUMN continuation_command_id TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN validation_proof_status TEXT;

CREATE UNIQUE INDEX ux_v2_repair_apply_idempotency
ON v2_repair_proposals(apply_idempotency_key)
WHERE apply_idempotency_key IS NOT NULL;
