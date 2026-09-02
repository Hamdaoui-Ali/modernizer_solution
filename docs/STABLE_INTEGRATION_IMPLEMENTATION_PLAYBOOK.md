# Stable Integration Implementation Playbook

## 1. Executive Summary

Build `stable` from the exact semantic baseline:

```text
before@fad82fdf8619644bade3cdde3a9ad184cd815d3d
```

Treat the exact donor commit as a source of feature intent only:

```text
V2IMPROVMENT@ff4c1f7c57b5b90243f5e20efc6959c1ce68a2b6
```

The target is:

```text
stable = before advanced F15 Control Tower governance
       + selected Spring Boot 4 / Stage 4 / report / PDF capabilities
       - donor regressions
       - donor absolute-path exposure
       - donor migration 0039
       - donor broad UI redesign
```

The implementation must be a manual, file-by-file port. Do not merge
`V2IMPROVMENT`, cherry-pick its commits wholesale, or use conflict-resolution
shortcuts such as blindly selecting `ours` or `theirs`.

The principal engineering constraints are:

- `before` remains authoritative for F15 gates, checksums, revisions, repair,
  approval, stage progression, sandbox binding, POM governance, and cockpit
  safety.
- Stage 4 is an extension of the existing governed progression, not a parallel
  pipeline.
- Stage 4 input is resolved by the backend from persisted and accepted Stage 3
  output. No frontend or chatbot path is accepted.
- Reports are generated from accepted artifacts, proof, gates, and event
  history.
- Report JSON, Markdown, and PDF outputs are registered artifacts with
  checksums.
- APIs return artifact metadata and download URLs, never absolute filesystem
  paths.
- The frontend uses `web/control-tower/lib/controlTowerApi.ts` for all report
  operations.

Main product-code scope:

```text
migration_factory/
web/control-tower/
```

Required scope exceptions:

```text
modernizer-solution-ai-hub/profiles/springboot-3.5-java21-to-4.0-java21.yaml
modernizer-solution-ai-hub/catalogs/openrewrite/springboot-3.5-java21-to-4.0-java21.yaml
tests/
```

The profile and catalog exception is mandatory: setup readiness must not
reference a profile or catalog that is absent from the repository.

## 2. Verified Git State

### Exact commands run

```powershell
git fetch --all --prune
git rev-parse before
git rev-parse origin/V2IMPROVMENT
git merge-base fad82fdf8619644bade3cdde3a9ad184cd815d3d ff4c1f7c57b5b90243f5e20efc6959c1ce68a2b6
git status --short
git branch --show-current
```

### Exact output summary

```text
before:
fad82fdf8619644bade3cdde3a9ad184cd815d3d

origin/V2IMPROVMENT:
ff4c1f7c57b5b90243f5e20efc6959c1ce68a2b6

merge base:
651f37d4b7213d84d8d661e0d56d154909c211c4

current branch:
frontend/cockpit-chatbot-ui-polish
```

The expected merge base is verified:

```text
651f37d4b7213d84d8d661e0d56d154909c211c4
```

The local `V2IMPROVMENT` branch may be stale. All donor inspection commands
must use either the exact donor commit or `origin/V2IMPROVMENT` after verifying
that it resolves to the exact donor commit.

Use:

```powershell
$BASE_COMMIT = "fad82fdf8619644bade3cdde3a9ad184cd815d3d"
$DONOR_COMMIT = "ff4c1f7c57b5b90243f5e20efc6959c1ce68a2b6"
$MERGE_BASE = "651f37d4b7213d84d8d661e0d56d154909c211c4"

git rev-parse $BASE_COMMIT
git rev-parse $DONOR_COMMIT
git merge-base $BASE_COMMIT $DONOR_COMMIT
```

Do not use an unverified local branch name in a diff:

```powershell
# Do not rely on this if the local branch is stale.
git diff before..V2IMPROVMENT
```

Use exact commits:

```powershell
git diff --name-status $MERGE_BASE..$DONOR_COMMIT -- migration_factory web
git diff --stat $MERGE_BASE..$DONOR_COMMIT -- migration_factory web
git diff --name-status $MERGE_BASE..$BASE_COMMIT -- migration_factory web
```

### Dirty worktree warning

The worktree was dirty during playbook creation:

```text
 M web/control-tower/app/globals.css
 M web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx
 M web/control-tower/app/migrations/[jobId]/page.tsx
 M web/control-tower/tests/migrationCockpit.test.tsx
?? web/control-tower/lib/assistantTranscript.ts
?? web/control-tower/tests/assistantTranscript.test.ts
```

These changes are user-owned and must not be discarded, staged, moved, or
included in `stable`.

### Recommended worktree creation

Create `stable` in a separate worktree:

```powershell
git fetch --all --prune
git rev-parse before
git rev-parse origin/V2IMPROVMENT
git merge-base fad82fdf8619644bade3cdde3a9ad184cd815d3d ff4c1f7c57b5b90243f5e20efc6959c1ce68a2b6

git worktree add -b stable ..\modernizer-solution-stable fad82fdf8619644bade3cdde3a9ad184cd815d3d
Set-Location ..\modernizer-solution-stable
git status --short
git rev-parse HEAD
git branch --show-current
```

Expected new-worktree state:

```text
HEAD   = fad82fdf8619644bade3cdde3a9ad184cd815d3d
branch = stable
status = clean
```

If `stable` already exists, stop and inspect it. Do not delete or reset it
without explicit approval.

## 3. Donor Feature Inventory

Inspect each donor commit independently:

```powershell
git show --stat --name-status ac0ce3e7232b42fd706eda782b0d6932dd1325d3
git show --stat --name-status c98827f2354c89a2767d57a8be50ce52229ab9af
git show --stat --name-status 98a9f55090b59f0f8a3618119111eb64aa62c706
git show --stat --name-status ff4c1f7c57b5b90243f5e20efc6959c1ce68a2b6
```

Do not apply these commits. Read their diffs and manually reconstruct approved
capabilities on top of `before`.

### `ac0ce3e7232b42fd706eda782b0d6932dd1325d3`

Subject:

```text
Added report generation + upgrading until spring boot 4
```

Changed relevant files:

```text
migration_factory/agents/planning_agent/output_validator.py
migration_factory/control_tower/adapters/fastapi/app.py
migration_factory/control_tower/application/v2_final_report_service.py
migration_factory/control_tower/application/v2_job_service.py
migration_factory/control_tower/application/v2_orchestrator_runner.py
migration_factory/control_tower/application/v2_setup_service.py
migration_factory/control_tower/application/v2_stage_progression.py
migration_factory/control_tower/application/v2_worker_stage.py
migration_factory/control_tower/infrastructure/sqlite/migrations/0039_v2_stage4_support.sql
migration_factory/final_report/writer.py
migration_factory/orchestrator/artifact_validation.py
migration_factory/orchestrator/summary.py
modernizer-solution-ai-hub/catalogs/openrewrite/springboot-3.5-java21-to-4.0-java21.yaml
modernizer-solution-ai-hub/profiles/springboot-3.5-java21-to-4.0-java21.yaml
tests/agents/planning_agent/test_boot4_stage_profile.py
tests/control_tower/test_sqlite_migrations.py
tests/control_tower/test_v2_e2e.py
tests/control_tower/test_v2_final_report_service.py
tests/control_tower/test_v2_job_service.py
tests/control_tower/test_v2_orchestrator_runner.py
tests/control_tower/test_v2_setup_service.py
tests/control_tower/test_v2_stage_progression.py
tests/control_tower/test_v2_worker_stage.py
tests/test_final_report.py
web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx
web/control-tower/app/migrations/new/NewMigrationForm.tsx
web/control-tower/lib/contracts.ts
web/control-tower/lib/controlTowerApi.ts
web/control-tower/tests/controlTowerApi.test.ts
web/control-tower/tests/migrationCockpit.test.tsx
```

Safe intent to port:

- Stage 4 profile and OpenRewrite catalog.
- Stage 4 unit-order validation.
- Four-stage job chain and Boot 4 pipeline identity.
- Setup readiness requiring all four profiles/catalogs.
- Stage 4 configuration using Java 21.
- Stage 4 terminal behavior.
- Report narrative/history/timing concepts.
- Focused Stage 4 and report tests.

Rejected implementation details:

- `0039_v2_stage4_support.sql`.
- Report roots inferred from `argv_json`.
- Writes/copies/deletes under `docs/migration-reports`.
- Environment-variable deferral policy.
- Ambient Maven environment propagation.
- Broad page and cockpit restyling.
- Any donor change that replaces F15 progression or gate handling.

### `c98827f2354c89a2767d57a8be50ce52229ab9af`

Subject:

```text
Added report generation + download option as pdf
```

Changed relevant files:

```text
migration_factory/control_tower/adapters/fastapi/app.py
migration_factory/control_tower/application/v2_final_report_service.py
migration_factory/final_report/pdf_writer.py
migration_factory/final_report/writer.py
tests/control_tower/test_v2_final_report_service.py
web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx
web/control-tower/lib/contracts.ts
web/control-tower/lib/controlTowerApi.ts
web/control-tower/tests/controlTowerApi.test.ts
web/control-tower/tests/migrationCockpit.test.tsx
```

Safe intent to port:

- Dependency-light Markdown-to-PDF renderer.
- Explicit report status/generation/download operations.
- Report artifact metadata contracts.
- Cockpit controls for status, generation, and download.

Rejected implementation details:

- `FileResponse(Path(report.run_report_pdf))`.
- Absolute `run_report_json`, `run_report_markdown`, `run_report_pdf`, or
  `run_dir` response fields.
- A job-based PDF route that trusts a path stored in a report snapshot.
- Immediate automatic PDF download after generation.
- Rendering report filesystem paths in the cockpit.

### `98a9f55090b59f0f8a3618119111eb64aa62c706`

Subject:

```text
Enhanced report generation
```

Changed relevant files:

```text
migration_factory/final_report/pdf_writer.py
migration_factory/final_report/writer.py
tests/reporting/test_pdf_writer.py
tests/test_final_report.py
```

Safe intent to port:

- Full migration journey.
- Stage-by-stage history.
- Source/target stack narrative.
- Aggregated duration.
- Change summary.
- Wrapped PDF table cells and dynamic row heights.

Manual-review requirement:

- Port additions into the `before` writer without removing its existing
  Copilot/advisory fields, dependency-policy information, repair trace, AI
  trace, redaction, limitations, or artifact references.

### `ff4c1f7c57b5b90243f5e20efc6959c1ce68a2b6`

Subject:

```text
Merge pull request #132 from Ali-Hamdaoui/UI-update
```

This is the donor branch tip and a merge commit. It is not an independent
implementation slice. Use it to verify the final donor state, not as a
cherry-pick target.

### Generated donor content

The donor commits also added generated report files under
`docs/migration-reports`. These are runtime artifacts and must not be ported,
committed, copied, or used as architecture.

## 4. Baseline Protection Inventory

### Protected F15 systems

The following behavior from `before` is non-negotiable:

- F15 phase gates.
- Gate decisions.
- Gate checksums and stale-checksum rejection.
- Gate-bound artifact resolution.
- Artifact revisions and lineage.
- Accepted-revision enforcement.
- Analysis and planning revision flows.
- Repair gates and reviewer requirements.
- Validation and rollback flow.
- Assistant read-only authority.
- Backend-owned stage progression.
- Persisted-output resolution.
- Stage-skip blocking.
- POM proposal/review/apply governance.
- Current cockpit gate, evidence, approval, repair, and assistant panels.
- Migrations `0039` through `0045`.

Why these protections matter:

- Gates ensure the chatbot can interpret but cannot decide or execute.
- Checksums bind human decisions to exact evidence.
- Accepted revisions prevent draft or superseded artifacts from feeding later
  stages.
- Backend-owned progression prevents client-controlled paths, commands, and
  stage skipping.
- Repair and rollback preserve sandbox-only mutation and proof.
- Existing cockpit panels expose the human decision points required by F15.
- Existing migrations define the current schema. Reusing donor migration 0039
  would collide with and erase later schema additions.

### Protected files

Do not replace these files with donor versions. Changes are allowed only when a
specific Stage 4 integration requires a narrow, reviewed extension:

```text
migration_factory/control_tower/application/v2_analysis_diff_summary.py
migration_factory/control_tower/application/v2_analysis_scope_mapping.py
migration_factory/control_tower/application/v2_assistant_response_composer.py
migration_factory/control_tower/application/v2_evidence_pack_builder.py
migration_factory/control_tower/application/v2_gate_action_service.py
migration_factory/control_tower/application/v2_gate_artifact_resolver.py
migration_factory/control_tower/application/v2_gate_assistant.py
migration_factory/control_tower/application/v2_gate_errors.py
migration_factory/control_tower/application/v2_model_role_router.py
migration_factory/control_tower/application/v2_phase_gate_service.py
migration_factory/control_tower/application/v2_plan_diff_summary.py
migration_factory/control_tower/application/v2_plan_revision_adapter.py
migration_factory/control_tower/application/v2_repair_gate_service.py
migration_factory/control_tower/domain/f15_events.py
migration_factory/control_tower/domain/gate_artifact_ref.py
migration_factory/control_tower/domain/gate_audit.py
migration_factory/control_tower/domain/gate_checksum.py
migration_factory/control_tower/infrastructure/sqlite/v2_artifact_revision_repository.py
migration_factory/control_tower/infrastructure/sqlite/v2_gate_decision_repository.py
migration_factory/control_tower/infrastructure/sqlite/v2_phase_gate_repository.py
migration_factory/control_tower/schemas/artifact_revision.py
migration_factory/control_tower/schemas/phase_gate.py
```

### Protected migrations

Keep these migrations unchanged and in sequence:

```text
migration_factory/control_tower/infrastructure/sqlite/migrations/0039_v2_phase_gates.sql
migration_factory/control_tower/infrastructure/sqlite/migrations/0040_v2_gate_decisions.sql
migration_factory/control_tower/infrastructure/sqlite/migrations/0041_v2_artifact_revisions.sql
migration_factory/control_tower/infrastructure/sqlite/migrations/0042_v2_gate_decisions_add_reason.sql
migration_factory/control_tower/infrastructure/sqlite/migrations/0043_v2_commands_add_gate_references.sql
migration_factory/control_tower/infrastructure/sqlite/migrations/0044_run_configurations_relax_fk.sql
migration_factory/control_tower/infrastructure/sqlite/migrations/0045_v2_repair_proposals_revision_metadata.sql
```

Add Stage 4 support only through:

```text
migration_factory/control_tower/infrastructure/sqlite/migrations/0046_v2_stage4_support.sql
```

### Current role of required implementation areas

| Area | Current `before` role |
|---|---|
| `migration_factory/control_tower/adapters/fastapi/app.py` | Main FastAPI composition root and F15 HTTP surface; owns gate actions, assistant constraints, cockpit projections, artifact previews, POM governance, and existing safe file-alias download behavior. |
| `migration_factory/control_tower/application/v2_final_report_service.py` | Does not exist in `before`; add it as an adapter over existing governance and artifact services. |
| `migration_factory/control_tower/application/v2_job_service.py` | Creates durable V2 jobs and the fixed three-stage chain. |
| `migration_factory/control_tower/application/v2_orchestrator_runner.py` | Executes backend-owned manifests, validates strict success proof, emits events, opens gates, and queues progression. |
| `migration_factory/control_tower/application/v2_setup_service.py` | Creates setups, validates toolchains, performs readiness checks, and checks AI Hub profiles/catalogs. |
| `migration_factory/control_tower/application/v2_stage_progression.py` | Enforces sequential progression, continuation policy, persisted-output resolution, and optional gate/decision trace on commands. |
| `migration_factory/control_tower/application/v2_worker_stage.py` | Builds and persists the backend-owned Stage 1 command manifest. |
| `migration_factory/control_tower/infrastructure/sqlite/migrations/` | Append-only schema history through F15 migration 0045. |
| `migration_factory/final_report/writer.py` | Generates deterministic final JSON/Markdown from orchestration artifacts, repair/dependency/AI trace, redaction, and validation facts. |
| `migration_factory/final_report/pdf_writer.py` | Does not exist in `before`; add the donor renderer as a focused output adapter. |
| `migration_factory/orchestrator/artifact_validation.py` | Validates required orchestration artifacts and result contracts. |
| `migration_factory/orchestrator/summary.py` | Finalizes orchestration state and currently generates final report outputs as part of successful orchestration. |
| `migration_factory/agents/planning_agent/output_validator.py` | Validates planning artifacts and allowed migration-unit ordering. |
| `web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx` | Current V2 cockpit with stage, pipeline, evidence, approval, failure/repair, assistant, gate, and POM review surfaces. |
| `web/control-tower/app/migrations/new/NewMigrationForm.tsx` | Setup/preflight/start workflow; currently contains direct API helpers that should be centralized only if this file is touched. |
| `web/control-tower/lib/contracts.ts` | Shared frontend response/request types, including V2 stages, artifacts, gates, repair, and POM types. |
| `web/control-tower/lib/controlTowerApi.ts` | Central frontend HTTP client, URL validation, V2 job/gate/assistant/POM operations. |
| `web/control-tower/tests/controlTowerApi.test.ts` | Validates route construction, required job IDs, requests, and safe client contracts. |
| `web/control-tower/tests/migrationCockpit.test.tsx` | Validates cockpit stages, controls, governance, and visible panels. |

## 5. Conflict and Integration Matrix

| file | subsystem | before role | donor role | decision | implementation instruction |
|---|---|---|---|---|---|
| `migration_factory/agents/planning_agent/output_validator.py` | Planning | Validates allowed migration-unit orders | Adds Boot 4-only unit order | `PORT` | Add only the Boot 4 order; retain all existing orders and validation behavior. |
| `migration_factory/control_tower/adapters/fastapi/app.py` | API/governance | Main F15 API surface | Adds path-based report routes | `MANUAL_REWRITE` | Add report status/generate/artifact-download routes through a safe service; preserve all F15 routes. |
| `migration_factory/control_tower/application/v2_final_report_service.py` | Reporting | Absent | Derives run roots from argv and writes repository docs | `MANUAL_REWRITE` | Create a new service that consumes accepted backend records and registers outputs through existing artifact infrastructure. |
| `migration_factory/control_tower/application/v2_job_service.py` | Pipeline | Creates fixed three-stage chain | Adds Stage 4 and new pipeline ID | `PORT` | Extend stage inputs and chain to 4 without changing ownership or persistence semantics. |
| `migration_factory/control_tower/application/v2_orchestrator_runner.py` | Execution/governance | Strict proof, events, gates, repair, progression | Makes Stage 4 terminal and changes report events | `MANUAL_REWRITE` | Extend current runner; keep strict proof and gate handling; terminalize only after Stage 4 accepted completion. |
| `migration_factory/control_tower/application/v2_setup_service.py` | Readiness | Checks toolchain and three profiles/catalogs | Requires Boot 4 profile/catalog | `PORT` | Add the fourth profile to both profile and catalog checks. |
| `migration_factory/control_tower/application/v2_stage_progression.py` | Progression | Sequential, policy-aware, persisted-output progression | Adds Stage 4 and ambient env copying | `MANUAL_REWRITE` | Add Stage 4 config and accepted-output guard; retain gate/decision tracing; reject ambient env copying. |
| `migration_factory/control_tower/application/v2_worker_stage.py` | Command creation | Builds backend-owned Stage 1 manifest | Updates pipeline ID and ambient env | `MANUAL_REWRITE` | Update pipeline identity only; keep current backend-owned env allowlist. |
| `migration_factory/control_tower/infrastructure/sqlite/migrations/0039_v2_stage4_support.sql` | Database | Conflicts with F15 migration 0039 | Rebuilds five old tables | `REJECT` | Do not copy, rename, or edit it. Design complete migration 0046 from post-0045 schema. |
| `migration_factory/control_tower/infrastructure/sqlite/migrations/0046_v2_stage4_support.sql` | Database | New required migration | No safe donor equivalent | `MANUAL_REWRITE` | Widen seven stage constraints while preserving every post-0045 column, index, and trigger. |
| `migration_factory/final_report/pdf_writer.py` | PDF | Absent | Dependency-light PDF renderer | `PORT` | Port renderer and focused tests; keep it unaware of repositories, HTTP, and paths outside caller-provided registered storage. |
| `migration_factory/final_report/writer.py` | Reports | Deterministic redacted report with repair/dependency/AI trace | Adds history, timing, narrative; removes some baseline fields | `MANUAL_REWRITE` | Port additive narrative helpers only; preserve all baseline facts, warnings, advisory fields, redaction, and limitations. |
| `migration_factory/orchestrator/artifact_validation.py` | Validation | Requires successful run artifacts | Allows env-driven report deferral | `MANUAL_REWRITE` | Separate stage success validation from report-generation eligibility using explicit backend policy/state, not process env. |
| `migration_factory/orchestrator/summary.py` | Orchestration | Finalizes summaries and reports | Adds `AI_MIGRATION_DEFER_FINAL_REPORT` | `MANUAL_REWRITE` | Remove report output from intermediate stages via explicit stage context; never use ambient env as execution policy. |
| `modernizer-solution-ai-hub/profiles/springboot-3.5-java21-to-4.0-java21.yaml` | Profile | Absent | Defines Stage D Boot 4 migration | `PORT` | Port after schema validation; retain sandbox-only, high-risk, human-approval guardrails. |
| `modernizer-solution-ai-hub/catalogs/openrewrite/springboot-3.5-java21-to-4.0-java21.yaml` | Catalog | Absent | Defines Boot 4 recipe/plugin | `PORT` | Port recipe configuration; validate with profile and focused tests. |
| `web/control-tower/app/globals.css` | UI | Current application styles | Broad redesign | `REJECT` | No changes for this feature. |
| `web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx` | Cockpit | F15 panels and controls | Adds report panel plus broad redesign | `MANUAL_REWRITE` | Add a compact report panel while preserving all existing panels and layout behavior. |
| `web/control-tower/app/migrations/new/NewMigrationForm.tsx` | Setup UI | Setup, preflight, and start | Broad redesign and direct API helpers | `KEEP` | Prefer no change. If touched, move all API helpers to `controlTowerApi.ts`; do not redesign. |
| `web/control-tower/lib/contracts.ts` | Contracts | Safe V2/gate/artifact types | Adds path-bearing report response | `MANUAL_REWRITE` | Define artifact summaries with IDs/checksums/download URLs; forbid absolute path fields. |
| `web/control-tower/lib/controlTowerApi.ts` | Frontend API | Centralized API operations | Adds report methods and job PDF URL | `MANUAL_REWRITE` | Add status/generate/download helpers using artifact IDs and returned download URLs. |
| `web/control-tower/tests/controlTowerApi.test.ts` | Frontend tests | Validates API safety | Tests path-bearing response | `MANUAL_REWRITE` | Assert route encoding, artifact metadata, and absence of path fields. |
| `web/control-tower/tests/migrationCockpit.test.tsx` | Frontend tests | Protects cockpit governance | Adds Stage 4/report tests | `MANUAL_REWRITE` | Add Stage 4/report cases while retaining gate/approval/repair/assistant assertions. |
| Donor changes to legacy job panels/pages | UI | Existing V1/diagnostic surfaces | Cosmetic redesign | `REJECT` | Leave unchanged. |
| Donor generated `docs/migration-reports/**` | Runtime output | Not source | Commits generated reports | `REJECT` | Never port or generate in repository docs. |

## 6. Final Safe Architecture

### Stage chain

```text
Stage 1
  -> accepted persisted output
Stage 2
  -> accepted persisted output
Stage 3
  -> accepted persisted output
Stage 4: Spring Boot 3.5 / Java 21 -> Spring Boot 4 / Java 21
  -> terminal governed completion
```

Every transition uses the existing F15 continuation policy, gates, checksums,
artifact revisions, backend-owned commands, and repair controls.

### Stage 4 flow

1. Stage 3 completes with strict success proof.
2. Required Stage 3 gate is resolved by a human.
3. The accepted Stage 3 artifact revision is persisted.
4. `V2StageProgressionService` resolves the accepted Stage 3 output from backend
   repositories.
5. The service validates that the requested transition is exactly `3 -> 4`.
6. The backend builds a Stage 4 command using:

   ```text
   profile = springboot-3.5-java21-to-4.0-java21
   JDK     = JAVA21_HOME
   source  = accepted Stage 3 output
   target  = backend-owned output root
   ```

7. No frontend/chatbot request contains `sandbox_path`, argv, env, command,
   filesystem target, or report root.
8. Stage 4 executes in the sandbox and uses current proof, approval, repair, and
   event machinery.
9. A successful accepted Stage 4 result is terminal. No Stage 5 command is
   created.

### Report flow

1. An operator requests report generation through a path-free endpoint.
2. The service checks:

   - Job exists.
   - Stage 4 terminal command completed successfully.
   - Strict proof is present.
   - Required phase gates are resolved and accepted.
   - Required accepted artifact revisions exist.
   - No blocking repair/reviewer/approval gate remains open.

3. A backend report-context builder resolves registered artifacts, accepted
   revisions, proof records, and event history.
4. `final_report/writer.py` creates deterministic JSON and Markdown under a
   backend-owned registered artifact root.
5. `final_report/pdf_writer.py` renders the Markdown to PDF under the same
   registered root.
6. Each file is hashed and registered.
7. The report service returns only artifact summaries and redacted narrative.
8. Repeated generation either returns the existing artifact set for the same
   input checksum or creates an explicit new report revision.

### Artifact and download flow

```text
report service
  -> registered root selected by backend
  -> output written
  -> containment validated
  -> SHA-256 computed
  -> immutable artifact record inserted
  -> artifact metadata returned
```

Download:

```text
GET /v1/v2/jobs/{job_id}/report-artifacts/{artifact_id}/download
  -> find artifact for job
  -> validate allowed report kind/content type
  -> resolve registered root + relative path
  -> assert root containment
  -> recompute/verify checksum
  -> stream bytes
```

The API must never return:

```python
# forbidden
run_report_json: str
run_report_markdown: str
run_report_pdf: str
run_dir: str
sandbox_path: str
```

### Frontend flow

1. `MigrationCockpit.tsx` loads report status with the rest of cockpit state.
2. A generate button is enabled only when backend eligibility says it is
   allowed. The frontend does not infer safety from stage status alone.
3. Generation calls `generateV2FinalReport(jobId)`.
4. The response displays a redacted summary and artifact metadata.
5. The user explicitly clicks a download link supplied by the API.
6. No component constructs a local path or reads filesystem fields.
7. All calls live in `web/control-tower/lib/controlTowerApi.ts`.

## 7. Implementation Phases

### Phase 1. Worktree and scope setup

#### Goal

Create a clean `stable` worktree at the exact baseline without disturbing
existing changes.

#### Files to edit

None.

#### Donor references to inspect

All four donor commits, read-only.

#### Exact implementation steps

```powershell
git fetch --all --prune

$BASE_COMMIT = "fad82fdf8619644bade3cdde3a9ad184cd815d3d"
$DONOR_COMMIT = "ff4c1f7c57b5b90243f5e20efc6959c1ce68a2b6"
$EXPECTED_MERGE_BASE = "651f37d4b7213d84d8d661e0d56d154909c211c4"

if ((git rev-parse before) -ne $BASE_COMMIT) {
  throw "before does not match BASE_COMMIT"
}
if ((git rev-parse origin/V2IMPROVMENT) -ne $DONOR_COMMIT) {
  throw "origin/V2IMPROVMENT does not match DONOR_COMMIT"
}
if ((git merge-base $BASE_COMMIT $DONOR_COMMIT) -ne $EXPECTED_MERGE_BASE) {
  throw "Unexpected merge base"
}

git worktree add -b stable ..\modernizer-solution-stable $BASE_COMMIT
Set-Location ..\modernizer-solution-stable
git status --short
git rev-parse HEAD
git branch --show-current
```

Create a file ownership list before editing. Never stage outside it.

#### Tests to add/update

None.

#### Commands to run

```powershell
git status --short
git diff --check
```

#### Commit message

No commit.

### Phase 2. Boot 4 profile/catalog support

#### Goal

Add the Stage 4 profile/catalog and validate planning/readiness recognition.

#### Files to edit

```text
modernizer-solution-ai-hub/profiles/springboot-3.5-java21-to-4.0-java21.yaml
modernizer-solution-ai-hub/catalogs/openrewrite/springboot-3.5-java21-to-4.0-java21.yaml
migration_factory/agents/planning_agent/output_validator.py
migration_factory/control_tower/application/v2_setup_service.py
tests/agents/planning_agent/test_boot4_stage_profile.py
tests/control_tower/test_v2_setup_service.py
```

#### Donor references to inspect

```powershell
git show ac0ce3e7232b42fd706eda782b0d6932dd1325d3 -- `
  modernizer-solution-ai-hub/profiles/springboot-3.5-java21-to-4.0-java21.yaml `
  modernizer-solution-ai-hub/catalogs/openrewrite/springboot-3.5-java21-to-4.0-java21.yaml `
  migration_factory/agents/planning_agent/output_validator.py `
  migration_factory/control_tower/application/v2_setup_service.py `
  tests/agents/planning_agent/test_boot4_stage_profile.py
```

#### Exact implementation steps

1. Add the donor profile and catalog after validating them against the current
   profile schema.
2. Keep these guardrails:

   ```text
   risk_level: high
   production_allowed: false
   human approval required
   source Java: 21
   source Spring Boot: 3.5.x
   target Java: 21
   target Spring Boot: 4.x
   sandbox transform only
   ```

3. Add the allowed unit order:

   ```python
   (
       "baseline",
       "spring-boot-4-0",
       "jakarta",
       "dependency-cleanup",
       "existing-test-migration",
   )
   ```

4. Extend `_check_ai_hub_profiles` and `_check_ai_hub_catalogs` with:

   ```python
   "springboot-3.5-java21-to-4.0-java21"
   ```

5. Verify missing profile or missing catalog fails readiness.

#### Tests to add/update

- Profile loads and validates against the current schema.
- Profile remains sandbox-only/high-risk/human-approved.
- Catalog uses the expected Boot 4 OpenRewrite recipe.
- Planning unit order is accepted.
- Setup readiness fails if the fourth profile is missing.
- Setup readiness fails if the fourth catalog is missing.

#### Commands to run

```powershell
python -m pytest `
  tests/agents/planning_agent/test_boot4_stage_profile.py `
  tests/control_tower/test_v2_setup_service.py

git diff --check
```

#### Commit message

Include this phase with Phase 4 in:

```text
feat(f15): extend governed pipeline through spring boot 4
```

### Phase 3. Safe 0046 migration

#### Goal

Allow Stage 4 in all current V2/F15 stage-bearing tables without losing
post-0045 columns, indexes, triggers, or data.

#### Files to edit

```text
migration_factory/control_tower/infrastructure/sqlite/migrations/0046_v2_stage4_support.sql
tests/control_tower/test_sqlite_migrations.py
tests/control_tower/test_v2_phase_gate_migration.py
tests/control_tower/test_v2_artifact_revision_migration.py
```

#### Donor references to inspect

Read for table names and old intent only:

```powershell
git show ac0ce3e7232b42fd706eda782b0d6932dd1325d3:`
migration_factory/control_tower/infrastructure/sqlite/migrations/0039_v2_stage4_support.sql
```

Read the authoritative baseline migrations:

```powershell
Get-Content migration_factory/control_tower/infrastructure/sqlite/migrations/0030_v2_jobs_and_commands.sql
Get-Content migration_factory/control_tower/infrastructure/sqlite/migrations/0031_v2_approvals.sql
Get-Content migration_factory/control_tower/infrastructure/sqlite/migrations/0032_v2_assistant.sql
Get-Content migration_factory/control_tower/infrastructure/sqlite/migrations/0034_v2_job_events.sql
Get-Content migration_factory/control_tower/infrastructure/sqlite/migrations/0035_v2_approval_job_id.sql
Get-Content migration_factory/control_tower/infrastructure/sqlite/migrations/0039_v2_phase_gates.sql
Get-Content migration_factory/control_tower/infrastructure/sqlite/migrations/0041_v2_artifact_revisions.sql
Get-Content migration_factory/control_tower/infrastructure/sqlite/migrations/0043_v2_commands_add_gate_references.sql
```

#### Exact implementation steps

Widen these constraints to permit exactly stages 1 through 4:

```text
v2_stage_commands.stage_index
v2_approval_decisions.stage_index
v2_resume_commands.stage_index
v2_pending_action_drafts.stage_index
v2_job_events.stage
v2_phase_gates.stage_index
v2_artifact_revisions.stage_index
```

For each recreated table:

1. Rename to a unique temporary name ending `_old_0046`.
2. Drop reusable indexes/triggers.
3. Recreate the full post-0045 table.
4. Copy every column explicitly.
5. Recreate every index, including partial unique indexes.
6. Recreate every append-only or immutable trigger.
7. Drop the temporary table.

Required preservation:

```text
v2_stage_commands.gate_id
v2_stage_commands.decision_id
v2_approval_decisions.job_id
v2_gate_decisions.reason remains unaffected
phase-gate open uniqueness
accepted artifact-revision uniqueness
resolved/superseded phase-gate immutability
all no-update/no-delete triggers
```

#### Tests to add/update

- Upgrade an existing database through migration 0045, seed rows, apply 0046,
  and compare retained values.
- Stage 4 inserts succeed in all seven tables.
- Stage 5 inserts fail in all seven tables.
- `gate_id` and `decision_id` survive command-table recreation.
- Phase-gate open uniqueness remains enforced.
- Artifact accepted-revision uniqueness remains enforced.
- Append-only and immutable triggers still reject updates/deletes.

#### Commands to run

```powershell
python -m pytest `
  tests/control_tower/test_sqlite_migrations.py `
  tests/control_tower/test_v2_phase_gate_migration.py `
  tests/control_tower/test_v2_artifact_revision_migration.py

git diff --check
```

#### Commit message

```text
feat(f15): add governed stage 4 schema support
```

### Phase 4. Stage 4 backend pipeline

#### Goal

Represent a fixed four-stage pipeline and use the Boot 4 profile for Stage 4.

#### Files to edit

```text
migration_factory/control_tower/application/v2_job_service.py
migration_factory/control_tower/application/v2_stage_progression.py
migration_factory/control_tower/application/v2_worker_stage.py
tests/control_tower/test_v2_job_service.py
tests/control_tower/test_v2_stage_progression.py
tests/control_tower/test_v2_worker_stage.py
```

#### Donor references to inspect

```powershell
git show ac0ce3e7232b42fd706eda782b0d6932dd1325d3 -- `
  migration_factory/control_tower/application/v2_job_service.py `
  migration_factory/control_tower/application/v2_stage_progression.py `
  migration_factory/control_tower/application/v2_worker_stage.py
```

#### Exact implementation steps

1. Add:

   ```python
   STAGE_INPUTS[4] = {
       "pipeline_stage": "Stage 4",
       "input_kind": "stage_3_sandbox",
   }
   ```

2. Update the pipeline identity consistently:

   ```python
   PIPELINE_ID = "springboot-216-to-400-java21-four-stage"
   ```

3. Generate stage-chain entries for `(1, 2, 3, 4)`.
4. Add Stage 4 configuration:

   ```python
   STAGE_CONFIG[4] = {
       "profile": "springboot-3.5-java21-to-4.0-java21",
       "jdk_env": "JAVA21_HOME",
       "jdk_id": "java21",
       "expected_major": 21,
   }
   ```

5. Keep argv and env backend-owned.
6. Do not copy ambient `MAVEN_OPTS` or `MAVEN_USER_HOME`.
7. Do not add `AI_MIGRATION_DEFER_FINAL_REPORT`.
8. Keep command `gate_id` and `decision_id` trace support.

#### Tests to add/update

- New jobs contain exactly four stages.
- Stage 4 input kind is `stage_3_sandbox`.
- Pipeline ID is consistent in job and worker services.
- Stage 4 uses Java 21 and the Boot 4 profile.
- Stage command response does not accept client argv/env/path fields.

#### Commands to run

```powershell
python -m pytest `
  tests/control_tower/test_v2_job_service.py `
  tests/control_tower/test_v2_stage_progression.py `
  tests/control_tower/test_v2_worker_stage.py

git diff --check
```

#### Commit message

```text
feat(f15): extend governed pipeline through spring boot 4
```

### Phase 5. Governed Stage 3 to Stage 4 progression

#### Goal

Queue Stage 4 only from accepted, persisted Stage 3 output.

#### Files to edit

```text
migration_factory/control_tower/application/v2_stage_progression.py
migration_factory/control_tower/application/v2_gate_action_service.py
migration_factory/control_tower/application/v2_phase_gate_service.py
tests/control_tower/test_v2_stage_progression.py
tests/control_tower/test_v2_stage_progression_policy.py
tests/control_tower/test_v2_phase_gate_service.py
```

Only edit the gate services if the existing accepted-revision lookup cannot be
reused without an adapter. Prefer adding a narrow resolver dependency to
`V2StageProgressionService`.

#### Donor references to inspect

Donor Stage 4 config only. Do not copy donor progression wholesale:

```powershell
git show ac0ce3e7232b42fd706eda782b0d6932dd1325d3 -- `
  migration_factory/control_tower/application/v2_stage_progression.py
```

#### Exact implementation steps

1. Extend chain validation to allow `3 -> 4`.
2. Keep exact-next-stage validation.
3. Replace any direct path argument in the F15 entry point with accepted-output
   resolution.
4. Resolve Stage 3 output from:

   - persisted Stage 3 command result;
   - accepted Stage 3 artifact revision;
   - gate-bound artifact references/checksum.

5. Reject:

   - missing output;
   - draft revision;
   - superseded revision;
   - checksum mismatch;
   - open/rejected Stage 3 gate;
   - requested jump from Stage 2 to Stage 4.

6. Build the Stage 4 command only after successful resolution.
7. Preserve continuation policy:

   - `AUTO_ON_GREEN`;
   - `MANUAL`;
   - `MANUAL_ON_WARNING_OR_FAILURE`.

8. Preserve `gate_id` and `decision_id` on queued commands.

#### Tests to add/update

- Stage 4 cannot be queued without Stage 3 persisted output.
- Draft Stage 3 revision cannot feed Stage 4.
- Superseded Stage 3 revision cannot feed Stage 4.
- Stage 4 cannot be skipped to.
- Checksum mismatch blocks progression.
- Manual continuation policy opens/uses the current F15 gate flow.
- Accepted Stage 3 output queues one idempotent Stage 4 command.

#### Commands to run

```powershell
python -m pytest `
  tests/control_tower/test_v2_stage_progression.py `
  tests/control_tower/test_v2_stage_progression_policy.py `
  tests/control_tower/test_v2_phase_gate_service.py

git diff --check
```

#### Commit message

```text
feat(f15): extend governed pipeline through spring boot 4
```

### Phase 6. Stage 4 terminal orchestrator behavior

#### Goal

Make Stage 4 the terminal governed stage without weakening proof or gates.

#### Files to edit

```text
migration_factory/control_tower/application/v2_orchestrator_runner.py
migration_factory/orchestrator/artifact_validation.py
migration_factory/orchestrator/summary.py
tests/control_tower/test_v2_orchestrator_runner.py
tests/control_tower/test_v2_cockpit_events.py
```

#### Donor references to inspect

```powershell
git show ac0ce3e7232b42fd706eda782b0d6932dd1325d3 -- `
  migration_factory/control_tower/application/v2_orchestrator_runner.py `
  migration_factory/orchestrator/artifact_validation.py `
  migration_factory/orchestrator/summary.py
```

#### Exact implementation steps

1. Keep current strict-success proof checks.
2. Keep current phase-gate creation and repair routing.
3. Do not stop at Stage 3.
4. For successful Stage 3, apply current continuation policy and queue Stage 4.
5. For Stage 4:

   - emit normal transform/build/test/proof events;
   - create/resolve required completion gates;
   - persist accepted terminal artifacts;
   - emit terminal migration completion only when governance is satisfied;
   - never call next-stage queueing.

6. Replace hard-coded Stage 3 final-report event logic. Report generation is a
   separate governed operation after terminal eligibility.
7. Do not use `AI_MIGRATION_DEFER_FINAL_REPORT`.
8. If intermediate-stage orchestration currently generates final artifacts,
   introduce an explicit backend field such as:

   ```python
   final_report_policy = "terminal_stage_only"
   terminal_stage_index = 4
   ```

   This policy must originate from backend pipeline configuration, not request
   data or ambient environment.

#### Tests to add/update

- Stage 3 success queues Stage 4.
- Stage 4 success does not queue Stage 5.
- Stage 4 failure enters existing failure/repair behavior.
- Stage 4 does not bypass approval/reviewer gates.
- Migration-completed event occurs only after terminal governance.
- No automatic report-completed event is emitted for Stage 3.

#### Commands to run

```powershell
python -m pytest `
  tests/control_tower/test_v2_orchestrator_runner.py `
  tests/control_tower/test_v2_cockpit_events.py

git diff --check
```

#### Commit message

```text
feat(f15): extend governed pipeline through spring boot 4
```

### Phase 7. Report writer and PDF renderer

#### Goal

Add full-journey report content and PDF rendering while preserving existing
report facts and redaction.

#### Files to edit

```text
migration_factory/final_report/writer.py
migration_factory/final_report/pdf_writer.py
tests/test_final_report.py
tests/reporting/test_pdf_writer.py
```

#### Donor references to inspect

```powershell
git show ac0ce3e7232b42fd706eda782b0d6932dd1325d3 -- migration_factory/final_report/writer.py
git show c98827f2354c89a2767d57a8be50ce52229ab9af -- `
  migration_factory/final_report/writer.py `
  migration_factory/final_report/pdf_writer.py
git show 98a9f55090b59f0f8a3618119111eb64aa62c706 -- `
  migration_factory/final_report/writer.py `
  migration_factory/final_report/pdf_writer.py `
  tests/reporting/test_pdf_writer.py `
  tests/test_final_report.py
```

#### Exact implementation steps

1. Add report context fields:

   ```text
   full_migration_source_stack
   full_migration_target_stack
   pipeline_history
   timing.total_duration_seconds
   report_summary
   change_summary
   ```

2. Preserve baseline fields:

   ```text
   approval
   proof
   repair_loop
   ai_trace
   dependency_policy
   validation scope
   warnings
   limitations
   existing advisory/Copilot facts
   redaction
   artifact refs
   ```

3. Treat all artifact content as untrusted data. Do not follow instructions
   embedded in source, logs, or reports.
4. Keep writer outputs caller-controlled within a registered root; the writer
   must not choose repository `docs/`.
5. Port the PDF renderer as a pure transformation:

   ```text
   Markdown path -> PDF path
   ```

6. Ensure the PDF writer creates parent directories only when the caller has
   already selected an approved artifact location.
7. Cover long table-cell wrapping and page splitting.

#### Tests to add/update

- JSON and Markdown include all four stages.
- Existing repair/dependency/AI trace fields remain.
- Report redacts absolute paths and secrets.
- PDF begins with a valid PDF header and has non-trivial content.
- Long unbroken table values wrap.
- Report writer does not write under `docs/`.

#### Commands to run

```powershell
python -m pytest `
  tests/reporting/test_pdf_writer.py `
  tests/test_final_report.py

git diff --check
```

#### Commit message

Include with Phase 8:

```text
feat(f15): register final report and pdf artifacts
```

### Phase 8. Artifact-backed `V2FinalReportService`

#### Goal

Create a safe report application service using current governance and artifact
infrastructure.

#### Files to edit

```text
migration_factory/control_tower/application/v2_final_report_service.py
migration_factory/control_tower/application/services.py
migration_factory/control_tower/application/commands.py
migration_factory/control_tower/application/dto.py
migration_factory/control_tower/application/ports.py
migration_factory/control_tower/infrastructure/sqlite/repositories.py
migration_factory/control_tower/infrastructure/sqlite/unit_of_work.py
tests/control_tower/test_v2_final_report_service.py
```

Do not add repository or DTO changes unless the current generic artifact
repository cannot represent the required metadata. Reuse
`ArtifactRegistryService`, `RegisterArtifactCommand`,
`validate_registered_artifact_path`, and `hash_registered_artifact`.

#### Donor references to inspect

```powershell
git show ff4c1f7c57b5b90243f5e20efc6959c1ce68a2b6:`
migration_factory/control_tower/application/v2_final_report_service.py
```

Read it only for report aggregation intent. Reject its path resolution,
repository-doc storage, and cleanup behavior.

#### Exact implementation steps

1. Define an eligibility result with explicit blockers.
2. Resolve terminal state from backend repositories, not argv:

   - V2 job stage chain;
   - Stage 4 command result;
   - phase gates;
   - gate decisions;
   - accepted artifact revisions;
   - proof/event records.

3. Resolve report inputs only through registered artifact references or
   gate-bound accepted refs.
4. Compute a canonical report-input checksum.
5. If an existing report revision has the same input checksum, return it.
6. Otherwise:

   - allocate a backend-owned report artifact directory/root;
   - generate JSON and Markdown;
   - generate PDF;
   - validate containment;
   - hash each output;
   - register each output;
   - persist an explicit report revision/lineage record if needed;
   - emit artifact-written and report-completed events without paths.

7. Return:

   ```python
   @dataclass(frozen=True)
   class V2ReportArtifactSummary:
       artifact_id: str
       kind: str
       checksum_sha256: str
       size_bytes: int
       content_type: str
       download_url: str
   ```

8. Never return absolute paths or filesystem refs.

#### Tests to add/update

- Eligibility fails before Stage 4 completion.
- Eligibility fails with open/rejected required gates.
- Eligibility fails without accepted terminal revision/proof.
- Inputs come from registered artifacts, not command argv.
- JSON, Markdown, and PDF are registered with checksums.
- Response has no absolute path fields.
- Same input checksum is idempotent.
- Changed input checksum creates an explicit revision.
- No files appear under repository `docs/`.

#### Commands to run

```powershell
python -m pytest `
  tests/control_tower/test_v2_final_report_service.py `
  tests/test_final_report.py `
  tests/reporting/test_pdf_writer.py

git diff --check
```

#### Commit message

```text
feat(f15): register final report and pdf artifacts
```

### Phase 9. Safe report API routes

#### Goal

Expose report status/generation/download without exposing paths.

#### Files to edit

```text
migration_factory/control_tower/adapters/fastapi/app.py
tests/control_tower/test_v2_e2e.py
tests/control_tower/test_v2_cockpit_events.py
tests/control_tower/test_v2_final_report_service.py
```

#### Donor references to inspect

```powershell
git show ac0ce3e7232b42fd706eda782b0d6932dd1325d3 -- migration_factory/control_tower/adapters/fastapi/app.py
git show c98827f2354c89a2767d57a8be50ce52229ab9af -- migration_factory/control_tower/adapters/fastapi/app.py
```

#### Exact implementation steps

Add routes conceptually equivalent to:

```text
GET  /v1/v2/jobs/{job_id}/report
POST /v1/v2/jobs/{job_id}/report
GET  /v1/v2/jobs/{job_id}/report-artifacts/{artifact_id}/download
```

Status response:

```text
job_id
status
eligible
blockers
generated_at
input_checksum
redacted_summary
artifacts[]
```

Generation:

- Empty body or a body containing only idempotency metadata.
- No path, stage, argv, env, command, profile, or sandbox fields.
- Actor comes from the existing actor provider.
- Events contain IDs, kinds, checksums, and statuses, not paths.

Download:

- Query artifact by `(job_id, artifact_id)`.
- Restrict to report artifact kinds.
- Validate registered root containment.
- Recompute and compare checksum.
- Validate expected content type.
- Stream with a safe server-generated filename.

Do not implement:

```python
FileResponse(Path(report.run_report_pdf))
```

#### Tests to add/update

- Status returns 404 only for unknown jobs; known job without report returns a
  structured not-generated status.
- Generation rejects extra path/command fields.
- Download rejects unknown artifact ID.
- Download rejects wrong job ID.
- Download rejects checksum mismatch.
- Download rejects non-report artifact.
- Responses contain no absolute paths.
- Events contain no filesystem paths.

#### Commands to run

```powershell
python -m pytest `
  tests/control_tower/test_v2_e2e.py `
  tests/control_tower/test_v2_cockpit_events.py `
  tests/control_tower/test_v2_final_report_service.py

git diff --check
```

#### Commit message

```text
feat(f15): expose safe report artifact APIs
```

### Phase 10. Frontend contracts/API client

#### Goal

Add path-free report contracts and centralized client methods.

#### Files to edit

```text
web/control-tower/lib/contracts.ts
web/control-tower/lib/controlTowerApi.ts
web/control-tower/tests/controlTowerApi.test.ts
```

#### Donor references to inspect

```powershell
git show c98827f2354c89a2767d57a8be50ce52229ab9af -- `
  web/control-tower/lib/contracts.ts `
  web/control-tower/lib/controlTowerApi.ts `
  web/control-tower/tests/controlTowerApi.test.ts
```

#### Exact implementation steps

1. Add path-free report types.
2. Add:

   ```typescript
   getV2FinalReport(jobId)
   generateV2FinalReport(jobId)
   ```

3. Use the `download_url` returned for each artifact.
4. If a helper is needed, validate it as an API-relative URL. Do not accept
   filesystem paths.
5. Keep `requireJobId`.
6. If `NewMigrationForm.tsx` must change for any reason, move its direct API
   helpers into `controlTowerApi.ts`; otherwise leave it unchanged.

#### Tests to add/update

- Empty job ID fails before fetch.
- Routes URL-encode job ID.
- Generate uses POST with no path-bearing body.
- Artifact summaries contain IDs/checksums/content types/download URLs.
- Contracts do not define `run_dir`, `sandbox_path`, or `run_report_*`.
- Download links are API URLs, not filesystem paths.

#### Commands to run

```powershell
npm --prefix web/control-tower test -- tests/controlTowerApi.test.ts
npm --prefix web/control-tower run typecheck
```

#### Commit message

Include with Phase 9:

```text
feat(f15): expose safe report artifact APIs
```

### Phase 11. Cockpit report panel

#### Goal

Add report status/generate/download controls without removing or redesigning
existing governance panels.

#### Files to edit

```text
web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx
web/control-tower/tests/migrationCockpit.test.tsx
```

Do not edit global CSS unless a minimal class is impossible within existing
styles. Broad styling changes are out of scope.

#### Donor references to inspect

```powershell
git show c98827f2354c89a2767d57a8be50ce52229ab9af -- `
  web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx `
  web/control-tower/tests/migrationCockpit.test.tsx
```

#### Exact implementation steps

1. Load report status through `getV2FinalReport`.
2. Use backend `eligible` and `blockers`; do not infer readiness from highest
   `chain_status`.
3. Add a compact panel showing:

   - status;
   - redacted summary;
   - eligibility blockers;
   - artifact kind/checksum/size;
   - explicit generate/regenerate action;
   - explicit download links.

4. Do not automatically start a download after generation.
5. Do not show report paths.
6. Preserve:

   - gate panel;
   - evidence panel;
   - approval panel;
   - repair/failure panel;
   - assistant panel;
   - POM review panel.

7. Do not add controls for starting Stage 4, selecting Stage 4 input, or
   selecting a report root.

#### Tests to add/update

- Four stages render in order.
- No manual Stage 4 start control exists.
- Report generation uses backend eligibility.
- Generate does not automatically download.
- Download uses response URL.
- Existing gate/approval/repair/assistant panels still render.
- No path fields are displayed.

#### Commands to run

```powershell
npm --prefix web/control-tower test -- `
  tests/migrationCockpit.test.tsx `
  tests/controlTowerApi.test.ts

npm --prefix web/control-tower run typecheck
```

#### Commit message

```text
feat(f15): add cockpit report controls
```

### Phase 12. Focused tests and verification

#### Goal

Verify the complete Stage 4/report flow without running unrelated suites.

#### Files to edit

Only focused tests required by failures discovered in this phase.

#### Donor references to inspect

All donor focused tests, but retain baseline governance assertions.

#### Exact implementation steps

1. Run the backend targeted set.
2. Run the frontend targeted set.
3. Run frontend typecheck and build.
4. Run diff checks.
5. Inspect staged names before each commit.
6. Confirm no generated report files exist under `docs/`.
7. Confirm no absolute path response fields were introduced.

#### Tests to add/update

See Section 9.

#### Commands to run

```powershell
python -m pytest `
  tests/agents/planning_agent/test_boot4_stage_profile.py `
  tests/control_tower/test_sqlite_migrations.py `
  tests/control_tower/test_v2_phase_gate_migration.py `
  tests/control_tower/test_v2_artifact_revision_migration.py `
  tests/control_tower/test_v2_job_service.py `
  tests/control_tower/test_v2_setup_service.py `
  tests/control_tower/test_v2_stage_progression.py `
  tests/control_tower/test_v2_stage_progression_policy.py `
  tests/control_tower/test_v2_orchestrator_runner.py `
  tests/control_tower/test_v2_worker_stage.py `
  tests/control_tower/test_v2_cockpit_events.py `
  tests/control_tower/test_v2_final_report_service.py `
  tests/reporting/test_pdf_writer.py `
  tests/test_final_report.py

npm --prefix web/control-tower test -- `
  tests/controlTowerApi.test.ts `
  tests/migrationCockpit.test.tsx `
  tests/newMigrationForm.test.tsx

npm --prefix web/control-tower run typecheck
npm --prefix web/control-tower run build

git diff --check
git diff --cached --check
git status --short
```

#### Commit message

```text
test(f15): cover stage 4 and report governance
```

## 8. Code Skeletons

These snippets are focused implementation guides, not drop-in full files.

### 0046 migration shape

The actual migration must reproduce every post-0045 column, index, and trigger.

```sql
-- 0046_v2_stage4_support.sql
-- Widen all V2/F15 stage constraints from 1..3 to 1..4.
-- Preserve complete post-0045 schemas and immutable behavior.

ALTER TABLE v2_stage_commands
RENAME TO v2_stage_commands_old_0046;

DROP INDEX ix_v2_stage_commands_job;
DROP TRIGGER v2_stage_commands_no_update;
DROP TRIGGER v2_stage_commands_no_delete;

CREATE TABLE v2_stage_commands (
    command_id TEXT PRIMARY KEY,
    job_id TEXT NOT NULL,
    stage_index INTEGER NOT NULL CHECK (stage_index IN (1, 2, 3, 4)),
    manifest_checksum TEXT NOT NULL,
    argv_json TEXT NOT NULL DEFAULT '[]',
    env_json TEXT NOT NULL DEFAULT '{}',
    status TEXT NOT NULL DEFAULT 'manifest_ready',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    result_json TEXT,
    gate_id TEXT,
    decision_id TEXT
);

INSERT INTO v2_stage_commands (
    command_id,
    job_id,
    stage_index,
    manifest_checksum,
    argv_json,
    env_json,
    status,
    created_at,
    updated_at,
    result_json,
    gate_id,
    decision_id
)
SELECT
    command_id,
    job_id,
    stage_index,
    manifest_checksum,
    argv_json,
    env_json,
    status,
    created_at,
    updated_at,
    result_json,
    gate_id,
    decision_id
FROM v2_stage_commands_old_0046;

CREATE INDEX ix_v2_stage_commands_job
ON v2_stage_commands(job_id, stage_index);

CREATE TRIGGER v2_stage_commands_no_update
BEFORE UPDATE ON v2_stage_commands
BEGIN
    SELECT RAISE(ABORT, 'v2_stage_commands is append-only');
END;

CREATE TRIGGER v2_stage_commands_no_delete
BEFORE DELETE ON v2_stage_commands
BEGIN
    SELECT RAISE(ABORT, 'v2_stage_commands is append-only');
END;

DROP TABLE v2_stage_commands_old_0046;

-- Repeat the complete post-0045 pattern for:
-- v2_approval_decisions
-- v2_resume_commands
-- v2_pending_action_drafts
-- v2_job_events
-- v2_phase_gates
-- v2_artifact_revisions
--
-- Preserve partial unique indexes:
-- uq_v2_phase_gates_open
-- uq_v2_artifact_revisions_accepted
--
-- Preserve phase-gate immutability triggers.
```

### Stage 4 validation constants/helpers

```python
PIPELINE_ID = "springboot-216-to-400-java21-four-stage"
TERMINAL_STAGE_INDEX = 4

STAGE_INPUTS = {
    1: {"pipeline_stage": "Stage 1", "input_kind": "legacy_source"},
    2: {"pipeline_stage": "Stage 2", "input_kind": "stage_1_sandbox"},
    3: {"pipeline_stage": "Stage 3", "input_kind": "stage_2_sandbox"},
    4: {"pipeline_stage": "Stage 4", "input_kind": "stage_3_sandbox"},
}

STAGE_CONFIG = {
    # Existing Stage 2 and Stage 3 entries remain.
    4: {
        "profile": "springboot-3.5-java21-to-4.0-java21",
        "jdk_env": "JAVA21_HOME",
        "jdk_id": "java21",
        "expected_major": 21,
    },
}


def is_terminal_stage(stage_index: int) -> bool:
    return stage_index == TERMINAL_STAGE_INDEX


def validate_next_stage(current_stage: int, requested_stage: int) -> None:
    if requested_stage != current_stage + 1:
        raise StageProgressionBlocked("Stages must progress sequentially")
    if requested_stage not in (2, 3, 4):
        raise StageProgressionBlocked("Requested stage is outside the pipeline")
```

### Stage 4 progression guard

Adapt this to the existing `V2StageProgressionService`,
`SqliteArtifactRevisionRepository`, and gate services:

```python
def queue_next_stage_from_persisted(
    self,
    *,
    job_id: str,
    setup_id: str,
    current_stage: int,
    gate_id: str | None = None,
    decision_id: str | None = None,
) -> StageContinuationResult:
    requested_stage = current_stage + 1
    self.validate_stage_chain(job_id, current_stage, requested_stage)

    if requested_stage == 4:
        accepted = self._artifact_revision_repo.find_accepted(
            job_id=job_id,
            stage_index=3,
            revision_kind="stage_output",
        )
        if accepted is None:
            raise StageProgressionBlocked(
                "Stage 4 requires accepted Stage 3 output"
            )

        stage3_output = self._stage_output_resolver.resolve_accepted_output(
            job_id=job_id,
            stage_index=3,
            revision=accepted,
        )
        if stage3_output is None:
            raise StageProgressionBlocked(
                "Accepted Stage 3 output could not be resolved"
            )

        return self._queue_backend_owned_command(
            job_id=job_id,
            setup_id=setup_id,
            from_stage=3,
            to_stage=4,
            source=stage3_output,
            profile=STAGE_CONFIG[4]["profile"],
            gate_id=gate_id,
            decision_id=decision_id,
        )

    return self._queue_existing_governed_transition(...)
```

The public HTTP/chatbot surface must not expose the `source` or
`sandbox_path` parameter.

### Terminal Stage 4 guard

```python
if stage_index == TERMINAL_STAGE_INDEX:
    self._record_terminal_stage_evidence(...)
    self._open_or_resolve_terminal_completion_gate(...)
    self._event(
        job_id=job_id,
        stage=stage_index,
        event_type="migration_completed",
        status="completed",
        message="Governed Stage 4 migration completed.",
        payload={
            "command_id": command_id,
            "final_status": final_status,
        },
    )
    return

self._auto_queue_next_stage(...)
```

Do not include `sandbox_path` in public event payloads.

### Report artifact registration

Adapt to existing `ArtifactRegistryService` and registered-root DTOs:

```python
@dataclass(frozen=True)
class V2ReportArtifactSummary:
    artifact_id: str
    kind: str
    checksum_sha256: str
    size_bytes: int
    content_type: str
    download_url: str


def _register_report_output(
    *,
    job_id: str,
    command_id: str,
    registered_root_id: str,
    relative_path: str,
    kind: str,
    content_type: str,
) -> V2ReportArtifactSummary:
    artifact = artifact_registry.register_artifact(
        RegisterArtifactCommand(
            job_id=job_id,
            command_id=command_id,
            kind=kind,
            registered_root_id=registered_root_id,
            relative_path=relative_path,
        )
    )
    return V2ReportArtifactSummary(
        artifact_id=artifact.artifact_id,
        kind=kind,
        checksum_sha256=artifact.sha256,
        size_bytes=artifact.size_bytes,
        content_type=content_type,
        download_url=(
            f"/v1/v2/jobs/{job_id}/report-artifacts/"
            f"{artifact.artifact_id}/download"
        ),
    )
```

Use actual repository DTO field names after inspection. Do not invent a second
artifact registry if the current generic registry can serve the report.

### Report eligibility

```python
def evaluate_report_eligibility(job_id: str) -> ReportEligibility:
    terminal_command = command_repo.latest_for_stage(job_id, stage=4)
    if terminal_command is None or not terminal_command_has_strict_success(terminal_command):
        return ReportEligibility.blocked("stage_4_not_completed")

    if phase_gate_repo.list_open(job_id):
        return ReportEligibility.blocked("required_gate_open")

    accepted = artifact_revision_repo.find_accepted(
        job_id,
        stage_index=4,
        revision_kind="stage_output",
    )
    if accepted is None:
        return ReportEligibility.blocked("accepted_stage_4_output_missing")

    if not proof_repository.has_terminal_success(job_id, stage_index=4):
        return ReportEligibility.blocked("terminal_proof_missing")

    return ReportEligibility.allowed()
```

Use exact current proof/gate repository methods rather than adding duplicate
systems.

### Safe artifact download route

```python
@app.get(
    "/v1/v2/jobs/{job_id}/report-artifacts/{artifact_id}/download"
)
def download_v2_report_artifact(
    job_id: str,
    artifact_id: str,
) -> StreamingResponse:
    with _read_unit_of_work(unit_of_work_factory) as uow:
        _require_v2_job(uow, job_id)
        record = uow.artifacts.get_for_job(job_id, artifact_id)

    if record is None:
        raise _error(404, "V2_REPORT_ARTIFACT_NOT_FOUND", "Report artifact not found.")
    if record.kind not in REPORT_ARTIFACT_KINDS:
        raise _error(404, "V2_REPORT_ARTIFACT_NOT_FOUND", "Report artifact not found.")

    validated = validate_registered_artifact_path(
        registered_roots=backend_registered_roots,
        registered_root_id=record.registered_root_id,
        relative_path=record.relative_path,
    )
    checksum = hash_registered_artifact(validated)
    if checksum.sha256 != record.sha256:
        raise _error(409, "V2_REPORT_ARTIFACT_CHECKSUM_MISMATCH", "Artifact integrity check failed.")

    return stream_registered_artifact(
        validated,
        content_type=REPORT_CONTENT_TYPES[record.kind],
        filename=safe_report_filename(job_id, record.kind),
    )
```

Use the existing path-validation function signatures after inspection. The
route must resolve by artifact identity, not a path from a report response.

### Frontend report contract types

```typescript
export type V2ReportArtifactSummary = {
  artifact_id: string;
  kind: "final_report_json" | "final_report_markdown" | "final_report_pdf";
  checksum_sha256: string;
  size_bytes: number;
  content_type: "application/json" | "text/markdown" | "application/pdf";
  download_url: string;
};

export type V2FinalReportResponse = {
  job_id: string;
  status: "not_generated" | "generating" | "generated" | "blocked" | "failed";
  eligible: boolean;
  blockers: string[];
  generated_at: string | null;
  input_checksum: string | null;
  redacted_summary: string;
  artifacts: V2ReportArtifactSummary[];
};

// Forbidden fields:
// run_report_json
// run_report_markdown
// run_report_pdf
// run_dir
// sandbox_path
```

### `controlTowerApi` report methods

```typescript
export async function getV2FinalReport(
  jobId: string,
): Promise<V2FinalReportResponse> {
  const safeJobId = requireJobId(jobId);
  return getJson<V2FinalReportResponse>(
    `/v1/v2/jobs/${encodeURIComponent(safeJobId)}/report`,
  );
}

export async function generateV2FinalReport(
  jobId: string,
): Promise<V2FinalReportResponse> {
  const safeJobId = requireJobId(jobId);
  return postJson<V2FinalReportResponse>(
    `/v1/v2/jobs/${encodeURIComponent(safeJobId)}/report`,
    {},
  );
}

export function resolveReportDownloadUrl(downloadUrl: string): string {
  if (!downloadUrl.startsWith("/v1/")) {
    throw new Error("Invalid report download URL.");
  }
  return `${CONTROL_TOWER_API_BASE_URL}${downloadUrl}`;
}
```

### Cockpit report panel integration

```tsx
const [report, setReport] = useState<V2FinalReportResponse | null>(null);
const [reportBusy, setReportBusy] = useState(false);

async function refreshReport(): Promise<void> {
  setReport(await getV2FinalReport(normalizedJobId));
}

async function handleGenerateReport(): Promise<void> {
  setReportBusy(true);
  try {
    setReport(await generateV2FinalReport(normalizedJobId));
  } finally {
    setReportBusy(false);
  }
}

<section className="panel">
  <h2>Final Report</h2>
  {report?.blockers.map((blocker) => (
    <p className="warning-text" key={blocker}>{blocker}</p>
  ))}
  <button
    type="button"
    disabled={reportBusy || !report?.eligible}
    onClick={() => void handleGenerateReport()}
  >
    Generate report
  </button>
  {report?.artifacts.map((artifact) => (
    <a
      key={artifact.artifact_id}
      href={resolveReportDownloadUrl(artifact.download_url)}
    >
      Download {artifact.kind}
    </a>
  ))}
</section>
```

Do not create and click an anchor automatically after generation.

## 9. Test Plan

### Backend targeted command

```powershell
python -m pytest `
  tests/agents/planning_agent/test_boot4_stage_profile.py `
  tests/control_tower/test_sqlite_migrations.py `
  tests/control_tower/test_v2_phase_gate_migration.py `
  tests/control_tower/test_v2_artifact_revision_migration.py `
  tests/control_tower/test_v2_job_service.py `
  tests/control_tower/test_v2_setup_service.py `
  tests/control_tower/test_v2_stage_progression.py `
  tests/control_tower/test_v2_stage_progression_policy.py `
  tests/control_tower/test_v2_orchestrator_runner.py `
  tests/control_tower/test_v2_worker_stage.py `
  tests/control_tower/test_v2_cockpit_events.py `
  tests/control_tower/test_v2_final_report_service.py `
  tests/reporting/test_pdf_writer.py `
  tests/test_final_report.py
```

If a listed donor test file does not yet exist, add it in its implementation
phase before running this command.

### Frontend targeted commands

```powershell
npm --prefix web/control-tower test -- `
  tests/controlTowerApi.test.ts `
  tests/migrationCockpit.test.tsx `
  tests/newMigrationForm.test.tsx

npm --prefix web/control-tower run typecheck
npm --prefix web/control-tower run build
```

### Required negative cases

Backend:

- Stage 4 cannot be queued without Stage 3 persisted output.
- Draft Stage 3 revision cannot feed Stage 4.
- Superseded Stage 3 revision cannot feed Stage 4.
- Stage 4 cannot be skipped to.
- Stage 4 is terminal and never followed by Stage 5.
- Report generation fails before Stage 4 completion.
- Report generation fails before required gates/proof are accepted.
- Report response contains no absolute paths.
- Artifact download rejects unknown artifact IDs.
- Artifact download rejects wrong job IDs.
- Artifact download rejects checksum mismatches.
- Artifact download rejects non-report artifacts.
- Report generation is idempotent for the same input checksum or creates
  explicit revisions for changed inputs.
- No report files appear under repository `docs/`.
- Stage 5 database inserts fail.
- Post-0045 command gate/decision references survive migration 0046.

Frontend:

- Report panel does not remove gate, approval, repair, assistant, evidence, or
  POM panels.
- Report generation remains disabled when backend eligibility is false.
- Frontend does not infer eligibility only from stage status.
- Generation does not automatically download PDF.
- API methods never depend on absolute filesystem paths.
- No report contract contains `run_dir`, `sandbox_path`, or `run_report_*`.
- No Stage 4 input/path/start control is present.

### Final repository checks

```powershell
git diff --check
git diff --cached --check
git status --short
```

Also run:

```powershell
git diff --name-only $BASE_COMMIT...HEAD
git grep -n "docs/migration-reports" -- migration_factory web tests
git grep -n -E "run_report_(json|markdown|pdf)|sandbox_path|run_dir" -- `
  web/control-tower/lib/contracts.ts `
  web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx
```

Any match must be reviewed. Test names and explicit forbidden-field assertions
are acceptable; production response fields are not.

## 10. Commit Plan

Commit coherent slices in this order:

1. Database:

   ```text
   feat(f15): add governed stage 4 schema support
   ```

2. Profile, readiness, pipeline, progression, terminal behavior:

   ```text
   feat(f15): extend governed pipeline through spring boot 4
   ```

3. Writer, PDF renderer, artifact registration, report service:

   ```text
   feat(f15): register final report and pdf artifacts
   ```

4. Backend API and frontend API contracts/client:

   ```text
   feat(f15): expose safe report artifact APIs
   ```

5. Cockpit report controls:

   ```text
   feat(f15): add cockpit report controls
   ```

6. Final focused test completion:

   ```text
   test(f15): cover stage 4 and report governance
   ```

For each commit:

```powershell
git status --short
git add <explicit-owned-files>
git diff --cached --check
git diff --cached --name-only
git diff --cached
git commit -m "<commit message>"
git status --short
git log -1 --oneline
```

Never use:

```powershell
git add .
```

Do not stage generated outputs, `.env`, `.next/`, caches, databases, logs,
runtime files, unrelated changes, or `web/control-tower/next-env.d.ts` unless
explicitly owned.

## 11. Do Not Do List

- Do not merge `V2IMPROVMENT`.
- Do not cherry-pick donor commits wholesale.
- Do not use `ours` or `theirs` blindly.
- Do not reuse, rename, or edit donor
  `0039_v2_stage4_support.sql`.
- Do not modify baseline migrations `0039` through `0045`.
- Do not delete or weaken phase gates, gate decisions, checksums, accepted
  revisions, reviewer requirements, repair controls, rollback, or proof.
- Do not allow frontend/chatbot Stage 4 execution or path selection.
- Do not accept `sandbox_path`, argv, env, raw command, report root, or
  filesystem target in new APIs.
- Do not derive report roots by parsing `argv_json`.
- Do not return absolute report paths, run directories, sandbox paths, or
  artifact filesystem refs.
- Do not implement `FileResponse(Path(report.run_report_pdf))`.
- Do not write, copy, clean, or delete reports under
  `docs/migration-reports/<job-id>`.
- Do not commit generated reports.
- Do not use `AI_MIGRATION_DEFER_FINAL_REPORT` as execution policy.
- Do not propagate ambient `MAVEN_OPTS` or `MAVEN_USER_HOME` into manifests.
- Do not treat `highest stage completed` as sufficient report eligibility.
- Do not add direct fetch/API helpers to `NewMigrationForm.tsx`.
- Do not automatically download PDF after generation.
- Do not broadly redesign the cockpit or global UI.
- Do not remove gate, evidence, approval, repair, assistant, or POM panels.
- Do not introduce a second orchestrator, event stream, artifact store,
  repository layer, repair flow, revision system, or validation system.
- Do not run the full test suite unless explicitly requested.
- Do not push `stable` until explicitly requested.
