[TASK] Define checkpoint state model — F1 checkpoints

---

## Objective

Define checkpoint statuses and transitions so Analysis and Planning can stop safely for user decisions.

---

## Steps / Subtasks

1. Define waiting, accepted, rejected, changes requested, stopped, stale, and failed-closed states.
2. Map state transitions to existing phase gate and artifact revision concepts.
3. Define terminal states and idempotent retry behavior.
4. Bind state to job ID, stage, artifact refs, checksums, and profile context.
5. List files to inspect: `v2_phase_gate_service.py`, `phase_gate.py`, `artifact_revision.py`, SQLite repositories.

---

## Inputs / Dependencies

* Depends on: DEMO3-F1-STORY
* Requires access to: Control Tower phase gate services, schemas, repositories

---

## Output / Deliverable

Checkpoint state model.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration governance
Story Points: 3
Priority: High
Parent: DEMO3-F1-STORY

[TASK] Define user decision contract — F1 checkpoints

---

## Objective

Define safe user decisions that bind to checkpoint artifacts without accepting execution authority from the user or chatbot.

---

## Steps / Subtasks

1. Define continue, stop, request analysis modification, request planning modification, download, and resume actions.
2. Define required fields: checkpoint ID, artifact revision ID, checksum, decision, reason, comments.
3. Define rejected fields: `sandbox_path`, argv, env, raw command, filesystem target, provider, endpoint, deployment, env ref.
4. Map decisions to existing gate action and approval mapping concepts.
5. List files to inspect: `v2_gate_action_service.py`, `v2_phase_gate_service.py`, `phase_gate.py`, `run_configuration.py`, FastAPI app.

---

## Inputs / Dependencies

* Depends on: F1-T1
* Requires access to: gate action service, API schemas, artifact revision schema

---

## Output / Deliverable

User decision contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration governance
Story Points: 3
Priority: High
Parent: DEMO3-F1-STORY

[TASK] Define Analysis checkpoint — F1 checkpoints

---

## Objective

Define the Analysis checkpoint so reviewed Analysis output is accepted, revised, or stopped before Planning begins.

---

## Steps / Subtasks

1. Define required Analysis artifact refs and checksums.
2. Define user-visible Analysis summary, preview, and download references.
3. Define accepted, modification requested, stopped, and stale Analysis outcomes.
4. Define how Analysis comments feed a later revision without bypassing backend validation.
5. List files to inspect: `migration_factory/agents/analysis_agent/`, `v2_orchestrator_runner.py`, `v2_stage_progression.py`, gate services.

---

## Inputs / Dependencies

* Depends on: F1-T1, F1-T2, DEMO3-F2-STORY
* Requires access to: Analysis agent outputs and Control Tower gate services

---

## Output / Deliverable

Analysis checkpoint contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration governance
Story Points: 3
Priority: High
Parent: DEMO3-F1-STORY

[TASK] Define Planning checkpoint — F1 checkpoints

---

## Objective

Define the Planning checkpoint so reviewed Planning output is accepted, revised, or stopped before transformation/build/test steps.

---

## Steps / Subtasks

1. Define required Planning artifact refs and checksums.
2. Define user-visible Planning summary, preview, and download references.
3. Define accepted, modification requested, stopped, and stale Planning outcomes.
4. Define how Planning comments feed a later revision without bypassing backend validation.
5. List files to inspect: `migration_factory/agents/planning_agent/`, `v2_orchestrator_runner.py`, `v2_stage_progression.py`, gate services.

---

## Inputs / Dependencies

* Depends on: F1-T1, F1-T2, DEMO3-F2-STORY
* Requires access to: Planning agent outputs and Control Tower gate services

---

## Output / Deliverable

Planning checkpoint contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration governance
Story Points: 3
Priority: High
Parent: DEMO3-F1-STORY

[TASK] Define safe auto-continue rules — F1 checkpoints

---

## Objective

Define when later stages may proceed without user action while stopping on risk, failure, target reached, or approval requirements.

---

## Steps / Subtasks

1. Define transformation auto-continue only when no risk is detected.
2. Define Build Agent auto-continue only on build success.
3. Define Test Agent auto-continue only on test success.
4. Define reviewer failure, stale artifact, and target reached as stop conditions.
5. List files to inspect: `v2_stage_progression.py`, `v2_orchestrator_runner.py`, build agent, test agent.

---

## Inputs / Dependencies

* Depends on: F1-T1, F1-T2
* Requires access to: stage progression, runner, build/test agents

---

## Output / Deliverable

Safe auto-continue policy.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration governance
Story Points: 2
Priority: High
Parent: DEMO3-F1-STORY

[TASK] Define stop-condition matrix — F1 checkpoints

---

## Objective

Define every condition that stops the pipeline and the persisted state/user action associated with it.

---

## Steps / Subtasks

1. List stop conditions: risk, build failure, test failure, target reached, stale artifact, reviewer failure, approval required, user stop.
2. Map each condition to checkpoint state and event payload.
3. Define allowed user actions for each stop condition.
4. Define how F5 repair entry is reached from build/test failure stops.
5. List files to inspect: `v2_stage_progression.py`, `v2_phase_gate_service.py`, `v2_repair_flow.py`, domain entities, SQLite repositories.

---

## Inputs / Dependencies

* Depends on: F1-T1, F1-T5
* Requires access to: stage progression, gates, repair flow, persistence

---

## Output / Deliverable

Stop-condition matrix.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration governance
Story Points: 3
Priority: High
Parent: DEMO3-F1-STORY

[TASK] Define artifact preview/download behavior — F1 checkpoints

---

## Objective

Define safe artifact preview and download behavior through artifact references and checksums.

---

## Steps / Subtasks

1. Define previewable artifact types and downloadable artifact types.
2. Define artifact ref, revision ID, checksum, content type, and authorization requirements.
3. Define stale artifact and missing artifact behavior.
4. Define redaction requirements for sandbox paths and execution details.
5. List files to inspect: `v2_gate_artifact_resolver.py`, `artifact_revision.py`, FastAPI artifact routes, SQLite repositories.

---

## Inputs / Dependencies

* Depends on: F1-T1, F1-T2
* Requires access to: artifact resolver, artifact schema, API routes

---

## Output / Deliverable

Artifact presentation contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration governance
Story Points: 2
Priority: High
Parent: DEMO3-F1-STORY

[TASK] Define resume behavior — F1 checkpoints

---

## Objective

Define backend-owned resume behavior that continues from valid checkpoints without accepting user-supplied execution details.

---

## Steps / Subtasks

1. Define resume inputs as checkpoint ID, artifact refs, checksums, decision, and comments.
2. Define stale, foreign, incompatible, or terminal checkpoint rejection.
3. Define backend-owned next-stage resolution.
4. Define idempotency behavior for repeated resume requests.
5. List files to inspect: `v2_stage_progression.py`, `v2_orchestrator_runner.py`, `run_configuration.py`, domain entities, SQLite repositories.

---

## Inputs / Dependencies

* Depends on: F1-T1, F1-T2, F1-T6
* Requires access to: stage progression, runner, run configuration, persistence

---

## Output / Deliverable

Resume contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration governance
Story Points: 3
Priority: High
Parent: DEMO3-F1-STORY
