-- PR-G: Governed LLM invocation ledger for proposer/reviewer/fallback telemetry.
--
-- V2 governed table with role, responsibility, checksums, safe alias fields,
-- and redacted summaries. Distinct from v1_model_invocations which lacks
-- role, responsibility, proposal/gate correlation, context_checksum,
-- output_checksum, fallback_used, and safe deployment alias/hash fields.
--
-- Security constraints:
--   * Raw prompts, completions, endpoints, and API keys are never stored.
--   * provider_alias is a safe display label, not a raw provider ID.
--   * deployment_alias_hash is a content-derived hash, not the raw deployment name.
--   * redacted_error stores only sanitized/redacted error text.
--   * redacted_summary stores only a safe summary string.
--   * No foreign key to avoid cascading issues; job_id is an opaque reference.
--
-- Invariants preserved:
--   * proposer and reviewer invocations have distinct invocation_id values.
--   * role + responsibility uniquely describe the model's function in the pipeline.
--   * For a given proposal, proposer and reviewer must not have the same invocation_id.

CREATE TABLE v2_llm_invocations (
    invocation_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    proposal_id TEXT,
    gate_id TEXT,
    role TEXT NOT NULL,
    responsibility TEXT NOT NULL,
    provider_alias TEXT,
    deployment_alias_hash TEXT,
    context_checksum TEXT,
    input_checksum TEXT,
    output_checksum TEXT,
    schema_name TEXT,
    status TEXT NOT NULL,
    fallback_used INTEGER DEFAULT 0,
    redacted_error TEXT,
    redacted_summary TEXT,
    prompt_tokens INTEGER,
    completion_tokens INTEGER,
    total_tokens INTEGER,
    latency_ms INTEGER,
    created_at TEXT NOT NULL,
    completed_at TEXT,
    CHECK (role IN ('main', 'reviewer', 'fallback')),
    CHECK (responsibility IN ('repair_proposal', 'repair_review', 'revision_proposal', 'revision_review', 'diagnosis', 'explanation')),
    CHECK (status IN ('started', 'completed', 'failed', 'fallback')),
    CHECK (fallback_used IN (0, 1))
);

CREATE INDEX ix_v2_llm_invocations_job_created
ON v2_llm_invocations(job_id, created_at);

CREATE INDEX ix_v2_llm_invocations_proposal
ON v2_llm_invocations(proposal_id);

CREATE INDEX ix_v2_llm_invocations_gate
ON v2_llm_invocations(gate_id);

CREATE INDEX ix_v2_llm_invocations_role
ON v2_llm_invocations(role);

CREATE INDEX ix_v2_llm_invocations_status
ON v2_llm_invocations(status);

CREATE TRIGGER v2_llm_invocations_no_delete
BEFORE DELETE ON v2_llm_invocations
BEGIN
    SELECT RAISE(ABORT, 'v2_llm_invocations is append-only');
END;
