-- Preserve repair revision metadata for reloadable, checksum-faithful proposals.
ALTER TABLE v2_repair_proposals ADD COLUMN source_proposal_id TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN revision_of TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN revision_number INTEGER;
ALTER TABLE v2_repair_proposals ADD COLUMN context_pack_checksum TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN allowed_scope TEXT;
ALTER TABLE v2_repair_proposals ADD COLUMN proposal_checksum TEXT;
