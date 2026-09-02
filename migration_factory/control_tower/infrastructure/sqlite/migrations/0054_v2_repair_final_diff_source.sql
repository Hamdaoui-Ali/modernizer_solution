-- AMF-252: durable selected final diff origin.
ALTER TABLE v2_repair_proposals ADD COLUMN final_diff_source TEXT;
