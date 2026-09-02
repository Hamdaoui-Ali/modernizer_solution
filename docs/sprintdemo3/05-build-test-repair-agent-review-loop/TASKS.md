[TASK] Define build/test failure evidence capture — F5 Repair Agent

---

## Objective

Capture complete build/test failure evidence so the Repair Agent starts from deterministic backend-owned context.

---

## Steps / Subtasks

1. Define captured inputs for build logs, test logs, compiler output, test output, changed files, repo state, profiles, prior accepted artifacts, and checksums.
2. Define build-failure and test-failure source classification.
3. Define evidence redaction and storage boundaries.
4. Define evidence capture tests for build and test failures.
5. List files to inspect: `migration_factory/agents/build_agent/`, `migration_factory/agents/test_agent/`, `migration_factory/repair_loop/evidence_collector.py`, `v2_orchestrator_runner.py`.

---

## Inputs / Dependencies

* Depends on: DEMO3-F5-STORY
* Requires access to: Build Agent, Test Agent, evidence collector, orchestrator runner

---

## Output / Deliverable

Failure evidence schema.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 3
Priority: Highest
Parent: DEMO3-F5-STORY

[TASK] Define Repair Agent input context — F5 Repair Agent

---

## Objective

Define the checksum-bound context pack passed to the Primary Repair LLM.

---

## Steps / Subtasks

1. Include failure evidence, compiler/test output, changed files, current repo state, previous accepted artifacts, source profile, target profile, user comments, prior proposals, and reviewer notes.
2. Define context ordering and truncation rules.
3. Define redaction of secrets, raw commands, and sandbox paths.
4. Define context checksum and stale-context rejection behavior.
5. List files to inspect: `evidence_collector.py`, `v2_gate_artifact_resolver.py`, `v2_model_schemas.py`, artifact repositories.

---

## Inputs / Dependencies

* Depends on: F5-T1
* Requires access to: evidence collector, artifact resolver, model schemas

---

## Output / Deliverable

Repair context pack.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 3
Priority: Highest
Parent: DEMO3-F5-STORY

[TASK] Define deterministic failure artifact — F5 Repair Agent

---

## Objective

Normalize build/test failure data into a deterministic failure artifact before model reasoning.

---

## Steps / Subtasks

1. Define normalized compiler error fields.
2. Define normalized test failure fields.
3. Define changed-file and profile context metadata.
4. Define stable ordering and checksum rules.
5. List files to inspect: build agent, test agent, `evidence_collector.py`, `rule_registry.py`.

---

## Inputs / Dependencies

* Depends on: F5-T1, F5-T2
* Requires access to: failure evidence schema and repair loop rule registry

---

## Output / Deliverable

Deterministic failure artifact schema.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 3
Priority: Highest
Parent: DEMO3-F5-STORY

[TASK] Define Primary Repair LLM role — F5 Repair Agent

---

## Objective

Define how the Primary Repair LLM proposes root cause, fix strategy, and exact diff without gaining execution authority.

---

## Steps / Subtasks

1. Define Primary Repair LLM inputs from the repair context pack.
2. Define output fields: root cause, fix strategy, changed files, proposed diff, risks, confidence, and rationale.
3. Forbid execution, approval, sandbox selection, filesystem target selection, and raw command authority.
4. Define malformed output and no-fix proposal behavior.
5. List files to inspect: `v2_model_schemas.py`, `v2_assistant_model_client.py`, `v2_model_role_router.py`, `v2_repair_flow.py`.

---

## Inputs / Dependencies

* Depends on: F5-T2, F5-T3
* Requires access to: model schemas, model client, role router, repair flow

---

## Output / Deliverable

Primary repair reasoning contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 3
Priority: Highest
Parent: DEMO3-F5-STORY

[TASK] Define Reviewer LLM role for repair — F5 Repair Agent

---

## Objective

Define reviewer validation for repair reasoning and exact proposed diff.

---

## Steps / Subtasks

1. Define reviewer inputs: failure artifact, repair context, primary reasoning, proposed diff, changed files, profiles, policy hints, and checksums.
2. Define review dimensions: root cause, diff correctness, target-profile fit, safety, stale state, and backend policy concerns.
3. Define reviewer outputs: accept, reject, request improvement, notes, risks, confidence.
4. Bind review to exact diff checksum.
5. List files to inspect: `v2_reviewer_service.py`, `v2_repair_gate_service.py`, `v2_model_schemas.py`, reviewer repositories.

---

## Inputs / Dependencies

* Depends on: F5-T4
* Requires access to: reviewer service, repair gate service, model schemas

---

## Output / Deliverable

Repair reviewer contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 3
Priority: Highest
Parent: DEMO3-F5-STORY

[TASK] Define proposed diff artifact — F5 Repair Agent

---

## Objective

Define immutable storage for the exact proposed diff and its metadata.

---

## Steps / Subtasks

1. Define diff artifact fields: files, hunks, base checksum, proposal checksum, context checksum, and authoring model role.
2. Define immutability and revision lineage.
3. Define display-safe diff metadata for UI/API.
4. Define checksum mismatch behavior.
5. List files to inspect: `artifact_revision.py`, repair proposal repositories, `v2_repair_flow.py`.

---

## Inputs / Dependencies

* Depends on: F5-T4, F5-T5
* Requires access to: artifact revision schema and repair persistence

---

## Output / Deliverable

Proposed diff artifact schema.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 3
Priority: Highest
Parent: DEMO3-F5-STORY

[TASK] Define policy validation before presentation — F5 Repair Agent

---

## Objective

Define backend policy validation that runs before a repair proposal can be presented as approvable.

---

## Steps / Subtasks

1. Validate proposed diff scope, changed files, target profile fit, stale state, and unsafe operations.
2. Validate backend-allowlisted repair modes such as OpenRewrite/Jackson scenarios.
3. Define fail-closed behavior for out-of-policy proposals.
4. Define policy validation artifact and tests.
5. List files to inspect: `patch_gate.py`, `rule_registry.py`, `v2_repair_flow.py`, `v2_repair_gate_service.py`.

---

## Inputs / Dependencies

* Depends on: F5-T6
* Requires access to: patch gate, rule registry, repair flow

---

## Output / Deliverable

Policy validation artifact.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 3
Priority: Highest
Parent: DEMO3-F5-STORY

[TASK] Define user decision actions — F5 Repair Agent

---

## Objective

Define approve, reject, and request-another-review actions for reviewed repair proposals.

---

## Steps / Subtasks

1. Define approval fields bound to proposal checksum, reviewer checksum, context checksum, and checkpoint ID.
2. Define rejection fields with required reason/comments.
3. Define request-another-review fields with user comments.
4. Reject unreviewed, stale, or policy-failed proposals.
5. List files to inspect: FastAPI app, `v2_gate_action_service.py`, `v2_repair_gate_service.py`, approval mapping.

---

## Inputs / Dependencies

* Depends on: F5-T5, F5-T6, F5-T7
* Requires access to: API routes, gate action service, repair gate service

---

## Output / Deliverable

Repair decision contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 3
Priority: Highest
Parent: DEMO3-F5-STORY

[TASK] Define request-another-review loop — F5 Repair Agent

---

## Objective

Define how user comments and prior repair context feed another Primary Repair LLM and Reviewer LLM cycle.

---

## Steps / Subtasks

1. Include original failure context, previous diff, prior reasoning, reviewer notes, user comments, current repo state, and checksums.
2. Define new proposal and reviewer artifact lineage.
3. Define stale previous proposal behavior.
4. Define tests proving user comments affect the next context.
5. List files to inspect: `v2_repair_flow.py`, `v2_reviewer_service.py`, artifact repositories, repair repositories.

---

## Inputs / Dependencies

* Depends on: F5-T8
* Requires access to: repair flow, reviewer service, artifact persistence

---

## Output / Deliverable

Repair revision loop spec.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 3
Priority: Highest
Parent: DEMO3-F5-STORY

[TASK] Define exact-diff approval and apply behavior — F5 Repair Agent

---

## Objective

Define backend application of only the exact reviewed diff that the user approved.

---

## Steps / Subtasks

1. Validate proposal checksum, reviewer checksum, context checksum, repo state checksum, and policy result before apply.
2. Apply only the exact approved reviewed diff in the backend sandbox.
3. Reject user-edited, unreviewed, stale, or mismatched diffs.
4. Record apply result artifact and ledger entry.
5. List files to inspect: `patch_apply.py`, `patch_gate.py`, `v2_repair_flow.py`, `v2_repair_gate_service.py`.

---

## Inputs / Dependencies

* Depends on: F5-T6, F5-T7, F5-T8
* Requires access to: patch apply, patch gate, repair flow

---

## Output / Deliverable

Exact-apply policy.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 3
Priority: Highest
Parent: DEMO3-F5-STORY

[TASK] Define build/test rerun behavior — F5 Repair Agent

---

## Objective

Define rerun behavior after approved diff application so proof comes from Build Agent or Test Agent output.

---

## Steps / Subtasks

1. Rerun Build Agent when the original failure source was build.
2. Rerun Test Agent when the original failure source was test.
3. Persist rerun inputs, outputs, status, and proof artifacts.
4. Define continuation or stop behavior after successful rerun.
5. List files to inspect: build agent, test agent, `v2_orchestrator_runner.py`, `validation_runner.py`.

---

## Inputs / Dependencies

* Depends on: F5-T10
* Requires access to: Build Agent, Test Agent, orchestrator runner, validation runner

---

## Output / Deliverable

Rerun policy.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 2
Priority: High
Parent: DEMO3-F5-STORY

[TASK] Define repeated failure behavior — F5 Repair Agent

---

## Objective

Define how repeated build/test failure starts another Repair Agent cycle with full prior context.

---

## Steps / Subtasks

1. Detect repeated failure after rerun.
2. Include prior proposals, reviewer notes, apply result, user comments, and current repo state in the next context.
3. Define retry/cycle metadata and limits.
4. Define repeated failure tests.
5. List files to inspect: `v2_repair_flow.py`, `v2_stage_progression.py`, SQLite repositories, `validation_runner.py`.

---

## Inputs / Dependencies

* Depends on: F5-T9, F5-T11
* Requires access to: repair flow, stage progression, persistence, validation runner

---

## Output / Deliverable

Repeated failure policy.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 2
Priority: High
Parent: DEMO3-F5-STORY

[TASK] Define rollback and proof behavior — F5 Repair Agent

---

## Objective

Define rollback and proof rules so repair success is controlled by backend validation, not model assertion.

---

## Steps / Subtasks

1. Define when rollback is required after failed apply or rerun.
2. Define rollback artifact and ledger fields.
3. Define proof artifact fields for successful build/test rerun.
4. Define tests for rollback, proof, and failure reporting.
5. List files to inspect: `validation_runner.py`, `patch_apply.py`, repair ledger concepts, SQLite repositories.

---

## Inputs / Dependencies

* Depends on: F5-T10, F5-T11, F5-T12
* Requires access to: validation runner, patch apply, repair persistence

---

## Output / Deliverable

Rollback/proof policy.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 2
Priority: High
Parent: DEMO3-F5-STORY

[TASK] Define UI/API presentation contract — F5 Repair Agent

---

## Objective

Define the safe presentation contract for repair proposals without adding frontend implementation in this docs task.

---

## Steps / Subtasks

1. Define fields for error summary, root cause, changed files, proposed diff, explanation, risks, confidence, reviewer notes, controls, and comment input.
2. Define approve, reject, and request-another-review API contracts.
3. Exclude raw commands, sandbox paths, filesystem targets, provider/model/deployment/env refs.
4. Define contract/redaction tests.
5. List files to inspect: FastAPI app, phase gate schemas, artifact revision schema, existing cockpit contracts if present.

---

## Inputs / Dependencies

* Depends on: F5-T6, F5-T8
* Requires access to: API routes, schemas, artifact metadata

---

## Output / Deliverable

Presentation contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 2
Priority: High
Parent: DEMO3-F5-STORY

[TASK] Define F5 test matrix — F5 Repair Agent

---

## Objective

Define focused tests for the full Build/Test Repair Agent review loop.

---

## Steps / Subtasks

1. Cover build failure, test failure, deterministic failure artifact, Primary Repair LLM output, Reviewer LLM exact-diff review, and policy validation.
2. Cover approve, reject, request-another-review with comments, stale diff, unreviewed diff, and exact approved diff apply.
3. Cover build/test rerun, repeated failure cycle, rollback, and proof artifacts.
4. Cover OpenRewrite/Jackson as backend-allowlisted proof scenario only.
5. List files to inspect: build/test/repair/reviewer tests and F5 implementation files.

---

## Inputs / Dependencies

* Depends on: F5-T1, F5-T2, F5-T3, F5-T4, F5-T5, F5-T6, F5-T7, F5-T8, F5-T9, F5-T10, F5-T11, F5-T12, F5-T13, F5-T14
* Requires access to: focused F5 test locations and repair fixtures

---

## Output / Deliverable

F5 test plan.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration repair-agent
Story Points: 2
Priority: High
Parent: DEMO3-F5-STORY
