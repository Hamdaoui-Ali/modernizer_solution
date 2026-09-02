---
name: migration-governance
description: |
  Governance guidance for AI Migration Factory. Use this skill when reviewing
  or drafting instructions for migration-only scope, read-only legacy handling,
  human approval, approved plan locks, deterministic gates, sandbox-only source
  mutation, Copilot advisory boundaries, and production promotion restrictions.
---

# Migration Governance Skill

Use this skill for AI Migration Factory (AIMF) governance work. AIMF performs
migration only. The legacy application is read-only input. Do not update,
enhance, maintain, or add features to the legacy application after migration.
All source-changing work must happen only in a sandbox workspace or an
explicitly approved migration target flow.

## Governance Baseline

- Fail closed on missing artifacts, invalid schemas, hash mismatches,
  unsupported decisions, unknown modes, or ambiguous paths.
- Preserve human approval. Do not bypass `approval_decision.json`.
- Preserve the approved plan lock. Do not bypass `approved_plan_lock.json` or
  accept mismatched artifact hashes.
- Preserve deterministic gates. Scanner facts, schema validation, artifact
  validation, hash checks, build results, and test results are authoritative.
- Preserve sandbox isolation. Source mutation is allowed only in sandbox or an
  explicitly approved migration target flow.
- Preserve promotion limits. Do not generate PRs, deploy, release, or promote
  to production unless a future milestone explicitly enables that flow.

## Phase Rules

### Read-Only Assessment

- Allowed: analysis, planning, assessment, approval readiness, and human
  approval interruption.
- Not allowed: transformation, OpenRewrite apply, source writes, migrated
  build/test execution, final migration, PR creation, deploy, or production
  promotion.

### Full Sandbox Migration

- Allowed: approved migration changes in the sandbox workspace after approval
  and plan-lock validation.
- Required: migration ledger updates, sandbox build validation, sandbox test
  validation when applicable, and final migration reporting.
- Not allowed: direct legacy edits or source changes outside sandbox.

### Future Target Flow

- Treat target promotion as unavailable unless explicitly implemented,
  approved, and guarded by deterministic checks.

## Copilot Boundary

- Copilot is advisory-only unless a factory phase explicitly enables it.
- Copilot may enrich summaries, risk notes, and migration suggestions.
- Copilot must not alter deterministic facts, approvals, locks, schema results,
  build/test outcomes, ledgers, or final gate decisions.
- If Copilot conflicts with deterministic artifacts, keep the deterministic
  result and record the conflict.

## Review Checklist

Use this checklist for governance review:

- Legacy source remains read-only evidence.
- Source-changing work is sandbox-only or explicitly approved target flow.
- Approval decision and approved plan lock are required before transformation.
- Locked artifact hashes are recomputed and compared.
- Deterministic gate failures stop the flow or become explicit blockers.
- Artifact writes stay inside allowed run, sandbox, or approved target paths.
- Build/test validation runs in sandbox or approved target context, not legacy.
- No language suggests autonomous product development, direct legacy fixes,
  uncontrolled refactoring, PR generation, deployment, or production promotion.

## Finding Template

```markdown
**[Severity] Boundary: Short title**

File/line: path:line

Issue:
Explain the violated AIMF migration boundary or deterministic gate.

Risk:
Explain how this could mutate legacy, bypass approval/lock, override a gate, or
imply unsupported promotion.

Compliant fix:
Describe the smallest change that preserves migration-only scope and gate
integrity.
```
