-- V2-P0-004: Assistant message and pending action draft persistence
--
-- This migration adds tables to persist V2 assistant messages and
-- pending action drafts. Previously, V2AssistantService stored
-- everything in-memory.
--
-- Invariants preserved:
--   * Messages are append-only (no update, no delete).
--   * Drafts are append-only (no delete; status changes append new).
--   * Assistant cannot execute, approve, or write files.
--   * Drafts start as "draft", can be submitted but not executed.

CREATE TABLE v2_assistant_messages (
    message_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    correlation_id TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX ix_v2_assistant_messages_job
ON v2_assistant_messages(job_id, created_at);

CREATE TABLE v2_pending_action_drafts (
    action_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    action_type TEXT NOT NULL,
    reason TEXT NOT NULL DEFAULT '',
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3)),
    payload_checksum TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'draft',
    created_at TEXT NOT NULL
);

CREATE INDEX ix_v2_pending_action_drafts_job
ON v2_pending_action_drafts(job_id, status);

CREATE TRIGGER v2_assistant_messages_no_update
BEFORE UPDATE ON v2_assistant_messages
BEGIN
    SELECT RAISE(ABORT, 'v2_assistant_messages is append-only');
END;

CREATE TRIGGER v2_assistant_messages_no_delete
BEFORE DELETE ON v2_assistant_messages
BEGIN
    SELECT RAISE(ABORT, 'v2_assistant_messages is append-only');
END;

CREATE TRIGGER v2_pending_action_drafts_no_delete
BEFORE DELETE ON v2_pending_action_drafts
BEGIN
    SELECT RAISE(ABORT, 'v2_pending_action_drafts is append-only');
END;
