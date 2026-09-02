# Analysis Agent

Responsible for analyzing the legacy/target application and producing the initial application baseline.

## Planner handoff outputs

Required artifacts:
- `analysis_report.json`
- `dependency_graph.json`
- `test_inventory.json`
- `analysis_summary.md`

Optional artifacts:
- `config_inventory.json`
- `rewrite_preview.json`
- `rewrite_dry_run.patch`
- `rewrite_plugin_plan.json`
- `rewrite_impact_summary.json`
- `copilot_assist.json`

For artifact purposes, status expectations, and compatibility notes, see:
- `docs/analysis-planner-handoff.md`
