-- Durable lineage for post-target-version validation.

ALTER TABLE v2_pom_changes ADD COLUMN logical_stage_index INTEGER;
ALTER TABLE v2_pom_changes ADD COLUMN execution_stage_index INTEGER;
ALTER TABLE v2_pom_changes ADD COLUMN route_step_index INTEGER;
ALTER TABLE v2_pom_changes ADD COLUMN expected_checksum TEXT;
ALTER TABLE v2_pom_changes ADD COLUMN request_checksum TEXT;
ALTER TABLE v2_pom_changes ADD COLUMN command_id TEXT;
ALTER TABLE v2_pom_changes ADD COLUMN validation_context_ref TEXT;
ALTER TABLE v2_pom_changes ADD COLUMN validation_context_checksum TEXT;
ALTER TABLE v2_pom_changes ADD COLUMN repair_linkage_json TEXT;
ALTER TABLE v2_pom_changes ADD COLUMN repair_proposal_id TEXT;
ALTER TABLE v2_pom_changes ADD COLUMN pom_path_ref TEXT;

ALTER TABLE v2_pom_validations ADD COLUMN logical_stage_index INTEGER;
ALTER TABLE v2_pom_validations ADD COLUMN execution_stage_index INTEGER;
ALTER TABLE v2_pom_validations ADD COLUMN route_step_index INTEGER;
ALTER TABLE v2_pom_validations ADD COLUMN command_id TEXT;
ALTER TABLE v2_pom_validations ADD COLUMN validation_context_ref TEXT;
ALTER TABLE v2_pom_validations ADD COLUMN validation_context_checksum TEXT;
ALTER TABLE v2_pom_validations ADD COLUMN build_status TEXT;
ALTER TABLE v2_pom_validations ADD COLUMN test_status TEXT;
ALTER TABLE v2_pom_validations ADD COLUMN repair_linkage_json TEXT;
