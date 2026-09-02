# Migration Factory Frontend Refactor

## Mission

Refactor the **existing** Migration Factory frontend screens to implement the approved designs for:

1. **New Migration**
2. **Migration Cockpit**

This is an in-place frontend refactor.

Do not create parallel screens, new production routes, duplicate API layers, or backend changes.

---

## Required branch and startup sequence

Follow this sequence exactly before editing code.

### 1. Confirm repository state

Run:

```bash
git status --short
git branch --show-current
```

If the worktree contains unrelated uncommitted changes, stop and report them. Do not discard, stash, or overwrite user work without explicit permission.

### 2. Fetch and switch to the required branch

The required branch is:

```text
frontendv2
```

Run:

```bash
git fetch origin
git switch frontendv2
```

If the branch does not exist locally but exists remotely:

```bash
git switch --track origin/frontendv2
```

Verify:

```bash
git branch --show-current
```

The output must be exactly:

```text
frontendv2
```

Do not implement on another branch.

### 3. Pull before analysis

Run:

```bash
git pull --ff-only origin frontendv2
```

Do not use a merge pull. If fast-forward pull fails, stop and report the repository state.

### 4. Read project instructions

Read, in this order:

1. Every applicable `AGENTS.md` from repository root to the frontend files being edited.
2. `DESIGN.md`.
3. Existing frontend README, package scripts, and architecture notes.
4. TypeScript/API contracts used by the two screens.

Do not start implementation before reading `DESIGN.md`.

### 5. Read the approved prototypes

Locate and read the complete HTML prototypes:

- `preview(1).html`
  - Approved New Migration visual reference.
  - Page title: `New Migration · Migration Factory`.
- `migration-factory-migration-cockpit-v3-3-pipeline-accurate(2).html`
  - Approved Migration Cockpit visual reference.
  - Page title: `Migration Cockpit V3.3 · Migration Factory`.

If filenames differ in the repository, search by the page titles and distinctive headings.

Read the HTML, CSS, interaction model, responsive rules, and states. Treat them as design references only.

### 6. Read the current implementation

Before changing anything:

- Locate the current New Migration route and component tree.
- Locate the current Migration Cockpit route and component tree.
- Locate all hooks, stores, contexts, API clients, query keys, mutations, stream/polling code, contracts, and tests used by each screen.
- Read the complete files, not isolated snippets.
- Trace the data from backend response to rendered UI.
- Identify all conditional states and actions.

### 7. Produce a preservation map

Before editing, write a concise implementation plan containing:

| Existing field/state/action | Current source | New location | Preservation approach |
|---|---|---|---|

The map must include every backend-rendered item and every user action on both screens.

Do not remove anything because it is not shown in a prototype.

---

## Scope

### In scope

- Layout and visual hierarchy.
- Styling and design-token alignment.
- Component composition.
- Responsive behavior.
- Accessible disclosure, tabs, dialogs, drawers, and popovers.
- Refactoring existing presentational components.
- Moving existing information to the approved locations.
- Improving labels and helper text without changing meaning.
- Preserving and presenting all current backend states.

### Out of scope

- Backend code.
- API endpoints.
- Request or response schemas.
- Database changes.
- New migration stages or pipeline phases.
- New assistant capabilities.
- New report behavior.
- New approval or repair behavior.
- New auth, CI, or monitoring features.
- New production pages or routes.
- Replacing backend data with hardcoded prototype data.
- Unrelated cleanup.

---

## Core preservation rule

The current frontend and backend contracts are authoritative.

The prototypes are not authoritative for data.

All values currently rendered from the backend must remain rendered, including:

- Job ID and job status.
- Stream state.
- Route, profiles, stage order, stage status, and route validation.
- Pipeline names, statuses, messages, order, and artifact counts.
- Gate and approval state.
- Repair proposal and reviewer state.
- Checksums and timestamps.
- Evidence and raw logs.
- Build, test, warning, failure, and cancellation state.
- Reports and report availability.
- Dependency comparison state.
- Assistant messages, availability, errors, and revision outcomes.
- New Migration fields, validation, preflight checks, and start conditions.

Never replace these values with HTML-prototype examples.

Never infer a status from its position. Use the backend value.

Never normalize `PASS_WITH_WARNINGS` to `PASS`.

Unknown backend states must render safely instead of disappearing.

---

## No-new-screen rule

Refactor the existing route in place.

Do not:

- Add a second New Migration page.
- Add a second Cockpit page.
- Add `v2`, `v3`, `new`, `preview`, or temporary production routes.
- Leave the old page active and introduce a parallel replacement.
- Duplicate the API client or state flow.

New small presentational components are allowed only when:

- The route remains the same.
- The existing behavior and state ownership remain the same.
- The component is reusable or materially improves maintainability.
- No parallel screen is created.

Prefer refactoring current components over replacing the entire feature.

---

## Prototype translation rules

Use the approved prototypes for:

- Information hierarchy.
- Placement.
- Density.
- Spacing.
- Typography direction.
- Panel and button treatment.
- Responsive layout.
- Progressive disclosure.
- Assistant and evidence interactions.

Do not copy:

- Prototype JavaScript simulations.
- Hardcoded jobs, paths, stages, timestamps, logs, counts, or chat responses.
- Fake success modals.
- Fake streaming timers.
- Inline one-file architecture.
- Duplicate top bars if the application shell already exists.

Translate prototype behavior into existing React and application primitives.

---

## Screen 1 — New Migration

Refactor the current New Migration screen to use the approved hierarchy:

1. Existing application header and breadcrumb.
2. Page title and `Save setup`.
3. Migration route board.
4. Main form sections:
   - Project and paths.
   - Java and Maven.
   - Source and target.
   - Azure and preflight.
5. Sticky review/readiness sidebar.
6. `Start migration` as the final primary action.

### Preserve

- Every current form field.
- Current defaults.
- Environment import behavior.
- Field validation.
- Azure settings check.
- Preflight checks and returned messages.
- Route calculation and route validation.
- Included, skipped, and excluded stages.
- Checksum behavior.
- Save behavior.
- Start behavior and payload.
- Loading, warning, failure, and success states.

### Do not

- Hardcode a four-stage route.
- Simulate preflight with timers.
- Enable Start based on prototype-local state.
- Remove a field because it is not in the HTML.
- change endpoint smoke-test semantics.
- Change backend paths or payload names.

---

## Screen 2 — Migration Cockpit

Refactor the current Cockpit screen to use:

1. Job header:
   - Job ID.
   - Overall status.
   - Cancel action.
2. Dark command deck:
   - Current execution.
   - Route progress.
   - Gate, continuation, approval, and validation facts.
   - Compact Evidence & logs trigger.
3. Unified `Execution control` panel:
   - Route transition/timeline.
   - Pipeline status.
   - Route, gate, and attention context.
4. Job Details tabs:
   - Approval Decisions.
   - Repair Proposal.
   - Proof & Reports.
   - Target Dependency Versions.
5. Floating assistant launcher and popover.
6. Evidence drawer hidden by default.

### Pipeline rules

Render the pipeline array returned by the existing backend/frontend contract.

For every phase, preserve:

- Order.
- Name.
- Status.
- Action/reason text.
- Artifact count.

The approved prototype demonstrates this current state:

```text
PENDING  Preflight          V2 migration job created.                                          0 artifacts
PENDING  Cancellation       Waiting for backend-owned evidence.                                0 artifacts
PASS     Analysis Agent     analysis phase completed.                                         48 artifacts
PASS     Planning Agent     planning phase completed.                                         41 artifacts
PASS     Assessment Agent   assessment phase completed.                                       22 artifacts
PASS     Human Approval     Human approval phase complete; sandbox transform has started.       4 artifacts
PASS     Transform Agent    Sandbox transform completed.                                      24 artifacts
PASS     Build Agent        Sandbox build completed.                                           0 artifacts
PASS     Test Validation    Sandbox tests accepted with status: PASS_WITH_WARNINGS.             0 artifacts
PENDING  Repair/Failure     Waiting for backend-owned evidence.                                0 artifacts
PENDING  Result Contract    Waiting for backend-owned evidence.                                0 artifacts
PENDING  Final Report       Waiting for backend-owned evidence.                                8 artifacts
PASS     Stage Report       Final report written.                                               0 artifacts
```

This is reference data, not a hardcoded production list.

### Timeline rules

- Variable route length.
- Completed items compact.
- Current item expanded.
- Pending items compact.
- Use backend status, not array position, to determine state.
- Preserve runtime profile, catalog, execution JDK, artifacts, evidence, and action when returned.

### Evidence rules

- Hidden by default.
- Opens from the compact Evidence & logs button.
- Uses current evidence/log data.
- Preserves event status, timestamp, message, raw log, and stream state.
- No simulated log generation in production.
- Escape and close control work.
- Focus returns to the trigger.

### Assistant rules

- Keep the existing assistant API and state flow.
- Floating launcher opens a compact popover.
- Real history is rendered.
- Existing unavailable, fallback, loading, error, idempotency, stale-state, and revision states remain.
- Keep the authority restriction note.
- Do not hardcode a chatbot answer from the prototype.

### Job Details rules

Preserve all existing:

- Approval controls and decisions.
- Repair proposal, reviewer, checksum, diff, apply, rebuild, retest, and revision behavior.
- Proof/report state and generation conditions.
- CSV/XLSX dependency comparison and Change action.

Do not replace a non-empty backend state with an empty message.

---

## Styling rules

Follow `DESIGN.md`.

Key requirements:

- White application background.
- Dark graphite only for command deck and log console.
- Restrained borders and shadows.
- One clear primary action per region.
- Status uses label, symbol, and color.
- Body text remains readable; do not copy extremely small prototype sizes.
- Mono is reserved for technical data.
- No excessive gradients, glass, glow, or decorative animation.
- Liquid/glass treatment is limited to the floating assistant.
- Cards follow content height.
- Empty states are compact.
- Evidence and logs do not occupy permanent dashboard space.

Use existing design-system primitives when they can meet the approved result.

Do not add a new CSS framework.

---

## Accessibility requirements

Implement with semantic HTML and current project accessibility primitives.

Must include:

- Visible labels.
- Focus-visible states.
- Accessible names for icon buttons.
- `type="button"` for non-submit buttons.
- `aria-expanded` and `aria-controls` for disclosure triggers.
- Correct tab semantics and keyboard behavior.
- Correct dialog/drawer focus management.
- Escape handling where appropriate.
- Focus return to invoking controls.
- Status meaning beyond color.
- Reduced-motion support.
- No noisy live-region announcement for every log line.

Do not add ARIA when native HTML already provides the correct semantics.

---

## Implementation order

Implement in this order unless the current architecture requires a smaller preparatory refactor:

1. Shared token and primitive alignment using existing project infrastructure.
2. New Migration screen.
3. New Migration behavior verification.
4. Cockpit command deck and unified execution layout.
5. Pipeline rendering using real backend data.
6. Evidence drawer.
7. Job Details tabs.
8. Floating assistant.
9. Responsive and accessibility refinement.
10. Tests and final cleanup.

Do not change both screens through a single uncontrolled rewrite.

---

## Validation commands

First inspect available scripts:

```bash
cat package.json
```

Use the repository’s actual package manager and scripts.

Run all applicable checks, including the existing equivalents of:

```bash
npm run lint
npm run typecheck
npm test
npm run build
```

Do not invent commands when scripts differ.

Also run existing targeted frontend tests for:

- New Migration.
- Cockpit.
- Pipeline rendering.
- Approval and repair panels.
- Assistant.
- File upload and dependency comparison.

If browser tests or Playwright exist, use them for the two current routes.

---

## Required manual verification

Verify with real current frontend state or existing test fixtures.

### New Migration

- Initial form.
- Environment import.
- Missing required field.
- Azure loading/pass/fail.
- Preflight loading/pass/warning/fail.
- Route with different step counts.
- Start disabled/enabled.
- Start error and success.

### Cockpit

- Running.
- Completed.
- Failed.
- Cancelled.
- Stream connected/reconnecting/disconnected.
- Open approval gate.
- Repair required.
- Repair proposal available.
- No repair.
- Report unavailable/available.
- Pipeline long action text.
- Zero and non-zero artifact counts.
- Evidence drawer open/closed.
- Assistant unavailable/loading/message/error.
- Dependency file selected/invalid/applied/error.

### Responsive widths

Check at least:

```text
1760
1440
1280
1024
768
390
```

Confirm:

- No page-level horizontal overflow.
- No clipped actions.
- No hidden backend status.
- Drawers and popovers remain usable.
- Sticky content does not overlap the header.

---

## Stop conditions

Stop and report instead of guessing when:

- The required branch cannot be checked out.
- `git pull --ff-only` fails.
- The worktree contains unrelated user changes.
- A prototype cannot be found.
- Current backend data is rendered in a way that cannot be mapped confidently.
- The refactor appears to require a backend contract change.
- An existing action has no identifiable handler.
- Tests reveal unrelated failures that make validation unreliable.

Do not silently bypass these conditions.

---

## Definition of done

The task is complete only when:

- Both existing screens are refactored in place.
- No alternate production route exists.
- All existing backend-rendered information remains visible.
- All current actions still call the current handlers.
- No backend or contract change is included.
- No prototype mock data is shipped.
- Lint, typecheck, tests, and build pass, or exact pre-existing failures are documented.
- Keyboard and responsive behavior are verified.
- The final diff contains no unrelated changes.

---

## Final response format

Report:

### Branch

- Confirm `frontendv2`.
- Include the pulled commit SHA.

### Files changed

- List each changed file and why.

### Behavior preserved

- Summarize API, state, and actions preserved.

### UI implemented

- New Migration summary.
- Cockpit summary.

### Validation

- Command.
- Result.
- Any pre-existing failure.

### Risks or TODOs

- Only real unresolved items.
- Do not claim completion when a required state was not verified.
