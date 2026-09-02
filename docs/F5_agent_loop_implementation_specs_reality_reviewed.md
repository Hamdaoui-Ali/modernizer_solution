# DEMO3 F5 — Reality-Reviewed Agent Loop Implementation Specs

**Purpose:** Review and improve the previous F5 implementation spec against the current `demov3` codebase and the uploaded Reality Mood skill.

**Current base to start from:** `demov3` at `ca346fec007badace732172be3c7d38077f5d80b` (`feat: produce reviewed Analysis and Planning artifacts`).

**Current verdict:** F5 is architecturally ready, but the original spec was too optimistic about task independence. F5 must be implemented as a tightly governed extension of the F2 review-chain path, not as a new repair subsystem and not as a raw Copilot wrapper.

---

## Web Check

External checks that matter for this implementation:

1. **LLM output is untrusted by default.** OWASP lists prompt injection and insecure/improper LLM output handling as major LLM application risks. For this project, that means Repair LLM outputs must be parsed, schema-validated, policy-validated, checksum-bound, reviewed by a second model, then approved by a human before any backend apply.
   - Source: https://owasp.org/www-project-top-10-for-large-language-model-applications/
   - Source: https://genai.owasp.org/llmrisk/llm01-prompt-injection/
   - Source: https://genai.owasp.org/llmrisk/llm05-supply-chain-vulnerabilities/

2. **Structured model output is the right direction, but not sufficient alone.** OpenAI structured outputs/function calling can force JSON-schema-shaped responses, but the backend still needs semantic validation: checksum match, diff format, path policy, stale repo state, reviewer acceptance, and human gate.
   - Source: https://developers.openai.com/api/docs/guides/structured-outputs
   - Source: https://developers.openai.com/api/docs/guides/function-calling

3. **`git apply --check` is useful but not a security boundary.** Official Git docs say `git apply --check` checks whether the patch applies; it does not decide whether the patch is safe, in scope, reviewed, human-approved, or policy-compliant. F5 must keep `patch_gate.py` path/security validation before apply and verify exact checksum before apply.
   - Source: https://git-scm.com/docs/git-apply

4. **Subprocess execution must remain backend-owned and command-list based.** Python docs warn about shell behavior and security considerations around `shell=True`. F5 should not introduce raw shell strings from model/frontend/chatbot and should not let the LLM choose validation commands.
   - Source: https://docs.python.org/3/library/subprocess.html

5. **Add CI/security follow-up after F5.** GitHub code scanning and secret scanning/push protection are appropriate next controls once the repair loop can propose patches, because repair-generated diffs increase the chance of unsafe code or accidental secrets entering commits.
   - Source: https://docs.github.com/code-security/code-scanning/introduction-to-code-scanning/about-code-scanning-with-codeql
   - Source: https://docs.github.com/en/code-security/concepts/secret-security/push-protection

---

## Critical Review (HONEST)

### What is solid

- The core F5 direction is correct: **reuse the F2 review-chain pattern**.
- The current code already has important foundations:
  - `migration_factory/orchestrator/review_chain.py` for Analysis/Planning proposer-reviewer production.
  - `migration_factory/control_tower/application/v2_review_chain_contracts.py` for checksum/contract validation.
  - `migration_factory/control_tower/application/v2_repair_flow.py` for proposal/apply lifecycle.
  - `migration_factory/control_tower/application/v2_repair_gate_service.py` and `v2_gate_action_service.py` for repair gate decisions.
  - `migration_factory/repair_loop/patch_gate.py` for deterministic patch safety validation.
  - `migration_factory/repair_loop/patch_apply.py` for snapshot + `git apply --check` + apply + rollback.
  - `migration_factory/repair_loop/validation_runner.py` for rerun proof.
- The previous spec correctly identified the big gap: the old repair path is Copilot-centered, while F5 needs the same proposer/reviewer/human-gate backend-apply pattern that F2 now uses.

### What was wrong or too weak in the previous implementation spec

1. **It treated 15 subtasks as independently assignable, but they are not.** F5-T4 through F5-T13 share contracts, persistence, checksums, proposal IDs, gate state, and apply semantics. A sub-agent can work on a slice only if prior contract outputs are already merged or mocked in a strict way.

2. **It did not clearly separate three different records:**
   - `V2RepairProposalRecord`: lifecycle/proposal database row.
   - `ArtifactRevisionRecord(revision_kind="repair")`: immutable reviewed repair artifact/revision history.
   - Patch/apply/rollback artifacts: proof of filesystem mutation and validation after apply.
   Mixing these will create subtle bugs where approval status is treated as artifact immutability or vice versa.

3. **It leaned too casually on `git apply --check`.** That check only verifies patch applicability. F5 still needs diff schema validation, path validation, blocked-path validation, policy validation, checksum validation, stale-repo validation, and exact artifact load-by-ref before apply.

4. **It was ambiguous about checksums and volatile fields.** `created_at` should not be part of stable content checksums unless the checksum is explicitly an envelope checksum. For reproducibility, use two checksum layers:
   - `content_checksum`: stable, excludes volatile metadata.
   - `artifact_checksum`: includes full persisted envelope if needed.

5. **It under-specified repo state checksum.** A full repository hash can be expensive and unstable. F5 needs a defined `base_repo_state_checksum`, preferably based on touched files + relevant project metadata + profile route + accepted upstream artifacts. Approval/apply must reject if those inputs changed.

6. **It did not explicitly call out SQLite migration risk.** If F5 adds columns/tables for repair artifacts, policy validation, apply result, proof, or review-chain metadata, it must add SQLite migrations and repository tests. Avoid adding ad-hoc JSON files without persistence/search strategy.

7. **It said “replace Copilot path” but did not give a safe transition strategy.** Do not rip out old code blindly. New F5 should make the Control Tower V2 path authoritative and leave old Copilot repair loop disabled/quarantined unless existing tests require it. Remove or hard-disable old behavior only when tests prove no active path depends on it.

8. **It did not define public projection hard enough.** Full diff exposure can be large or sensitive. API should expose summary, changed files, risk, reviewer notes, checksums, and bounded diff preview; full diff download should be artifact-ref-based and redacted/safe.

9. **It did not force model output schema discipline.** F5 must use strict coercion with explicit failure for malformed model output. Prompt instructions alone are not enough.

10. **It did not force final F5 e2e proof early enough.** Unit tests are necessary, but F5 is a workflow feature. The implementation must include at least one synthetic end-to-end path: failure evidence -> repair proposal -> reviewer accept -> policy allow -> human approve -> exact patch apply -> validation rerun proof.

---

## Suggested Fix

Use the revised implementation loop below. It keeps the requested subtask format, but adds sharper dependencies, persistence boundaries, checksum semantics, and “do not duplicate current code” rules.

# Revised F5 Agent Loop File

## Global execution rules for all F5 agents

> SUMMARY: Start from `demov3` at `ca346fe`, reuse F2/F4 governance, and never let model output become execution authority.

### Required preflight

```powershell
git fetch origin
git switch demov3
git pull --ff-only origin demov3
git status --short
git log -1 --oneline
git rev-parse HEAD
git rev-parse origin/demov3
```

Expected:

```text
HEAD == origin/demov3
HEAD is ca346fec007badace732172be3c7d38077f5d80b or later
working tree clean
```

Recommended branch:

```powershell
git switch -c demo3/f5-build-test-repair-review-loop
```

### Non-negotiable architecture rules

- Backend is execution authority.
- Assistant/chatbot may explain or draft, but must not apply or approve.
- Primary Repair LLM proposes only.
- Reviewer Repair LLM reviews only.
- Human gate decides.
- Backend applies only exact reviewed, policy-allowed, checksum-bound diff loaded from artifact storage.
- No raw diff from frontend/API request body is authoritative.
- No arbitrary `sandbox_path`, `argv`, `env`, `raw_command`, `endpoint`, `deployment`, `provider`, `env_ref`, `filesystem_target`, or `user_supplied_file_path` in public/model contracts.
- Keep old Copilot-centered repair path non-authoritative for F5. Do not wrap Copilot as the F5 reviewer/proposer chain.

### Recommended implementation batching

1. **Foundation contracts:** F5-T1, F5-T2, F5-T3.
2. **Repair review-chain producer:** F5-T4, F5-T5, F5-T6.
3. **Policy + gates:** F5-T7, F5-T8, F5-T9.
4. **Apply/rerun/proof:** F5-T10, F5-T11, F5-T12, F5-T13.
5. **Presentation + final matrix:** F5-T14, F5-T15.

---

## F5-T1 — Build/Test Failure Evidence Capture
> SUMMARY: Create stable, redacted, backend-owned failure evidence before any Repair LLM prompt exists.

### TASK
Implement a deterministic failure evidence builder for build/test/validation failures. It must normalize logs, compiler/test errors, profiles, changed files, repo-state signals, upstream accepted artifacts, and artifact refs into a stable evidence object.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/agents/build_agent/agent.py`
- `migration_factory/agents/build_agent/classifier.py`
- `migration_factory/agents/test_agent/agent.py`
- `migration_factory/repair_loop/evidence_collector.py`
- `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- `migration_factory/control_tower/application/redaction.py`
- `migration_factory/control_tower/domain/checksums.py`

Reality check:
- `collect_failure_evidence()` exists, but it is not yet a F2-style deterministic artifact anchor.
- Do not confuse evidence collection with LLM repair proposal creation.

### APPROACH
1. Create `migration_factory/repair_loop/failure_evidence.py`.
2. Define strict models/dataclasses:
   - `FailureSource`: `build`, `test`, `validation`, `transform`, `unknown`.
   - `NormalizedCompilerError`.
   - `NormalizedTestFailure`.
   - `FailureEvidence`.
3. Define two checksum layers:
   - `content_checksum`: canonical checksum over stable normalized evidence excluding volatile `created_at`.
   - `artifact_checksum`: checksum over persisted envelope if needed.
4. Include refs, not unbounded logs:
   - full build/test logs stay as artifacts.
   - evidence object includes bounded `stdout_tail`, `stderr_tail`, `safe_log_preview`.
5. Sort all arrays deterministically: changed files, compiler errors, test failures, artifact refs.
6. Redact before model-facing persistence.
7. Add builder functions:
   - `build_failure_evidence_from_state(...)`
   - `write_failure_evidence(run_dir, evidence)`
8. Wire only where build/test failure is already detected. If unclear, expose the builder first and integrate in F5-T3/T11.

### CONSTRAINTS
- No LLM calls in this task.
- Do not change Build Agent/Test Agent success semantics.
- Do not include raw command argv/env or absolute sandbox paths.
- Do not include volatile timestamps in `content_checksum`.
- Do not parse unlimited logs into evidence.

### OUTPUT
Create/modify:
- `migration_factory/repair_loop/failure_evidence.py`
- `tests/control_tower/test_v2_repair_failure_evidence.py`
- optionally `migration_factory/repair_loop/evidence_collector.py` only as adapter code.

Expected result:
- Build/test failure evidence can be produced deterministically.
- Tests prove redaction, stable ordering, checksum stability, changed-file sensitivity, and build/test failure coverage.

---

## F5-T2 — Repair Context Pack
> SUMMARY: Build the exact checksum-bound context pack passed to the Primary Repair LLM.

### TASK
Create a model-safe `RepairContextPack` from failure evidence plus migration/job state, upstream accepted artifacts, previous repair attempts, reviewer notes, user comments, and bounded artifact previews.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/orchestrator/review_chain.py`
- `migration_factory/control_tower/application/v2_gate_artifact_resolver.py`
- `migration_factory/control_tower/application/v2_evidence_pack_builder.py`
- `migration_factory/control_tower/application/v2_model_schemas.py`
- `migration_factory/control_tower/infrastructure/sqlite/v2_artifact_revision_repository.py`
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/control_tower/application/redaction.py`

### APPROACH
1. Create `migration_factory/repair_loop/repair_context.py`.
2. Define `RepairContextPack` with:
   - job/stage/command identity.
   - failure evidence ref/checksum.
   - source/target profile.
   - accepted analysis/planning artifacts.
   - prior repair proposal IDs/checksums.
   - prior reviewer notes.
   - user comments.
   - changed files.
   - bounded safe log preview.
   - `base_repo_state_checksum`.
   - `context_pack_checksum`.
3. Resolve accepted artifacts through repositories, not arbitrary paths.
4. Define `compute_base_repo_state_checksum()` explicitly:
   - include touched-file content hashes if files exist.
   - include source/target profile IDs.
   - include accepted upstream artifact checksums.
   - do not include absolute sandbox path.
5. Add stale-context helper inputs; actual rejection happens in approval/apply tasks.
6. Unit-test checksum changes when user comment, repo state, or accepted artifact checksum changes.

### CONSTRAINTS
- Do not build context in FastAPI route code.
- Do not include raw `argv`, `env`, `sandbox_path`, endpoints, deployments, or user-provided file paths.
- Do not allow frontend/chatbot to construct this pack.
- Do not call model here.

### OUTPUT
Create/modify:
- `migration_factory/repair_loop/repair_context.py`
- `tests/control_tower/test_v2_repair_context_pack.py`

Expected result:
- A stable, redacted, checksum-bound context pack exists and can be consumed by the repair review-chain producer.

---

## F5-T3 — Deterministic Repair Artifact
> SUMMARY: Convert failure evidence + context pack into the root deterministic artifact for the repair review chain.

### TASK
Implement the F2-equivalent deterministic repair artifact that anchors all primary/reviewer/final diff checksums.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/control_tower/application/v2_review_chain_contracts.py`
- `migration_factory/orchestrator/review_chain.py`
- `migration_factory/control_tower/domain/checksums.py`
- `migration_factory/repair_loop/rule_registry.py`
- F5-T1/F5-T2 outputs.

### APPROACH
1. Add repair-specific contract types in `v2_review_chain_contracts.py` or a new imported repair-contract module:
   - `DeterministicRepairFacts`.
   - `RepairDeterministicArtifactBinding` if generic binding is insufficient.
2. Required fields:
   - failure source.
   - normalized compiler/test errors.
   - failure summary.
   - changed files.
   - source/target profile.
   - context pack checksum.
   - base repo state checksum.
   - accepted artifact checksums.
   - allowed repair modes/rule hints.
3. Add `validate_deterministic_repair_facts()`.
4. Write `deterministic_repair_artifact.json` under a repair review-chain directory.
5. Compute stable checksum with `sha256_canonical_json`.
6. Add tests for missing fields, unsafe fields, stable ordering, and checksum changes.

### CONSTRAINTS
- No model output inside deterministic artifact.
- No Copilot output as deterministic fact.
- No volatile fields in stable checksum.
- Do not duplicate Analysis/Planning validator logic; extend or reuse patterns.

### OUTPUT
Create/modify:
- `migration_factory/control_tower/application/v2_review_chain_contracts.py`
- `migration_factory/orchestrator/repair_review_chain.py`
- `tests/control_tower/test_v2_review_chain_contracts.py`
- `tests/control_tower/test_v2_repair_review_chain_producer.py`

Expected result:
- Repair has a F2-grade deterministic artifact binding accepted by strict validators.

---

## F5-T4 — Primary Repair LLM Contract
> SUMMARY: Make the proposer model produce a structured repair proposal and exact unified diff, without execution authority.

### TASK
Implement the Primary Repair LLM input/output contract and producer call. It receives deterministic repair evidence/context and produces root cause, fix strategy, affected files, risks, confidence, and proposed unified diff.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/orchestrator/review_chain.py`
- `migration_factory/control_tower/application/v2_assistant_model_client.py`
- `migration_factory/control_tower/application/v2_model_role_router.py`
- `migration_factory/control_tower/application/v2_review_chain_contracts.py`
- `migration_factory/control_tower/application/v2_model_schemas.py`

### APPROACH
1. In `migration_factory/orchestrator/repair_review_chain.py`, define:
   - `RepairPrimaryLLMInput`.
   - `RepairPrimaryLLMOutput`.
2. Primary output must include:
   - `root_cause`.
   - `fix_strategy`.
   - `changed_files`.
   - `proposed_diff`.
   - `deterministic_rule_id` or explicit `no_safe_rule`.
   - `risk`.
   - `confidence`.
   - `rationale`.
   - `no_fix_reason` if no safe repair.
   - `machine_readable_metadata`.
3. Build `_primary_repair_prompt()` with sorted JSON input and strict JSON-only output instructions.
4. Use `V2ModelRole.PROPOSER` via `V2AssistantModelClient.answer_with_role()`.
5. Strictly coerce/parse output; malformed output fails closed.
6. Validate no execution instructions and no forbidden fields.
7. Persist `primary_repair_llm_output.json` and checksum.

### CONSTRAINTS
- Primary model cannot apply patch or choose commands.
- Primary model cannot choose sandbox/root path.
- Proposed diff is untrusted until reviewer + policy + human approval.
- Do not reuse Copilot proposal as authoritative output.

### OUTPUT
Create/modify:
- `migration_factory/orchestrator/repair_review_chain.py`
- `migration_factory/control_tower/application/v2_review_chain_contracts.py`
- `tests/control_tower/test_v2_repair_review_chain_producer.py`

Expected result:
- Valid primary repair output is persisted and checksummed.
- Malformed, unsafe, no-rule, or execution-instruction outputs fail closed.

---

## F5-T5 — Reviewer Repair LLM Contract
> SUMMARY: Make the reviewer model validate the exact proposed diff and bind acceptance to checksums.

### TASK
Implement the Reviewer Repair LLM input/output contract. Reviewer must evaluate primary reasoning, proposed diff, path scope, target-profile fit, stale state, safety, and policy concerns.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/orchestrator/review_chain.py`
- `migration_factory/control_tower/application/v2_review_chain_contracts.py`
- `migration_factory/control_tower/application/v2_reviewer_service.py`
- `migration_factory/control_tower/application/v2_repair_gate_service.py`

### APPROACH
1. Define:
   - `RepairReviewerLLMInput`.
   - `RepairReviewerLLMOutput`.
2. Reviewer input includes:
   - deterministic repair artifact checksum.
   - context pack checksum.
   - primary output checksum.
   - proposed diff checksum.
   - changed files.
   - source/target profiles.
   - policy hints.
3. Reviewer output includes:
   - `decision`: `accept`, `revise`, `reject`.
   - `notes`.
   - `risks`.
   - `confidence`.
   - `policy_concerns`.
   - `reviewed_context_checksum`.
   - `reviewed_primary_output_checksum`.
   - `reviewed_diff_checksum`.
4. Use `V2ModelRole.REVIEWER`.
5. Persist `reviewer_repair_llm_output.json` and checksum.
6. Record reviewer critique through `V2ReviewerService` only after checksum validation passes.
7. Reviewer `revise` or `reject` must prevent final reviewed repair artifact production.

### CONSTRAINTS
- Reviewer acceptance is not human approval.
- Reviewer cannot apply patch.
- Do not let reviewer modify the diff after acceptance unless a new primary/reviewer cycle is created.
- Do not skip reviewer because policy validation passes.

### OUTPUT
Create/modify:
- `migration_factory/orchestrator/repair_review_chain.py`
- `migration_factory/control_tower/application/v2_review_chain_contracts.py`
- `tests/control_tower/test_v2_repair_review_chain_producer.py`
- `tests/control_tower/test_v2_review_chain_contracts.py`

Expected result:
- Reviewer output is checksum-bound to exact context/primary/diff and fail-closed on mismatch/reject/revise.

---

## F5-T6 — Final Reviewed Repair Diff Artifact
> SUMMARY: Store the exact reviewed diff as an immutable repair artifact and draft repair revision.

### TASK
Persist the exact reviewed repair diff as an immutable artifact that can later be approved by the human gate and applied exactly by checksum.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/control_tower/schemas/artifact_revision.py`
- `migration_factory/control_tower/infrastructure/sqlite/v2_artifact_revision_repository.py`
- `migration_factory/control_tower/infrastructure/sqlite/v2_repair_repository.py`
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/repair_loop/patch_gate.py`

### APPROACH
1. Define `FinalReviewedRepairArtifact` with:
   - proposal/job/command/stage identity.
   - deterministic artifact checksum.
   - context pack checksum.
   - primary output checksum.
   - reviewer output checksum.
   - proposed diff checksum.
   - changed files.
   - base repo state checksum.
   - policy validation ref/checksum placeholder.
   - artifact checksum.
2. Write:
   - `final_reviewed_repair_artifact.json`.
   - `final_reviewed_repair.diff`.
3. Create `ArtifactRevisionRecord` with:
   - `revision_kind="repair"`.
   - `revision_status="draft"`.
   - artifact refs/checksums JSON.
4. Keep `V2RepairProposalRecord` for proposal lifecycle status. Do not use it as the immutable diff artifact.
5. Add lineage fields if revision/supersession needs new DB support; if new persistence is required, add SQLite migration and repository tests.

### CONSTRAINTS
- Do not mutate existing reviewed diff artifacts.
- Do not store user-edited diff as reviewed diff.
- Do not mark repair as approved here.
- Do not expose unsafe fields in public projection.

### OUTPUT
Create/modify:
- `migration_factory/orchestrator/repair_review_chain.py`
- `migration_factory/control_tower/application/v2_review_chain_contracts.py`
- `migration_factory/control_tower/application/v2_repair_flow.py`
- possibly SQLite migration if repository schema needs new artifact refs.
- `tests/control_tower/test_v2_repair_diff_artifact.py`

Expected result:
- Reviewed repair diff is immutable, checksum-bound, and linked to a draft repair revision.

---

## F5-T7 — Policy Validation Before Gate Presentation
> SUMMARY: Run deterministic patch policy before a repair proposal can be presented as approvable.

### TASK
Validate the final reviewed diff with backend policy/path/security checks before opening an approvable `repair_review` gate.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/repair_loop/patch_gate.py`
- `migration_factory/repair_loop/rule_registry.py`
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/control_tower/application/v2_repair_gate_service.py`

### APPROACH
1. Build adapter from `FinalReviewedRepairArtifact` to the current `evaluate_patch_proposal()` input shape.
2. Required adapter fields:
   - `deterministic_rule_id`.
   - `risk`.
   - `requires_human_review`.
   - `unified_diff`.
3. Run path/security validation before gate creation.
4. Persist `repair_policy_validation.json` with:
   - status: `allowed`, `blocked`, `invalid`, `human_review_required`.
   - touched paths.
   - blocked reasons.
   - rule ID.
   - checked checksums.
5. F5 rule: only `allowed` is approvable. `human_review_required` may be shown, but approve action must be disabled unless product explicitly defines a higher-risk approval lane later.
6. Gate source refs include policy validation checksum.

### CONSTRAINTS
- `git apply --check` is not policy validation.
- LLM does not choose policy.
- Do not weaken `patch_gate.py` path validation.
- Do not make Jackson/OpenRewrite the hardcoded repair universe; use it only as one test proof case.

### OUTPUT
Create/modify:
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/orchestrator/repair_review_chain.py`
- `tests/control_tower/test_v2_repair_policy_validation.py`

Expected result:
- Unsafe, malformed, stale, or out-of-scope diffs cannot open an approvable repair gate.

---

## F5-T8 — Human Repair Decision Actions
> SUMMARY: Bind approve/reject/request-another-review to exact gate/proposal/context/reviewer/policy checksums.

### TASK
Finalize repair gate actions so human decisions are typed, idempotent, checksum-bound, and fail closed on stale/unreviewed/policy-failed proposals.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/control_tower/schemas/phase_gate.py`
- `migration_factory/control_tower/application/v2_gate_action_service.py`
- `migration_factory/control_tower/application/v2_repair_gate_service.py`
- `migration_factory/control_tower/adapters/fastapi/app.py`

### APPROACH
1. Review current `approve_repair`, `reject_repair`, `request_repair_revision` behavior.
2. Ensure approve requires:
   - expected gate checksum.
   - proposal checksum.
   - context pack checksum.
   - reviewer output/checksum or accepted critique checksum.
   - policy validation checksum.
   - final reviewed repair artifact checksum.
   - human actor.
3. Decide apply timing:
   - Preferred: approval resolves gate and queues/returns backend-owned apply command/action.
   - Do not apply inside frontend/chatbot path.
4. Reject stores reason and applies nothing.
5. Request another review stores comments and creates a new repair review-chain cycle in F5-T9.
6. Add API schema changes only if current endpoint cannot pass required checksums/comments.

### CONSTRAINTS
- Assistant can draft, backend validates.
- Non-human actor cannot approve/reject authoritative repair decision.
- Do not accept missing comments for reject/revision.
- Do not approve policy-blocked or reviewer-rejected proposal.

### OUTPUT
Create/modify:
- `migration_factory/control_tower/application/v2_gate_action_service.py`
- `migration_factory/control_tower/application/v2_repair_gate_service.py`
- `migration_factory/control_tower/adapters/fastapi/app.py` only if API contract needs it.
- `tests/control_tower/test_v2_gate_action_service.py`
- `tests/control_tower/test_v2_gate_api.py`

Expected result:
- Human repair decisions are safe, checksum-rich, and idempotent.

---

## F5-T9 — Request Another Review Loop
> SUMMARY: When user asks for another review, create a new repair review-chain cycle with prior context and comments.

### TASK
Implement repair revision flow where user feedback, prior proposal, prior reviewer notes, current repo state, and original failure evidence feed into a new primary/reviewer cycle.

### CONTEXT
Current code to reuse/inspect:
- `V2RepairFlowService.create_revision_proposal()`.
- `V2GateActionService.request_repair_revision()`.
- `V2ReviewerService`.
- `ArtifactRevisionRecord` lineage and supersession.

### APPROACH
1. On `request_repair_revision`, resolve current repair gate with `REVISE`.
2. Persist user comments/reason.
3. Build new `RepairContextPack` containing:
   - original failure evidence.
   - previous final reviewed diff.
   - previous primary output.
   - previous reviewer notes.
   - user comments.
   - current base repo state checksum.
   - prior proposal checksum.
4. Run the same `produce_repair_review_chain()` path.
5. Link new revision/proposal to previous proposal/revision IDs.
6. Supersede only draft/unaccepted previous revisions. Never mutate accepted records.
7. Open a new `repair_review` gate if new chain + policy pass.

### CONSTRAINTS
- No special “reviewer-only retry” unless spec later defines it.
- User comments do not override policy.
- Do not retry on stale repo state without explicit new evidence.
- Do not overwrite old proposal artifacts.

### OUTPUT
Create/modify:
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/control_tower/application/v2_gate_action_service.py`
- `migration_factory/orchestrator/repair_review_chain.py`
- `tests/control_tower/test_v2_repair_revision_loop.py`

Expected result:
- Request-another-review creates a new checksum-bound repair chain with lineage and user feedback.

---

## F5-T10 — Exact-Diff Apply
> SUMMARY: Apply only the exact reviewed diff loaded from artifact storage and approved by checksum.

### TASK
Implement backend apply for approved repair diffs. The backend loads the diff from artifact ref, verifies all checksums and repo state, runs policy/apply checks, then applies exactly that diff.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/repair_loop/patch_apply.py`
- `migration_factory/repair_loop/patch_gate.py`
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/control_tower/application/v2_repair_gate_service.py`

### APPROACH
1. Extend `V2RepairFlowService.apply_patch()` or add `apply_reviewed_repair_diff()`.
2. Inputs should be refs/checksums, not raw diff:
   - proposal ID.
   - final reviewed repair artifact ref/checksum.
   - policy checksum.
   - expected base repo state checksum.
3. Load diff from backend artifact storage.
4. Verify:
   - proposal status approved.
   - repair revision accepted or approved as required by gate decision.
   - reviewer accepted exact diff checksum.
   - policy status allowed.
   - current repo state matches approved base state.
5. Call `apply_patch_to_sandbox()` with exact diff and touched paths.
6. Persist `repair_apply_result.json` with safe metadata:
   - touched paths.
   - before/after hashes.
   - apply status.
   - errors.
   - artifact checksum.
   - snapshot ref redacted/safe.

### CONSTRAINTS
- No raw diff from API body.
- Do not apply primary output.
- Do not bypass `git apply --check`.
- Do not expose absolute snapshot/sandbox paths publicly.
- Do not continue after apply failure.

### OUTPUT
Create/modify:
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/repair_loop/patch_apply.py` only if safe metadata extension is required.
- `tests/control_tower/test_v2_repair_apply.py`

Expected result:
- Backend applies exact approved diff only; stale/user-edited/unreviewed/policy-failed diffs are rejected.

---

## F5-T11 — Build/Test Rerun Proof
> SUMMARY: After apply, rerun deterministic validation and persist proof; never trust model success claims.

### TASK
After exact diff apply, rerun the correct Build Agent/Test Agent validation path and persist the proof result.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/agents/build_agent/agent.py`
- `migration_factory/agents/test_agent/agent.py`
- `migration_factory/repair_loop/validation_runner.py`
- `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- `migration_factory/control_tower/application/v2_stage_progression.py`

### APPROACH
1. Define rerun policy from failure source:
   - build failure -> build validation.
   - test failure -> test or build+test validation according to current runner contract.
2. Do not let model choose validation command.
3. Reuse `run_validation_after_patch()` unless it lacks required proof metadata.
4. Persist `repair_rerun_result.json`:
   - validation kind.
   - status.
   - artifact refs/checksums.
   - failure summary if failed.
   - proof checksum.
5. Emit events for apply started/completed, validation started/completed, proof written.
6. On success, mark repair cycle validated and allow normal progression.
7. On failure, hand off to repeated failure behavior.

### CONSTRAINTS
- No success based on LLM statement.
- No raw unsafe argv in public projection.
- No change to existing build/test success semantics.

### OUTPUT
Create/modify:
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/repair_loop/validation_runner.py` only if needed.
- `migration_factory/control_tower/application/v2_orchestrator_runner.py` only for event/progression integration.
- `tests/control_tower/test_v2_repair_rerun.py`

Expected result:
- Repair success requires deterministic rerun proof artifact.

---

## F5-T12 — Repeated Failure Behavior
> SUMMARY: If validation fails after apply, start a bounded new repair cycle with full prior context.

### TASK
Implement repeated repair cycle behavior after rerun failure, with attempt limits, cycle lineage, prior diff/reviewer/apply/proof context, and terminal failure when attempts are exhausted.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/repair_loop/orchestrator.py` old max-attempt style.
- `migration_factory/control_tower/application/v2_repair_gate_service.py` attempt handling.
- `migration_factory/control_tower/application/v2_stage_progression.py` stop behavior.
- `migration_factory/repair_loop/validation_runner.py`.

### APPROACH
1. Add repair cycle metadata:
   - cycle number.
   - max cycles.
   - previous cycle IDs.
   - failure-after-apply metadata.
2. On rerun failure, create new `FailureEvidence` from rerun result and prior repair context.
3. Include prior:
   - failure evidence.
   - context pack.
   - primary/reviewer output.
   - final reviewed diff.
   - apply result.
   - rerun proof/failure result.
   - user comments.
4. Start another repair review-chain if attempts remain.
5. If attempts exhausted, record terminal repair failure and do not continue stage progression.
6. Detect repeated same-file/same-error loops and stop cleanly.

### CONSTRAINTS
- No auto-apply of second patch.
- Do not reset attempts by creating a new gate.
- Do not overwrite previous cycle artifacts.
- Do not hide repeated failure as success.

### OUTPUT
Create/modify:
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/control_tower/application/v2_repair_gate_service.py`
- `migration_factory/control_tower/application/v2_stage_progression.py` if needed.
- `tests/control_tower/test_v2_repair_repeated_failure.py`

Expected result:
- Repeated failures either create a new governed cycle or fail closed with complete evidence.

---

## F5-T13 — Rollback and Proof Artifacts
> SUMMARY: Roll back safely on failed apply/rerun and persist explicit proof for all terminal outcomes.

### TASK
Define rollback and proof artifacts for repair cycles. Failed apply or failed validation should roll back according to policy and persist proof/rollback status.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/repair_loop/patch_apply.py`
- `migration_factory/repair_loop/validation_runner.py`
- `migration_factory/repair_loop/ledger.py`
- `migration_factory/control_tower/application/v2_repair_flow.py`

### APPROACH
1. Define `repair_rollback_result.json`:
   - proposal/revision IDs.
   - apply action ID.
   - touched files.
   - created files.
   - rollback status.
   - before/after hashes.
   - reason.
   - checksum.
2. Define `repair_proof.json`:
   - validation status.
   - rerun refs/checksums.
   - repaired files.
   - final repo state checksum.
   - proof checksum.
3. Extend apply flow to write proof/rollback artifact in every terminal outcome.
4. If rollback fails, record explicit rollback failure and block progression.
5. Redact snapshot/sandbox paths in public projection.

### CONSTRAINTS
- No repair success without rerun proof.
- No evidence deletion after rollback.
- No absolute snapshot path leak.
- No rollback if apply never mutated files; record no-op safely.

### OUTPUT
Create/modify:
- `migration_factory/control_tower/application/v2_repair_flow.py`
- `migration_factory/repair_loop/patch_apply.py` only if return detail needs extension.
- `migration_factory/repair_loop/ledger.py` only if ledger needs proof fields.
- `tests/control_tower/test_v2_repair_rollback_proof.py`

Expected result:
- Every repair cycle ends with success proof, rollback proof, or terminal failure proof.

---

## F5-T14 — UI/API Presentation Contract
> SUMMARY: Expose safe repair proposal/gate data for Cockpit/API without letting UI supply diffs or execution details.

### TASK
Define backend projection for repair proposal review: error summary, root cause, changed files, diff preview, risks, confidence, reviewer notes, policy result, checksums, and allowed actions.

### CONTEXT
Current code to reuse/inspect:
- `migration_factory/control_tower/adapters/fastapi/app.py`
- `migration_factory/control_tower/schemas/phase_gate.py`
- `migration_factory/control_tower/schemas/artifact_revision.py`
- `migration_factory/control_tower/application/v2_gate_artifact_resolver.py`
- `migration_factory/control_tower/application/redaction.py`
- `web/control-tower/` only if API contract type changes require frontend type updates.

### APPROACH
1. Add backend projection helper, not frontend logic first.
2. Projection fields:
   - proposal ID.
   - gate ID.
   - stage index.
   - failure source.
   - error summary.
   - root cause.
   - fix strategy.
   - changed files.
   - bounded diff preview.
   - reviewed diff artifact ref/checksum.
   - risk/confidence.
   - reviewer decision/notes.
   - policy status/reason/checksum.
   - gate checksum.
   - allowed actions.
3. Full diff should be loaded by backend artifact ref/download endpoint, not embedded unbounded in every gate response.
4. Add redaction tests for forbidden keys.
5. Run frontend typecheck only if backend response schema mirrored in TS types.

### CONSTRAINTS
- UI cannot submit diff content as authority.
- Do not expose raw full diff if policy blocked.
- No unsafe fields in projection.
- No deployment/provider internals unless existing safe metadata policy explicitly allows it; default block.

### OUTPUT
Create/modify:
- `migration_factory/control_tower/adapters/fastapi/app.py`
- `migration_factory/control_tower/application/v2_gate_artifact_resolver.py` or a new projection helper.
- `tests/control_tower/test_v2_repair_presentation_contract.py`
- optional `web/control-tower/` type updates only if needed.

Expected result:
- Cockpit/API can present repair gate safely and with enough checksums for user decisions.

---

## F5-T15 — F5 Test Matrix and Merge Gate
> SUMMARY: Create the executable proof suite for the full governed repair loop before merging F5.

### TASK
Build the focused F5 test suite covering contracts, review chain, policy, gates, exact apply, rerun proof, repeated cycles, rollback, and public projection.

### CONTEXT
Must include tests from all prior F5 tasks and protect F2/F4 regressions.

### APPROACH
1. Add/extend test files:
   - `tests/control_tower/test_v2_repair_failure_evidence.py`
   - `tests/control_tower/test_v2_repair_context_pack.py`
   - `tests/control_tower/test_v2_repair_review_chain_producer.py`
   - `tests/control_tower/test_v2_repair_diff_artifact.py`
   - `tests/control_tower/test_v2_repair_policy_validation.py`
   - `tests/control_tower/test_v2_repair_flow.py`
   - `tests/control_tower/test_v2_repair_apply.py`
   - `tests/control_tower/test_v2_repair_rerun.py`
   - `tests/control_tower/test_v2_repair_repeated_failure.py`
   - `tests/control_tower/test_v2_repair_rollback_proof.py`
   - `tests/control_tower/test_v2_repair_presentation_contract.py`
2. Required positive path:
   - build/test failure evidence.
   - context pack.
   - deterministic repair artifact.
   - primary proposal.
   - reviewer accept.
   - final reviewed diff.
   - policy allow.
   - human approve.
   - exact diff apply.
   - build/test rerun proof.
3. Required fail-closed paths:
   - missing reviewer.
   - reviewer reject/revise.
   - malformed diff.
   - unsafe path.
   - stale repo state.
   - stale context checksum.
   - user-edited diff.
   - non-human approval.
   - policy failed.
   - apply failure.
   - validation failure/rollback.
4. Use fake proposer/reviewer model clients; no real model calls in tests.
5. Run F2/F4 regression sweeps.

### CONSTRAINTS
- No skipped tests to hide broken F5 behavior.
- No real network/model calls.
- No absolute local path dependency.
- Do not weaken F2/F4 assertions.

### OUTPUT
Expected final verification commands:

```powershell
py -m pytest tests/control_tower/test_v2_repair_failure_evidence.py -q
py -m pytest tests/control_tower/test_v2_repair_context_pack.py -q
py -m pytest tests/control_tower/test_v2_repair_review_chain_producer.py -q
py -m pytest tests/control_tower/test_v2_review_chain_contracts.py -q
py -m pytest tests/control_tower/test_v2_repair_diff_artifact.py -q
py -m pytest tests/control_tower/test_v2_repair_policy_validation.py -q
py -m pytest tests/control_tower/test_v2_gate_action_service.py tests/control_tower/test_v2_gate_api.py -q
py -m pytest tests/control_tower/test_v2_repair_apply.py -q
py -m pytest tests/control_tower/test_v2_repair_rerun.py -q
py -m pytest tests/control_tower/test_v2_repair_repeated_failure.py -q
py -m pytest tests/control_tower/test_v2_repair_rollback_proof.py -q
py -m pytest tests/control_tower/test_v2_repair_presentation_contract.py -q
py -m pytest tests/control_tower/test_v2_orchestrator_runner.py -q
py -m pytest tests/control_tower/test_v2_stage_progression.py -q
git diff --check
git status --short
```

Expected result:
- F5 governed repair loop is proven end-to-end and does not regress F2/F4.

---

# After F5 — System Improvements Worth Doing Next

These are not F5 scope. They should be backlog items after F5 lands.

## 1. Assistant answer quality upgrade
Improve chatbot responses without giving it execution authority.

Recommended features:
- Better artifact retrieval/ranking for questions.
- Richer gate summaries.
- “Why model was/was not used” metadata.
- Natural-language summaries from safe backend projections.
- Assistant answer tests for static/template-heavy paths.

## 2. Review-chain observability dashboard
Expose a timeline for:
- deterministic artifact created.
- proposer called.
- reviewer called.
- final artifact created.
- validator accepted/rejected.
- human decision.

Keep deployment/endpoint/env details redacted.

## 3. Model-output schema hardening
Move proposer/reviewer outputs toward strict structured-output schemas where provider support exists, while retaining backend semantic validation.

## 4. Security automation
Add/verify:
- GitHub CodeQL code scanning.
- Secret scanning/push protection.
- Prompt-injection regression prompts for assistant/proposer/reviewer roles.
- Patch-policy adversarial tests.

## 5. Repair policy registry expansion
Turn `rule_registry.py` into a clearer repair-mode catalog:
- Maven/POM repair.
- Java source compile repair.
- Test expectation repair.
- Config repair.
- OpenRewrite recipe repair.

Each mode should define allowed files, forbidden paths, validation commands, and risk level.

## 6. Multi-cycle repair UX
After F5, improve cockpit visibility for repeated repair cycles:
- cycle number.
- previous failed patch.
- what changed in new proposal.
- reviewer delta.
- reason previous attempt failed.

## 7. Cost/rate/concurrency controls for model chains
Add model budget controls:
- max repair cycles.
- max tokens per context pack.
- max concurrent repair reviews.
- explicit failure when model unavailable.

## 8. Full audit export
Generate a downloadable proof bundle:
- failure evidence.
- primary proposal.
- reviewer output.
- final reviewed diff.
- policy validation.
- human decision.
- apply result.
- rerun proof.
- rollback proof if applicable.

This will be useful for enterprise governance demos.
