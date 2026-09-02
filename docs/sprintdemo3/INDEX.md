# DEMO3 Sprint Documentation Index

DEMO3 builds a controlled migration pipeline and Build/Test Repair Agent review workflow for AI Migration Control Tower.

The sprint is docs-only at this stage. It prepares the implementation backlog for F0-F5 and keeps Stage 4/Jackson as one concrete F5 proof scenario, not the sprint frame.

## What DEMO3 Is Building

```text
FastAPI backend
-> deterministic agents
-> primary LLM
-> reviewer LLM
-> final Markdown artifact
-> stored artifact/checkpoint
-> user decision
-> next pipeline step
```

Core rule:

```text
A model reviews another model.
For supported model-required outputs, reviewer LLM is mandatory, not optional.
```

Authority invariant:

```text
Chatbot interprets.
Human decides.
Backend validates, persists, executes in sandbox, and proves with artifacts.
```

## F0-F5 Product Spine

| Feature | Story | Product outcome |
|---|---|---|
| F0 | [Pre-feature codebase cleanup](00-pre-feature-cleanup/STORY.md) | Old Copilot, TUI, CLI, duplicate orchestration, and stale terminology paths are inventoried and quarantined before feature work. |
| F1 | [Agent checkpoints and user decisions](01-agent-checkpoints/STORY.md) | Analysis and Planning stop at governed checkpoints with safe user decisions. |
| F2 | [Deterministic artifact + primary LLM + reviewer LLM](02-llm-review-chain/STORY.md) | Analysis and Planning produce reviewed, checksum-bound Markdown artifacts before downstream use. |
| F3 | [Target profile control](03-profile-targeting/STORY.md) | The user selects the target profile and the backend stops at that target. |
| F4 | [Start from current app state](04-source-profile-start/STORY.md) | Already-modernized apps start from their current source profile and skip older stages. |
| F5 | [Build/Test Repair Agent review loop](05-build-test-repair-agent-review-loop/STORY.md) | Build/test failures produce reviewed repair proposals and exact approved diffs only. |

## How To Read The Backlog

- [../DEMO3/PRD.md](../DEMO3/PRD.md) is the product baseline.
- [BACKLOG.md](BACKLOG.md) is the product backlog summary with story and task tables.
- [TASKS.md](TASKS.md) is the master task index for F0-F5.
- [ROADMAP.md](ROADMAP.md), [ARCHITECTURE.md](ARCHITECTURE.md), and [RISKS.md](RISKS.md) describe delivery order, system shape, and F0-F5 risks.
- Each feature folder contains a `README.md`, `STORY.md`, and `TASKS.md`.
- Historical implementation slices live under [implementation-slices/](implementation-slices/). They remain engineering detail mapped under F0-F5 and must not replace the product spine.
- [F0-F5-MAPPING.md](F0-F5-MAPPING.md) maps retained implementation slices to F0-F5 or marks future remove candidates.

## Implementation Order

1. F0 cleanup.
2. Foundry/model boundary hardening if needed.
3. F1 checkpoint foundation.
4. F2 Analysis reviewer chain.
5. F2 Planning reviewer chain.
6. F3 target profile.
7. F4 source/current-state start.
8. F5 Repair Agent evidence and proposal.
9. F5 reviewer and user decision loop.
10. F5 sandbox apply, rerun, proof.
11. Stage 4/Jackson as concrete F5 proof scenario.

## Historical Implementation Slices

Old `01-18` implementation slices are archived under [implementation-slices/](implementation-slices/). They are not top-level product feature folders.

Use [F0-F5-MAPPING.md](F0-F5-MAPPING.md) before reading any archived slice.

## Next Recommended Implementation Branch

Use a new implementation branch from the stable baseline after this docs branch is accepted, for example:

```text
feature/demo3-f0-cleanup
```

Do not start implementation from this docs-only cleanup.
