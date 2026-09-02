# F3 - Target profile control

F3 lets the migration operator select the migration target profile and ensures the backend stops there.

## Product Goal

Validate `source_profile` and `target_profile`, derive required stages only, persist target metadata, and prevent migration beyond the selected target.

## Backlog Docs

- [Story](STORY.md)
- [Tasks](TASKS.md)
- [Sprint backlog](../BACKLOG.md)

## Example

```text
source_profile = spring-boot-2
target_profile = spring-boot-3
-> migrate to Spring Boot 3
-> stop at Spring Boot 3
-> do not continue to Spring Boot 4
```

## Files To Inspect Before Implementation

- `migration_factory/control_tower/application/v2_stage_progression.py`
- `migration_factory/control_tower/schemas/run_configuration.py`
- `migration_factory/control_tower/schemas/pipeline_definition.py`
- `migration_factory/profiles/`
- `migration_factory/profile_reader.py`
- `migration_factory/agents/planning_agent/`
- `migration_factory/control_tower/adapters/fastapi/app.py`
