# DEMO3 F0-F5 Mapping

F0-F5 are the sprint product source of truth. Retained implementation slices are archived under `docs/sprintdemo3/implementation-slices/` and may be used only as engineering detail.

| Retained implementation slice | Mapping | Status | Notes |
|---|---|---|---|
| `implementation-slices/01-stage4-reconciliation/` | F5 Build/Test Repair Agent | Archived implementation detail | Stage 4/Jackson is one F5 proof scenario only. It must not drive the product roadmap. |
| `implementation-slices/02-api-hardening/` | F0 cleanup; F1 checkpoints/user decisions; F3 target profile; F5 Build/Test Repair Agent | Archived implementation detail | Use only for backend-safe API boundaries, forbidden fields, and policy hardening detail. |
| `implementation-slices/03-stage-checkpoint/` | F1 checkpoints/user decisions | Archived implementation detail | Supports checkpoint state and gate concepts under F1. |
| `implementation-slices/04-stage-attempt/` | F1 checkpoints/user decisions; F5 Build/Test Repair Agent | Archived implementation detail | Supports attempts, reruns, and stage result persistence. |
| `implementation-slices/05-retry-resume-fork/` | F1 checkpoints/user decisions; F4 current app state; F5 Build/Test Repair Agent | Archived implementation detail | Use for resume/retry concepts only when aligned with checksum-bound backend decisions. |
| `implementation-slices/06-failure-evidence/` | F5 Build/Test Repair Agent | Archived implementation detail | Maps to build/test failure evidence capture. |
| `implementation-slices/07-failure-classifier-registry/` | F5 Build/Test Repair Agent | Archived implementation detail | Maps to deterministic failure classification under Repair Agent context. |
| `implementation-slices/08-retrieval-pack-builder/` | F5 Build/Test Repair Agent | Archived implementation detail | Maps to Repair Agent input context pack. |
| `implementation-slices/09-repair-mode-registry/` | F5 Build/Test Repair Agent | Archived implementation detail | Maps to backend-allowlisted repair modes and unsafe vendor recipe prevention. |
| `implementation-slices/10-llm-repair-candidate-generator/` | F5 Build/Test Repair Agent | Archived implementation detail | Maps to Primary Repair LLM proposal generation. |
| `implementation-slices/11-independent-reviewer/` | F2 LLM review chain; F5 Build/Test Repair Agent | Archived implementation detail | Maps to mandatory reviewer LLM for model-required outputs and repair diffs. |
| `implementation-slices/12-backend-policy-validator/` | F0 cleanup; F3 target profile; F5 Build/Test Repair Agent | Archived implementation detail | Maps to backend-owned validation, forbidden public fields, profile policy, and repair proposal policy. |
| `implementation-slices/13-human-approval-gate/` | F1 checkpoints/user decisions; F5 Build/Test Repair Agent | Archived implementation detail | Maps to explicit human decisions and exact-diff repair approval. |
| `implementation-slices/14-sandbox-repair-executor/` | F5 Build/Test Repair Agent | Archived implementation detail | Maps to backend sandbox apply after exact approved reviewed diff. |
| `implementation-slices/15-validation-runner/` | F5 Build/Test Repair Agent | Archived implementation detail | Maps to build/test rerun proof and rollback behavior. |
| `implementation-slices/16-checkpoint-promoter/` | F1 checkpoints/user decisions; F4 current app state; F5 Build/Test Repair Agent | Archived implementation detail | Maps to artifact/checkpoint promotion only after accepted decisions and proof. |
| `implementation-slices/17-cockpit-recovery-ux/` | F1 checkpoints/user decisions; F5 Build/Test Repair Agent | Archived implementation detail | Maps to presentation contracts only; frontend implementation is out of scope for this docs cleanup. |
| `implementation-slices/18-e2e-fixtures/` | F1-F5 acceptance proof | Archived implementation detail | Use as acceptance fixtures only when aligned with F0-F5 backlog. |

## Remove Candidates

No retained implementation slice is currently marked for deletion. If a future implementation pass finds a slice duplicated, stale, or product-misleading, mark it `archived / superseded / remove candidate` here before deleting it.

## Rules

- The master backlog is [BACKLOG.md](BACKLOG.md).
- The master task index is [TASKS.md](TASKS.md).
- Stage 4/Jackson is only an F5 proof scenario.
- Copilot and TUI are not DEMO3 product runtime surfaces.
- Provider/model/deployment/env refs, `sandbox_path`, argv, env, raw commands, and filesystem targets are not product API fields.
