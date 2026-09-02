---
description: "Safety gates for AI Migration Factory migration phases, artifacts, approvals, and sandbox execution."
applyTo: "**"
---

# AI Migration Factory Safety Gates

AIMF performs migration only. The legacy application is read-only input. Do not
update, enhance, maintain, or add features to the legacy app after migration.
All source-changing work must happen only in a sandbox workspace or an
explicitly approved migration target flow.

## Non-Negotiable Gates

- No approval bypass. Transformation requires the expected approval artifacts
  for the active phase.
- No `approved_plan_lock.json` bypass. Locked artifact hashes must match the
  current run artifacts before migration work proceeds.
- No source mutation outside sandbox or explicitly approved migration target
  flow.
- No deterministic gate override. Schema validation, artifact validation,
  scanner facts, hash checks, build classification, and test classification are
  authoritative.
- No production promotion, PR creation, deploy, release tagging, or production
  readiness claim unless a future milestone explicitly enables and guards that
  flow.

## Phase Boundaries

- `read_only_assessment` may run analysis, planning, assessment, and approval
  interruption/readiness only. It must not execute transformation, OpenRewrite
  apply, source writes, migrated builds, migrated tests, final migration, PR, or
  deploy.
- `full_sandbox_migration` may perform approved migration changes only in the
  sandbox workspace and only after approval and plan-lock validation.
- Legacy source trees are evidence only. Build/test commands that validate
  migrated behavior must target the sandbox or approved target workspace, not
  the legacy source.

## Copilot and AI Assistance

- Copilot is advisory-only unless a factory phase explicitly enables it.
- AI output may enrich risk notes, summaries, or migration suggestions, but it
  must not change deterministic facts, approval decisions, locked artifacts, or
  gate outcomes.
- If AI output conflicts with scanner output, schemas, locks, or validation
  reports, keep the deterministic result and record the conflict as a warning or
  blocker.

## Tool and File Controls

- Use explicit allowlists for file writes per phase.
- Deny path traversal and writes outside the run directory, sandbox, or approved
  target flow.
- Treat shell commands, file deletion, dependency rewrite, OpenRewrite apply,
  build, test, PR, and deploy operations as high-impact actions requiring the
  correct phase and gate state.
- Fail closed on missing artifacts, invalid JSON/YAML, schema errors, hash
  mismatches, ambiguous paths, unsupported approval decisions, or unknown modes.

## Audit and Artifacts

- Preserve append-only audit and run artifacts where applicable.
- Do not edit or delete approval decisions, plan locks, ledgers, assessment
  reports, build reports, test reports, or final migration reports to force a
  pass.
- Record blockers and warnings rather than papering over failed gates.
- Keep artifact references relative, stable, and schema-valid.

## Review Checklist

- Legacy read-only rule is preserved.
- Source-changing work is sandbox-only or explicitly approved target flow.
- Approval decision and plan lock are required and validated.
- Deterministic validations cannot be overridden by Copilot or manual wording.
- No docs or code imply autonomous product development, direct legacy edits,
  uncontrolled refactoring, PR generation, deployment, or production promotion.
