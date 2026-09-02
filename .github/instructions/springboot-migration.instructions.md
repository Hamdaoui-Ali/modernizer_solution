---
description: "Spring Boot migration guidance for AI Migration Factory governed migration flows."
applyTo: "**/*.java, **/*.kt, **/pom.xml, **/build.gradle, **/build.gradle.kts, **/application*.yml, **/application*.properties"
---

# Spring Boot Migration Instructions

Use these instructions only for AIMF migration work. AIMF performs migration
only. The legacy application is read-only input. Do not update, enhance,
maintain, or add features to the legacy application after migration. All
source-changing work must happen only in a sandbox workspace or an explicitly
approved migration target flow.

## Migration Scope

- Focus on Spring Boot compatibility migration such as Boot version alignment,
  Java runtime requirements, Jakarta namespace migration, configuration binding
  compatibility, dependency compatibility, and test startup compatibility.
- Do not add endpoints, features, observability products, authentication flows,
  business logic, or architecture changes unless the approved migration plan
  explicitly requires them to preserve behavior.
- Do not perform broad cleanup, package reshaping, style-only refactoring, or
  dependency maintenance unrelated to the migration unit.

## Legacy Read-Only Rule

- Treat legacy Spring configuration, source, tests, and build files as evidence
  for analysis and planning.
- Do not edit legacy controllers, services, repositories, configuration, tests,
  or build files directly.
- If the legacy app has security, datasource, or startup issues, report them as
  migration risks or blockers unless the approved sandbox/target flow requires a
  compatibility change.

## Approved Change Locations

- `read_only_assessment`: no Spring source/config/build changes.
- `full_sandbox_migration`: approved Spring migration changes may be applied
  only in the sandbox workspace after approval and `approved_plan_lock.json`
  validation.
- Future target promotion or PR flows must be explicitly implemented and
  guarded before docs or agents claim they are available.

## Spring Boot Compatibility Patterns

- Preserve existing public API behavior unless a migration compatibility issue
  requires a minimal change.
- Prefer targeted fixes for migration breakage: `javax.*` to `jakarta.*`, Boot
  2 to Boot 3 API changes, removed properties, security configuration migration,
  test slice updates, and dependency/plugin compatibility.
- Keep configuration externalization and secrets handling intact. Do not create
  new secrets or hardcode credentials.
- Be cautious with security, datasource, transaction, serialization, and
  actuator changes. Flag behavioral risk for human review.
- Avoid changing package structure or layering unless the approved plan calls
  for it and sandbox gates validate it.

## Build and Verification

- Use only the factory-approved build/test commands for the active phase.
- Run migrated build/test validation in sandbox or approved target workspace,
  not in the legacy source tree.
- Do not edit reports, locks, schemas, ledgers, or classifications to force a
  pass.
- Failed startup, build, or tests are blockers to record, not gates to bypass.

## Copilot Role

Copilot is advisory-only unless a factory phase explicitly enables it. Copilot
may identify Spring migration candidates, but deterministic analysis,
approved plans, approval decisions, plan locks, sandbox build/test results, and
schema validation are authoritative.
