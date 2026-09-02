---
description: "Code review instructions for AI Migration Factory safety, contracts, and migration-only scope."
applyTo: "**"
excludeAgent: ["coding-agent"]
---

# AI Migration Factory Code Review

Review changes as AIMF migration governance work, not as general product
development. AIMF performs migration only. The legacy application is read-only
input. Do not update, enhance, maintain, or add features to the legacy
application after migration. All source-changing work must happen only in a
sandbox workspace or an explicitly approved migration target flow.

## Review Priorities

### Critical: Block

- Approval bypass, approval decision spoofing, or missing approval validation.
- `approved_plan_lock.json` bypass, stale hash acceptance, or lock weakening.
- Source mutation outside sandbox or explicitly approved migration target flow.
- Any write, format, transform, delete, or generated-code operation against the
  legacy source tree.
- Deterministic gate override: schema validation, artifact validation, scanner
  facts, build/test classification, and hash checks must remain authoritative.
- PR generation, deploy, production promotion, release tagging, or production
  readiness claims without an explicitly implemented future milestone.
- Code changes that alter Python runtime behavior, approval/lock behavior,
  sandbox transform/build/test gates, or production promotion behavior when the
  request is documentation-only.

### Important: Discuss

- Copilot or AI output treated as source-of-truth instead of advisory metadata.
- Migration units that are too broad to audit or not tied to approved artifacts.
- Dependency upgrades, refactors, or style churn unrelated to migration
  compatibility.
- Missing blockers/warnings for high-risk Spring security, datasource,
  serialization, reflection, generated-code, or test compatibility changes.
- Artifact refs that are unstable, absolute when contracts expect relative, or
  inconsistent with schema expectations.

### Suggestion: Non-Blocking

- Clearer references to phase names, artifact names, run directories, and
  sandbox paths.
- More precise migration risk wording.
- Better review comments that separate deterministic facts from advisory AI
  interpretation.

## Review Method

- Lead with findings, ordered by severity, with exact file and line references.
- Explain the migration risk and the violated AIMF boundary.
- Suggest the smallest compliant fix.
- Do not recommend autonomous feature work, direct legacy edits, uncontrolled
  refactoring, PR/deploy automation, or production promotion.
- If no issues are found, say so and mention any residual test or validation
  gap.

## Checklist

- Legacy app remains read-only input.
- Source-changing work is sandbox-only or explicitly approved target flow.
- Human approval, `approval_decision.json`, and `approved_plan_lock.json` are
  required where transformation can occur.
- Deterministic gates fail closed and cannot be overridden by Copilot or manual
  edits.
- Copilot remains advisory-only unless a factory phase explicitly enables it.
- Build/test validation occurs only in the sandbox or approved target context.
- No docs or code imply product enhancement, direct legacy maintenance, PR
  creation, deploy, or production promotion as currently available.
- Runtime behavior, approval/lock behavior, sandbox gates, and production
  promotion behavior are unchanged unless the user explicitly requested code
  changes in that area.
