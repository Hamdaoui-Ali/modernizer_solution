[TASK] Inventory Copilot runtime paths — F0 cleanup

---

## Objective

Find every Copilot-related path so product-reachable behavior can be separated from legacy-only compatibility code.

---

## Steps / Subtasks

1. Search Copilot imports, adapters, schemas, report generators, and runtime calls.
2. Classify each path as product-reachable, test-only, legacy-only, or unknown.
3. Trace references from FastAPI, orchestrator, assistant model client, and final report paths.
4. Record any public docs or contracts that imply Copilot participates in DEMO3.
5. List files to inspect: `migration_factory/copilot_assist/`, `migration_factory/copilot_repair/`, `migration_factory/final_report/`, `migration_factory/orchestrator/`, `migration_factory/control_tower/adapters/fastapi/app.py`.

---

## Inputs / Dependencies

* Depends on: DEMO3-F0-STORY
* Requires access to: repository search, `migration_factory/`, sprint docs

---

## Output / Deliverable

Copilot runtime inventory with product-reachable and legacy-only paths separated.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: tech-debt backend governance
Story Points: 2
Priority: Highest
Parent: DEMO3-F0-STORY

[TASK] Quarantine Copilot from product runtime — F0 cleanup

---

## Objective

Define how DEMO3 product paths fail closed or route away from Copilot so Copilot cannot drive product migration behavior.

---

## Steps / Subtasks

1. Use the Copilot runtime inventory to identify reachable product paths.
2. Define quarantine, removal, or compatibility-only decisions for each path.
3. Identify tests needed to prove product APIs and orchestrator paths cannot invoke Copilot.
4. Define documentation wording that marks retained Copilot code as outside DEMO3 product runtime.
5. List files to inspect: `migration_factory/control_tower/application/v2_orchestrator_runner.py`, `migration_factory/control_tower/application/v2_assistant_model_client.py`, `migration_factory/control_tower/application/v2_model_role_router.py`, `migration_factory/orchestrator/`.

---

## Inputs / Dependencies

* Depends on: F0-T1
* Requires access to: Copilot runtime inventory, Control Tower application services

---

## Output / Deliverable

Copilot quarantine decision with tests and documentation follow-ups.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: tech-debt backend governance
Story Points: 3
Priority: Highest
Parent: DEMO3-F0-STORY

[TASK] Inventory TUI and CLI runtime paths — F0 cleanup

---

## Objective

Find TUI entrypoints, CLI commands, debug commands, product commands, and mutation paths before cleanup decisions are made.

---

## Steps / Subtasks

1. Search all TUI modules and CLI command definitions.
2. Identify commands that start, resume, repair, mutate, or execute workflows.
3. Classify each command as product-reachable, developer-only, legacy-only, or unknown.
4. Identify command execution paths that could bypass backend governance.
5. List files to inspect: `migration_factory/tui/`, `migration_factory/cli.py`, `migration_factory/control_tower/adapters/fastapi/app.py`.

---

## Inputs / Dependencies

* Depends on: DEMO3-F0-STORY
* Requires access to: CLI/TUI modules and product API routes

---

## Output / Deliverable

TUI/CLI runtime inventory with product and non-product paths classified.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: tech-debt backend governance
Story Points: 2
Priority: Highest
Parent: DEMO3-F0-STORY

[TASK] Quarantine TUI from product workflow — F0 cleanup

---

## Objective

Define how TUI paths are excluded from DEMO3 product operation while preserving explicitly retained compatibility behavior.

---

## Steps / Subtasks

1. Use the TUI/CLI inventory to identify reachable product workflow paths.
2. Mark TUI paths as removed, quarantined, developer-only, or retained compatibility-only.
3. Identify docs that must stop presenting TUI as a DEMO3 product surface.
4. Define tests or checks proving the FastAPI backend/UI path is the product control surface.
5. List files to inspect: `migration_factory/tui/`, `migration_factory/cli.py`, `docs/`.

---

## Inputs / Dependencies

* Depends on: F0-T3
* Requires access to: TUI/CLI runtime inventory and sprint/product docs

---

## Output / Deliverable

TUI quarantine decision and documentation/test follow-ups.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: tech-debt backend governance
Story Points: 2
Priority: Highest
Parent: DEMO3-F0-STORY

[TASK] Identify duplicate orchestration logic — F0 cleanup

---

## Objective

Compare orchestration concepts so F1-F5 reuse V2 stage progression, runner, gates, repair flow, and persistence instead of duplicating them.

---

## Steps / Subtasks

1. Compare old orchestrator modules with V2 stage progression and runner.
2. Compare gate services, repair flow, assistant routing, and artifact resolution responsibilities.
3. Identify duplicate stage ordering, command construction, repair, validation, rollback, or ledger concepts.
4. Recommend reuse, wrapper, adapter, or quarantine decisions.
5. List files to inspect: `migration_factory/orchestrator/`, `migration_factory/control_tower/application/v2_stage_progression.py`, `migration_factory/control_tower/application/v2_orchestrator_runner.py`, `migration_factory/control_tower/application/v2_gate_action_service.py`, `migration_factory/control_tower/application/v2_repair_flow.py`.

---

## Inputs / Dependencies

* Depends on: DEMO3-F0-STORY
* Requires access to: orchestration and Control Tower application services

---

## Output / Deliverable

Duplicate orchestration report with reuse points and duplication risks.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: tech-debt backend governance
Story Points: 3
Priority: Highest
Parent: DEMO3-F0-STORY

[TASK] Identify unused modules and dependencies — F0 cleanup

---

## Objective

Identify modules and dependencies that are unused, legacy-only, or quarantine candidates before implementation starts.

---

## Steps / Subtasks

1. Search package metadata, imports, tests, and runtime entrypoints.
2. Classify modules as active product, test-only, legacy compatibility, unused, or unknown.
3. Separate safe removal candidates from compatibility-retained code.
4. Identify dependency cleanup tests needed before later removal.
5. List files to inspect: package metadata, `migration_factory/`, `tests/`.

---

## Inputs / Dependencies

* Depends on: F0-T1, F0-T3, F0-T5
* Requires access to: repository search, package metadata, test references

---

## Output / Deliverable

Unused module/dependency report with removal and retention candidates.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: tech-debt infra governance
Story Points: 2
Priority: Highest
Parent: DEMO3-F0-STORY

[TASK] Clean stale terminology — F0 cleanup

---

## Objective

Identify and plan cleanup for stale terms that imply Copilot, TUI, raw command, provider selection, or old Stage 4-centered product behavior.

---

## Steps / Subtasks

1. Search docs, API schemas, UI-facing text, and product descriptions for stale terms.
2. Identify language that exposes provider/model/deployment/env refs as product concepts.
3. Identify language that presents Stage 4/Jackson as the DEMO3 product center.
4. Define replacement terminology around backend-governed F0-F5 workflow.
5. List files to inspect: `docs/`, `migration_factory/control_tower/adapters/fastapi/app.py`, public schemas.

---

## Inputs / Dependencies

* Depends on: DEMO3-F0-STORY
* Requires access to: sprint docs, product docs, API schemas

---

## Output / Deliverable

Terminology cleanup list with replacement wording.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: tech-debt documentation governance
Story Points: 2
Priority: Highest
Parent: DEMO3-F0-STORY

[TASK] Generate cleanup report — F0 cleanup

---

## Objective

Produce the F0 cleanup report so implementation can proceed from an audited product-runtime baseline.

---

## Steps / Subtasks

1. Merge Copilot, TUI/CLI, duplicate orchestration, unused module, and terminology findings.
2. Summarize removed, quarantined, retained, and follow-up candidates.
3. Include forbidden public contract fields found during inspection.
4. Link recommended focused tests for implementation.
5. List files to inspect: all F0 inventory outputs and affected docs.

---

## Inputs / Dependencies

* Depends on: F0-T1, F0-T2, F0-T3, F0-T4, F0-T5, F0-T6, F0-T7
* Requires access to: all F0 inventory deliverables

---

## Output / Deliverable

F0 cleanup report.

---

## Definition of Done

* [ ] Deliverable produced and verified
* [ ] Linked to parent story/epic
* [ ] No regressions introduced

---

Labels: tech-debt documentation governance
Story Points: 2
Priority: Highest
Parent: DEMO3-F0-STORY
