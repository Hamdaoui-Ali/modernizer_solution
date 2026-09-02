---
name: h2-runtime-smoke
description: Reviews H2-only runtime startup smoke evidence and its limitations for migration proof.
---

# H2 Runtime Smoke

Use this skill for H2 startup report evidence.

Scope:

- H2 startup is not SQL Server validation.
- Production database scripts are not validated.
- Endpoint and business behavior are not validated.
- Missing local keystore, JWT, or secret material can be a warning when startup is optional.

Do not claim production readiness from H2 evidence.
