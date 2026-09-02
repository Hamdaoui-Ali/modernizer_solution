# Modernizer Solution AI Hub

This directory is the source of truth for migration profiles, OpenRewrite catalogs, and agent policies used by the migration engine.

## Repository Separation

- `modernizer-solution` is the root repository for engine, agent code, and the AI Hub.
- `modernizer-solution-ai-hub` is the tracked AI Hub directory, containing pinned profiles, catalogs, schemas, and policies.
- `shoppoc-app` is the legacy application input for test migration runs.
- `modernized-app/.migration/runs/<run_id>` is where migration run outputs belong.

The engine may generate `.migration/ai-hub` content as run support, snapshots, or execution material. That generated directory is not the source of truth. Update this tracked AI Hub directory instead, then pass its path and profile id to the planner.

## Current Default Profile

- AI Hub path: `C:\Users\hamdaoui.ali\modernizer-solution\modernizer-solution-ai-hub`
- Profile id: `springboot-2.7-to-3.5-java17`
- Target: Spring Boot `3.5.14`, Spring Framework `6.2.18`, Java `17`

## Safety Model

Analysis and planning are read-only for source code. Transformation may write source files only after explicit human approval.

## Copilot Documentation Proof

`agents/copilot-doc-agent.yaml` describes the first GitHub Copilot integration proof. It is documentation-only and runs after successful sandbox validation. It reads deterministic run artifacts and writes Markdown under `final/copilot_docs/` with source-artifact traceability. It cannot mutate source, approval decisions, approved plan locks, migration plans, gates, promotions, pull requests, or deployments.
