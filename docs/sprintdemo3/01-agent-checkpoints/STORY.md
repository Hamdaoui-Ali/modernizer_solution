[STORY] Agent checkpoints and user decisions

As a migration operator,
I want the pipeline to stop after Analysis and Planning and expose safe user decisions,
So that I can review important outputs before downstream transformation/build/test steps continue.

---

## Acceptance Criteria

* [ ] Given Analysis completes with a reviewed artifact, when the pipeline reaches the Analysis checkpoint, then the backend stops and waits for a user decision.
* [ ] Given Planning completes with a reviewed artifact, when the pipeline reaches the Planning checkpoint, then the backend stops and waits for a user decision.
* [ ] Given a user continues from a checkpoint, when the decision is submitted, then the backend validates the checkpoint ID, artifact refs, checksums, and allowed next stage.
* [ ] Given a user requests modification, when comments are submitted, then the backend persists the comments and binds them to the relevant artifact revision.
* [ ] Given risk, build failure, test failure, target reached, stale artifact, reviewer failure, or approval required occurs, when stage progression evaluates next action, then the system stops instead of auto-continuing.
* [ ] Given artifact preview or download is requested, when the backend resolves the artifact, then it returns artifact refs/checksum-bound content without exposing raw sandbox paths.
* [ ] Given a checkpoint is resumed, when the stored state is stale, foreign, or profile-incompatible, then resume is rejected.

---

## Scope

**In scope:** Checkpoint state model, user decision contract, Analysis checkpoint, Planning checkpoint, safe auto-continue rules, stop-condition matrix, artifact preview/download behavior, and resume behavior.
**Out of scope:** Building frontend screens, implementing F5 repair behavior, accepting user-supplied paths/commands, or creating a duplicate checkpoint system.

---

## Technical Notes

* Files/services to inspect: `migration_factory/control_tower/application/v2_stage_progression.py`, `migration_factory/control_tower/application/v2_orchestrator_runner.py`, `migration_factory/control_tower/application/v2_gate_action_service.py`, `migration_factory/control_tower/application/v2_phase_gate_service.py`.
* Schemas/persistence to inspect: `migration_factory/control_tower/schemas/phase_gate.py`, `migration_factory/control_tower/schemas/run_configuration.py`, `migration_factory/control_tower/domain/entities.py`, `migration_factory/control_tower/infrastructure/sqlite/`.
* Agent/API files to inspect: `migration_factory/control_tower/adapters/fastapi/app.py`, `migration_factory/agents/analysis_agent/`, `migration_factory/agents/planning_agent/`, `migration_factory/agents/build_agent/`, `migration_factory/agents/test_agent/`.
* Reuse existing V2 stage progression, gates, artifact revision, repositories, and unit-of-work concepts.

---

## Definition of Done

* [ ] Code reviewed & merged
* [ ] Unit tests passing
* [ ] AC validated by PO
* [ ] No blocking issues

---

Labels: checkpoints governance sprint-DEMO3
Story Points: 13
Priority: High
Epic Link: DEMO3 - Controlled Migration Pipeline and Build/Test Repair Agent Review
