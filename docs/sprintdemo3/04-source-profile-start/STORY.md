[STORY] Start from current app state

As a migration operator,
I want the system to start from the application's current modernization state,
So that already-modernized apps skip older stages and only run the stages required for the chosen target profile.

---

## Acceptance Criteria

* [ ] Given Analysis inspects the application, when source-profile detection runs, then it emits a source profile, evidence, confidence, and uncertainty notes.
* [ ] Given detected source profile is wrong or uncertain, when the user submits a manual override with comments, then the backend validates and persists the override reason.
* [ ] Given the application is already modernized past older stages, when stage progression is planned, then older stages are skipped.
* [ ] Given stages are skipped, when artifacts and events are stored, then the skipped-stage ledger records what was skipped and why.
* [ ] Given detected or overridden source profile and selected target profile exist, when validation runs, then incompatible profile pairs are rejected.
* [ ] Given an accepted checkpoint exists, when resume is requested, then source/target profile compatibility and artifact checksums are validated before continuation.
* [ ] Given an already-modernized app targets a later profile, when the pipeline runs, then it starts from the current profile path rather than forcing old migration stages.

---

## Scope

**In scope:** Source-profile detection artifact, manual source-profile override action, skipped-stage ledger, profile pair validation, resume-from-checkpoint behavior, and already-modernized app tests.
**Out of scope:** Arbitrary checkpoint import, manual editing of backend-owned commands, user-supplied filesystem targets, or bypassing F3 profile validation.

---

## Technical Notes

* Files/services to inspect: `migration_factory/agents/analysis_agent/`, `migration_factory/profile_reader.py`, `migration_factory/profiles/`.
* Stage/config files to inspect: `migration_factory/control_tower/application/v2_stage_progression.py`, `migration_factory/control_tower/application/v2_orchestrator_runner.py`, `migration_factory/control_tower/schemas/run_configuration.py`, `migration_factory/control_tower/schemas/pipeline_definition.py`.
* Persistence/API files to inspect: `migration_factory/control_tower/infrastructure/sqlite/`, `migration_factory/control_tower/adapters/fastapi/app.py`.
* Resume must be backend-owned, checksum-bound, and profile-compatible.

---

## Definition of Done

* [ ] Code reviewed & merged
* [ ] Unit tests passing
* [ ] AC validated by PO
* [ ] No blocking issues

---

Labels: source-profile migration sprint-DEMO3
Story Points: 8
Priority: High
Epic Link: DEMO3 - Controlled Migration Pipeline and Build/Test Repair Agent Review
