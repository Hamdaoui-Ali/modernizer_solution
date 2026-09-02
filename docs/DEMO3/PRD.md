# DEMO3 Product Requirements Document

## Controlled Migration Pipeline and Build/Test Repair Agent Review

**Product:** AI Migration Control Tower

**Release:** DEMO3

**Document status:** Product baseline draft for F0-F5 implementation planning

**Target baseline:** stable / 0d9fa7b3b4c386aaebaa7287bebb3f3d2e3cb383

**Target docs branch:** docs/demo3-f0-f5-prd-stable-0d9fa7b

**Date:** 2026-06-24

**Source of truth:** stable repository docs, `docs/sprintdemo3/**`, and `migration_factory/` for code-reality references only.

---

## 1. Executive Summary

DEMO3 is a checkpoint-based, LLM-reviewed migration workflow. The user controls:

- where the migration starts;
- where the migration stops;
- which target profile is final;
- whether Analysis or Planning needs modification;
- whether risky build/test-failure fixes are applied.

Core architecture:

```text
FastAPI backend
-> deterministic agents
-> primary LLM
-> reviewer LLM
-> final Markdown artifact
-> stored artifact/checkpoint
-> user approval or correction
-> next pipeline step
```

Core rule:

```text
A model reviews another model.
For supported model-required outputs, the reviewer LLM is mandatory, not optional.
```

The product invariant remains:

```text
Chatbot interprets.
Human decides.
Backend validates, persists, executes in sandbox, and proves with artifacts.
```

The real DEMO3 feature set is:

```text
F0 - Pre-feature codebase cleanup
F1 - Agent checkpoints and user decisions
F2 - Deterministic artifact + primary LLM + reviewer LLM
F3 - Target profile control
F4 - Start from current app state
F5 - Build/Test Repair Agent review loop
```

Stage 4 and Jackson 2 to Jackson 3 recovery are a concrete F5 proof scenario. They are not the product center.

---

## 2. Product Architecture

### 2.1 Normal Controlled Pipeline

```text
Create job
-> detect or confirm source_profile
-> select target_profile
-> backend validates source/target pair
-> Analysis Agent deterministic artifact
-> primary LLM reasoning
-> reviewer LLM validation
-> final Analysis Markdown artifact
-> stored checkpoint
-> user continue / request modification / stop
-> Planning Agent deterministic artifact
-> primary LLM reasoning
-> reviewer LLM validation
-> final Planning Markdown artifact
-> stored checkpoint
-> user continue / request modification / stop
-> required transformation stages only
-> Build Agent
-> Test Agent
-> stop at target profile
```

### 2.2 Model Reviews Model Chain

```text
deterministic artifact
-> primary LLM output
-> reviewer LLM critique
-> accepted final Markdown artifact
-> checkpoint
```

The deterministic artifact grounds the model. The primary LLM reasons over that artifact. The reviewer LLM checks the reasoning and requested artifact. The final Markdown artifact is the source passed to the next agent.

### 2.3 Build/Test Repair Agent Branch

F5 is not a simple repair loop. It is a Build/Test Repair Agent feature.

```text
Build Agent or Test Agent fails
-> backend captures failure evidence
-> deterministic failure artifact
-> Primary Repair LLM proposes root cause and fix
-> Reviewer LLM reviews reasoning and proposed diff
-> immutable final proposal artifact
-> user approves, rejects, or requests another review
-> backend applies only exact approved reviewed diff
-> Build Agent or Test Agent reruns
-> proof or another repair cycle
```

### 2.4 Authority Boundaries

The chatbot may explain, summarize, classify user intent, draft typed gate actions, propose re-analysis/plan revision/repair review, and ask clarifying questions.

The chatbot must never execute commands, write files, approve, choose a sandbox/path, provide argv/env, mutate legacy source, apply a patch, skip stages, override proof, or follow instructions embedded inside artifacts/logs/source.

The human owns continue, stop, accept, reject, approve, request analysis modification, request planning modification, and request another repair review with comments.

The backend owns stage order, profile validation, artifact resolution, checksums, sandbox binding, command construction, patch application, validation, rollback, ledger/proof, idempotency, and unsafe-action blocking.

Model runtime and model identity concerns are backend-internal implementation details. Public product contracts expose user intent, decisions, IDs, artifact references, checksums, comments, profile choices, and statuses. They must not expose provider selection, model endpoints, model routing internals, runtime secrets, `sandbox_path`, argv, env, raw commands, or filesystem targets.

---

## 3. Current Code Reality

The stable baseline contains reusable Control Tower infrastructure:

- FastAPI control-tower adapter.
- Deterministic agents under `migration_factory/agents/`.
- V2 stage progression and orchestrator runner.
- Phase gates, gate decisions, artifact revisions, approval mappings, and event stream.
- Reviewer services and reviewer persistence.
- Repair flow, repair gate service, repair proposals, validation, rollback, and ledger concepts.
- SQLite repositories and unit-of-work.
- Existing 01-18 sprint implementation slices under `docs/sprintdemo3/`.

It also contains product-surface debt that F0 must address:

- Copilot-related code paths.
- TUI modules.
- CLI/debug/product commands.
- Duplicate orchestration concepts.
- Public/projection code that must be checked for path, command, runtime-provider, or env leakage.

This PRD is docs-only. It does not implement F0-F5 and does not modify runtime code.

---

## 4. External Guidance Used for Wording

Current official Microsoft Foundry guidance reinforces these documentation requirements:

- agent systems should be designed for test, deploy, monitor, and trace workflows;
- observability should include evaluation, monitoring, tracing, operational metrics, latency/error tracking, token usage where available, quality/safety thresholds, and alerts;
- responsible AI guidance emphasizes governance through tracing, monitoring, and compliance-oriented controls;
- model access should remain behind controlled application boundaries rather than becoming a frontend product contract.

Current official OpenRewrite guidance reinforces these F5 requirements:

- Java migration recipes can be composed into backend-allowlisted repair modes;
- Java version migration recipes can update build configuration and APIs when a clear migration exists;
- Jackson 2 to Jackson 3 recipes cover dependency updates, package/type changes, method renames, and related migration changes;
- OpenRewrite should be invoked by backend policy after review and approval, not directly by an LLM or frontend.

These sources improve terminology and governance wording only. Repository docs and code reality remain the product source of truth.

---

## 5. F0 - Pre-Feature Codebase Cleanup

### Goal

Clean the codebase before implementation so the product workflow is backend/API driven, auditable, and not dependent on old workflow surfaces.

### Why It Matters

Checkpoint governance is weak if old Copilot, TUI, CLI, or duplicate orchestration paths can still drive migration behavior. F0 creates a clean product baseline before F1-F5 add new behavior.

### Current Code Reality

Copilot, TUI, final-report, CLI, orchestrator, assistant, and model-routing code still exist in the repository. Some of this may be retained for compatibility, but it must not be reachable from the DEMO3 product runtime unless explicitly approved.

### Required Behavior

- GitHub Copilot is not part of the product migration workflow.
- TUI is not part of the product workflow.
- Old CLI/debug/product commands are removed or quarantined.
- Duplicate orchestration logic is identified.
- Unused modules/dependencies are identified.
- Stale docs are updated.
- Cleanup report is generated.

### Tasks

- F0-T1: Inventory Copilot runtime paths.
- F0-T2: Disable or quarantine Copilot from product runtime.
- F0-T3: Inventory TUI and CLI runtime paths.
- F0-T4: Remove or quarantine TUI from product workflow.
- F0-T5: Identify duplicate orchestration logic.
- F0-T6: Identify unused modules/dependencies.
- F0-T7: Clean stale product terminology.
- F0-T8: Generate cleanup report.

### Subtasks

- Search for Copilot imports, adapters, schemas, and runtime calls.
- Search for TUI entrypoints and any product docs that imply TUI is supported.
- Search for CLI commands that can start, resume, repair, or mutate product workflows.
- Search for command execution paths and backend-owned command construction.
- Search for runtime-provider leakage in public contracts, UI text, docs, and DTOs.
- Verify no public API exposes `sandbox_path`, argv, env, command, provider, endpoint, deployment, or env ref as a product API field.
- Mark retained legacy modules as quarantined, internal, or compatibility-only.
- Produce a cleanup report with removed, quarantined, retained, and follow-up items.

### Files To Inspect Before Implementation

- `migration_factory/orchestrator/`
- `migration_factory/copilot_assist/`
- `migration_factory/copilot_repair/`
- `migration_factory/final_report/`
- `migration_factory/tui/`
- `migration_factory/cli.py`
- `migration_factory/control_tower/adapters/fastapi/app.py`
- `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- `migration_factory/control_tower/application/v2_settings.py`
- `migration_factory/control_tower/application/v2_model_role_router.py`
- `migration_factory/control_tower/application/v2_assistant_model_client.py`
- `migration_factory/agents/analysis_agent/`
- `migration_factory/agents/planning_agent/`

### Data / Artifacts Required

- Cleanup inventory.
- Quarantine/removal decision log.
- Public contract leakage scan.
- Cleanup report.

### API/UI Contract Implications

The backend API/UI path is the product control surface. F0 must not add provider-selection controls or product fields for execution paths, argv, env, raw commands, or runtime secrets.

### Acceptance Criteria

- Copilot is not part of the migration workflow.
- TUI is not part of the product workflow.
- No product path can invoke Copilot.
- Backend API/UI path is the product control surface.
- Cleanup report is generated.

### Risks

- Legacy code remains reachable through a secondary path.
- Search-based cleanup misses dynamic imports or indirect entrypoints.
- Product terminology keeps implying unsupported Copilot/TUI behavior.

### Out Of Scope

- Implementing replacement migration behavior.
- Removing historical artifacts needed for compatibility.
- Changing runtime code as part of this docs task.

---

## 6. F1 - Agent Checkpoints and User Decisions

### Goal

Stop after Analysis and Planning so users can review, request changes, continue, stop, or download artifacts.

### Why It Matters

Analysis and Planning shape every downstream migration step. If those artifacts are wrong, the rest of the pipeline can produce high-cost mistakes. F1 makes user checkpoints first-class.

### Current Code Reality

The stable codebase already has gates, gate actions, artifact revisions, stage progression, run configuration, repositories, and eventing. F1 should reuse those concepts rather than create a second checkpoint system.

### Required Behavior

- Analysis Agent stops for review.
- Planning Agent stops for review.
- Transformation Agents auto-continue only when no risk is detected.
- Build Agent auto-continues only when build succeeds.
- Test Agent auto-continues only when tests pass.
- System stops on risk, build failure, test failure, target profile reached, stale artifact, reviewer failure, or approval required.

### Tasks

- F1-T1: Define checkpoint state model.
- F1-T2: Define user decisions.
- F1-T3: Define Analysis checkpoint.
- F1-T4: Define Planning checkpoint.
- F1-T5: Define safe auto-continue rules.
- F1-T6: Define stop conditions.
- F1-T7: Define artifact download/preview behavior.
- F1-T8: Define resume behavior.

### Subtasks

- Define checkpoint statuses and terminal states.
- Define user actions: continue, stop, request analysis modification, request planning modification, download artifacts.
- Define required persisted fields: job state, current agent/stage, checkpoint status, artifact refs, user decision, comments, reason, resume decision.
- Bind checkpoint decisions to artifact revision IDs and checksums.
- Define stale-artifact rejection rules.
- Define when transformation/build/test may auto-continue.
- Define how resume selects backend-owned next stage without user-supplied paths or commands.

### Files To Inspect Before Implementation

- `migration_factory/control_tower/application/v2_stage_progression.py`
- `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- `migration_factory/control_tower/application/v2_gate_action_service.py`
- `migration_factory/control_tower/application/v2_phase_gate_service.py`
- `migration_factory/control_tower/schemas/phase_gate.py`
- `migration_factory/control_tower/schemas/run_configuration.py`
- `migration_factory/control_tower/domain/entities.py`
- `migration_factory/control_tower/infrastructure/sqlite/`
- `migration_factory/control_tower/adapters/fastapi/app.py`
- `migration_factory/agents/analysis_agent/`
- `migration_factory/agents/planning_agent/`
- `migration_factory/agents/build_agent/`
- `migration_factory/agents/test_agent/`

### Data / Artifacts Required

- Checkpoint state artifact.
- User decision artifact.
- Artifact preview/download references.
- Resume decision artifact.

### API/UI Contract Implications

UI/API actions should use checkpoint IDs, artifact refs, checksums, decisions, and comments. They must not accept `sandbox_path`, argv, env, command, provider, endpoint, deployment, or env ref.

### Acceptance Criteria

- Analysis and Planning stop for review.
- Safe auto-continue rules are explicit.
- Stop conditions include risk, build failure, test failure, target reached, stale artifact, reviewer failure, and approval required.
- User decisions and reasons are persisted.
- Resume is backend-owned and checksum-bound.

### Risks

- Checkpoint terminology conflicts with existing LangGraph persistence.
- Auto-continue hides risk.
- Resume accidentally revives stale execution state.

### Out Of Scope

- Building new UI code in this docs task.
- Implementing F5 repair behavior.

---

## 7. F2 - Deterministic Artifact + Primary LLM + Reviewer LLM

### Goal

For Analysis and Planning first, every model-required output must be produced through:

```text
deterministic artifact
-> primary LLM reasoning
-> reviewer LLM validation
-> final Markdown artifact
-> next agent input
```

### Why It Matters

Deterministic artifacts provide evidence. Primary LLM output provides reasoning. Reviewer LLM output provides independent review. The final Markdown artifact gives the next agent one stable, reviewed source.

### Current Code Reality

The repository already has deterministic Analysis and Planning agents, reviewer service concepts, model schemas, artifact revisions, artifact resolution, and model-routing code. F2 should extend these rather than create separate review infrastructure.

### Required Behavior

- Deterministic artifact is created first.
- Primary LLM reasons from deterministic artifact only.
- Reviewer LLM reviews primary output.
- Reviewer may accept, reject, or request revision.
- Final Markdown artifact is persisted and checksum-bound.
- Next agent receives final reviewed Markdown, not raw LLM output.
- Deterministic fallback cannot satisfy model-required reviewed artifact.

### Tasks

- F2-T1: Define deterministic artifact contract.
- F2-T2: Define primary LLM role.
- F2-T3: Define reviewer LLM role.
- F2-T4: Define reviewer decisions.
- F2-T5: Define final Markdown artifact schema.
- F2-T6: Define retry/revision behavior.
- F2-T7: Define metadata and checksum binding.
- F2-T8: Define tests for reviewer-required behavior.

### Subtasks

- Define Analysis deterministic artifact shape.
- Define Planning deterministic artifact shape.
- Define primary LLM prompt inputs as artifact refs/checksums, not raw frontend state.
- Define reviewer input as deterministic artifact plus primary reasoning.
- Define reviewer decisions: accept, reject, request revision, failed closed.
- Define revision loop and stale-input handling.
- Persist primary reasoning, reviewer notes, and final Markdown as distinct artifacts.
- Record prompt/context checksum, output checksum, reviewer decision, and confidence.

### Final Markdown Artifact Schema

Each final Markdown artifact must include:

- Summary
- Inputs used
- Deterministic findings
- File names and file paths
- Primary LLM reasoning
- Reviewer LLM notes
- Risks
- Confidence
- Recommended next step
- Machine-readable metadata

### Files To Inspect Before Implementation

- `migration_factory/agents/analysis_agent/`
- `migration_factory/agents/planning_agent/`
- `migration_factory/control_tower/application/v2_reviewer_service.py`
- `migration_factory/control_tower/application/v2_model_role_router.py`
- `migration_factory/control_tower/application/v2_assistant_model_client.py`
- `migration_factory/control_tower/application/v2_model_schemas.py`
- `migration_factory/control_tower/application/v2_gate_artifact_resolver.py`
- `migration_factory/control_tower/schemas/artifact_revision.py`
- `migration_factory/control_tower/infrastructure/sqlite/`
- `migration_factory/control_tower/adapters/fastapi/app.py`

### Data / Artifacts Required

- Deterministic Analysis artifact.
- Primary Analysis reasoning artifact.
- Reviewer Analysis artifact.
- Final Analysis Markdown artifact.
- Deterministic Planning artifact.
- Primary Planning reasoning artifact.
- Reviewer Planning artifact.
- Final Planning Markdown artifact.

### API/UI Contract Implications

UI may display deterministic, primary, reviewer, and final Markdown artifacts separately. The public contract must identify artifact refs and checksums, not model runtime internals.

### Acceptance Criteria

- Reviewer LLM is mandatory for supported model-required outputs.
- Final Markdown is persisted and checksum-bound.
- Next agent receives final reviewed Markdown.
- Deterministic fallback alone cannot satisfy a model-required reviewed artifact.
- Stale or rejected reviewer output blocks checkpoint acceptance.

### Risks

- Primary LLM output is accidentally passed forward.
- Reviewer becomes optional in error paths.
- Final Markdown lacks machine-readable metadata.

### Out Of Scope

- Applying this chain to every later agent in the first slice.
- Public provider/model selection.

---

## 8. F3 - Target Profile Control

### Goal

User can choose the final migration target profile and the system must stop there.

### Why It Matters

Users may want Spring Boot 2 to Spring Boot 3 without continuing to Spring Boot 4. Target overshoot can create unnecessary breakage and false failures.

### Current Code Reality

The repository has stage progression, pipeline definitions, run configuration, planning profile code, and profile readers. F3 should align target profile control with those existing concepts.

### Required Behavior

- User provides or confirms `source_profile`.
- User selects `target_profile`.
- Backend validates source/target pair.
- Pipeline includes only required stages.
- Pipeline stops at target profile.
- Pipeline does not continue to higher profiles.
- Target profile is persisted in job configuration.

Example:

```text
source_profile = spring-boot-2
target_profile = spring-boot-3
```

Expected:

- migrate to Spring Boot 3;
- stop at Spring Boot 3;
- do not continue to Spring Boot 4.

### Tasks

- F3-T1: Define profile model.
- F3-T2: Define profile validation.
- F3-T3: Define stage/profile mapping.
- F3-T4: Define stop-at-target behavior.
- F3-T5: Define API fields.
- F3-T6: Define artifact/checkpoint metadata.
- F3-T7: Define tests for target overshoot prevention.

### Subtasks

- Define allowed profile identifiers and ordering.
- Define invalid profile pair behavior.
- Define how planning records included and excluded stages.
- Define checkpoint metadata for source and target profile.
- Define stop condition when target is reached.
- Define overshoot regression tests.

### Files To Inspect Before Implementation

- `migration_factory/control_tower/application/v2_stage_progression.py`
- `migration_factory/control_tower/schemas/run_configuration.py`
- `migration_factory/control_tower/schemas/pipeline_definition.py`
- `migration_factory/profiles/`
- `migration_factory/profile_reader.py`
- `migration_factory/agents/planning_agent/`
- `migration_factory/control_tower/adapters/fastapi/app.py`

### Data / Artifacts Required

- Profile selection artifact.
- Profile validation artifact.
- Stage/profile mapping artifact.
- Checkpoint metadata carrying source and target profile.

### API/UI Contract Implications

Public contracts may include `source_profile`, `target_profile`, profile validation status, and stage inclusion/exclusion summaries. They must not include model runtime internals or execution details.

### Acceptance Criteria

- User can select target profile.
- Backend validates source/target pair.
- Pipeline stops at target profile.
- Pipeline does not continue to higher profiles.
- Target profile is persisted.

### Risks

- Profiles are treated as display labels rather than validated routing inputs.
- Target overshoot occurs after repair or resume.
- Stage mapping duplicates planning logic.

### Out Of Scope

- Provider/model profile selection.
- Custom arbitrary execution stages.

---

## 9. F4 - Start From Current App State

### Goal

User can start from the real current state of the application and skip older stages.

### Why It Matters

Already-modernized applications should not be forced through obsolete migrations. Starting from the wrong source profile wastes time and can introduce invalid changes.

### Current Code Reality

Analysis and planning code already reads project state and profile-related inputs. Stage progression and run configuration can be reused to express skipped stages and resume behavior.

### Required Behavior

- Analysis detects source profile.
- User can manually override source profile.
- Backend validates override reason.
- Skipped stages are recorded.
- Skipped stages are explained.
- Already-modernized apps are not forced through old migration stages.
- Resume from checkpoint is supported.

Example:

```text
Application already on Spring Boot 3.
User chooses target Spring Boot 4.
System starts from Spring Boot 3 path and records skipped older stages.
```

### Tasks

- F4-T1: Define source-profile detection artifact.
- F4-T2: Define manual override action.
- F4-T3: Define skipped-stage ledger.
- F4-T4: Define profile pair validation.
- F4-T5: Define resume-from-checkpoint behavior.
- F4-T6: Define tests for already-modernized apps.

### Subtasks

- Define source-profile evidence fields.
- Define confidence and uncertainty reporting.
- Define manual override comments and validation.
- Define skipped-stage ledger entry format.
- Define how skipped stages appear in final Markdown artifacts.
- Define resume from checkpoint with profile compatibility checks.

### Files To Inspect Before Implementation

- `migration_factory/agents/analysis_agent/`
- `migration_factory/profile_reader.py`
- `migration_factory/profiles/`
- `migration_factory/control_tower/application/v2_stage_progression.py`
- `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- `migration_factory/control_tower/schemas/run_configuration.py`
- `migration_factory/control_tower/schemas/pipeline_definition.py`
- `migration_factory/control_tower/infrastructure/sqlite/`
- `migration_factory/control_tower/adapters/fastapi/app.py`

### Data / Artifacts Required

- Source-profile detection artifact.
- Manual override decision artifact.
- Skipped-stage ledger.
- Resume compatibility artifact.

### API/UI Contract Implications

UI/API may expose detected profile, confidence, evidence summary, manual override action, skipped-stage explanation, and checkpoint resume options. It must not allow user-supplied execution paths or commands.

### Acceptance Criteria

- Analysis detects current source profile.
- User can override detected source profile.
- Backend validates override reason.
- Skipped stages are recorded and explained.
- Already-modernized apps skip old stages.
- Resume from checkpoint is supported with checksum/profile compatibility.

### Risks

- Incorrect detection silently selects wrong route.
- Override bypasses validation.
- Skipped stages are not auditable.

### Out Of Scope

- Arbitrary import of external checkpoints.
- Manual editing of backend-owned stage commands.

---

## 10. F5 - Build/Test Repair Agent Review Loop

### Goal

When build or tests fail, a dedicated Repair Agent analyzes the failure and proposes a reviewed diff before any patch is applied.

This is not a simple repair loop. It is an agentic repair workflow with a primary repair model and an independent reviewer model.

### Why It Matters

Build and test failures are where risky code changes are most tempting. F5 keeps repair flexible while preserving human control, exact-diff approval, backend policy validation, sandbox execution, rollback, and proof.

### Current Code Reality

The repository already has Build and Test agents, repair flow, repair gate service, reviewer service, model schemas, stage progression, artifact revisions, SQLite persistence, and `migration_factory/repair_loop/` modules for evidence collection, rules, patch gating, patch application, and validation runner concepts.

### Repair Agent Inputs

- build failure logs;
- test failure logs;
- compiler output;
- test output;
- changed files;
- previous accepted artifacts;
- current source profile;
- target profile;
- current repository state;
- migration plan;
- prior repair proposals if any;
- previous reviewer notes;
- user comments if this is another review cycle;
- current artifact/checkpoint checksums.

### Required Behavior

The Repair Agent must handle both:

- Build Agent failures;
- Test Agent failures.

Repair Agent flow:

1. Capture build/test failure evidence.
2. Build deterministic failure artifact.
3. Primary Repair LLM analyzes root cause.
4. Primary Repair LLM proposes a fix strategy.
5. Primary Repair LLM creates a proposed diff.
6. Reviewer LLM reviews failure interpretation, root cause hypothesis, proposed changed files, proposed diff, risks, target-profile fit, and backend policy concerns.
7. Reviewer LLM accepts, rejects, or requests improvement.
8. Backend stores proposal and reviewer result as immutable artifacts.
9. User sees error summary, root cause hypothesis, changed files, proposed diff, explanation, risks, confidence, reviewer notes, approve/reject/request-review controls, and comment box.
10. If user rejects, status becomes `STOPPED_BY_USER`, no patch is applied, rejection reason is stored, and artifacts remain downloadable.
11. If user requests another review, original failure context, previous proposed diff, previous reasoning, reviewer notes, user comments, current repo state, and current checksums return to the Repair Agent.
12. Repair Agent creates a new proposal.
13. Reviewer LLM reviews the new proposal.
14. User decides again.
15. If user approves, backend applies exact reviewed diff only, reruns the Build or Test Agent depending on failure source, continues or stops at checkpoint on success, and starts another repair cycle on repeated failure.

### Tasks

- F5-T1: Define failure evidence capture.
- F5-T2: Define Repair Agent input context.
- F5-T3: Define deterministic failure artifact.
- F5-T4: Define primary Repair LLM role.
- F5-T5: Define Reviewer LLM role for repair.
- F5-T6: Define proposed diff artifact.
- F5-T7: Define policy validation before presentation.
- F5-T8: Define user decision actions.
- F5-T9: Define request-another-review loop.
- F5-T10: Define exact-diff approval and apply behavior.
- F5-T11: Define build/test rerun behavior.
- F5-T12: Define repeated failure behavior.
- F5-T13: Define rollback and proof behavior.
- F5-T14: Define UI/API presentation contract.
- F5-T15: Define tests.

### Subtasks

- Capture build logs.
- Capture test logs.
- Normalize compiler/test errors.
- Collect changed files.
- Collect previous artifacts.
- Build repair context pack.
- Bind context pack to checksum.
- Call Primary Repair LLM.
- Call Reviewer LLM.
- Store primary reasoning.
- Store reviewer notes.
- Store proposed diff.
- Validate proposed diff.
- Present diff to user.
- Accept/reject/request-review.
- Store user comments.
- Re-run repair with comments.
- Apply only approved exact diff.
- Rerun build/test.
- Record proof.
- Roll back on failure when required.

### OpenRewrite / Jackson Guidance

OpenRewrite and Jackson recipes can be backend-allowlisted repair strategies for migration-specific failures, especially Jackson 2 to Jackson 3. The LLM does not execute OpenRewrite directly. The Repair Agent may recommend an allowlisted backend repair mode. The backend validates and executes allowed repair modes only after user approval and checksum binding.

### Files To Inspect Before Implementation

- `migration_factory/agents/build_agent/`
- `migration_factory/agents/test_agent/`
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/control_tower/application/v2_repair_gate_service.py`
- `migration_factory/control_tower/application/v2_reviewer_service.py`
- `migration_factory/control_tower/application/v2_model_role_router.py`
- `migration_factory/control_tower/application/v2_assistant_model_client.py`
- `migration_factory/control_tower/application/v2_model_schemas.py`
- `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- `migration_factory/control_tower/application/v2_stage_progression.py`
- `migration_factory/control_tower/schemas/artifact_revision.py`
- `migration_factory/control_tower/schemas/phase_gate.py`
- `migration_factory/control_tower/domain/entities.py`
- `migration_factory/control_tower/infrastructure/sqlite/`
- `migration_factory/control_tower/adapters/fastapi/app.py`
- `migration_factory/repair_loop/evidence_collector.py`
- `migration_factory/repair_loop/rule_registry.py`
- `migration_factory/repair_loop/patch_gate.py`
- `migration_factory/repair_loop/patch_apply.py`
- `migration_factory/repair_loop/validation_runner.py`

### Data / Artifacts Required

- Failure evidence artifact.
- Deterministic failure artifact.
- Repair context pack.
- Primary repair reasoning artifact.
- Proposed diff artifact.
- Reviewer repair artifact.
- User repair decision artifact.
- Apply result artifact.
- Build/test rerun proof artifact.
- Rollback artifact when required.

### API/UI Contract Implications

UI/API must support build/test error summary, root cause hypothesis, files that will change, proposed diff, why the fix is needed, risks, confidence, reviewer notes, approve, reject, request another review, and user comments.

API must bind decisions to exact proposal and reviewer checksums. It must never accept an unreviewed diff for application.

### Acceptance Criteria

- Build and test failures both enter Repair Agent flow.
- Primary Repair LLM proposes root cause and fix.
- Reviewer LLM reviews the exact proposed diff.
- Final proposal is stored as an artifact.
- User sees diff plus explanation.
- User can approve, reject, or request another review with comments.
- Backend applies only exact approved reviewed diff.
- Backend reruns Build or Test Agent depending on failure source.
- Repeat failure starts another Repair Agent review cycle.
- Rejection applies no patch and stores reason.

### Risks

- A stale diff is applied after repository state changes.
- User comments are not included in repeated review.
- Reviewer accepts a proposal that violates target profile or backend policy.
- Web/vendor recipe is used without backend allowlist.
- Patch is applied without exact approval.

### Out Of Scope

- Autonomous repair execution.
- LLM-selected commands.
- LLM-selected sandbox or filesystem targets.
- Frontend code changes in this docs task.

---

## 11. Sprint Mapping

The existing 01-18 implementation slices remain useful engineering details, but they are not the product spine.

| Feature | Implementation mapping |
|---|---|
| F0 | Foundation cleanup, Copilot quarantine, TUI removal, stale terminology cleanup |
| F1 | stage checkpoint, stage attempt, retry/resume/fork, cockpit checkpoint UX |
| F2 | LLM candidate generator + independent reviewer, extended to Analysis and Planning |
| F3 | profile-targeting docs and implementation |
| F4 | source-profile-start docs and implementation |
| F5 | failure evidence, classifier, retrieval pack, repair mode, LLM repair candidate, reviewer, backend validator, human approval, sandbox executor, validation runner, checkpoint promoter, cockpit recovery UX, e2e fixtures |

---

## 12. Delivery Order

1. F0 cleanup.
2. Foundry/model boundary hardening if needed.
3. F1 checkpoint foundation.
4. F2 Analysis reviewer chain.
5. F2 Planning reviewer chain.
6. F3 target profile.
7. F4 source/current-state start.
8. F5 Repair Agent evidence and proposal.
9. F5 reviewer and user decision loop.
10. F5 sandbox apply, rerun, proof.
11. Stage 4/Jackson as concrete F5 proof scenario.

---

## 13. Acceptance For This Planning Baseline

- PRD is on stable baseline `0d9fa7b3b4c386aaebaa7287bebb3f3d2e3cb383`.
- PRD no longer names the old feature branch as the target branch.
- Every F0-F5 feature has tasks and subtasks.
- Every F0-F5 feature has clear implementation description.
- Every F0-F5 feature lists files to inspect.
- F5 is described as a Build/Test Repair Agent, not a simple repair loop.
- F5 includes Primary Repair LLM plus Reviewer LLM.
- F5 includes proposed diff, explanation, approve/reject/request another review with comments.
- Sprint docs match the PRD direction.
- No runtime code changed.
