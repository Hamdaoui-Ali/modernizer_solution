# F2 - Deterministic artifact + primary LLM + reviewer LLM

F2 requires a model-reviews-model chain for supported model-required Analysis and Planning outputs.

## Product Goal

Produce deterministic evidence, primary LLM reasoning, reviewer LLM validation, and a final checksum-bound Markdown artifact before downstream agents consume Analysis or Planning output.

## Backlog Docs

- [Story](STORY.md)
- [Tasks](TASKS.md)
- [Sprint backlog](../BACKLOG.md)

## Required Chain

```text
deterministic artifact
-> primary LLM reasoning
-> reviewer LLM validation
-> final Markdown artifact
-> stored checkpoint / next agent input
```

Reviewer LLM is mandatory for supported model-required outputs. Raw primary LLM output must not be passed forward.

## Files To Inspect Before Implementation

- `migration_factory/agents/analysis_agent/`
- `migration_factory/agents/planning_agent/`
- `migration_factory/control_tower/application/v2_reviewer_service.py`
- `migration_factory/control_tower/application/v2_model_role_router.py`
- `migration_factory/control_tower/application/v2_assistant_model_client.py`
- `migration_factory/control_tower/application/v2_model_schemas.py`
- `migration_factory/control_tower/application/v2_gate_artifact_resolver.py`
- `migration_factory/control_tower/schemas/artifact_revision.py`
- `migration_factory/control_tower/infrastructure/sqlite/`
- `migration_factory/control_tower/adapters/fastapi/app.py`
