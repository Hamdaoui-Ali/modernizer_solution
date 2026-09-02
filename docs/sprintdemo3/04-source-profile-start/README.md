# F4 - Start from current app state

F4 lets the system start from the application's actual modernization state instead of forcing older stages.

## Product Goal

Detect or confirm the current source profile, validate it against the selected target profile, record skipped stages, and resume from compatible checkpoints.

## Backlog Docs

- [Story](STORY.md)
- [Tasks](TASKS.md)
- [Sprint backlog](../BACKLOG.md)

## Example

```text
Application already on Spring Boot 3.
User chooses target Spring Boot 4.
System starts from the Spring Boot 3 path and records skipped older stages.
```

## Files To Inspect Before Implementation

- `migration_factory/agents/analysis_agent/`
- `migration_factory/profile_reader.py`
- `migration_factory/profiles/`
- `migration_factory/control_tower/application/v2_stage_progression.py`
- `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- `migration_factory/control_tower/schemas/run_configuration.py`
- `migration_factory/control_tower/schemas/pipeline_definition.py`
- `migration_factory/control_tower/infrastructure/sqlite/`
- `migration_factory/control_tower/adapters/fastapi/app.py`
