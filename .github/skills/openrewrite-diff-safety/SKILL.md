---
name: openrewrite-diff-safety
description: Reviews OpenRewrite dry-run or sandbox diff evidence for high-risk migration changes.
---

# OpenRewrite Diff Safety

Use this skill for OpenRewrite patch and diff evidence.

Flag high risk when evidence shows:

- Deleted non-generated source or config files.
- Spring Security behavior changes.
- Added or broadened `permitAll`.
- Disabled CSRF, CORS, JWT, resource-server filters, auth filters, or keystore checks.
- Removed beans, config classes, filters, controllers, repositories, scheduled jobs, or business logic.
- POM dependency removals or scope weakening.

Low-risk examples include mechanical `javax` to `jakarta` imports, formatting, generated output, and planned dependency version bumps.
