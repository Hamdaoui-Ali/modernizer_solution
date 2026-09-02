# F0 - Pre-feature codebase cleanup

F0 prepares the codebase before implementation by inventorying and quarantining old workflow paths.

## Product Goal

Clean and quarantine Copilot, TUI, dead CLI commands, duplicate orchestration, unused modules/dependencies, and stale terminology so DEMO3 is backend/API-controlled and auditable.

## Backlog Docs

- [Story](STORY.md)
- [Tasks](TASKS.md)
- [Sprint backlog](../BACKLOG.md)

## Files To Inspect Before Implementation

- `migration_factory/orchestrator/`
- `migration_factory/copilot_assist/`
- `migration_factory/copilot_repair/`
- `migration_factory/final_report/`
- `migration_factory/tui/`
- `migration_factory/cli.py`
- `migration_factory/control_tower/adapters/fastapi/app.py`
- `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- `migration_factory/control_tower/application/v2_settings.py`
- `migration_factory/control_tower/application/v2_model_role_router.py`
- `migration_factory/control_tower/application/v2_assistant_model_client.py`
- `migration_factory/agents/analysis_agent/`
- `migration_factory/agents/planning_agent/`

## Scope Boundary

F0 defines cleanup implementation work. This docs task does not modify runtime code, remove compatibility modules, or implement F1-F5.
