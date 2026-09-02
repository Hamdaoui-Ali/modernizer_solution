# F5 - Build/Test Repair Agent review loop

F5 is not a simple repair loop. It is a Build/Test Repair Agent feature with a Primary Repair LLM, Reviewer LLM, immutable proposed diff, and explicit user decision.

## Product Goal

When build or tests fail, capture deterministic failure evidence, generate a reviewed proposed diff, store it as an artifact, wait for user approve/reject/request-another-review, apply only the exact approved reviewed diff, rerun build/test, and record proof.

## Backlog Docs

- [Story](STORY.md)
- [Tasks](TASKS.md)
- [Sprint backlog](../BACKLOG.md)

## Required Flow

```text
Build/Test failure
-> deterministic failure artifact
-> Primary Repair LLM proposes root cause + fix + diff
-> Reviewer LLM reviews reasoning and exact diff
-> backend stores immutable proposal artifact
-> user approves / rejects / requests another review with comments
-> backend applies only exact approved reviewed diff
-> build/test reruns
-> proof or another Repair Agent cycle
```

Stage 4/Jackson and OpenRewrite are one backend-allowlisted proof scenario under this generic F5 flow.

## Files To Inspect Before Implementation

- `migration_factory/agents/build_agent/`
- `migration_factory/agents/test_agent/`
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/control_tower/application/v2_repair_gate_service.py`
- `migration_factory/control_tower/application/v2_reviewer_service.py`
- `migration_factory/control_tower/application/v2_model_role_router.py`
- `migration_factory/control_tower/application/v2_assistant_model_client.py`
- `migration_factory/control_tower/application/v2_model_schemas.py`
- `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- `migration_factory/control_tower/application/v2_stage_progression.py`
- `migration_factory/control_tower/schemas/artifact_revision.py`
- `migration_factory/control_tower/schemas/phase_gate.py`
- `migration_factory/control_tower/domain/entities.py`
- `migration_factory/control_tower/infrastructure/sqlite/`
- `migration_factory/control_tower/adapters/fastapi/app.py`
- `migration_factory/repair_loop/evidence_collector.py`
- `migration_factory/repair_loop/rule_registry.py`
- `migration_factory/repair_loop/patch_gate.py`
- `migration_factory/repair_loop/patch_apply.py`
- `migration_factory/repair_loop/validation_runner.py`
