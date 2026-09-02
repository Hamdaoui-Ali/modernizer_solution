[STORY] Pre-feature codebase cleanup

As a platform engineer,
I want to clean and quarantine old workflow paths before implementation,
So that the product workflow is backend/API-controlled, auditable, and not dependent on Copilot, TUI, dead CLI commands, or duplicate orchestration.

---

## Acceptance Criteria

* [ ] Given the repository contains Copilot-related modules, when F0 inventory is complete, then every product-reachable and legacy-only Copilot path is classified.
* [ ] Given the product workflow uses the FastAPI backend, when cleanup decisions are defined, then no DEMO3 product path can invoke Copilot.
* [ ] Given TUI and CLI entrypoints exist, when runtime paths are inventoried, then product workflow paths and non-product compatibility paths are separated.
* [ ] Given duplicate orchestration concepts exist, when F0 analysis is complete, then reuse points and duplication risks are documented before implementation.
* [ ] Given public product contracts are reviewed, when forbidden fields are found, then `sandbox_path`, argv, env, raw commands, filesystem targets, provider, endpoint, deployment, and env refs are listed for removal or quarantine.
* [ ] Given stale docs or labels imply Copilot/TUI product behavior, when terminology cleanup is planned, then DEMO3 docs describe only the backend/API-governed workflow.
* [ ] Given F0 cleanup decisions are complete, when the cleanup report is produced, then removed, quarantined, retained, and follow-up items are listed.

---

## Scope

**In scope:** Inventory and quarantine planning for Copilot, TUI, CLI, duplicate orchestration, unused modules/dependencies, stale terminology, and forbidden public contract fields.
**Out of scope:** Implementing F1-F5, deleting compatibility code without an explicit cleanup decision, modifying runtime code in this docs-only task, adding frontend behavior, or changing migration_factory code.

---

## Technical Notes

* Files/services to inspect: `migration_factory/orchestrator/`, `migration_factory/copilot_assist/`, `migration_factory/copilot_repair/`, `migration_factory/final_report/`, `migration_factory/tui/`, `migration_factory/cli.py`.
* Control Tower files to inspect: `migration_factory/control_tower/adapters/fastapi/app.py`, `migration_factory/control_tower/application/v2_orchestrator_runner.py`, `migration_factory/control_tower/application/v2_settings.py`, `migration_factory/control_tower/application/v2_model_role_router.py`, `migration_factory/control_tower/application/v2_assistant_model_client.py`.
* Agent files to inspect: `migration_factory/agents/analysis_agent/`, `migration_factory/agents/planning_agent/`.
* F0 must preserve the product invariant that the chatbot interprets, the human decides, and the backend validates, persists, executes, and proves.

---

## Definition of Done

* [ ] Code reviewed & merged
* [ ] Unit tests passing
* [ ] AC validated by PO
* [ ] No blocking issues

---

Labels: cleanup governance sprint-DEMO3
Story Points: 8
Priority: Highest
Epic Link: DEMO3 - Controlled Migration Pipeline and Build/Test Repair Agent Review
