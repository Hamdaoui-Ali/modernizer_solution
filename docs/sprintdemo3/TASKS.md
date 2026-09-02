# DEMO3 Master Task Index

Epic: DEMO3 - Controlled Migration Pipeline and Build/Test Repair Agent Review

This is the master task index for F0-F5. Archived implementation slices under `implementation-slices/` are engineering detail only and must not override this index.

## Stories

| Story ID | Story | Priority | Story points | Docs |
|---|---|---|---:|---|
| DEMO3-F0-STORY | Pre-feature codebase cleanup | Highest | 8 | [STORY](00-pre-feature-cleanup/STORY.md), [TASKS](00-pre-feature-cleanup/TASKS.md) |
| DEMO3-F1-STORY | Agent checkpoints and user decisions | High | 13 | [STORY](01-agent-checkpoints/STORY.md), [TASKS](01-agent-checkpoints/TASKS.md) |
| DEMO3-F2-STORY | Deterministic artifact + primary LLM + reviewer LLM | High | 13 | [STORY](02-llm-review-chain/STORY.md), [TASKS](02-llm-review-chain/TASKS.md) |
| DEMO3-F3-STORY | Target profile control | High | 8 | [STORY](03-profile-targeting/STORY.md), [TASKS](03-profile-targeting/TASKS.md) |
| DEMO3-F4-STORY | Start from current app state | High | 8 | [STORY](04-source-profile-start/STORY.md), [TASKS](04-source-profile-start/TASKS.md) |
| DEMO3-F5-STORY | Build/Test Repair Agent review loop | High | 13 | [STORY](05-build-test-repair-agent-review-loop/STORY.md), [TASKS](05-build-test-repair-agent-review-loop/TASKS.md) |

## F0-F5 Tasks

### F0 - Pre-feature codebase cleanup

| Task ID | Parent story | Task title | Priority | Story points | Deliverable | Files to inspect | Dependencies |
|---|---|---|---|---:|---|---|---|
| F0-T1 | DEMO3-F0-STORY | Inventory Copilot runtime paths | Highest | 2 | Copilot runtime inventory | `copilot_assist/`; `copilot_repair/`; `final_report/`; `orchestrator/`; FastAPI app | DEMO3-F0-STORY |
| F0-T2 | DEMO3-F0-STORY | Quarantine Copilot from product runtime | Highest | 3 | Copilot quarantine decision | V2 runner; assistant model client; model role router; `orchestrator/` | F0-T1 |
| F0-T3 | DEMO3-F0-STORY | Inventory TUI and CLI runtime paths | Highest | 2 | TUI/CLI runtime inventory | `migration_factory/tui/`; `migration_factory/cli.py`; FastAPI app | DEMO3-F0-STORY |
| F0-T4 | DEMO3-F0-STORY | Quarantine TUI from product workflow | Highest | 2 | TUI quarantine decision | `migration_factory/tui/`; `migration_factory/cli.py`; docs | F0-T3 |
| F0-T5 | DEMO3-F0-STORY | Identify duplicate orchestration logic | Highest | 3 | Duplicate orchestration report | `orchestrator/`; V2 progression/runner; gates; repair flow | DEMO3-F0-STORY |
| F0-T6 | DEMO3-F0-STORY | Identify unused modules and dependencies | Highest | 2 | Unused module/dependency report | package metadata; `migration_factory/`; tests | F0-T1, F0-T3, F0-T5 |
| F0-T7 | DEMO3-F0-STORY | Clean stale terminology | Highest | 2 | Terminology cleanup list | docs; FastAPI contracts; public schemas | DEMO3-F0-STORY |
| F0-T8 | DEMO3-F0-STORY | Generate cleanup report | Highest | 2 | F0 cleanup report | all F0 inventory outputs | F0-T1 through F0-T7 |

### F1 - Agent checkpoints and user decisions

| Task ID | Parent story | Task title | Priority | Story points | Deliverable | Files to inspect | Dependencies |
|---|---|---|---|---:|---|---|---|
| F1-T1 | DEMO3-F1-STORY | Define checkpoint state model | High | 3 | Checkpoint state model | phase gate service/schema; artifact revision; SQLite | DEMO3-F1-STORY |
| F1-T2 | DEMO3-F1-STORY | Define user decision contract | High | 3 | User decision contract | gate action service; phase gate service; run configuration; FastAPI app | F1-T1 |
| F1-T3 | DEMO3-F1-STORY | Define Analysis checkpoint | High | 3 | Analysis checkpoint contract | Analysis agent; V2 runner/progression; gates | F1-T1, F1-T2, DEMO3-F2-STORY |
| F1-T4 | DEMO3-F1-STORY | Define Planning checkpoint | High | 3 | Planning checkpoint contract | Planning agent; V2 runner/progression; gates | F1-T1, F1-T2, DEMO3-F2-STORY |
| F1-T5 | DEMO3-F1-STORY | Define safe auto-continue rules | High | 2 | Auto-continue policy | V2 progression/runner; Build/Test agents | F1-T1, F1-T2 |
| F1-T6 | DEMO3-F1-STORY | Define stop-condition matrix | High | 3 | Stop-condition matrix | V2 progression; phase gate service; repair flow; domain entities; SQLite | F1-T1, F1-T5 |
| F1-T7 | DEMO3-F1-STORY | Define artifact preview/download behavior | High | 2 | Artifact presentation contract | artifact resolver; artifact revision; FastAPI artifact routes; SQLite | F1-T1, F1-T2 |
| F1-T8 | DEMO3-F1-STORY | Define resume behavior | High | 3 | Resume contract | V2 progression/runner; run configuration; domain entities; SQLite | F1-T1, F1-T2, F1-T6 |

### F2 - Deterministic artifact + primary LLM + reviewer LLM

| Task ID | Parent story | Task title | Priority | Story points | Deliverable | Files to inspect | Dependencies |
|---|---|---|---|---:|---|---|---|
| F2-T1 | DEMO3-F2-STORY | Define deterministic artifact contract | High | 3 | Deterministic artifact schema | Analysis agent; Planning agent; artifact revision | DEMO3-F2-STORY |
| F2-T2 | DEMO3-F2-STORY | Define primary LLM role | High | 3 | Primary LLM output contract | model schemas; assistant model client; model role router; Analysis/Planning agents | F2-T1 |
| F2-T3 | DEMO3-F2-STORY | Define reviewer LLM role | High | 3 | Reviewer contract | reviewer service; model role router; model schemas; reviewer repositories | F2-T1, F2-T2 |
| F2-T4 | DEMO3-F2-STORY | Define reviewer decisions | High | 2 | Reviewer decision matrix | reviewer service; phase gate service; gate action service; artifact revision | F2-T3 |
| F2-T5 | DEMO3-F2-STORY | Define final Markdown artifact schema | High | 3 | Final Markdown schema | artifact resolver; artifact revision; SQLite; FastAPI app | F2-T1 through F2-T4 |
| F2-T6 | DEMO3-F2-STORY | Define retry/revision behavior | High | 2 | Revision behavior spec | artifact revision; gate action service; reviewer service; SQLite | F2-T4, DEMO3-F1-STORY |
| F2-T7 | DEMO3-F2-STORY | Define metadata and checksum binding | High | 3 | Metadata/checksum spec | artifact revision; reviewer service; artifact resolver; SQLite | F2-T1, F2-T2, F2-T3, F2-T5 |
| F2-T8 | DEMO3-F2-STORY | Define reviewer-required tests | High | 2 | Reviewer-required test plan | reviewer/model/artifact/agent tests | F2-T1 through F2-T7 |

### F3 - Target profile control

| Task ID | Parent story | Task title | Priority | Story points | Deliverable | Files to inspect | Dependencies |
|---|---|---|---|---:|---|---|---|
| F3-T1 | DEMO3-F3-STORY | Define profile model | High | 2 | Profile model spec | run configuration; pipeline definition; profiles; profile reader; Planning agent | DEMO3-F3-STORY |
| F3-T2 | DEMO3-F3-STORY | Define profile validation | High | 2 | Profile validation spec | V2 progression; run configuration; pipeline definition; profile reader | F3-T1 |
| F3-T3 | DEMO3-F3-STORY | Define stage/profile mapping | High | 3 | Stage/profile map | pipeline definition; V2 progression; Planning agent; profiles | F3-T1, F3-T2 |
| F3-T4 | DEMO3-F3-STORY | Define stop-at-target behavior | High | 3 | Stop-at-target policy | V2 progression; V2 runner; pipeline definition | F3-T2, F3-T3 |
| F3-T5 | DEMO3-F3-STORY | Define safe API fields | High | 2 | API contract | FastAPI app; run configuration; public schemas | F3-T1, F3-T2 |
| F3-T6 | DEMO3-F3-STORY | Define artifact/checkpoint metadata | High | 2 | Profile metadata spec | artifact revision; phase gate; SQLite; artifact resolver | F3-T1, F3-T2, F3-T3 |
| F3-T7 | DEMO3-F3-STORY | Define target-overshoot prevention tests | High | 2 | Overshoot test plan | stage progression tests; runner tests; pipeline definition tests | F3-T4, F3-T6 |

### F4 - Start from current app state

| Task ID | Parent story | Task title | Priority | Story points | Deliverable | Files to inspect | Dependencies |
|---|---|---|---|---:|---|---|---|
| F4-T1 | DEMO3-F4-STORY | Define source-profile detection artifact | High | 3 | Source-profile detection schema | Analysis agent; profile reader; profiles | DEMO3-F4-STORY, DEMO3-F2-STORY |
| F4-T2 | DEMO3-F4-STORY | Define manual source-profile override action | High | 2 | Override action contract | gate action service; FastAPI app; run configuration; phase gate schema | F4-T1, DEMO3-F1-STORY, DEMO3-F3-STORY |
| F4-T3 | DEMO3-F4-STORY | Define skipped-stage ledger | High | 2 | Skipped-stage ledger schema | V2 progression; domain entities; SQLite; artifact revision | F4-T1, DEMO3-F3-STORY |
| F4-T4 | DEMO3-F4-STORY | Define profile pair validation | High | 2 | Profile pair validation behavior | V2 progression; run configuration; pipeline definition; profile reader; Planning agent | F4-T1, F4-T2, DEMO3-F3-STORY |
| F4-T5 | DEMO3-F4-STORY | Define resume-from-checkpoint behavior | High | 3 | Resume-from-checkpoint spec | V2 progression/runner; run configuration; SQLite | F4-T3, F4-T4, DEMO3-F1-STORY |
| F4-T6 | DEMO3-F4-STORY | Define already-modernized app tests | High | 2 | Already-modernized app test plan | Analysis fixtures; stage progression tests; runner tests; profile tests | F4-T1, F4-T3, F4-T4, F4-T5 |

### F5 - Build/Test Repair Agent review loop

| Task ID | Parent story | Task title | Priority | Story points | Deliverable | Files to inspect | Dependencies |
|---|---|---|---|---:|---|---|---|
| F5-T1 | DEMO3-F5-STORY | Define build/test failure evidence capture | Highest | 3 | Failure evidence schema | Build/Test agents; evidence collector; V2 runner | DEMO3-F5-STORY |
| F5-T2 | DEMO3-F5-STORY | Define Repair Agent input context | Highest | 3 | Repair context pack | evidence collector; artifact resolver; model schemas; repositories | F5-T1 |
| F5-T3 | DEMO3-F5-STORY | Define deterministic failure artifact | Highest | 3 | Deterministic failure artifact schema | Build/Test agents; evidence collector; rule registry | F5-T1, F5-T2 |
| F5-T4 | DEMO3-F5-STORY | Define Primary Repair LLM role | Highest | 3 | Primary repair reasoning contract | model schemas; assistant model client; model role router; repair flow | F5-T2, F5-T3 |
| F5-T5 | DEMO3-F5-STORY | Define Reviewer LLM role for repair | Highest | 3 | Repair reviewer contract | reviewer service; repair gate service; model schemas; reviewer repositories | F5-T4 |
| F5-T6 | DEMO3-F5-STORY | Define proposed diff artifact | Highest | 3 | Proposed diff artifact schema | artifact revision; repair proposal repositories; repair flow | F5-T4, F5-T5 |
| F5-T7 | DEMO3-F5-STORY | Define policy validation before presentation | Highest | 3 | Policy validation artifact | patch gate; rule registry; repair flow; repair gate service | F5-T6 |
| F5-T8 | DEMO3-F5-STORY | Define user decision actions | Highest | 3 | Repair decision contract | FastAPI app; gate action service; repair gate service; approval mapping | F5-T5, F5-T6, F5-T7 |
| F5-T9 | DEMO3-F5-STORY | Define request-another-review loop | Highest | 3 | Repair revision loop spec | repair flow; reviewer service; artifact repositories; repair repositories | F5-T8 |
| F5-T10 | DEMO3-F5-STORY | Define exact-diff approval and apply behavior | Highest | 3 | Exact-apply policy | patch apply; patch gate; repair flow; repair gate service | F5-T6, F5-T7, F5-T8 |
| F5-T11 | DEMO3-F5-STORY | Define build/test rerun behavior | High | 2 | Rerun policy | Build/Test agents; V2 runner; validation runner | F5-T10 |
| F5-T12 | DEMO3-F5-STORY | Define repeated failure behavior | High | 2 | Repeated failure policy | repair flow; V2 progression; SQLite; validation runner | F5-T9, F5-T11 |
| F5-T13 | DEMO3-F5-STORY | Define rollback and proof behavior | High | 2 | Rollback/proof policy | validation runner; patch apply; repair ledger concepts; SQLite | F5-T10, F5-T11, F5-T12 |
| F5-T14 | DEMO3-F5-STORY | Define UI/API presentation contract | High | 2 | Presentation contract | FastAPI app; phase gate schemas; artifact revision; cockpit contracts | F5-T6, F5-T8 |
| F5-T15 | DEMO3-F5-STORY | Define F5 test matrix | High | 2 | F5 test plan | build/test/repair/reviewer tests and F5 files | F5-T1 through F5-T14 |

## Detailed Feature Task Docs

- [F0 tasks](00-pre-feature-cleanup/TASKS.md)
- [F1 tasks](01-agent-checkpoints/TASKS.md)
- [F2 tasks](02-llm-review-chain/TASKS.md)
- [F3 tasks](03-profile-targeting/TASKS.md)
- [F4 tasks](04-source-profile-start/TASKS.md)
- [F5 tasks](05-build-test-repair-agent-review-loop/TASKS.md)