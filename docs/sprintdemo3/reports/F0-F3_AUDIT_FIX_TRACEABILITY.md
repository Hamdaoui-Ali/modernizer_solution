# F0-F3 Audit Fix Traceability

Date: 2026-06-26
Branch: demo3/f0-f3-audit-fixes

This report maps the F0-F3 audit remediation evidence added on top of latest
`demov3`. It does not start F4 or F5 runtime work.

## Audit Findings

| Finding | Status | Evidence |
|---|---|---|
| F0 stale Copilot runner tests expected Copilot env/event behavior | Fixed | `tests/control_tower/test_v2_orchestrator_runner.py::test_v2_runner_does_not_forward_copilot_env_to_product_subprocess`; `test_v2_runner_emits_failure_repair_events_from_result` |
| Product/public DTOs exposed runtime internals | Fixed for touched product surfaces | `StageProgressRequest` rejects `sandbox_path`/`argv`; `V2StageProgressionService.continuation_to_public_dict()` redacts execution internals; `/v1/settings/ai` and `/v1/model-profiles*` omit provider/env-ref/deployment fields; frontend contracts align |
| F2 reviewed-result gate was missing for Analysis/Planning phase completion | Partially fixed: reviewed-result validation gate added; production producer integration remains follow-up | `V2OrchestratorRunner._handle_reviewed_phase_completed()` requires accepted checksum-bound reviewer output and `final_reviewed_markdown` before opening Analysis/Planning review gates if those fields are produced |

## F2 Producer Path Trace

Current production Analysis/Planning execution does not create `review_chain`,
`primary_llm_output`, `reviewer_llm_output`, or `final_reviewed_markdown`.

| Question | Trace |
|---|---|
| Which function executes Analysis phase? | `migration_factory/orchestrator/phase_services.py::run_analysis_phase()` calls `_run_phase(..., service=_run_analysis_service)`, which calls `migration_factory/agents/analysis_agent/analysis_agent/main.py::run_analysis_agent()` |
| Which function executes Planning phase? | `migration_factory/orchestrator/phase_services.py::run_planning_phase()` calls `_run_phase(..., service=_run_planning_service)`, which calls `migration_factory/agents/planning_agent/node.py::planning_node()` |
| What shape does the real command result currently have? | The subprocess entrypoint `migration_factory/orchestrator/runner.py` serializes the graph state through `_render_result()`. Analysis returns status, errors/blockers/warnings, and deterministic `artifact_refs`. Planning returns status, errors/blockers/warnings, planning metadata, and deterministic `artifact_refs`. |
| Where are artifacts persisted? | Analysis writes files under the run analysis output through `MigrationContext.get_output_path()`. Planning writes files through planning-agent writers such as `write_migration_plan()`, `write_migration_units()`, `write_approval_request()`, and `write_plan_summary()`. Control Tower records artifact refs in events and phase gates; it does not yet persist F2 final reviewed Markdown from these producers. |
| Where would deterministic artifact, primary LLM output, reviewer output, and final reviewed Markdown be created? | Deterministic artifacts already come from Analysis/Planning agents. Primary LLM output, Reviewer LLM output, final reviewed Markdown assembly, and checksum-bound persistence would need to be added to the production Analysis/Planning producer path before the runner sees the result. |
| Is there already a reviewer service/model router path that can be reused? | Partially. `V2AssistantModelClient` and `V2ModelRoleRouter` support role-based model calls, including reviewer role. `V2ReviewerService` currently records proposal critiques for repair/POM proposal gates, not the F2 Analysis/Planning review-chain artifact producer. |
| Is there already artifact revision/persistence support that can store final reviewed Markdown? | Partially. `ArtifactRevision` and SQLite repositories can track versioned evidence refs/checksums, and `V2GateArtifactResolver` can resolve gate-bound refs. There is not yet a production Analysis/Planning writer that stores `final_reviewed_markdown` and returns its checksum-bound review chain. |

## AMF Traceability

| Jira key | Behavior | Implementation files | Tests |
|---|---|---|---|
| AMF-250 | Safe auto-continue stops for Analysis, Planning, risk, build/test failure, reviewer failure, stale artifact, approval requirement, and target reached; clean green later stages may continue | `migration_factory/control_tower/application/v2_stage_progression.py`, `migration_factory/control_tower/application/v2_orchestrator_runner.py` | `tests/control_tower/test_v2_stage_progression.py::test_analysis_checkpoint_stops_progression`, `test_planning_checkpoint_stops_progression`, `test_build_failure_stops_progression`, `test_test_failure_stops_progression`, `test_clean_green_stage3_auto_continues_to_4` |
| AMF-251 | Stop-condition matrix lists condition, event type, restorable flag, repair eligibility, and allowed user actions | `migration_factory/control_tower/application/v2_stage_progression.py` | `tests/control_tower/test_v2_stage_progression.py::test_all_stop_conditions_defined`, `test_stop_condition_allowed_actions`, `test_restorable_stop_conditions`, `test_repair_eligible_only_build_test_failure` |
| AMF-264 | Source/target profile route maps only required stages and records excluded/skipped stages | `migration_factory/control_tower/application/v2_stage_progression.py`, `migration_factory/control_tower/schemas/profile_model.py` | `tests/control_tower/test_v2_stage_progression.py::test_valid_route_stops_at_target_springboot_35_java17`, `test_valid_route_stops_at_target_springboot_35_java21`, `test_route_metadata_includes_all_stages` |
| AMF-265 | Pipeline stops when target profile is reached and blocks higher-profile stages | `migration_factory/control_tower/application/v2_stage_progression.py`, `migration_factory/control_tower/application/v2_orchestrator_runner.py` | `tests/control_tower/test_v2_stage_progression.py::test_target_reached_after_stage3_for_boot35_target`, `test_stage_queue_blocked_when_target_reached`, `test_stage3_continues_to_4_when_target_is_boot4` |
| AMF-268 | Target overshoot is prevented on normal progression and resume-after-target scenarios | `migration_factory/control_tower/application/v2_stage_progression.py`, `migration_factory/control_tower/schemas/run_configuration.py` | `tests/control_tower/test_v2_stage_progression.py::test_resume_after_target_reached_is_blocked`, `test_higher_profile_exists_but_must_not_execute`, `test_excluded_skipped_stages_visible_in_route_metadata`, `test_profile_metadata_preserves_source_and_target` |

## F2 Reviewed-Result Validation Evidence

F2 runtime producer integration remains BLOCKED / follow-up. This branch only
validates reviewed phase results when produced.

| Scenario | Test evidence |
|---|---|
| Analysis supplied reviewed result opens gate only with final reviewed Markdown | `tests/control_tower/test_v2_orchestrator_runner.py::test_analysis_reviewed_result_validation_requires_final_reviewed_markdown` |
| Planning supplied reviewed result opens gate only with final reviewed Markdown | `tests/control_tower/test_v2_orchestrator_runner.py::test_planning_reviewed_result_validation_requires_final_reviewed_markdown` |
| Missing reviewer fails closed | `test_missing_reviewer_fails_closed_for_phase_checkpoint` |
| Rejected reviewer blocks checkpoint | `test_rejected_or_revision_reviewed_result_blocks_checkpoint` |
| Request revision blocks checkpoint | `test_rejected_or_revision_reviewed_result_blocks_checkpoint` |
| Stale/checksum mismatch fails closed | `test_stale_or_checksum_mismatched_reviewer_fails_closed` |
| Raw primary output is not downstream input | `test_raw_primary_output_is_not_downstream_checkpoint_input` |

## F2 Follow-Up

Real F2 runtime producer integration must wire Analysis and Planning to:

- create or resolve deterministic artifact data;
- call the primary model through the existing model/client/router services;
- call the reviewer model through existing reviewer/model services;
- build final reviewed Markdown only after backend validation passes;
- persist artifact refs/checksums through existing artifact revision or gate artifact mechanisms;
- return a real `review_chain` consumed by `V2OrchestratorRunner`.

## Public Field Redaction Evidence

| Surface | Evidence |
|---|---|
| Stage progression API request | `tests/control_tower/test_profile_api_contract.py::test_stage_progress_request_rejects_sandbox_path_and_argv` |
| Stage progression API response | `tests/control_tower/test_profile_api_contract.py::test_stage_continuation_public_projection_redacts_execution_details` |
| Settings API | `tests/control_tower/test_v2_settings.py::test_ai_settings_endpoint_returns_projection` |
| Model profiles API | `tests/control_tower/test_v1_09_model_profiles.py::TestV1ModelProfilesApi::test_register_and_get_model_profile` |
| Frontend API helper | `web/control-tower/lib/controlTowerApi.ts::progressV2Stage` posts only `setup_id` and `current_stage` |
| Frontend contracts | `web/control-tower/lib/contracts.ts` omits stage continuation `sandbox_path`/`argv` and settings provider/env-ref/deployment fields |
