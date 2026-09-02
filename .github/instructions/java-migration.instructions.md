---
applyTo: ["**/*.java", "**/pom.xml", "**/build.gradle", "**/build.gradle.kts"]
description: "Java migration guidance for AI Migration Factory governed migration flows."
---

# Java Migration Instructions

Use these instructions only for AIMF migration work. AIMF performs migration
only. The legacy application is read-only input. Do not update, enhance,
maintain, or add features to the legacy application after migration. All
source-changing work must happen only in a sandbox workspace or an explicitly
approved migration target flow.

## Scope

- Focus on migration compatibility: JDK level, removed/deprecated APIs,
  dependency compatibility, compiler configuration, test compatibility, and
  framework migration prerequisites.
- Do not introduce unrelated Java 17 language rewrites such as records, sealed
  classes, switch-expression style churn, text block rewrites, or broad
  refactoring unless the approved migration plan requires them.
- Do not change business behavior to "improve" the product. Preserve behavior
  unless a migration blocker requires a minimal compatibility adjustment.
- Keep changes small, traceable to a migration unit, and compatible with the
  migration ledger.

## Legacy Read-Only Rule

- Inspect legacy Java source, build files, and tests only as deterministic
  evidence.
- Do not edit, format, regenerate, delete, or run source-changing tools in the
  legacy source tree.
- If legacy code appears broken, report it as migration evidence or a blocker;
  do not fix the legacy app directly.

## Approved Change Locations

- In `read_only_assessment`, do not change Java source, build files, or tests.
- In `full_sandbox_migration`, Java source and build changes are allowed only
  in the sandbox workspace after approval and plan-lock validation.
- In any future target flow, source changes are allowed only when that flow is
  explicitly implemented, approved, and guarded.

## Java Compatibility Patterns

- Prefer deterministic build configuration changes needed by the approved plan,
  such as compiler `release`, toolchain, plugin versions, or Maven/Gradle test
  settings.
- Replace removed APIs only when required for the target Java runtime.
- Avoid Java preview features unless the approved migration plan explicitly
  permits them and the build/test gates validate them.
- Keep serialization, reflection, bytecode tooling, annotation processors, and
  generated-code behavior conservative; flag high-risk cases for human review.
- Treat dependency upgrades as migration-only changes tied to compatibility,
  not general maintenance.

## Verification

- Do not override deterministic scanner results, schema checks, build
  classification, or test classification.
- Use sandbox build/test gates for migrated validation. Do not treat a legacy
  build as migrated validation.
- If a build or test fails, preserve the failure and report blockers instead of
  changing gates or artifacts to force a pass.

## Copilot Role

Copilot is advisory-only unless the active factory phase explicitly enables it.
Copilot may suggest migration risks or compatibility candidates, but scanner
facts, approved plans, approval decisions, plan locks, and deterministic gates
remain authoritative.
