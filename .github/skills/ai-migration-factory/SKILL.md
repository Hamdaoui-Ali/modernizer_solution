---
name: ai-migration-factory
description: Understands AI Migration Factory artifact flow, sandbox boundaries, and proposal-only repair planning.
---

# AI Migration Factory

Use this skill when reviewing failure evidence from an AI Migration Factory run.

Important boundaries:

- Legacy source is immutable.
- Official statuses are deterministic factory outputs.
- Copilot output is advisory and proposal-only.
- Do not approve or reject runs.
- Do not change artifacts, gates, source, deployment state, or PR state.
- Return concise JSON repair proposals with limitations and human-review needs.
