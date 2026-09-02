# Planning Agent

Responsible for consuming the analysis output and producing the migration plan.

To be defined with the team:
- Inputs
- Outputs
- Migration unit format
- Dependencies on Analysis Agent
- Model/API usage
- Definition of Done

## Planning Assist Foundation

- Optional/fail-open only.
- No live Copilot SDK/MCP/network calls.
- Default config disables assist.

Manual run after Analysis Agent:

```bash
PYTHONPATH=. python -m migration_factory.agents.planning_agent.runner \
  --run-id <run_id> \
  --modernized <modernized_app_path> \
  --legacy <legacy_app_path> \
  --ai-hub <modernizer-solution-ai-hub_path> \
  --profile <profile_id>
```

Planning reads Analysis artifacts from `<modernized>/.migration/runs/<run_id>/analysis`
and writes Planning artifacts to `<modernized>/.migration/runs/<run_id>/planning`.

Manual validation:

1. `python -m compileall migration_factory`
2. `PYTHONPATH=. python -m migration_factory.agents.planning_agent.runner --run-id r1 --modernized /path/to/modernized --legacy /path/to/legacy --ai-hub /path/to/modernizer-solution-ai-hub --profile java17`
3. Confirm output includes `planning_assist_status='SKIPPED'` by default.
