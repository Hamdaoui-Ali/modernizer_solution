# F1 - Agent checkpoints and user decisions

F1 makes Analysis and Planning governed checkpoints with safe user decisions.

## Product Goal

Stop after Analysis and Planning so the migration operator can review reviewed artifacts, continue, stop, request modification, preview/download artifacts, or resume safely.

## Backlog Docs

- [Story](STORY.md)
- [Tasks](TASKS.md)
- [Sprint backlog](../BACKLOG.md)

## Files To Inspect Before Implementation

- `migration_factory/control_tower/application/v2_stage_progression.py`
- `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- `migration_factory/control_tower/application/v2_gate_action_service.py`
- `migration_factory/control_tower/application/v2_phase_gate_service.py`
- `migration_factory/control_tower/schemas/phase_gate.py`
- `migration_factory/control_tower/schemas/run_configuration.py`
- `migration_factory/control_tower/domain/entities.py`
- `migration_factory/control_tower/infrastructure/sqlite/`
- `migration_factory/control_tower/adapters/fastapi/app.py`
- `migration_factory/agents/analysis_agent/`
- `migration_factory/agents/planning_agent/`
- `migration_factory/agents/build_agent/`
- `migration_factory/agents/test_agent/`

## Scope Boundary

F1 reuses existing V2 stage progression, gates, artifacts, repositories, and runner concepts. It must not accept user-supplied paths, argv, env, raw commands, or filesystem targets.
