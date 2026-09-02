[STORY] Build/Test Repair Agent review loop

As a migration operator,
I want a Build/Test Repair Agent to analyze build and test failures, generate a reviewed proposed diff, and wait for my decision,
So that risky repairs are accurate, reviewed by another model, explained clearly, and applied only after explicit approval.

---

## Acceptance Criteria

* [ ] Given Build Agent fails, when failure handling begins, then the backend captures build logs, compiler output, changed files, repo state, profiles, prior artifacts, and checksums.
* [ ] Given Test Agent fails, when failure handling begins, then the backend captures test logs, test output, changed files, repo state, profiles, prior artifacts, and checksums.
* [ ] Given failure evidence is captured, when the Repair Agent runs, then the Primary Repair LLM proposes root cause, fix strategy, and exact diff.
* [ ] Given a proposed diff exists, when reviewer validation runs, then the Reviewer LLM reviews reasoning, changed files, exact diff, target-profile fit, risks, and policy concerns.
* [ ] Given the reviewer accepts a proposal, when the backend stores it, then the proposed diff and reviewer notes are immutable and checksum-bound.
* [ ] Given the user views a repair proposal, when the UI/API presents it, then error summary, root cause, changed files, proposed diff, explanation, risks, confidence, reviewer notes, approve/reject/request-another-review controls, and comment input are available.
* [ ] Given the user rejects a repair proposal, when the decision is persisted, then no patch is applied, rejection reason is stored, and artifacts remain downloadable.
* [ ] Given the user requests another review with comments, when the Repair Agent reruns, then previous context, proposed diff, reasoning, reviewer notes, user comments, current repo state, and checksums are included.
* [ ] Given the user approves a repair proposal, when the backend applies it, then only the exact approved reviewed diff is applied after checksum and policy validation.
* [ ] Given an approved diff is applied, when validation continues, then the backend reruns the Build Agent or Test Agent based on failure source and records proof or starts another Repair Agent cycle.

---

## Scope

**In scope:** Build/test failure evidence capture, Repair Agent input context, deterministic failure artifact, Primary Repair LLM role, Reviewer LLM repair role, proposed diff artifact, policy validation, user decisions, request-another-review loop, exact-diff apply behavior, build/test rerun behavior, repeated failure behavior, rollback/proof behavior, UI/API presentation contract, and F5 test matrix.
**Out of scope:** Autonomous repair execution, LLM-selected commands, LLM-selected sandbox or filesystem targets, unreviewed diff application, frontend implementation in this docs task, or making Stage 4/Jackson the product center.

---

## Technical Notes

* Agent files to inspect: `migration_factory/agents/build_agent/`, `migration_factory/agents/test_agent/`.
* Control Tower files to inspect: `migration_factory/control_tower/application/v2_repair_flow.py`, `migration_factory/control_tower/application/v2_repair_gate_service.py`, `migration_factory/control_tower/application/v2_reviewer_service.py`, `migration_factory/control_tower/application/v2_model_role_router.py`, `migration_factory/control_tower/application/v2_assistant_model_client.py`, `migration_factory/control_tower/application/v2_model_schemas.py`, `migration_factory/control_tower/application/v2_orchestrator_runner.py`, `migration_factory/control_tower/application/v2_stage_progression.py`.
* Schema/persistence/API files to inspect: `migration_factory/control_tower/schemas/artifact_revision.py`, `migration_factory/control_tower/schemas/phase_gate.py`, `migration_factory/control_tower/domain/entities.py`, `migration_factory/control_tower/infrastructure/sqlite/`, `migration_factory/control_tower/adapters/fastapi/app.py`.
* Repair loop files to inspect: `migration_factory/repair_loop/evidence_collector.py`, `migration_factory/repair_loop/rule_registry.py`, `migration_factory/repair_loop/patch_gate.py`, `migration_factory/repair_loop/patch_apply.py`, `migration_factory/repair_loop/validation_runner.py`.
* OpenRewrite/Jackson may be a backend-allowlisted repair strategy, but only as one F5 proof scenario after review, approval, checksum binding, and policy validation.

---

## Definition of Done

* [ ] Code reviewed & merged
* [ ] Unit tests passing
* [ ] AC validated by PO
* [ ] No blocking issues

---

Labels: repair-agent llm-review sprint-DEMO3
Story Points: 13
Priority: High
Epic Link: DEMO3 - Controlled Migration Pipeline and Build/Test Repair Agent Review
