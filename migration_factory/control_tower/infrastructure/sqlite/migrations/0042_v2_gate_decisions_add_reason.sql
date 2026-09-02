-- F15-JOB-030: Add reason column to v2_gate_decisions
--
-- This enables persisting rejection (and other decision) reasons
-- as part of the append-only decision record for full auditability.

ALTER TABLE v2_gate_decisions ADD COLUMN reason TEXT NOT NULL DEFAULT '';
