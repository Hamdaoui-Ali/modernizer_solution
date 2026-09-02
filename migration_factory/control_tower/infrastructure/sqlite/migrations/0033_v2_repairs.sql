-- V2-P0-004: Repair proposal and sandbox action persistence
--
-- This migration adds tables to persist V2 repair proposals and
-- sandbox actions. Previously, V2RepairFlowService stored everything
-- in-memory.
--
-- Invariants preserved:
--   * Proposals are append-only (no delete; status changes append new).
--   * Sandbox actions are append-only (no update, no delete).
--   * Approval required before patch application.
--   * Patches apply to sandbox only.
--   * Rollback on failure.

CREATE TABLE v2_repair_proposals (
    proposal_id TEXT PRIMARY KEY,
    command_id TEXT NOT NULL,
    failure_summary TEXT NOT NULL DEFAULT '',
    hypothesis TEXT NOT NULL DEFAULT '',
    patch_summary TEXT NOT NULL DEFAULT '',
    affected_paths_json TEXT NOT NULL DEFAULT '[]',
    status TEXT NOT NULL DEFAULT 'draft',
    approval_checksum TEXT,
    created_at TEXT NOT NULL
);

CREATE INDEX ix_v2_repair_proposals_command
ON v2_repair_proposals(command_id);

CREATE TABLE v2_sandbox_actions (
    action_id TEXT PRIMARY KEY,
    proposal_id TEXT NOT NULL,
    target_path TEXT NOT NULL,
    patch_content TEXT NOT NULL DEFAULT '',
    status TEXT NOT NULL DEFAULT 'pending',
    result_summary TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL
);

CREATE INDEX ix_v2_sandbox_actions_proposal
ON v2_sandbox_actions(proposal_id);

CREATE TRIGGER v2_repair_proposals_no_delete
BEFORE DELETE ON v2_repair_proposals
BEGIN
    SELECT RAISE(ABORT, 'v2_repair_proposals is append-only');
END;

CREATE TRIGGER v2_sandbox_actions_no_update
BEFORE UPDATE ON v2_sandbox_actions
BEGIN
    SELECT RAISE(ABORT, 'v2_sandbox_actions is append-only');
END;

CREATE TRIGGER v2_sandbox_actions_no_delete
BEFORE DELETE ON v2_sandbox_actions
BEGIN
    SELECT RAISE(ABORT, 'v2_sandbox_actions is append-only');
END;
