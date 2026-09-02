[TASK] Define deterministic artifact contract — F2 review chain

---

## Objective

Define deterministic Analysis and Planning artifacts that ground all model-required output before primary LLM reasoning.

---

## Steps / Subtasks

1. Define required Analysis deterministic facts, evidence, file paths, and checksums.
2. Define required Planning deterministic facts, selected stages, constraints, and checksums.
3. Define artifact refs as model inputs instead of frontend-supplied state.
4. Define schema validation and failure behavior for missing deterministic fields.
5. List files to inspect: `migration_factory/agents/analysis_agent/`, `migration_factory/agents/planning_agent/`, `artifact_revision.py`.

---

## Inputs / Dependencies

* Depends on: DEMO3-F2-STORY
* Requires access to: Analysis agent, Planning agent, artifact revision schema

---

## Output / Deliverable

Deterministic artifact schema.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration artifacts
Story Points: 3
Priority: High
Parent: DEMO3-F2-STORY

[TASK] Define primary LLM role — F2 review chain

---

## Objective

Define primary LLM inputs and outputs for Analysis and Planning so reasoning is grounded in persisted deterministic artifacts.

---

## Steps / Subtasks

1. Define primary LLM input context as artifact refs, checksums, source profile, target profile, and user comments where allowed.
2. Define output fields: reasoning, risks, confidence, recommended next step, and draft Markdown.
3. Define limits: no execution, no approval, no sandbox/path selection, no raw commands.
4. Define malformed output and timeout behavior.
5. List files to inspect: `v2_model_schemas.py`, `v2_assistant_model_client.py`, `v2_model_role_router.py`, Analysis and Planning agents.

---

## Inputs / Dependencies

* Depends on: F2-T1
* Requires access to: model schemas, model client, role router

---

## Output / Deliverable

Primary LLM output contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration llm-review
Story Points: 3
Priority: High
Parent: DEMO3-F2-STORY

[TASK] Define reviewer LLM role — F2 review chain

---

## Objective

Define reviewer LLM validation over deterministic artifacts and primary LLM outputs so supported model-required artifacts are reviewed by another model.

---

## Steps / Subtasks

1. Define reviewer inputs: deterministic artifact, primary reasoning, draft artifact, checksums, and profile context.
2. Define review dimensions: factual grounding, completeness, risk, stale input, profile fit, and downstream readiness.
3. Define reviewer outputs: accept, reject, request revision, failed closed, notes, confidence.
4. Define model-role separation requirements for primary and reviewer.
5. List files to inspect: `v2_reviewer_service.py`, `v2_model_role_router.py`, `v2_model_schemas.py`, reviewer repositories.

---

## Inputs / Dependencies

* Depends on: F2-T1, F2-T2
* Requires access to: reviewer service, model role router, model schemas

---

## Output / Deliverable

Reviewer contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration llm-review
Story Points: 3
Priority: High
Parent: DEMO3-F2-STORY

[TASK] Define reviewer decisions — F2 review chain

---

## Objective

Define reviewer decision semantics and how they control checkpoint acceptance, revision, and fail-closed behavior.

---

## Steps / Subtasks

1. Define accept behavior and required persisted metadata.
2. Define reject behavior and downstream blocking.
3. Define request revision behavior with previous artifact lineage.
4. Define stale, malformed, missing, and failed reviewer outcomes as fail-closed.
5. List files to inspect: `v2_reviewer_service.py`, `v2_phase_gate_service.py`, `v2_gate_action_service.py`, `artifact_revision.py`.

---

## Inputs / Dependencies

* Depends on: F2-T3
* Requires access to: reviewer service, gate services, artifact revision schema

---

## Output / Deliverable

Reviewer decision matrix.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration llm-review
Story Points: 2
Priority: High
Parent: DEMO3-F2-STORY

[TASK] Define final Markdown artifact schema — F2 review chain

---

## Objective

Define the final reviewed Markdown artifact consumed by checkpoints and downstream agents.

---

## Steps / Subtasks

1. Define required sections: summary, inputs used, deterministic findings, file names and file paths, primary reasoning, reviewer notes, risks, confidence, recommended next step, metadata.
2. Define machine-readable metadata block and checksum fields.
3. Define artifact naming and revision lineage.
4. Define display/download behavior for final Markdown.
5. List files to inspect: `v2_gate_artifact_resolver.py`, `artifact_revision.py`, SQLite repositories, FastAPI app.

---

## Inputs / Dependencies

* Depends on: F2-T1, F2-T2, F2-T3, F2-T4
* Requires access to: artifact resolver, artifact schema, persistence

---

## Output / Deliverable

Final Markdown schema.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration artifacts
Story Points: 3
Priority: High
Parent: DEMO3-F2-STORY

[TASK] Define retry/revision behavior — F2 review chain

---

## Objective

Define how rejected or change-requested model outputs produce new primary/reviewer artifacts without losing lineage.

---

## Steps / Subtasks

1. Define revision triggers from reviewer rejection and user modification request.
2. Define how comments, prior reasoning, reviewer notes, and artifact checksums enter the new context.
3. Define new artifact revision IDs and lineage links.
4. Define retry limits and failure reporting.
5. List files to inspect: `artifact_revision.py`, `v2_gate_action_service.py`, `v2_reviewer_service.py`, SQLite repositories.

---

## Inputs / Dependencies

* Depends on: F2-T4, DEMO3-F1-STORY
* Requires access to: artifact revisions, gate action service, reviewer service

---

## Output / Deliverable

Revision behavior spec.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration artifacts
Story Points: 2
Priority: High
Parent: DEMO3-F2-STORY

[TASK] Define metadata and checksum binding — F2 review chain

---

## Objective

Define metadata and checksum binding so model-required artifacts are traceable, immutable, and safe to consume.

---

## Steps / Subtasks

1. Define source artifact checksum, prompt/context checksum, primary output checksum, reviewer output checksum, and final artifact checksum.
2. Define persisted model role, artifact type, revision, and decision metadata without exposing runtime provider internals.
3. Define stale checksum rejection behavior.
4. Define metadata round-trip tests.
5. List files to inspect: `artifact_revision.py`, `v2_reviewer_service.py`, `v2_gate_artifact_resolver.py`, SQLite repositories.

---

## Inputs / Dependencies

* Depends on: F2-T1, F2-T2, F2-T3, F2-T5
* Requires access to: artifact schema, reviewer service, persistence

---

## Output / Deliverable

Metadata/checksum spec.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration artifacts
Story Points: 3
Priority: High
Parent: DEMO3-F2-STORY

[TASK] Define reviewer-required tests — F2 review chain

---

## Objective

Define focused tests proving reviewer LLM validation is mandatory for supported model-required Analysis and Planning outputs.

---

## Steps / Subtasks

1. Define missing reviewer fail-closed test.
2. Define stale reviewer checksum fail-closed test.
3. Define rejected reviewer blocks checkpoint acceptance test.
4. Define final Markdown schema and downstream input tests.
5. List files to inspect: tests for reviewer service, model schemas, artifact revisions, Analysis and Planning agents.

---

## Inputs / Dependencies

* Depends on: F2-T1, F2-T2, F2-T3, F2-T4, F2-T5, F2-T7
* Requires access to: focused test locations for reviewer, artifacts, and agents

---

## Output / Deliverable

Reviewer-required test plan.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration llm-review
Story Points: 2
Priority: High
Parent: DEMO3-F2-STORY
