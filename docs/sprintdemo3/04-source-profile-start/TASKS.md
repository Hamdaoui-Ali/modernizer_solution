[TASK] Define source-profile detection artifact — F4 current-state start

---

## Objective

Define the artifact emitted by Analysis to explain the application's current modernization state.

---

## Steps / Subtasks

1. Define detected source profile, evidence, file signals, confidence, and uncertainty fields.
2. Define checksum binding for detected evidence and source files inspected.
3. Define how detection appears in final Analysis Markdown.
4. Define fixture tests for already-modernized applications.
5. List files to inspect: `migration_factory/agents/analysis_agent/`, `profile_reader.py`, `migration_factory/profiles/`.

---

## Inputs / Dependencies

* Depends on: DEMO3-F4-STORY, DEMO3-F2-STORY
* Requires access to: Analysis agent, profile reader, profile definitions

---

## Output / Deliverable

Source-profile detection schema.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 3
Priority: High
Parent: DEMO3-F4-STORY

[TASK] Define manual source-profile override action — F4 current-state start

---

## Objective

Define a governed user action that overrides detected source profile with validation and an audit trail.

---

## Steps / Subtasks

1. Define override fields: checkpoint ID, detected profile, requested source profile, reason, comments, artifact checksum.
2. Define validation and rejection behavior for unsupported overrides.
3. Define persistence of override reason and user decision.
4. Define tests for required comments and checksum binding.
5. List files to inspect: `v2_gate_action_service.py`, FastAPI app, `run_configuration.py`, phase gate schema.

---

## Inputs / Dependencies

* Depends on: F4-T1, DEMO3-F1-STORY, DEMO3-F3-STORY
* Requires access to: gate action service, API routes, run configuration

---

## Output / Deliverable

Override action contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 2
Priority: High
Parent: DEMO3-F4-STORY

[TASK] Define skipped-stage ledger — F4 current-state start

---

## Objective

Define how skipped older stages are recorded and explained when the app starts from its current profile.

---

## Steps / Subtasks

1. Define skipped stage ID, reason, source profile, target profile, evidence ref, and checksum fields.
2. Define how skipped stages appear in events, checkpoints, and final Markdown artifacts.
3. Define persistence and retrieval behavior.
4. Define skipped-stage ledger tests.
5. List files to inspect: `v2_stage_progression.py`, domain entities, SQLite repositories, artifact revision schema.

---

## Inputs / Dependencies

* Depends on: F4-T1, DEMO3-F3-STORY
* Requires access to: stage progression, persistence, artifact schemas

---

## Output / Deliverable

Skipped-stage ledger schema.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 2
Priority: High
Parent: DEMO3-F4-STORY

[TASK] Define profile pair validation — F4 current-state start

---

## Objective

Reuse F3 validation for detected or overridden source profile and selected target profile.

---

## Steps / Subtasks

1. Define validation using detected source profile.
2. Define validation using manually overridden source profile.
3. Define incompatible pair rejection and user-visible explanation.
4. Define already-modernized route tests.
5. List files to inspect: `v2_stage_progression.py`, `run_configuration.py`, `pipeline_definition.py`, `profile_reader.py`, Planning agent.

---

## Inputs / Dependencies

* Depends on: F4-T1, F4-T2, DEMO3-F3-STORY
* Requires access to: profile validation, stage progression, planning agent

---

## Output / Deliverable

Profile pair validation behavior.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 2
Priority: High
Parent: DEMO3-F4-STORY

[TASK] Define resume-from-checkpoint behavior — F4 current-state start

---

## Objective

Define resume behavior that respects current source profile, target profile, skipped stages, and accepted artifact checksums.

---

## Steps / Subtasks

1. Define resume compatibility checks for source profile, target profile, stage route, and checkpoint artifact checksums.
2. Define behavior when source/target profile changed after checkpoint creation.
3. Define skipped-stage preservation on resume.
4. Define idempotent resume and stale checkpoint rejection tests.
5. List files to inspect: `v2_stage_progression.py`, `v2_orchestrator_runner.py`, `run_configuration.py`, SQLite repositories.

---

## Inputs / Dependencies

* Depends on: F4-T3, F4-T4, DEMO3-F1-STORY
* Requires access to: stage progression, runner, run configuration, persistence

---

## Output / Deliverable

Resume-from-checkpoint spec.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 3
Priority: High
Parent: DEMO3-F4-STORY

[TASK] Define already-modernized app tests — F4 current-state start

---

## Objective

Define tests proving already-modernized apps skip older stages and follow only the route needed for the selected target profile.

---

## Steps / Subtasks

1. Define fixture for app already on Spring Boot 3 targeting Spring Boot 4.
2. Define fixture for app already at target profile.
3. Define assertions for skipped-stage ledger and checkpoint metadata.
4. Define assertions that old stages are not executed after resume.
5. List files to inspect: Analysis fixtures, stage progression tests, runner tests, profile tests.

---

## Inputs / Dependencies

* Depends on: F4-T1, F4-T3, F4-T4, F4-T5
* Requires access to: focused tests for analysis, profile routing, and stage progression

---

## Output / Deliverable

Already-modernized app test plan.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 2
Priority: High
Parent: DEMO3-F4-STORY
