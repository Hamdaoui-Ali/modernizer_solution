export type RunnerProfileOption = {
  runner_profile_id: string;
  runner_profile_version: string;
  display_name: string;
};

export type PipelineOption = {
  pipeline_id: string;
  pipeline_version: string;
  display_name: string;
};

export type FilesystemRootOption = {
  runner_profile_id: string;
  runner_profile_version: string;
  root_id: string;
  kind: "source" | "output" | string;
  display_name: string;
};

export type Catalog = {
  runnerProfiles: RunnerProfileOption[];
  pipelines: PipelineOption[];
  filesystemRoots: FilesystemRootOption[];
};

export type CreateDiagnosticJobFormValues = {
  runnerProfileKey: string;
  pipelineKey: string;
  legacySourceRootId: string;
  legacySourceRelativePath: string;
  outputRootId: string;
  outputRelativePath: string;
};

export type CreateDiagnosticJobRequest = {
  runner_profile_id: string;
  runner_profile_version: string;
  pipeline_id: string;
  pipeline_version: string;
  legacy_source_root_id: string;
  legacy_source_relative_path: string;
  output_root_id: string;
  output_relative_path: string;
  target_proof_level: "ANALYZED";
  enabled_gates: string[];
  policy: {
    continue_after_warning: boolean;
    enable_runtime_gate: boolean;
    enable_endpoint_gate: boolean;
  };
};

export type JobRepresentation = {
  job: {
    job_id: string;
    version: number;
    state: string;
    created_at: string;
    updated_at: string;
  };
  active_command: null | {
    command_id: string;
    job_id: string;
      operation: string;
      status: string;
      created_at: string;
      updated_at: string;
      command_manifest_artifact_id?: string | null;
      working_directory_root_id?: string | null;
      working_directory_relative_path?: string | null;
      worker_id?: string | null;
      launch_attempt?: number | null;
    };
  etag: string;
};

export type CommandRepresentation = NonNullable<JobRepresentation["active_command"]>;

export type CommandListResponse = {
  job_id: string;
  commands: CommandRepresentation[];
};

export type CommandOutputWindow = {
  command_id: string;
  job_id: string;
  stream: "stdout" | "stderr";
  requested_offset: number;
  start_offset: number;
  next_offset: number;
  data: string;
  encoding: string;
  replacement_characters_used: number;
  truncated: boolean;
  terminal: boolean;
  max_bytes: number;
};

export type ArtifactMetadata = {
  artifact_id: string;
  job_id: string;
  stage_run_id: string | null;
  artifact_type: string;
  registered_root_id: string;
  relative_path: string;
  normalized_relative_path: string;
  content_type: string | null;
  size_bytes: number;
  checksum_algorithm: string;
  checksum: string;
  created_at: string;
  created_by: string;
};

export type ArtifactListResponse = {
  job_id: string;
  artifacts: ArtifactMetadata[];
};

export type PublicRunEvent = {
  event_id: string;
  job_id: string;
  sequence: number;
  event_type: string;
  actor_type: string;
  actor_id: string;
  correlation_id: string | null;
  causation_id: string | null;
  payload: Record<string, unknown>;
  payload_checksum: string;
  created_at: string;
};

export type PublicEventReplayResponse = {
  job_id: string;
  after_sequence: number;
  next_after_sequence: number;
  latest_sequence: number;
  events: PublicRunEvent[];
};

export type PlanAmendmentPreviewChange = {
  stage_index: number;
  change_type: string;
  description: string;
  rationale?: string | null;
};

export type PlanAmendmentPreviewRequest = {
  title: string;
  summary: string;
  source_kind: "manual" | "fake_provider";
  notes: string[];
  changes: PlanAmendmentPreviewChange[];
};

export type PlanAmendmentPreviewResponse = {
  job_id: string;
  source_kind: string;
  title: string;
  summary: string;
  payload_checksum: string;
  change_count: number;
  affected_stage_indexes: number[];
  change_types: string[];
  redacted_summary: {
    source_kind: string;
    title: string;
    summary: string;
    change_count: number;
    affected_stage_indexes: number[];
    change_types: string[];
    non_authoritative: boolean;
  };
  validation_status: "PASS";
  warning_codes: string[];
  preview_persisted: false;
  preview_applied: false;
};

// ── Assistant panel types (V1-18F) ─────────────────────────────────

export type AssistantStreamEvent = {
  event_type: "message" | "tool_call" | "tool_result" | "error" | "done";
  data_json: string;
  sequence: number;
};

export type AssistantMessageData = {
  message_id: string;
  role: "user" | "assistant" | "tool_result";
  content: string;
  tool_call_id?: string;
};

export type AssistantToolResultData = {
  tool_call_id: string;
  tool_name: string;
  result: string;
  truncated: boolean;
  duration_ms: number;
};

export type AssistantToolCallData = {
  tool_call_id: string;
  tool_name: string;
  parameters: Record<string, unknown>;
};

export type AssistantErrorData = {
  tool_call_id: string;
  error: string;
};

export type AssistantDoneData = {
  status: string;
};

// ── Model activity panel types (V1-18D) ─────────────────────────────────

export type ModelInvocationEntry = {
  invocation_id: string;
  job_id: string | null;
  profile_id: string | null;
  model_name: string | null;
  prompt_tokens: number | null;
  completion_tokens: number | null;
  total_tokens: number | null;
  redacted_summary: string | null;
  actor_type: string | null;
  actor_id: string | null;
  created_at: string;
  correlation_id: string | null;
  causation_id: string | null;
};

export type ModelActivityResponse = {
  job_id: string;
  invocations: ModelInvocationEntry[];
};

// Raw backend response shape from V1-10 GET /v1/jobs/{job_id}/model-invocations
// The live backend returns { model_invocations: [...] } without top-level job_id.
// This type models both keys for forward/backward compatibility.
export type ModelActivityRawResponse = {
  model_invocations?: ModelInvocationEntry[];
  invocations?: ModelInvocationEntry[];
};

// ── Stage timeline panel types (V1-18B) ────────────────────────────────

export type StageChainEntry = {
  ledger_id: string;
  job_id: string;
  stage_index: number;
  stage_run_id: string;
  chain_status: string;
  input_source_kind: string;
  input_checksum: string | null;
  output_artifact_id: string | null;
  output_checksum: string | null;
  output_registered_at: string | null;
  created_at: string;
};

export type StageChainResponse = {
  job_id: string;
  stages: StageChainEntry[];
};

// ── Approvals panel types (V1-18C) ────────────────────────────────

export type ApprovalEntry = {
  approval_id: string;
  interrupt_id: string;
  decision: "approved" | "rejected" | "replan_required";
  approved_by: string;
  approval_comments: string;
  created_at: string;
};

export type ApprovalListResponse = {
  job_id: string;
  approvals: ApprovalEntry[];
};

export type PrivilegedActionEntry = {
  action_id: string;
  job_id: string;
  action_type: string;
  parameters: Record<string, unknown>;
  parameters_checksum: string;
  requested_by: string;
  status: string;
  requested_at: string;
  decided_at: string | null;
  decision: string | null;
  decided_by: string | null;
};

export type PrivilegedActionListResponse = {
  job_id: string;
  actions: PrivilegedActionEntry[];
};

// ── Repair panel types (V1-18E) ───────────────────────────────────

export type RepairClassificationEntry = {
  classification_id: string;
  command_id: string;
  job_id: string;
  command_status: string;
  evidence_kind: string;
  evidence_summary: string;
  evidence_checksum: string;
  classification_code: string;
  reason_code: string;
  repairable: boolean;
  attempt_limit: number;
  actor_type: string;
  actor_id: string;
  created_at: string;
};

export type FakeRepairProposalEntry = {
  proposal_id: string;
  classification_id: string;
  command_id: string;
  job_id: string;
  proposal_order: number;
  proposal_kind: string;
  proposal_summary: string;
  proposal_checksum: string;
  recommendation_type: string;
  confidence_label: string;
  confidence_score: number;
  warning_codes: string[];
  applicable: boolean;
  context_checksum: string;
  actor_type: string;
  actor_id: string;
  created_at: string;
};

export type RepairStatusEntry = {
  command_id: string;
  job_id: string;
  classification: RepairClassificationEntry | null;
  proposals: FakeRepairProposalEntry[];
  attempts: unknown[];
  repairable: boolean;
  created_at: string;
};

export type RepairProposalListResponse = {
  command_id: string;
  proposals: FakeRepairProposalEntry[];
};

// ── Proof and final report panel types (V1-18G) ───────────────────

export type ProofGateEntry = {
  stage_index: number;
  output_checksum: string;
  proof_gate_checksum: string;
  chain_status: string;
};

export type ProofGatesResponse = {
  job_id: string;
  gates: Record<string, string>;
  gate_count: number;
  required_gates: number;
  algorithm: string;
};

export type ProofReportEntry = {
  report_id: string;
  job_id: string;
  report_version: number;
  report_checksum: string;
  gate_count: number;
  all_gates_present: boolean;
  proof_complete: boolean;
  target_proof_level: string;
  pipeline_id: string;
  summary: Record<string, unknown>;
  gates: ProofGateEntry[];
  generated_at: string;
  generated_by: string;
};

// ── V2 migration cockpit types ──────────────────────────────────────

export type V2MigrationJobResponse = {
  job_id: string;
  setup_id: string;
  setup_checksum: string;
  pipeline_id: string;
  stages: V2StageEntry[];
  created_at: string;
  route_steps?: V2RouteStepEntry[];
  // F3/F4 — profile routing fields (backend-returned)
  source_profile?: MigrationProfileId;
  target_profile?: MigrationProfileId;
  validation_status?: "valid" | "invalid";
  validation_reason?: string;
  included_stages?: string[];
  excluded_stages?: string[];
  skipped_stages?: string[];
  stage_continuation_policy?: string;
  run_configuration_id?: string;
  auto_approval_enabled?: boolean;
};

export type V2RouteStepEntry = {
  route_step_index: number;
  stage_index: number;
  execution_stage_index?: number;
  source_profile: MigrationProfileId;
  target_profile: MigrationProfileId;
  runtime_profile: string;
  catalog: string;
  execution_jdk: string;
  status: "pending" | "queued" | "running" | "blocked" | "completed" | "failed" | string;
  approval_gate_id: string;
  artifact_refs: string[];
  evidence_refs: string[];
};

export type V2JobEvent = {
  event_id: string;
  job_id: string;
  stage: number | null;
  type: string;
  status: string;
  message: string;
  payload: Record<string, unknown>;
  created_at: string;
  sequence: number;
};

export type V2JobEventSnapshotResponse = {
  job_id: string;
  after: number;
  events: V2JobEvent[];
  latest_sequence: number;
};

export type V2PipelineRow = {
  key: string;
  label: string;
  status: "pending" | "running" | "pass" | "blocked" | "failed" | string;
  latest_message: string;
  artifact_count: number;
  last_updated: string;
};

export type V2PipelineResponse = {
  job_id: string;
  rows: V2PipelineRow[];
  evidence: V2JobEvent[];
  raw_logs: V2JobEvent[];
  active_stage_index: number;
};

export type V2StageEntry = {
  stage_index: number;
  stage_run_id: string;
  pipeline_stage: string;
  input_source_kind: string;
  chain_status: string;
};

export type V2StageCommandResponse = {
  command_id: string;
  job_id: string;
  stage_index: number;
  manifest_checksum: string;
  created_at: string;
};

export type V2ApprovalResponse = {
  card_id: string;
  job_id?: string;
  interrupt_id: string;
  request_checksum: string;
  stage_index: number;
  summary: string;
  status: string;
  created_at: string;
  reviewer_critique_id?: string;
  reviewer_decision?: string;
  reviewed_checksum?: string;
};

export type V2ResumeCommandResponse = {
  resume_id: string;
  card_id: string;
  decision: string;
  job_id: string;
  stage_index: number;
  command: string[];
};

export type V2AssistantMessageResponse = {
  message_id: string;
  job_id: string;
  role: string;
  content: string;
  correlation_id: string | null;
  created_at: string;
};

export type V2AssistantMessagesListResponse = {
  job_id: string;
  messages: V2AssistantMessageResponse[];
};

export type V2AssistantAskResponse = {
  job_id: string;
  user_message: V2AssistantMessageResponse;
  assistant_message: V2AssistantMessageResponse;
  model: {
    status: string;
    source: string;
    provider: string;
    role: string;
    failure_reason?: string;
  };
  guardrails: Record<string, boolean>;
};

export type V2ReviewerCritiqueResponse = {
  critique_id: string;
  proposal_id: string;
  proposal_type: string;
  proposal_checksum: string;
  context_pack_checksum: string;
  decision: string;
  reasoning: string;
  missing_evidence: string[];
  unsafe_assumptions: string[];
  model_invocation_id: string | null;
  created_at: string;
};

export type V2ReviewerCritiquesListResponse = {
  command_id: string;
  proposal_id: string;
  critiques: V2ReviewerCritiqueResponse[];
};

export type V2DraftActionResponse = {
  action_id: string;
  job_id: string;
  action_type: string;
  reason: string;
  stage_index: number;
  payload_checksum: string;
  status: string;
  created_at: string;
  // F05 optional revision steering fields
  source_proposal_id?: string | null;
  failed_command_id?: string | null;
  revision_instruction?: string | null;
  context_pack_checksum?: string | null;
  revision_of?: string | null;
  revision_number?: number | null;
  allowed_scope?: string | null;
  // F05 revision binding result when revise_repair_proposal is resolved
  revision_binding?: Record<string, unknown>;
};

export type V2ApprovalModeResponse = {
  job_id: string;
  auto_approval_enabled: boolean;
  autoApprovalEnabled?: boolean;
  auto_approved?: {
    resume_id: string;
    stage_index: number;
    card_id: string;
    gate_id: string;
    checksum: string;
    decision_id: string;
  } | null;
  job: V2MigrationJobResponse | null;
};

export type V2StageContinuationResponse = {
  continuation_id: string;
  job_id: string;
  from_stage: number;
  to_stage: number;
  status: string;
  reason: string;
  command_id?: string | null;
};

export type V2SettingsResponse = {
  azure: {
    profile_id: string;
    status: string;
    connection_configured: boolean;
    roles: Record<string, {
      configured: boolean;
      enabled: boolean;
    }>;
  };
  local_mode: {
    enabled: boolean;
    allowed_source_roots: string[];
    allowed_output_roots: string[];
  };
};

export type V2SetupResponse = {
  setup_id: string;
  run_name: string;
  legacy_app_path: string;
  output_parent_path: string;
  ai_hub_path: string;
  java_homes: Record<string, string>;
  maven_cmd: string;
  proof_level: string;
  skip_endpoint_smoke: boolean;
  migration_flags: Record<string, unknown>;
  setup_checksum: string;
  created_at: string;
};

export type V2PreflightResponse = {
  preflight_id: string;
  setup_id: string;
  all_ready: boolean;
  azure_model_ready?: boolean;
  azure_model_failure_reason?: string;
  azure_model_response_snippet?: string;
  azure_model_checked_at?: string;
  readiness: Record<string, boolean>;
  warnings: string[];
  errors: string[];
  checked_at: string;
};

export type V2AzureSmokeResponse = {
  success: boolean;
  failure_reason: string;
  redacted_summary: string;
  response_snippet: string;
  latency_ms: number;
  checked_at: string;
};

export type V2ReadinessResponse = {
  ready: boolean;
  setup_checksum: string;
  preflight_checksum_match: boolean;
  gates: Record<string, boolean>;
};

export type V2FailureSummaryItem = {
  type: string;
  stage: number | null;
  title: string;
  message: string;
  build_status: string;
  test_status: string;
  final_status: string;
  final_proof_level: string;
  repair_loop_status: string;
  repair_fallback: string;
  // ── SA4 diagnostic fields ──
  matched_line: string;
  command: string[];
  requested_command: string[];
  build_tool: string;
  module: string;
  main_class: string;
  unit_id: string;
  result_kind: string;
  java_home: string;
  detected_version: string;
  required_minimum: string;
  // ── Result contract diagnostic fields ──
  exit_code?: number | null;
  final_json_found?: boolean | null;
  parse_strategy?: string;
  stdout_tail?: string;
  stderr_tail?: string;
  event_types: string[];
  repair_events: { type: string; message: string }[];
  next_operator_action: string;
  supervision_trace: V2SupervisionTrace;
};

export type V2SupervisionTrace = {
  ai_diagnosis: V2AIDiagnosisResponse | null;
  evidence_used: string[];
  pom_analysis: V2PomAnalysisResponse | null;
  repair_proposal: V2RepairProposalTraceResponse | null;
  reviewer_verdict: V2ReviewerVerdictTraceResponse | null;
  validation_result: V2RepairValidationResponse | null;
};

export type V2AIDiagnosisResponse = {
  diagnosis_id: string;
  command_id: string;
  trigger_event_type: string;
  failure_type: string;
  context_pack_id: string;
  context_pack_checksum: string;
  repair_proposal_id: string;
  model_invocation_id: string;
  redaction_status: string;
  created_at: string;
};

export type V2PomAnalysisResponse = {
  pom_summary_ref: string;
  spring_boot_version: string;
  java_version: string;
  packaging: string;
  candidate_rules: string[];
  created_at: string;
};

export type V2RepairProposalTraceResponse = {
  proposal_id: string;
  source_proposal_id: string;
  command_id: string;
  revision_number: number | null;
  allowed_scope: string;
  proposal_checksum: string;
  status: string;
  created_at: string;
};

export type V2ReviewerVerdictTraceResponse = {
  critique_id: string;
  proposal_id: string;
  proposal_type: string;
  proposal_checksum: string;
  context_pack_checksum: string;
  decision: string;
  reasoning: string;
  missing_evidence: string[];
  unsafe_assumptions: string[];
  created_at: string;
};

export type V2RepairValidationResponse = {
  proposal_id: string;
  binding_checksum?: string;
  patch_gate_status?: string;
  deterministic_rule_id?: string;
  touched_paths?: string[];
  patch_ref?: string;
  patch_status?: string;
  passed?: boolean;
  build_status?: string;
  test_status?: string;
  h2_status?: string;
  artifact_refs?: Record<string, string>;
  rollback_status?: string;
  rollback_reason?: string;
  ledger_ref?: string;
  updated_at?: string;
};

export type V2FailureSummaryResponse = {
  job_id: string;
  has_failures: boolean;
  failures: V2FailureSummaryItem[];
  repair_loop_active: boolean;
  repair_events: { type: string; message: string }[];
  artifact_kinds: string[];
};

export type V2ArtifactPreviewResponse = {
  job_id: string;
  artifact_kind: string;
  source_type?: "artifact" | "file_alias";
  file_alias?: string;
  stage_index?: number;
  exists: boolean;
  preview: string;
  content?: string;
  truncated: boolean;
  content_type: string;
  download_url?: string | null;
  source_ref?: Record<string, string> | null;
  pom_checksum?: string | null;
  reason?: string | null;
};

// ── F14 — Stage 3 POM Dependency Review types ──────────────────────────

export type PomBaseline = {
  java_version: string;
  spring_boot_version: string;
  spring_boot_version_location: string;
  detected_from: string[];
};

export type PomDependencyFinding = {
  dependency_name: string;
  current_version: string;
  source_location: string;
  bucket: string;
  control_mode: string;
  risk: string;
  recommended_action: string;
  can_apply_now: boolean;
  reason: string;
  evidence_source: string;
};

export type PomDependencyReview = {
  job_id: string;
  stage: number;
  baseline: PomBaseline;
  buckets: Record<string, PomDependencyFinding[]>;
  findings: PomDependencyFinding[];
  evidence_loaded: string[];
  evidence_missing: string[];
  warnings: string[];
  created_at: string;
};

export type PomChangeProposal = {
  proposal_id: string;
  server_validated_plan_preview: Record<string, unknown>;
  risk: string;
  can_apply: boolean;
  warnings: string[];
  applied: boolean;
  control_mode: string;
  created_at: string;
};

export type PomApplyResult = {
  change_id: string;
  status: string;
  operation: string;
  target_desc: string;
  before_version: string;
  after_version: string;
  before_checksum: string;
  after_checksum: string;
  diff_summary: string;
  validation_id: string | null;
  rollback_available: boolean;
  idempotency_key: string | null;
  created_at: string;
  message: string;
};

export type PomValidationFailureDiagnosis = {
  failure_classification: string;
  failed_phase: string;
  exit_code: number;
  log_excerpt: string;
  log_ref: string;
  root_cause: string;
  evidence_sufficient: boolean;
  missing_evidence: string[];
};

export type PomRepairPlan = {
  repair_plan_id: string;
  change_id: string;
  summary: string;
  detailed_steps: string[];
  confidence: string;
  evidence_sources: string[];
  actions_available: string[];
  created_at: string;
};

export type PomValidationRun = {
  validation_id: string;
  change_id: string;
  status: string;
  command: string;
  build_status: string;
  test_status: string;
  exit_code: number | null;
  duration_ms: number | null;
  log_ref: string | null;
  test_log_ref: string | null;
  diagnosis: PomValidationFailureDiagnosis | null;
  repair_plan: PomRepairPlan | null;
  created_at: string;
  completed_at: string | null;
};

export type PomRollbackResult = {
  change_id: string;
  rollback_id: string;
  status: string;
  checksum_restored: boolean;
  validation_triggered: boolean;
  validation_id: string | null;
  created_at: string;
};

export type PomView = {
  job_id: string;
  stage: number;
  exists: boolean;
  content: string;
  truncated: boolean;
  content_type: string;
  redaction_applied: boolean;
  detected_baseline: PomBaseline | null;
  reason?: string | null;
  pom_checksum?: string | null;
};

export type PomChangeRecordSummary = {
  change_id: string;
  operation: string;
  target_desc: string;
  before_version: string;
  after_version: string;
  before_checksum: string;
  after_checksum: string;
  diff_summary: string;
  status: string;
  validation_id: string | null;
  rollback_id: string | null;
  created_at: string;
};

export type PomProposeRequest = {
  user_request: string;
  idempotency_key?: string;
};

export type PomApplyRequest = {
  proposal_id?: string;
  user_request?: string;
  idempotency_key?: string;
};

export type Stage4TargetVersionChangeRequest = {
  group_id: string;
  artifact_id: string;
  target_version: string;
};

export type Stage4TargetVersionApplyItem = {
  coordinate: string;
  group_id: string;
  artifact_id: string;
  target_version: string;
  before_version: string | null;
  after_version: string;
  version_source: string;
  status: "applied" | "skipped" | "blocked" | "noop";
  reason: string;
};

export type Stage4TargetVersionApplyResponse = {
  job_id: string;
  stage: number;
  idempotency_key: string | null;
  message: string;
  before_checksum: string;
  after_checksum: string;
  applied_count: number;
  skipped_count: number;
  blocked_count: number;
  items: Stage4TargetVersionApplyItem[];
  blockers: string[];
  change_id?: string | null;
  validation_id?: string | null;
  status?: string;
  validation?: Record<string, unknown> | null;
};

export type TargetVersionUpdateStatusResponse = {
  job_id: string;
  update: Record<string, unknown> | null;
  validation: Record<string, unknown> | null;
};

// ── F15 Gate types (jobs 101-117) ──────────────────────────────────────

export type GatePhase =
  | "analysis_review"
  | "planning_review"
  | "approval_review"
  | "repair_review"
  | "stage_completion_review";

export type GateDecision =
  | "continue"
  | "reanalyze"
  | "revise"
  | "approve"
  | "reject"
  | "override_source_profile";

export type GateActorType =
  | "human"
  | "assistant"
  | "api"
  | "system";

export type GateStatus =
  | "open"
  | "resolved"
  | "superseded";

export type GateRepresentation = {
  gate_id: string;
  job_id: string;
  gate_phase: GatePhase;
  stage_index: number;
  gate_status: GateStatus;
  gate_decision: GateDecision;
  source_artifact_checksum: string;
  source_artifact_refs: string[];
  created_at: string;
  resolved_at: string | null;
  resolved_by: string | null;
  checksum: string;
  available_actions: AvailableAction[];
};

export type AvailableAction = {
  action: string;
  label: string;
  description: string;
  blocked: boolean;
  block_reason: string;
};

export type GateActionRequest = {
  gate_id: string;
  job_id: string;
  action: GateDecision;
  expected_gate_checksum: string;
  idempotency_key: string;
  decided_by: string;
  actor_type: GateActorType;
  reason?: string;
  // Repair-specific fields (only for repair_review gates):
  proposal_id?: string;
  proposal_checksum?: string;
  context_pack_checksum?: string;
  user_feedback?: string;
  // F3/F4 — source-profile override fields (only for analysis_review gates):
  override_source_profile?: MigrationProfileId;
  detection_artifact_ref?: string;
  detected_source_profile?: MigrationProfileId;
  requested_source_profile?: MigrationProfileId;
  target_profile?: MigrationProfileId;
  expected_detection_artifact_checksum?: string;
  comments?: string;
};

export type GateActionResult = {
  decision_id: string;
  gate_id: string;
  job_id: string;
  action: GateDecision;
  status: string;
  result_gate_id: string | null;
  result_command_id: string | null;
  result_revision_id: string | null;
  reason: string;
};

export type RepairGateEvidence = {
  failure_summary: string;
  root_cause_hypothesis: string;
  patch_summary: string;
  affected_paths: string[];
  reviewer_critique: {
    critique_id: string;
    decision: string;
    reasoning: string;
  } | null;
  remaining_attempts: number;
  max_attempts: number;
};

export type GateDetailResponse = {
  gate: GateRepresentation;
  evidence: RepairGateEvidence | GateEvidencePack | null;
  checksum: string;
};

export type GateListResponse = {
  gates: GateRepresentation[];
};

export type GateActionResponse = {
  result: GateActionResult;
};

export type OpenGateForJobResponse = {
  gate: GateRepresentation | null;
};

// ── F15 Final Report types (Stage 4 / V2 Final Report) ──────────────────

export type V2ReportArtifactSummary = {
  artifact_id: string;
  kind: "final_report_json" | "final_report_markdown" | "final_report_pdf";
  checksum_sha256: string;
  size_bytes: number;
  content_type: "application/json" | "text/markdown" | "application/pdf";
  download_url: string;
};

export type V2FinalReportResponse = {
  job_id: string;
  status: "not_generated" | "generating" | "generated" | "blocked" | "failed";
  eligible: boolean;
  blockers: string[];
  generated_at: string | null;
  input_checksum: string | null;
  redacted_summary: string;
  artifacts: V2ReportArtifactSummary[];
};

// ── F3 / F4 — Profile routing types ────────────────────────────────────

export type MigrationProfileId =
  | "springboot-2.1-java11"
  | "springboot-2.7-java11"
  | "springboot-3.5-java17"
  | "springboot-3.5-java21"
  | "springboot-4.0-java21";

export type MigrationProfileOption = {
  id: MigrationProfileId;
  label: string;
  orderIndex: number;
  selectableAsSource: boolean;
  selectableAsTarget: boolean;
};

export const MIGRATION_PROFILE_OPTIONS: MigrationProfileOption[] = [
  {
    id: "springboot-2.1-java11",
    label: "Spring Boot 2.1 / Java 11",
    orderIndex: 0,
    selectableAsSource: true,
    selectableAsTarget: false,
  },
  {
    id: "springboot-2.7-java11",
    label: "Spring Boot 2.7 / Java 11",
    orderIndex: 1,
    selectableAsSource: true,
    selectableAsTarget: true,
  },
  {
    id: "springboot-3.5-java17",
    label: "Spring Boot 3.5 / Java 17",
    orderIndex: 2,
    selectableAsSource: true,
    selectableAsTarget: true,
  },
  {
    id: "springboot-3.5-java21",
    label: "Spring Boot 3.5 / Java 21",
    orderIndex: 3,
    selectableAsSource: true,
    selectableAsTarget: true,
  },
  {
    id: "springboot-4.0-java21",
    label: "Spring Boot 4.0 / Java 21",
    orderIndex: 4,
    selectableAsSource: false,
    selectableAsTarget: true,
  },
];

// ── PR-C — Reviewed Diff Proposal types (PR-B response contracts) ──────

export type SafeDiffLine = {
  kind: "context" | "addition" | "deletion";
  old_line_number: number | null;
  new_line_number: number | null;
  text: string;
  redacted: boolean;
};

export type SafeDiffHunk = {
  old_start: number;
  old_lines: number;
  new_start: number;
  new_lines: number;
  section_header: string | null;
  lines: SafeDiffLine[];
};

export type SafeDiffFile = {
  path: string;
  change_type: string;
  additions: number;
  deletions: number;
  hunks: SafeDiffHunk[];
  truncated: boolean;
};

export type SafeDiffPreview = {
  proposal_id: string;
  diff_ref: string | null;
  diff_checksum: string;
  files: SafeDiffFile[];
  total_additions: number;
  total_deletions: number;
  truncated: boolean;
  checksum_mismatch: boolean;
  parse_status?: string;
  redactions: string[];
};

export type ReviewerVerdictProjection = {
  reviewer_verdict_id: string | null;
  decision: string;
  reasoning: string | null;
  missing_evidence: string[];
  unsafe_assumptions: string[];
  model_invocation_id: string | null;
  output_checksum: string | null;
};

export type FilesChangedSummary = {
  path: string;
  change_type: string;
  additions: number;
  deletions: number;
};

export type ReviewedDiffProposal = {
  proposal_id: string;
  job_id: string | null;
  command_id: string | null;
  gate_id: string | null;
  route_step_index: number | null;
  stage_index: number | null;
  status: string;
  attempt_number: number | null;
  revision_number: number | null;
  failure_summary: string;
  diagnosis_ref: string | null;
  repair_plan_ref: string | null;
  diff_ref: string | null;
  diff_checksum: string;
  safe_diff_preview: SafeDiffPreview | null;
  reviewer_verdict: ReviewerVerdictProjection | null;
  files_changed: FilesChangedSummary[];
  risk: string | null;
  required_validation: string[];
  allowed_actions: string[];
  redactions: string[];
  apply_status?: string | null;
  rerun_status?: string | null;
  validation_proof_status?: string | null;
  final_diff_source?: "reviewer" | "proposer_fallback" | "proposer_fallback_reviewer_unavailable" | null;
  generation_status?: string | null;
  generation_reason?: string | null;
};

export type RepairState = {
  attempted: boolean;
  status: "not_attempted" | "running" | "unavailable" | "ready" | "attempts_exhausted" | "blocked" | "error";
  reason_code?: string | null;
  detail?: string | null;
  event_type?: string | null;
  created_at?: string | null;
  allowed_actions: string[];
};

export type RepairProposalCurrentResponse = {
  proposal: ReviewedDiffProposal | null;
  job_id: string;
  repair_state?: RepairState | null;
};

export type RepairProposalDetailResponse = {
  proposal: ReviewedDiffProposal | null;
  job_id: string;
  repair_state?: RepairState | null;
};

export type RepairProposalDiffResponse = {
  safe_diff_preview: SafeDiffPreview | null;
  job_id: string;
  reason?: string;
};

export type RepairAttemptSummary = {
  proposal_id: string;
  command_id: string | null;
  job_id: string | null;
  gate_id: string | null;
  attempt_number: number | null;
  revision_number: number | null;
  status: string;
  apply_status: string | null;
  rerun_status: string | null;
  rollback_status: string | null;
  reviewer_decision: string | null;
  diff_checksum: string | null;
  policy_validation_checksum: string | null;
  validation_result_ref: string | null;
  next_gate_id: string | null;
  next_gate_status: string | null;
  remaining_attempts: number | null;
  status_reason: string | null;
  created_at: string;
  completed_at: string | null;
  validation_proof_status?: string | null;
  final_diff_source?: "reviewer" | "proposer_fallback" | "proposer_fallback_reviewer_unavailable" | null;
};

export type RepairAttemptsResponse = {
  attempts: RepairAttemptSummary[];
  job_id: string;
};

export function formatFinalDiffSource(
  source: ReviewedDiffProposal["final_diff_source"] | string | null | undefined,
): string {
  switch (source) {
    case "reviewer": return "Reviewer";
    case "proposer_fallback":
    case "proposer_fallback_reviewer_unavailable": return "Proposer fallback";
    case null:
    case undefined: return "Source pending";
    default: return "Unknown";
  }
}

// ── PR-D — User Revision Request types ───────────────────────────────────

export type RepairProposalRevisionRequest = {
  user_instruction: string;
  previous_diff_checksum: string;
  previous_reviewer_verdict_id: string;
  expected_gate_checksum?: string;
  idempotency_key?: string;
};

export type RepairProposalRevisionResponse = {
  job_id: string;
  previous_proposal_id: string;
  proposal: ReviewedDiffProposal | null;
  status: "revision_requested" | "user_review_required" | "failed";
  event_ids: string[];
  artifact_refs: Record<string, string>;
};

// ── PR-E — Approve Reviewed Repair Sandbox Apply types ───────────────────

export type RepairProposalApproveRequest = {
  proposal_id: string;
  diff_checksum: string;
  idempotency_key: string;
};

export type RepairProposalRejectRequest = {
  proposal_id: string;
  reason?: string;
  idempotency_key: string;
};

export type RepairProposalApproveResponse = {
  job_id: string;
  proposal_id: string;
  status: string;
  apply_status: string;
  rerun_status: string;
  next_gate_id: string;
  next_gate_status: string;
  rollback_status: string;
  remaining_attempts: number;
  allowed_next_actions: string[];
  apply_succeeded?: boolean;
  validation_succeeded?: boolean;
  continuation_status?: string;
  continuation_failure_code?: string | null;
  continuation_retryable?: boolean;
};

export type RepairProposalContinueResponse = {
  job_id: string;
  proposal_id: string;
  status: string;
  reason: string;
  continuation_id: string;
  command_id: string | null;
  from_stage: number;
  to_stage: number;
};

export const PROFILE_BY_ID: Record<MigrationProfileId, MigrationProfileOption> =
  MIGRATION_PROFILE_OPTIONS.reduce(
    (acc, profile) => {
      acc[profile.id] = profile;
      return acc;
    },
    {} as Record<MigrationProfileId, MigrationProfileOption>,
  );

// Migration path stage numbers per profile pair (route-step aligned).
// Stage 0 is the public 2.1 source state; Stage 1..4 are executable route steps.
const PROFILE_TO_STAGE_INDEX: Record<MigrationProfileId, number> = {
  "springboot-2.1-java11": 0,
  "springboot-2.7-java11": 1,
  "springboot-3.5-java17": 2,
  "springboot-3.5-java21": 3,
  "springboot-4.0-java21": 4,
};

export const TRANSITION_STAGE_INDEXES: number[] = [1, 2, 3, 4];

export type RoutePreview = {
  included: string[];
  skipped: string[];
  excluded: string[];
};

export function getRouteValidationError(
  source: MigrationProfileId | null | undefined,
  target: MigrationProfileId | null | undefined,
): string | null {
  if (!source || !target) {
    return "Source and target profiles are required.";
  }
  const sourceProfile = PROFILE_BY_ID[source];
  const targetProfile = PROFILE_BY_ID[target];
  if (!sourceProfile || !targetProfile) {
    return "Unknown migration profile.";
  }
  if (source === target) {
    return "Source and target profiles must differ.";
  }
  if (targetProfile.orderIndex <= sourceProfile.orderIndex) {
    return "Target profile must be a higher stage than the source profile.";
  }
  if (!sourceProfile.selectableAsSource) {
    return "Source profile is not selectable as a source.";
  }
  if (!targetProfile.selectableAsTarget) {
    return "Target profile is not selectable as a target.";
  }
  return null;
}

export function getRoutePreview(
  source: MigrationProfileId,
  target: MigrationProfileId,
): RoutePreview | null {
  const error = getRouteValidationError(source, target);
  if (error !== null) return null;
  const sourceStage = PROFILE_TO_STAGE_INDEX[source];
  const targetStage = PROFILE_TO_STAGE_INDEX[target];
  const included: number[] = [];
  const skipped: number[] = [];
  const excluded: number[] = [];
  for (const stage of TRANSITION_STAGE_INDEXES) {
    if (stage <= sourceStage) {
      skipped.push(stage);
    } else if (stage <= targetStage) {
      included.push(stage);
    } else {
      excluded.push(stage);
    }
  }
  return {
    included: included.map((stage) => String(stage)),
    skipped: skipped.map((stage) => String(stage)),
    excluded: excluded.map((stage) => String(stage)),
  };
}

export function getRoutePreviewKey(
  source: MigrationProfileId,
  target: MigrationProfileId,
): string {
  return `${source}->${target}`;
}

// ── F4 — Source-profile detection evidence types ───────────────────────

export type GateEvidenceArtifact = {
  kind: string;
  checksum_verified: boolean;
  content: string;
  size_bytes: number;
  truncated: boolean;
};

export type GateEvidencePack = {
  pack_id: string;
  pack_type: string;
  gate_id: string;
  gate_phase: string;
  summary: string;
  artifacts: GateEvidenceArtifact[];
  missing_refs: string[];
  checksum_mismatches: string[];
  failure_message: string | null;
  resolved_artifact_count: number;
  total_artifact_count: number;
  redaction_status: string;
  created_at: string;
};

// ── RA — Repair Assistant Chat types ─────────────────────────────────────

export type RepairAssistantAction = "ANSWER_ONLY" | "REQUEST_REVISION" | "CLARIFICATION_REQUIRED" | "error" | "blocked" | "revision_failed";

export type RepairAssistantMessageStatus = "answered" | "clarification_required" | "revision_generating" | "revision_created" | "revision_failed" | "blocked" | "error";

export interface RepairAssistantMessage {
  message_id: string;
  job_id: string;
  proposal_id: string;
  role: "user" | "assistant";
  message: string;
  action?: RepairAssistantAction | null;
  status?: RepairAssistantMessageStatus | null;
  created_at: string;
  failure_stage?: string | null;
  failure_code?: string | null;
  safe_failure_message?: string | null;
  correlation_id?: string | null;
}

export interface RepairAssistantMessagesListResponse {
  messages: RepairAssistantMessage[];
}

export interface RepairAssistantIntent {
  user_instruction: string;
  resolved_instruction: string;
  constraints: string[];
  target_files: string[];
}

export interface RepairAssistantSendRequest {
  message: string;
  idempotency_key: string;
  base_diff_checksum: string;
}

export interface RepairAssistantSendResponse {
  message_id: string;
  assistant_message: string;
  action: RepairAssistantAction;
  revision_intent?: RepairAssistantIntent | null;
  revision_started: boolean;
  new_proposal_id?: string | null;
  new_attempt_number?: number | null;
  new_diff_checksum?: string | null;
  status: RepairAssistantMessageStatus;
  failure_stage?: string | null;
  failure_code?: string | null;
  safe_failure_message?: string | null;
  correlation_id?: string | null;
}
