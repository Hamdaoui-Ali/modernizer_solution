-- V1-07A: Persist Control Tower approval decisions
--
-- Records approval decisions from the orchestrator approval node and
-- queues resume commands for later execution.
--
-- The v1_approvals table is append-only. A new row is inserted each
-- time an approval decision is recorded for an interrupt/checksum pair.
-- Idempotency is guaranteed by the interrupt_id + request_checksum
-- unique constraint.
--
-- The v1_approval_resume_queue table stores pending resume commands
-- that will be executed when the approval is resumed. Resume commands
-- are never executed directly by the approval endpoint; they are queued
-- and later picked up by the worker.
--
-- Invariants preserved:
--   * Approval creation is idempotent by interrupt/checksum.
--   * Approval resume queues a command, never direct resume.
--   * Browser payloads cannot choose raw paths, Maven goals, shell
--     commands, working directories, or model deployments.
--   * LLM flows cannot execute commands, approve decisions, or write
--     files directly.
--   * Boot 4 NOT selectable
--   * 3.5.14 NOT execution-relevant

CREATE TABLE v1_approvals (
    approval_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    interrupt_id TEXT NOT NULL,
    request_checksum TEXT NOT NULL,
    decision TEXT NOT NULL CHECK (decision IN ('approved', 'rejected', 'replan_required')),
    approved_by TEXT NOT NULL,
    approval_comments TEXT DEFAULT '',
    actor_type TEXT NOT NULL DEFAULT 'system',
    actor_id TEXT NOT NULL DEFAULT 'system',
    payload_json TEXT NOT NULL DEFAULT '{}',
    payload_checksum TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    UNIQUE (interrupt_id, request_checksum)
);

CREATE INDEX ix_v1_approvals_job_id ON v1_approvals(job_id, created_at DESC);

CREATE INDEX ix_v1_approvals_interrupt_id ON v1_approvals(interrupt_id, request_checksum);

CREATE TABLE v1_approval_resume_queue (
    resume_id TEXT PRIMARY KEY,
    approval_id TEXT NOT NULL,
    job_id TEXT NOT NULL,
    command_type TEXT NOT NULL,
    command_payload_json TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'pending' CHECK (status IN ('pending', 'executed', 'failed')),
    created_at TEXT NOT NULL,
    executed_at TEXT,
    failure_reason TEXT,
    correlation_id TEXT,
    causation_id TEXT,
    FOREIGN KEY (approval_id) REFERENCES v1_approvals(approval_id)
);

CREATE INDEX ix_v1_approval_resume_queue_approval_id
ON v1_approval_resume_queue(approval_id);

CREATE INDEX ix_v1_approval_resume_queue_status
ON v1_approval_resume_queue(status, created_at);

CREATE TRIGGER v1_approvals_no_update
BEFORE UPDATE ON v1_approvals
BEGIN
    SELECT RAISE(ABORT, 'v1_approvals is append-only');
END;

CREATE TRIGGER v1_approvals_no_delete
BEFORE DELETE ON v1_approvals
BEGIN
    SELECT RAISE(ABORT, 'v1_approvals is append-only');
END;

CREATE TRIGGER v1_approval_resume_queue_no_update
BEFORE UPDATE OF resume_id, approval_id, job_id, command_type, command_payload_json, created_at
ON v1_approval_resume_queue
BEGIN
    SELECT RAISE(ABORT, 'v1_approval_resume_queue core fields are append-only');
END;

CREATE TRIGGER v1_approval_resume_queue_no_delete
BEFORE DELETE ON v1_approval_resume_queue
BEGIN
    SELECT RAISE(ABORT, 'v1_approval_resume_queue is append-only');
END;
