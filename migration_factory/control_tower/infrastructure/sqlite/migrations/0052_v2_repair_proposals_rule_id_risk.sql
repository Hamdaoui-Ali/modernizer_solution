-- AMF-252: Add deterministic_rule_id and risk columns for direct Option A
-- proposal persistence. Both fields are nullable so existing records
-- continue to load without changes.

ALTER TABLE v2_repair_proposals ADD COLUMN deterministic_rule_id TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN risk TEXT;
