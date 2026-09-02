---
description: "Repository-wide Copilot instructions for AI Migration Factory."
applyTo: "**"
---

# AI Migration Factory Instructions

This repository is the AI Migration Factory (AIMF). AIMF performs governed
application migration only. The legacy application is read-only input. Do not
update, enhance, maintain, or add features to the legacy application after
migration. All source-changing work must happen only in a sandbox workspace or
an explicitly approved migration target flow.

## Operating Boundaries

- Treat `legacy_app_path` as read-only evidence for analysis, planning, and
  assessment.
- Write migration artifacts only under the modernized app `.migration/runs/...`
  tree unless the active factory phase explicitly allows target workspace
  source changes.
- Do not mutate source outside the sandbox in `full_sandbox_migration` or an
  explicitly approved migration target flow.
- Do not bypass human approval, `approval_decision.json`, or
  `approved_plan_lock.json`.
- Do not bypass or weaken deterministic gates, artifact schema validation,
  hash checks, sandbox transform/build/test gates, or fail-closed behavior.
- Do not generate PRs, deploy, promote to production, or mark production
  readiness unless a future milestone explicitly enables that flow.

## Copilot Role

Copilot is advisory-only unless a factory phase explicitly enables it. Copilot
may summarize risks, suggest migration considerations, or draft review comments.
Copilot must not override deterministic facts, approval decisions, plan locks,
or gate outcomes.

When AI assistance is used for analysis or planning:

- Keep deterministic fields owned by scanners, contracts, and validators.
- Ignore or reject AI output that attempts to modify deterministic facts,
  approval state, lock state, or gate results.
- Preserve auditability: suggestions should reference artifacts, files, and
  phase context rather than inventing source-of-truth state.

## Migration Scope

- Allowed work: analyze legacy facts, plan migration units, assess readiness,
  apply approved migration changes in sandbox/target flow, run sandbox
  build/test validation, and produce migration reports.
- Disallowed work: feature development, direct legacy fixes, post-migration
  product maintenance, uncontrolled refactoring, dependency upgrades unrelated
  to the approved migration, or code style churn without migration purpose.

## Phase Discipline

- `read_only_assessment`: analysis, planning, assessment, and human approval
  readiness only. No transformation, build, migrated tests, final migration, PR,
  deploy, or production promotion.
- `full_sandbox_migration`: source-changing migration work is limited to the
  sandbox workspace and must follow approved artifacts and lock validation.
- Any future target promotion flow must be explicitly implemented, approved,
  and guarded by deterministic checks before it is referenced as available.

## Review Defaults

Prioritize findings that threaten migration safety, deterministic contracts,
read-only legacy handling, approval/lock integrity, sandbox isolation, and
artifact validity. Avoid suggestions that turn AIMF into a general autonomous
software development agent.
