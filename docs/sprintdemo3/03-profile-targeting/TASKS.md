[TASK] Define profile model — F3 profile control

---

## Objective

Define source and target profiles as backend-validated migration concepts rather than display labels.

---

## Steps / Subtasks

1. Define `source_profile`, `target_profile`, identifiers, display names, ordering, and compatibility metadata.
2. Define supported profile values and unknown profile behavior.
3. Define storage in run configuration and checkpoint metadata.
4. Define schema validation for API inputs.
5. List files to inspect: `run_configuration.py`, `pipeline_definition.py`, `migration_factory/profiles/`, `profile_reader.py`, Planning agent.

---

## Inputs / Dependencies

* Depends on: DEMO3-F3-STORY
* Requires access to: run configuration, profile reader, profile definitions

---

## Output / Deliverable

Profile model spec.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 2
Priority: High
Parent: DEMO3-F3-STORY

[TASK] Define profile validation — F3 profile control

---

## Objective

Define validation for source/target profile pairs so unsupported or unsafe routes fail before execution.

---

## Steps / Subtasks

1. Define valid, invalid, reversed, unsupported, and no-op profile pair behavior.
2. Define validation error payloads and user-visible messages.
3. Define where validation occurs in API and stage progression.
4. Define tests for invalid pair rejection.
5. List files to inspect: `v2_stage_progression.py`, `run_configuration.py`, `pipeline_definition.py`, `profile_reader.py`.

---

## Inputs / Dependencies

* Depends on: F3-T1
* Requires access to: profile model, stage progression, run configuration

---

## Output / Deliverable

Profile validation spec.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 2
Priority: High
Parent: DEMO3-F3-STORY

[TASK] Define stage/profile mapping — F3 profile control

---

## Objective

Define how profile gaps map to required stages and excluded stages without duplicating existing stage progression logic.

---

## Steps / Subtasks

1. Map supported source/target profile pairs to required stage sequences.
2. Define included and excluded stage artifact output.
3. Define how planning records profile-based route decisions.
4. Define route mapping tests.
5. List files to inspect: `pipeline_definition.py`, `v2_stage_progression.py`, Planning agent, `migration_factory/profiles/`.

---

## Inputs / Dependencies

* Depends on: F3-T1, F3-T2
* Requires access to: pipeline definitions, planning agent, stage progression

---

## Output / Deliverable

Stage/profile map.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 3
Priority: High
Parent: DEMO3-F3-STORY

[TASK] Define stop-at-target behavior — F3 profile control

---

## Objective

Define backend behavior that stops the pipeline as soon as the selected target profile is reached.

---

## Steps / Subtasks

1. Define target reached condition in stage progression.
2. Define terminal status and checkpoint/proof artifact after target reached.
3. Define prevention of higher-profile stages after selected target.
4. Define resume-after-target behavior.
5. List files to inspect: `v2_stage_progression.py`, `v2_orchestrator_runner.py`, `pipeline_definition.py`.

---

## Inputs / Dependencies

* Depends on: F3-T2, F3-T3
* Requires access to: stage progression, runner, pipeline definition

---

## Output / Deliverable

Stop-at-target policy.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 3
Priority: High
Parent: DEMO3-F3-STORY

[TASK] Define safe API fields — F3 profile control

---

## Objective

Define API fields for profile selection while excluding provider, runtime, path, command, and sandbox controls.

---

## Steps / Subtasks

1. Define allowed fields: `source_profile`, `target_profile`, validation status, included stages, excluded stages.
2. Define forbidden fields: provider, model, deployment, env ref, `sandbox_path`, argv, env, raw command, filesystem target.
3. Define API validation and error behavior.
4. Define redaction tests for public contracts.
5. List files to inspect: FastAPI app, `run_configuration.py`, public schemas.

---

## Inputs / Dependencies

* Depends on: F3-T1, F3-T2
* Requires access to: FastAPI routes and run configuration schema

---

## Output / Deliverable

API contract.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 2
Priority: High
Parent: DEMO3-F3-STORY

[TASK] Define artifact/checkpoint metadata — F3 profile control

---

## Objective

Define how source and target profile choices are persisted on artifacts and checkpoints.

---

## Steps / Subtasks

1. Define profile metadata fields for checkpoint and artifact revisions.
2. Define profile validation artifact contents.
3. Define included/excluded stage metadata.
4. Define persistence round-trip tests.
5. List files to inspect: `artifact_revision.py`, `phase_gate.py`, SQLite repositories, `v2_gate_artifact_resolver.py`.

---

## Inputs / Dependencies

* Depends on: F3-T1, F3-T2, F3-T3
* Requires access to: artifact/checkpoint schemas and persistence

---

## Output / Deliverable

Profile metadata spec.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 2
Priority: High
Parent: DEMO3-F3-STORY

[TASK] Define target-overshoot prevention tests — F3 profile control

---

## Objective

Define regression tests that prove the pipeline never runs beyond the selected target profile.

---

## Steps / Subtasks

1. Define Spring Boot 2 to Spring Boot 3 stop-at-target test.
2. Define resume-after-target does not overshoot test.
3. Define rejected higher-stage route test.
4. Define checkpoint metadata assertion for target profile.
5. List files to inspect: stage progression tests, runner tests, pipeline definition tests.

---

## Inputs / Dependencies

* Depends on: F3-T4, F3-T6
* Requires access to: focused stage progression and runner test locations

---

## Output / Deliverable

Overshoot test plan.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: backend migration profile-control
Story Points: 2
Priority: High
Parent: DEMO3-F3-STORY
