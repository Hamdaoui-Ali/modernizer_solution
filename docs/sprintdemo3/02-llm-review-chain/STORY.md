[STORY] Deterministic artifact + primary LLM + reviewer LLM

As a migration reviewer,
I want Analysis and Planning to produce deterministic evidence, primary LLM reasoning, reviewer LLM validation, and a final Markdown artifact,
So that downstream agents consume reviewed, auditable, checksum-bound outputs instead of raw or unreviewed model drafts.

---

## Acceptance Criteria

* [ ] Given Analysis requires model-written output, when Analysis runs, then a deterministic artifact is produced before primary LLM reasoning.
* [ ] Given Planning requires model-written output, when Planning runs, then a deterministic artifact is produced before primary LLM reasoning.
* [ ] Given a primary LLM output exists, when reviewer validation is required, then a reviewer LLM reviews the primary output and exact artifact checksum before acceptance.
* [ ] Given reviewer output is missing, stale, rejected, malformed, or failed, when checkpoint acceptance is attempted, then the backend fails closed.
* [ ] Given reviewer validation accepts the output, when the final artifact is stored, then the backend persists a checksum-bound Markdown artifact with required metadata.
* [ ] Given a downstream agent requests Analysis or Planning input, when artifact resolution occurs, then it receives final reviewed Markdown rather than raw primary LLM output.
* [ ] Given deterministic fallback exists, when a model-required reviewed artifact is expected, then fallback alone cannot satisfy the artifact contract.

---

## Scope

**In scope:** Deterministic artifact contract, primary LLM role, reviewer LLM role, reviewer decisions, final Markdown artifact schema, retry/revision behavior, metadata/checksum binding, and reviewer-required tests for Analysis and Planning.
**Out of scope:** Applying the chain to every later agent in the first slice, public model/provider selection, frontend implementation, or allowing reviewer optionality.

---

## Technical Notes

* Agent files to inspect: `migration_factory/agents/analysis_agent/`, `migration_factory/agents/planning_agent/`.
* Reviewer/model files to inspect: `migration_factory/control_tower/application/v2_reviewer_service.py`, `migration_factory/control_tower/application/v2_model_role_router.py`, `migration_factory/control_tower/application/v2_assistant_model_client.py`, `migration_factory/control_tower/application/v2_model_schemas.py`.
* Artifact/API files to inspect: `migration_factory/control_tower/application/v2_gate_artifact_resolver.py`, `migration_factory/control_tower/schemas/artifact_revision.py`, `migration_factory/control_tower/infrastructure/sqlite/`, `migration_factory/control_tower/adapters/fastapi/app.py`.
* Final Markdown must include summary, inputs used, deterministic findings, file names and file paths, primary LLM reasoning, reviewer LLM notes, risks, confidence, recommended next step, and machine-readable metadata.

---

## Definition of Done

* [ ] Code reviewed & merged
* [ ] Unit tests passing
* [ ] AC validated by PO
* [ ] No blocking issues

---

Labels: llm-review artifacts sprint-DEMO3
Story Points: 13
Priority: High
Epic Link: DEMO3 - Controlled Migration Pipeline and Build/Test Repair Agent Review
