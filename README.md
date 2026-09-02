# AI Migration Factory

AI Migration Factory (AIMF) is a governed proof-of-concept migration runner for Java/Spring Boot applications. The current TEST branch supports a read-only assessment flow and a human-approved full sandbox migration flow for the POC stack.

The factory is migration-only. The legacy application is read-only input for analysis, planning, and assessment. Do not use this repo for feature development, legacy maintenance, production promotion, pull requests, or deployment automation.

## Current Scope

- Analyze a legacy Java/Spring Boot Maven app without changing its source.
- Produce migration planning, approval, assessment, and validation artifacts under the modernized app run directory.
- Pause for human approval before source-changing migration work.
- After approval, copy the legacy app into a sandbox under the run directory, apply approved OpenRewrite migration units there, run build validation, parse Surefire test reports, record timing, and write a final report.
- Optionally write a local Copilot advisory statement from deterministic final-report facts. This is a local template/stub artifact, not a live GitHub Copilot API integration.

## Supported POC Stack

The active AI Hub profile is `springboot-2.7-to-3.5-java17`.

- Source POC: Java 11, Spring Boot 2.7, Maven
- Target: Java 17, Spring Boot 3.5.14, Spring Framework 6.2.18, Maven
- OpenRewrite catalog: `modernizer-solution-ai-hub/catalogs/openrewrite/springboot-3.5-java17.yaml`
- Profile file: `modernizer-solution-ai-hub/profiles/springboot-2.7-to-3.5-java17.yaml`

The profile also allows Java 8 as source input, but the POC runbook below assumes Java 11 to Java 17.

## Architecture

- Analysis: scans the legacy app, Maven metadata, imports, config, tests, dependency tree, OpenRewrite dry-run artifacts, and read-only verification.
- Planning: builds deterministic migration plan artifacts, migration units, approval request, and plan validation outputs.
- Assessment: checks analysis and planning outputs, approval readiness, blockers, read-only status, and execution claims.
- Human Approval: interrupts orchestration until a human records `approved`, `rejected`, or `replan_required`.
- Transform: after approval, creates a sandbox workspace and applies approved migration units there.
- Build: validates migration units through the Build Agent. Source-changing units use strict validation such as `mvn clean test`.
- Test Agent: parses Surefire reports from the sandbox validation output and writes post-transform test artifacts.
- Timing: records phase and command durations under `performance/`.
- Final Report: writes final sandbox migration JSON and Markdown summaries only after successful full sandbox validation.
- Copilot Documentation Agent: after successful sandbox validation, reads deterministic run artifacts and writes advisory Markdown documentation under `final/copilot_docs/` with source-artifact traceability.
- Optional Copilot advisory: when `AI_MIGRATION_ENABLE_COPILOT_STATEMENT=true`, writes local advisory statement artifacts based on deterministic facts.

## Repository Layout

- `migration_factory/`: orchestration, agents, contracts, approval, assessment, transform, and report code.
- `migration_factory/orchestrator/`: LangGraph runner, resume command, preflight, phase services, artifact validation, timing, and summary writer.
- `migration_factory/agents/analysis_agent/`: read-only analysis scanner.
- `migration_factory/agents/planning_agent/`: planning and approval request generation.
- `migration_factory/agents/build_agent/`: Maven/Gradle build and startup/test validation.
- `migration_factory/agents/test_agent/`: Surefire parsing and post-transform test report writing.
- `migration_factory/agents/copilot_doc_agent/`: advisory documentation package generation from completed run artifacts.
- `migration_factory/approval/`: approval decision and approved plan lock CLI/helpers.
- `migration_factory/final_report/`: final migration report writer.
- `modernizer-solution-ai-hub/`: profiles, OpenRewrite catalogs, policies, and schemas.
- `docs/`: design and backlog notes, including [orchestrator design](docs/milestone-3-orchestrator-design.md).
- `tests/`: unit and orchestration tests that document implemented behavior.

## Prerequisites

- Python 3.10 or newer
- Java 17 on `PATH`
- Maven on `PATH`, or a Maven wrapper in the app
- Git on `PATH` for sandbox checkpointing when available
- A legacy app path that exists and contains the source application
- A modernized app path where `.migration/runs/<run_id>` can be created
- This repo checked out locally

Recommended environment variables:

- `PYTHONPATH=.` when running modules from the repo root
- `AI_MIGRATION_ENABLE_COPILOT_STATEMENT=true` only if you want the optional local final-report advisory statement

## Setup

Run from the repository root:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -e .[test]
```

Define run variables. Keep `RUN_ID` simple: no spaces.

```powershell
$RUN_ID = "shoppoc-full-orch-report-002"
$LEGACY_APP = "C:\Users\abdelilah.mortaki\Desktop\shoppoc-app"
$MODERNIZED_APP = "C:\Users\abdelilah.mortaki\Desktop\modernized-app"
$AI_HUB = "C:\Users\abdelilah.mortaki\Desktop\modernizer-solution\modernizer-solution-ai-hub"
$PROFILE = "springboot-2.7-to-3.5-java17"
$APPROVED_BY = "abdelilah.mortaki"
$RUN_DIR = Join-Path $MODERNIZED_APP ".migration\runs\$RUN_ID"
$env:PYTHONPATH = "."
```

Optional final-report advisory:

```powershell
$env:AI_MIGRATION_ENABLE_COPILOT_STATEMENT = "true"
```

## Full Sandbox Migration Runbook

Start the full sandbox orchestration:

```powershell
python -m migration_factory.orchestrator.runner `
  --run-id $RUN_ID `
  --legacy $LEGACY_APP `
  --modernized $MODERNIZED_APP `
  --ai-hub $AI_HUB `
  --profile $PROFILE `
  --mode full_sandbox_migration
```

Expected first stop:

- Console JSON status: `human_approval_required`
- `approval_status`: `INTERRUPTED`
- Decision options: `approved`, `rejected`, `replan_required`
- Approval checkpoint: `$RUN_DIR\orchestration\approval_interrupt_state.json`

Review these artifacts before approval:

- `$RUN_DIR\analysis\analysis_report.json`
- `$RUN_DIR\analysis\read_only_verification.json`
- `$RUN_DIR\planning\migration_plan.yaml`
- `$RUN_DIR\planning\migration_units.yaml`
- `$RUN_DIR\planning\approval_request.json`
- `$RUN_DIR\assessment\assessment_report.json`

Resume with approval. This records `approval_decision.json`, creates `approved_plan_lock.json`, then runs sandbox transform, build validation, test parsing, timing, orchestration summary, and final report.

```powershell
python -m migration_factory.orchestrator.resume `
  --run-id $RUN_ID `
  --run-dir $RUN_DIR `
  --decision approved `
  --approved-by $APPROVED_BY `
  --comments "Approved for sandbox POC migration"
```

For artifact-only approval without resuming orchestration, use:

```powershell
python -m migration_factory.approval.approve_run `
  --run-dir $RUN_DIR `
  --run-id $RUN_ID `
  --approved-by $APPROVED_BY `
  --decision approved `
  --comments "Approved for sandbox POC migration"
```

If approval artifacts already exist and you need to run only Phase 2 manually:

```powershell
python -m migration_factory.transform_v1_after_approval `
  --run-dir $RUN_DIR `
  --legacy-app $LEGACY_APP `
  --modernized-app $MODERNIZED_APP `
  --ai-hub $AI_HUB `
  --profile $PROFILE `
  --approved-by $APPROVED_BY
```

Useful Phase 2 options:

- `--verbose` streams subprocess output while still writing the log.
- `--log-file C:\path\phase2.log` writes the full transform/build output to a custom log file.
- `--build-timeout 450` overrides sandbox build validation timeout in seconds.

## Read-Only Assessment

Use this when you want analysis, planning, assessment, and approval readiness only. It must not transform, build the migrated sandbox, run migrated tests, write a final migration report, create a PR, deploy, or promote.

```powershell
python -m migration_factory.orchestrator.runner `
  --run-id $RUN_ID `
  --legacy $LEGACY_APP `
  --modernized $MODERNIZED_APP `
  --ai-hub $AI_HUB `
  --profile $PROFILE `
  --mode read_only_assessment
```

This mode also stops at human approval. Its assessment execution claims should remain false for transformation, OpenRewrite apply, migrated build, migrated tests, and final migration.

## Output Artifacts

All run artifacts are written below:

```text
$MODERNIZED_APP\.migration\runs\$RUN_ID
```

Important paths:

- `analysis/analysis_report.json`
- `analysis/dependency_graph.json`
- `analysis/test_inventory.json`
- `analysis/read_only_verification.json`
- `planning/migration_plan.yaml`
- `planning/migration_units.yaml`
- `planning/approval_request.json`
- `planning/plan_validation_report.json`
- `assessment/assessment_report.json`
- `approval/approval_decision.json`
- `approval/approved_plan_lock.json`
- `workspaces/sandbox/`
- `workspaces/sandbox/.migration/ledger.json`
- `transformation/transformation_execution_plan.yaml`
- `transformation/openrewrite-plugin.xml`
- `build/build-error.json` when build validation fails
- `logs/phase2_transform.log`
- `test/post_transform/test_report.json`
- `test/post_transform/test_summary.md`
- `test/post_transform/test_agent.log`
- `performance/timing_report.json`
- `performance/timing_summary.md`
- `orchestration/orchestration_summary.json`
- `final/migration_report.json`
- `final/migration_summary.md`
- `final/copilot_docs/migration_overview.md`
- `final/copilot_docs/technical_changes.md`
- `final/copilot_docs/validation_evidence.md`
- `final/copilot_docs/risks_and_warnings.md`
- `final/copilot_docs/copilot_review.md`
- `final/copilot_migration_statement.json` and `.md` only when the optional advisory env var is enabled

## Expected Success Statuses

A successful `full_sandbox_migration` ends with:

- `approval_status`: `COMPLETED`
- `approval_decision`: `approved`
- `orchestration_status`: `PASS`
- `transform_status`: `TRANSFORM_APPLIED_IN_SANDBOX`
- `build_status`: `BUILD_PASSED_IN_SANDBOX`
- `test_status`: `TEST_PASSED`
- `final_status`: `TRANSFORM_APPLIED_IN_SANDBOX`
- `orchestration_artifacts_valid`: `true`
- `stop_reason`: `Sandbox migration candidate ready.`

Example test totals from implemented tests:

```json
{"tests": 3, "passed": 3, "failures": 0, "errors": 0, "skipped": 0}
```

or:

```json
{"tests": 5, "passed": 5, "failures": 0, "errors": 0, "skipped": 0}
```

## Timing And Performance

Strict validation currently repeats Maven validation commands such as `mvn clean test`, including post-transform checks. This is intentional for the current safety model and can be slow. Optimization, caching, or reducing repeated validation is future work and should not weaken approval, lock, sandbox, build, or test gates.

## Troubleshooting

- Bad `RUN_ID`: avoid spaces and shell-special characters. Run directories and checkpoint files are keyed by `run_id`.
- Java or port 8080 issues: stop old Java processes before rerunning. On PowerShell, inspect and stop Java with `Get-Process java -ErrorAction SilentlyContinue` and `Stop-Process -Id <pid>`.
- Sandbox cleanup failure: close terminals/editors pointing at `$RUN_DIR\workspaces\sandbox`, stop Java processes, delete the sandbox manually, or use a new `RUN_ID`.
- Timeout: use `--build-timeout` on `transform_v1_after_approval`, then inspect `$RUN_DIR\logs\phase2_transform.log`.
- Missing approval artifacts: verify Phase 1 produced required analysis, planning, assessment, and read-only artifacts, then use `orchestrator.resume` or `approval.approve_run`.
- Approval mismatch: `--approved-by` must match `approval_decision.json` when running `transform_v1_after_approval`.
- Run directory mismatch: `--run-dir` must equal `$MODERNIZED_APP\.migration\runs\$RUN_ID`.
- Missing Surefire reports: the Test Agent reports `TEST_ERROR` when no `target/surefire-reports` XML files are available after validation.

## Safety Boundaries

- No production promotion.
- No pull request creation.
- No deployment.
- No direct legacy app writes.
- Copilot documentation is advisory only and cannot mutate source, approvals, plans, gates, PRs, deployments, or promotion state.
- No bypassing human approval, `approval_decision.json`, `approved_plan_lock.json`, artifact schema validation, hash checks, sandbox transform/build/test gates, or fail-closed behavior.
