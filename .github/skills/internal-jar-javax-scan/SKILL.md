---
name: internal-jar-javax-scan
description: Reviews evidence for javax references left inside internal jars or migrated source after Jakarta migration.
---

# Internal Jar Javax Scan

Use this skill when failure evidence suggests leftover `javax.*` classes in source, generated code, or internal jars.

Consider:

- `ClassNotFoundException` or `NoClassDefFoundError` involving `javax`.
- Internal jars compiled against pre-Jakarta APIs.
- Transitive dependencies that still expose Java EE packages.

Recommend inspection or dependency replacement steps for human review.
