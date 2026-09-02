# Stable Integration Audit Report

## 1. Executive Verdict

**BLOCKED**

- `stable` HEAD is the expected latest implementation checkpoint, but required backend focused tests fail.
- Stage 4 input is resolved from a persisted command `sandbox_path`, not from an accepted Stage 3 artifact revision bound to a resolved gate/checksum.
- Stage 4 emits `migration_completed` immediately after command proof; terminal completion does not require accepted terminal artifacts or completion-gate resolution.
- Final report outputs are not registered or persisted through the existing artifact registry. Generated artifact IDs exist only in memory.
- Report download reconstructs paths from Stage 4 command result `sandbox_path`; registered-root containment is not validated.
- Pipeline/readiness seed data and existing focused tests were not updated for the four-stage pipeline.
- Migration `0046` appears structurally complete, but required `0046` upgrade/data-preservation and Stage 4/Stage 5 tests are absent.
- Frontend tests, typecheck, and production build pass, but report-specific frontend tests were not added.

## 2. Verified Git State

- current worktree path: `C:\Users\abdelilah.mortaki\Desktop\modernizer-solution-stable`
- branch: `stable`
- HEAD: `7408b4c5d448fcf11dee2d620791dd8de22fe9ea`
- ACTUAL_AUDIT_HEAD: `7408b4c5d448fcf11dee2d620791dd8de22fe9ea`
- base commit: `fad82fdf8619644bade3cdde3a9ad184cd815d3d`
- donor commit: `ff4c1f7c57b5b90243f5e20efc6959c1ce68a2b6`
- merge base: `651f37d4b7213d84d8d661e0d56d154909c211c4`
- initial status: clean
- commits audited: 6
- `7408b4c5` ancestor of ACTUAL_AUDIT_HEAD: yes
- newer commits after `7408b4c5`: none
- playbook status: missing from `stable`; present at `dad01ca5776361b5b1122bfc6109db8cffafde6a` on `frontend/cockpit-chatbot-ui-polish`, which is not an ancestor of `stable`

## 3. Scope Audited

- `migration_factory/agents/planning_agent/output_validator.py`
- V2 job, setup, worker, progression, orchestrator, gate, revision, artifact, and FastAPI code
- `migration_factory/control_tower/infrastructure/sqlite/migrations/0039` through `0046`
- `migration_factory/final_report/writer.py`
- `migration_factory/final_report/pdf_writer.py`
- Boot 4 profile and OpenRewrite catalog
- `web/control-tower/lib/contracts.ts`
- `web/control-tower/lib/controlTowerApi.ts`
- `web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx`
- focused backend and frontend tests named by the playbook
- playbook from the main worktree because it is absent from `stable`

## 4. Commit-by-Commit Review

| commit | declared purpose | files changed | audit result | notes |
|---|---|---:|---|---|
| `980c068` | add governed Stage 4 schema support | 1 | WARN | `0046` widens all seven tables and preserves visible columns/indexes/triggers, but no dedicated `0046` tests were committed. |
| `7505dee` | extend governed pipeline through Spring Boot 4 | 8 | FAIL | Four-stage constants/profile added, but seed data, tests, accepted-revision binding, terminal gates, and report-event logic remain incomplete. |
| `399b7cf` | register final report and PDF artifacts | 3 | BLOCKED | PDF/writer additions work, but report “artifacts” are not registered or persisted. |
| `851f6b9` | expose safe report artifact APIs | 3 | BLOCKED | Response shape is path-free, but download resolves from command-result filesystem paths and lacks registered-root containment. |
| `7b965eb` | add cockpit report controls | 1 | WARN | Compact panel uses backend eligibility and explicit downloads; no report-panel tests were added. |
| `7408b4c` | cover Stage 4 and report governance | 3 | FAIL | Adds only profile, PDF, and shallow report-result tests; required negative and integration cases remain missing. |

## 5. Playbook Compliance Matrix

| requirement | status | evidence | notes |
|---|---|---|---|
| Audit latest `stable` HEAD | PASS | dynamic HEAD is `7408b4c5...` | No newer stable commits. |
| Preserve migrations `0039`–`0045` | PASS | protected migration diff is empty | Files unchanged. |
| Exactly four stages and Boot 4 profile | PASS | `v2_job_service.py:41-48`; `v2_stage_progression.py:25-44` | Constants correct. |
| Backend resolves accepted Stage 3 output | BLOCKED | `v2_stage_progression.py:294-351,400-415` | Resolves raw command `result_json.sandbox_path`; no accepted revision/gate checksum validation. |
| Stage 4 terminal under governance | FAIL | `v2_orchestrator_runner.py:772-806` | Emits terminal completion before terminal gate/artifact acceptance. |
| No Stage 5 | PASS | Stage 4 returns at `v2_orchestrator_runner.py:794-806` | No next-stage call after Stage 4. |
| Artifact-backed report service | BLOCKED | `v2_final_report_service.py:197-283` | `v2_report_artifacts` repository is referenced but does not exist; generated snapshots are not saved. |
| Registered-root download safety | BLOCKED | `app.py:4471-4532` | Reconstructs `sandbox_path/final/...`; no registered-root lookup or containment check. |
| Path-free public report response | PASS | `app.py:11606-11625`; frontend contracts | Response omits absolute path fields. |
| Report eligibility includes proof/gates/revisions | BLOCKED | `v2_final_report_service.py:152-195`; UoW fields at `unit_of_work.py:142,154,156` | Service checks nonexistent `v2_phase_gates` and `v2_artifact_revisions` attributes, so gate/revision checks are skipped in real UoW. No proof repository/event check. |
| Idempotent/versioned report generation | FAIL | random IDs at `v2_final_report_service.py:270` | Input checksum is calculated but never queried or persisted. |
| No report writes under `docs/migration-reports` | PASS | grep returned no matches | Service writes under sandbox or `reports/<job>`, not docs. |
| No ambient final-report policy | PASS | no `AI_MIGRATION_DEFER_FINAL_REPORT` matches | Requirement met. |
| No ambient Maven options in manifests | PASS | stage manifests contain explicit JDK/Maven fields | Setup preflight still copies ambient Maven variables internally, but Stage 4 manifest does not. |
| Frontend centralized report API | PASS | `controlTowerApi.ts:563-588` | Report operations centralized. |
| Focused tests pass | FAIL | multiple backend failures | Cannot mark implementation ready. |
| Playbook committed on `stable` | WARN | file absent from stable tree | Documentation-branch mismatch. |

## 6. Governance Preservation Audit

- Phase gates, gate decisions, gate checksums, artifact revisions, repair governance, assistant constraints, and POM governance remain in the baseline code.
- Protected F15 files listed by the playbook were not modified in the audited range.
- Migrations `0039` through `0045` are unchanged.
- Cockpit gate, evidence, approval, repair/failure, assistant, and POM panels remain present.
- Accepted revision enforcement is not integrated into Stage 3→4 progression. `_validate_stage4_input` only checks for a resolvable command-result path.
- Gate-bound checksum validation is not performed before Stage 4 command creation.
- Stage 4 terminal completion bypasses a terminal completion gate and accepted terminal-artifact persistence.
- Assistant read-only authority was not weakened by the audited diff.
- Backend owns command construction, but internal progression still accepts a raw `sandbox_path` parameter and emits it in event payloads.

## 7. Stage 4 / Spring Boot 4 Audit

- Profile and catalog exist and focused schema/guardrail tests pass.
- Profile is high-risk, sandbox-only, Java 21→21, Boot 3.5→4.0, and human-approval-required.
- Setup readiness code requires the fourth profile/catalog, but existing setup fixtures were not updated; two focused setup tests fail.
- Job service creates four stages and uses `springboot-216-to-400-java21-four-stage`.
- Worker service uses the same pipeline ID.
- Stage 4 config uses `springboot-3.5-java21-to-4.0-java21` and `JAVA21_HOME`.
- `dev_app.py:88` still seeds only `springboot-216-to-356-java21-three-stage`; four-stage job creation cannot find the required pipeline seed.
- `app.py:3992` retains a hard-coded three-stage pipeline projection/default.
- Progression prevents a numeric skip to Stage 4, but does not prove accepted Stage 3 revision lineage.
- Stage 4 is terminal and does not queue Stage 5.
- Public report routes do not accept execution path fields. Existing continuation surfaces still contain legacy `sandbox_path` contracts; F15 manual policy rejects client-supplied paths, but progression internals remain path-based.

## 8. Migration 0046 Audit

- All seven required tables are recreated with stage checks permitting exactly `1,2,3,4`.
- `v2_stage_commands.gate_id`, `decision_id`, and `v2_approval_decisions.job_id` are preserved.
- Explicit column-copy statements are used for every recreated table.
- Visible post-`0045` columns are preserved.
- Required indexes are recreated, including `uq_v2_phase_gates_open` and `uq_v2_artifact_revisions_accepted`.
- Append-only/no-delete/no-update triggers and resolved/superseded gate immutability are recreated.
- Focused migration command result: 46 passed.
- Coverage gap: the three test files do not mention `0046`, Stage 4, Stage 5, `_old_0046`, or command gate/decision preservation.
- Therefore structural review is positive, but upgrade/data-preservation behavior is not acceptance-proven.

## 9. Report/PDF/Artifact Audit

- Writer and PDF focused tests pass; PDF header/content and wrapping helpers are covered.
- Writer preserves baseline repair, dependency, AI trace, advisory, warning, and artifact fields.
- Eligibility checks Stage 4 command status/result, but `_looks_like_success` is weaker than orchestrator strict-success proof.
- Real UoW exposes `phase_gates` and `artifact_revisions`; service checks `v2_phase_gates` and `v2_artifact_revisions`, skipping both controls.
- Report context is loaded from terminal command `result_json`, not assembled from registered artifacts, accepted revisions, proof records, gates, and event history.
- Report root is derived from `result_json.sandbox_path`.
- JSON/Markdown/PDF hashes are calculated, but no `ArtifactRegistryService` or `RegisterArtifactCommand` call occurs.
- Generated artifact snapshots are returned only in memory and disappear after the request.
- Status reload cannot find generated artifacts because no `v2_report_artifacts` repository exists.
- Download first depends on persisted report metadata that cannot be created by this implementation, then reconstructs a path from Stage 4 `sandbox_path`.
- Download checks checksum and kind, but does not validate a registered root, stored relative path, containment, or immutable artifact ownership.
- No idempotency/version lookup exists; every successful generation creates random IDs.
- No files are written under `docs/migration-reports`.

## 10. Frontend Audit

- Report contracts contain no `run_report_*`, `run_dir`, or report `sandbox_path` fields.
- `getV2FinalReport` and `generateV2FinalReport` use encoded job IDs and centralized API helpers.
- Download URLs must start with `/v1/`.
- Cockpit uses backend `eligible` and `blockers`.
- Generation does not auto-download; user clicks an explicit link.
- No manual Stage 4 start/input/path control was added.
- Existing gate, evidence, approval, repair/failure, assistant, and POM surfaces remain.
- No global CSS redesign was introduced.
- `NewMigrationForm.tsx` was unchanged by this range, though its pre-existing direct fetch helpers remain.
- Frontend focused tests: 71 passed.
- Typecheck: passed.
- Production build: passed.
- Coverage gap: committed frontend tests contain no assertions for report methods, eligibility/blockers, explicit download behavior, four-stage rendering, or forbidden report fields.

## 11. Forbidden Pattern Search

- Command: `git grep -n "docs/migration-reports" -- migration_factory web tests`
  - matches: none
  - classification: OK
- Command: `git grep -n -E "run_report_(json|markdown|pdf)|run_dir|sandbox_path" -- migration_factory web`
  - matches: many baseline/internal orchestration paths; critical new matches in `v2_stage_progression.py`, `v2_final_report_service.py`, and report download route
  - classification: BLOCKER for report path reconstruction and Stage 4 accepted-output bypass; WARN/OK for existing internal sandbox execution fields
- Command: `git grep -n "FileResponse(Path" -- migration_factory web`
  - matches: none
  - classification: OK
- Command: `git grep -n "AI_MIGRATION_DEFER_FINAL_REPORT" -- migration_factory web`
  - matches: none
  - classification: OK
- Command: `git grep -n -E "MAVEN_OPTS|MAVEN_USER_HOME" -- migration_factory web`
  - matches: existing service defaults and `v2_setup_service.py:751-754`
  - classification: WARN; setup preflight copies ambient values, but audited Stage 4 command manifests do not

## 12. Tests Run

| command | result | failures | warnings | notes |
|---|---|---:|---:|---|
| migration tests (`test_sqlite_migrations`, phase-gate migration, artifact-revision migration) | PASS | 0 | 0 | 46 passed; no `0046`-specific coverage. |
| pipeline tests (job, progression, policy, orchestrator, worker) | FAIL | 11 | 2 | 73 passed; stale three-stage tests and missing four-stage seed exposed. |
| report/PDF/writer tests | PASS | 0 | 0 | 26 passed; report service tests are shallow. |
| Boot 4 profile + setup readiness tests | FAIL | 2 | 2 | 50 passed; fixtures lack fourth profile/catalog. |
| V2 E2E + cockpit events + report service | FAIL | 26 | 3 | 10 passed; mostly seed/dependency failures plus stale pipeline expectations. |
| frontend focused tests | PASS | 0 | 0 | 71 passed. |
| frontend typecheck | PASS | 0 | 0 | `tsc --noEmit`. |
| frontend production build | PASS | 0 | 0 | Next.js build completed. |
| `git diff --check` | PASS | 0 | 0 | Before report creation. |
| `git diff --cached --check` | PASS | 0 | 0 | Nothing staged. |

## 13. Missing Tests / Coverage Gaps

- Upgrade through migration `0045`, seed all seven tables, apply `0046`, and verify every value survives.
- Stage 4 insert succeeds and Stage 5 insert fails in all seven tables.
- `gate_id` and `decision_id` survive `0046`.
- Stage 4 requires an accepted, non-superseded Stage 3 artifact revision.
- Draft and superseded Stage 3 revisions cannot feed Stage 4.
- Gate-bound checksum mismatch blocks Stage 4.
- Stage 3→4 command preserves `gate_id` and `decision_id`.
- Stage 4 completion waits for terminal gate/artifact acceptance.
- Stage 4 never queues Stage 5.
- Report generation requires strict terminal proof.
- Report generation fails with open/rejected gates.
- Report generation reads registered artifacts rather than command paths.
- JSON/Markdown/PDF registration and persistence.
- Same input checksum is idempotent; changed checksum creates a revision.
- Download rejects unknown artifact, wrong job, non-report kind, checksum mismatch, and containment escape.
- Report generation rejects path/command fields in request bodies.
- Frontend report API route encoding and path-free contracts.
- Cockpit report eligibility, explicit download, no auto-download, preserved panels, and four-stage rendering.

## 14. Findings

### P0 blocker

1. **File:** `migration_factory/control_tower/application/v2_stage_progression.py`
   - **Problem:** Stage 4 input is not bound to an accepted Stage 3 revision or resolved gate checksum.
   - **Evidence:** `resolve_prior_stage_output` reads `result_json.sandbox_path`; `_validate_stage4_input` only checks that this path exists in persisted JSON.
   - **Recommended fix:** Inject existing artifact-revision and gate-bound resolver services; require accepted Stage 3 `stage_output`, matching gate refs/checksum, and exact `3→4` transition before command creation.

2. **File:** `migration_factory/control_tower/application/v2_orchestrator_runner.py`
   - **Problem:** Stage 4 emits terminal migration completion immediately after strict command proof.
   - **Evidence:** Lines `794-806` emit `migration_completed` and return without terminal completion-gate resolution or accepted terminal revision enforcement.
   - **Recommended fix:** Persist terminal evidence, open/resolve required completion gate, accept terminal revision, then emit completion.

3. **File:** `migration_factory/control_tower/application/v2_final_report_service.py`
   - **Problem:** Report outputs are not registered or persisted; eligibility checks use wrong UoW attribute names.
   - **Evidence:** Real UoW exposes `phase_gates` and `artifact_revisions`; service checks `v2_phase_gates`/`v2_artifact_revisions`. Generated snapshots are returned but never saved. No `ArtifactRegistryService` call exists.
   - **Recommended fix:** Use existing repositories and artifact registry, registered roots, accepted revisions, proof/events, canonical input checksum, and persisted report lineage.

4. **File:** `migration_factory/control_tower/adapters/fastapi/app.py`
   - **Problem:** Download is path-based rather than artifact-record-based.
   - **Evidence:** Lines `4492-4507` derive `file_path` from Stage 4 command `sandbox_path`; no registered-root containment validation occurs.
   - **Recommended fix:** Resolve `(job_id, artifact_id)` through artifact repository, validate kind/root/relative path/containment, recompute checksum, and stream registered artifact.

### P1 must fix before PR

1. **File:** `migration_factory/control_tower/adapters/fastapi/dev_app.py`
   - **Problem:** Seed still uses the three-stage pipeline ID.
   - **Evidence:** Line `88`; focused job and cockpit tests fail because four-stage pipeline seed is absent.
   - **Recommended fix:** Seed the exact V2 four-stage pipeline while preserving V1 three-stage definitions.

2. **File:** focused backend tests
   - **Problem:** Required test suite fails and many expectations still assert no Stage 4.
   - **Evidence:** 11 pipeline failures, 2 setup failures, and 26 E2E/cockpit/report failures.
   - **Recommended fix:** Update fixtures and assertions, then add all negative governance cases listed above.

3. **File:** `migration_factory/control_tower/adapters/fastapi/app.py`
   - **Problem:** Report generation endpoint has no strict empty/idempotency-only request model.
   - **Evidence:** Handler accepts no body model, so extra JSON fields are not explicitly rejected or tested.
   - **Recommended fix:** Add a strict request schema with `extra="forbid"` and only approved idempotency metadata.

4. **File:** `docs/STABLE_INTEGRATION_IMPLEMENTATION_PLAYBOOK.md`
   - **Problem:** Playbook is absent from `stable`.
   - **Evidence:** File exists on `dad01ca` only; that commit is not reachable from stable.
   - **Recommended fix:** Add the approved playbook to stable in a dedicated documentation commit.

### P2 follow-up

1. **File:** `migration_factory/control_tower/application/v2_setup_service.py`
   - **Problem:** Preflight subprocess environment copies ambient `MAVEN_OPTS`/`MAVEN_USER_HOME`.
   - **Evidence:** Lines `751-754`.
   - **Recommended fix:** Prefer explicit backend configuration or document why preflight-only propagation is safe.

2. **File:** `web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx`
   - **Problem:** Report load/generation errors are silently swallowed or not shown.
   - **Evidence:** `refreshReport` catches without state; generation has no error state.
   - **Recommended fix:** Add bounded user-visible error status without path leakage.

## 15. Final Recommendation

**blocked until P0 findings are fixed**
