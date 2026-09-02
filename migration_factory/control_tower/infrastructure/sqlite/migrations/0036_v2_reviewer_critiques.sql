-- V2-P0-008: Reviewer critique persistence (F07)
--
-- Immutable records of reviewer LLM decisions on repair and POM proposals.
-- Each critique is bound to a specific proposal checksum and context pack
-- checksum. Only the latest critique for a given checksum pair is used for
-- gate decisions.
--
-- Reviewer accept is NOT human approval. It only enables approval-card
-- eligibility.
--
-- Invariants:
--   * Critiques are append-only (no update, no delete).
--   * Reviewer accept never changes proposal status.
--   * Gate checks use latest accepted critique matching current checksums.

CREATE TABLE v2_reviewer_critiques (
    critique_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    proposal_type TEXT NOT NULL DEFAULT 'repair',
    proposal_checksum TEXT NOT NULL,
    context_pack_checksum TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('accept', 'revise', 'reject')),
    reasoning TEXT NOT NULL DEFAULT '',
    missing_evidence_json TEXT NOT NULL DEFAULT '[]',
    unsafe_assumptions_json TEXT NOT NULL DEFAULT '[]',
    model_invocation_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX ix_v2_reviewer_critiques_proposal
ON v2_reviewer_critiques(proposal_id);

CREATE INDEX ix_v2_reviewer_critiques_proposal_type
ON v2_reviewer_critiques(proposal_id, proposal_type);

CREATE INDEX ix_v2_reviewer_critiques_checksum_match
ON v2_reviewer_critiques(proposal_id, proposal_checksum, context_pack_checksum, decision);

-- Append-only guard: no updates, no deletes

CREATE TRIGGER v2_reviewer_critiques_no_update
BEFORE UPDATE ON v2_reviewer_critiques
BEGIN
    SELECT RAISE(ABORT, 'v2_reviewer_critiques is append-only');
END;

CREATE TRIGGER v2_reviewer_critiques_no_delete
BEFORE DELETE ON v2_reviewer_critiques
BEGIN
    SELECT RAISE(ABORT, 'v2_reviewer_critiques is append-only');
END;
