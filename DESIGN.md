# Migration Factory Frontend Design System

> **Purpose:** This document is the design and implementation reference for refactoring the existing **New Migration** and **Migration Cockpit** screens.  
> It is not permission to create parallel pages, mock routes, new backend behavior, or static replacements for backend data.

---

## 0. Quick reference (read this first)

This section exists so an implementing agent can copy exact values instead of re-deriving them from prose. If anything below conflicts with a later prose section, **the prose section wins** — this block is a convenience cache, not a second source of truth.

**Precedence when sources disagree (highest to lowest):**

1. Current production frontend behavior/contracts (never override).
2. This file, §4 Canonical visual tokens.
3. `preview.html` (New Migration) / `migration-factory-migration-cockpit-v3-3-pipeline-accurate.html` (Cockpit) — visual reference only, per §1 Prototype rule.
4. Existing project design-system primitives, where equivalent.

**Ready-to-paste CSS custom properties** (verified against both approved prototype files; use these verbatim unless equivalent tokens already exist in the codebase):

```css
:root {
  /* Surfaces */
  --background-page: #F5F7FA;
  --background-surface: #FFFFFF;
  --background-subtle: #F8FAFC;
  --background-muted: #F0F3F7;

  /* Text */
  --text-primary: #172033;
  --text-strong: #0B0E14;
  --text-muted: #667085;
  --text-quiet: #8B94A3;

  /* Borders */
  --border-default: #DCE2E9;
  --border-strong: #C7D0DB;

  /* Primary action */
  --action-primary: #3157D5;

  /* Status — three tokens per status, see §4.2 for when to use which */
  --signal-info: #4C8DFF;          /* dot / icon / border for Running */
  --signal-success: #35D07F;       /* dot / icon / border for Pass — NOT for text on white */
  --signal-success-text: #137847;  /* text-on-white for success */
  --signal-success-soft: #EAF9F1;  /* chip/background tint for success */
  --signal-warning: #F5A623;
  --signal-warning-text: #956100;
  --signal-warning-soft: #FFF6E3;
  --signal-danger: #FF5C5C;
  --signal-danger-text: #B42318;
  --signal-danger-soft: #FFF0F0;

  /* Graphite (Cockpit command deck / log console only) */
  --graphite-base: #0B0E14;
  --graphite-panel: #12161F;
  --graphite-border: #2A3445;
  --graphite-text: #E4E7EC;
  --graphite-muted: #8B94A3;

  /* Geometry */
  --radius-panel: 14px;
  --radius-control: 9px;

  /* Motion */
  --motion-fast: 140ms;
  --motion-base: 200ms;
  --motion-slow: 260ms;
  --ease-standard: cubic-bezier(.2, 0, 0, 1);
}
```

**Font stacks** (from approved prototypes — do not substitute):

```css
/* UI text */
font-family: "Inter Variable", Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;

/* IDs, paths, checksums, commands, timestamps, logs — see §4.3 */
font-family: "SFMono-Regular", Consolas, "Liberation Mono", monospace;
```

**Per-screen max content width** (intentionally different — do not merge into a single shared value):

| Screen | Max width | Why |
|---|---:|---|
| New Migration | `1280px` | Form-focused, narrower reading measure |
| Migration Cockpit | `1760px` | Control-surface screen with parallel panels |

If the codebase already defines equivalent tokens (e.g. `--color-bg-page`), map to the existing name and keep the existing value only if it is visually equivalent to the value above. Do not introduce a second parallel token system.

---

## 1. Source of truth

Use the following references together, in this order:

1. **Current production frontend**
   - Source of truth for routes, behavior, backend contracts, data, actions, permissions, loading states, error states, and conditional rendering.
2. **This `DESIGN.md`**
   - Source of truth for shared visual rules, density, accessibility, responsive behavior, and component treatment.
3. **Approved HTML prototypes**
   - `preview(1).html` — approved **New Migration** visual reference.
   - `migration-factory-migration-cockpit-v3-3-pipeline-accurate(2).html` — approved **Migration Cockpit** visual reference.
4. **Existing project design primitives**
   - Reuse existing components, tokens, icons, utilities, and conventions when they can produce the approved result without changing behavior.

### Prototype rule

The HTML prototypes are **visual and interaction references**, not production code.

Do not copy:

- Hardcoded job IDs, paths, stages, timestamps, counts, logs, chat messages, statuses, or artifact values.
- Prototype-only timers, simulated streaming events, fake success flows, or browser-only demo state.
- Duplicate navigation, duplicate page shells, or mock API behavior.
- Inline CSS or JavaScript when the current frontend has established React, styling, state, and API patterns.

Translate the prototype into the current frontend architecture while preserving the current backend-driven behavior.

---

## 2. Product character

Migration Factory is an internal engineering control surface.

The interface should feel:

- Operational, precise, calm, and trustworthy.
- Dense enough for engineers without becoming cramped.
- Premium through hierarchy, spacing, typography, and state clarity—not decoration.
- Specific to software migration work, not like a generic AI SaaS dashboard.
- White-first, with dark graphite used selectively for the Cockpit command deck and log console.
- Consistent between setup and execution screens.

The interface should not feel:

- Like a marketing page.
- Like a collection of unrelated cards.
- Like an AI-generated dashboard with excessive gradients, glows, pills, and empty containers.
- Like a demo populated with invented data.
- Like a replacement for backend authority.

---

## 3. Non-negotiable implementation principles

### 3.1 Backend authority

All values returned by the backend must remain rendered.

This includes, but is not limited to:

- Job state and stream state.
- Route validation and route steps.
- Pipeline phase names, order, statuses, messages, and artifact counts.
- Approval and gate state.
- Repair proposal state and reviewer output.
- Build and test outcomes, including `PASS_WITH_WARNINGS`.
- Evidence, logs, reports, checksums, IDs, timestamps, paths, and dependency comparison results.
- Assistant availability and responses.
- Cancellation and mutation outcomes.

Never reinterpret a backend status to make the design look cleaner. Render the backend value faithfully and improve only its visual presentation.

### 3.2 No parallel screens

Refactor the current routes and components in place.

Do not:

- Create `/new-migration-v2`, `/cockpit-v3`, preview routes, or alternate production screens.
- Keep the old screen and add a new replacement beside it.
- Create a second API layer or duplicate state-management path.
- replace backend data with prototype constants.

Small presentational component extraction is allowed only when it keeps the same route, behavior, state ownership, and API contracts.

### 3.3 Progressive disclosure

Primary execution information stays visible. Secondary or noisy information opens on demand.

- Evidence and live logs are hidden by default and open from a compact button into a drawer.
- The assistant is a small floating launcher that opens a popover.
- Repair, approvals, reports, and dependency comparison live in Job Details tabs.
- Optional environment import is collapsed by default.
- Empty features occupy one concise state, not a large blank card.

### 3.4 No information loss

Before changing a screen, build a mapping:

| Current information/action | Current component | New location | Preserved state/handler |
|---|---|---|---|

Every existing field, state, action, and backend-rendered value must appear in this mapping. Nothing may be removed because it is absent from the prototype.

---

## 4. Canonical visual tokens

Use existing project tokens when they are equivalent. Otherwise introduce or map to the following semantic tokens centrally.

### 4.1 Color

| Token | Value | Use |
|---|---:|---|
| `background.page` | `#F5F7FA` | Main application canvas |
| `background.surface` | `#FFFFFF` | Panels, forms, drawers |
| `background.subtle` | `#F8FAFC` | Compact rows and nested regions |
| `background.muted` | `#F0F3F7` | Section headers and inactive areas |
| `text.primary` | `#172033` | Body text |
| `text.strong` | `#0B0E14` | Page and section headings |
| `text.muted` | `#667085` | Supporting copy |
| `text.quiet` | `#8B94A3` | Metadata |
| `border.default` | `#DCE2E9` | Panels and grouped content |
| `border.strong` | `#C7D0DB` | Controls and emphasized boundaries |
| `action.primary` | `#3157D5` | Primary action |
| `graphite.base` | `#0B0E14` | Cockpit command deck |
| `graphite.panel` | `#12161F` | Logs and evidence |
| `graphite.border` | `#2A3445` | Dark-surface boundaries |
| `graphite.text` | `#E4E7EC` | Primary text on dark surfaces |
| `graphite.muted` | `#8B94A3` | Secondary text on dark surfaces |

**Status colors use three tokens per status, not one.** A single flat hex per status cannot satisfy §9's contrast requirement: the saturated "signal" tone reads fine as a small dot or icon but fails text-on-white contrast, so a separate darker `-text` tone and a lighter `-soft` background tint exist for that purpose. Use the `signal.*` tone only for dots, icons, and borders — never as text color on a white/light surface.

| Status | `signal.*` (dot / icon / border) | `signal.*-text` (text on light surface) | `signal.*-soft` (chip / row background tint) |
|---|---|---|---|
| Info / Running | `#4C8DFF` | `#4C8DFF` is acceptable at 14px+/bold; prefer surrounding label text in `text.primary` | `#EDF4FF` |
| Success / Pass | `#35D07F` | `#137847` | `#EAF9F1` |
| Warning / Pending | `#F5A623` | `#956100` | `#FFF6E3` |
| Danger / Failed / Cancelled | `#FF5C5C` | `#B42318` | `#FFF0F0` |

A status chip is typically: `-soft` background + `-text` text color + a small `signal.*` dot. Do not use the raw `signal.*` value as the text color of a chip label.

### 4.2 Status semantics

Never communicate status with color alone. Combine:

- A readable label.
- A symbol or icon.
- A semantic color, applied per the three-tier rule in §4.1 (soft background + text-tone label + signal-tone dot).
- Supporting text when the state is not self-explanatory.

| Backend/UI state | Label | Status color set |
|---|---|---|
| Running / connected | `RUNNING`, `Connected` | Info |
| Pass / done / approved | `PASS`, `DONE`, `APPROVED` | Success |
| Pending / waiting | `PENDING` | Warning |
| Failed / rejected / cancelled | Exact backend label | Danger |
| Neutral / unavailable | Exact descriptive label | Gray (`text.muted` / `background.muted`, no signal tone) |

Do not convert `PASS_WITH_WARNINGS` to `PASS`. Preserve the full value; the status color set is still Success, but the label must remain `PASS_WITH_WARNINGS`.

### 4.3 Typography

Prefer the application's existing sans-serif family. If already available, use Inter or Inter Variable (`"Inter Variable", Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif`, per §0). Do not add a network font dependency solely for this refactor.

Use this monospace stack — `"SFMono-Regular", Consolas, "Liberation Mono", monospace` — for:

- IDs.
- Paths.
- Checksums.
- Commands.
- Timestamps.
- Runtime/profile names.
- Log output.

Recommended production scale:

| Role | Size | Weight |
|---|---:|---:|
| Page title | 24–30 px | 750–800 |
| Section title | 14–16 px | 700–760 |
| Panel title | 13–14 px | 700–760 |
| Body | 13–14 px | 400–550 |
| Labels | 12–13 px | 600–700 |
| Metadata | 11–12 px | 500–700 |
| Mono operational data | 11–12 px | 500–650 |

Do not blindly copy the smallest prototype font sizes. Production body text must remain readable.

### 4.4 Spacing and geometry

Use a 4 px spacing base.

| Purpose | Value |
|---|---:|
| Desktop page gutter | 32–40 px |
| Major panel gap | 14–22 px |
| Panel body padding | 14–18 px |
| Dense row padding | 8–12 px |
| Form-field gap | 14–16 px |
| Control height | 38–40 px |
| Primary action height | 42–44 px |
| Panel radius | 12–15 px |
| Control radius | 8–10 px |
| Pill radius | 999 px |

Cards should use restrained borders and soft shadows. Avoid excessive elevation and nested card-on-card styling.

### 4.5 Motion

Motion exists to explain state change.

Use:

- `140 ms` for hover and focus transitions.
- `200 ms` for drawers, popovers, and panel disclosure.
- `260 ms` maximum for emphasized entry transitions.
- Standard easing similar to `cubic-bezier(.2, 0, 0, 1)`.
- Stagger only for a short initial list entrance; do not re-animate on every polling update.
- `prefers-reduced-motion` support.

Avoid decorative looping animation. A subtle connected-stream pulse is acceptable.

---

## 5. Shared component rules

These names are conceptual. Reuse equivalent existing components rather than creating duplicates.

### 5.1 Page shell

- Sticky application header.
- Breadcrumb below the header.
- Clear page title and one sentence of operational context.
- Destructive action remains secondary and visually separated from the primary workflow.
- Use the current application shell; do not reproduce a second header inside the page.

### 5.2 Panel

- One panel represents one coherent task or information domain.
- Panel height follows content.
- Do not set large fixed heights to align unrelated panels.
- Nested panels should usually become rows, groups, or subtle bordered regions.
- Avoid empty panels.

### 5.3 Buttons

Hierarchy:

1. **Primary** — one dominant next action in a region.
2. **Secondary** — checks, save, retry, open, or neutral operations.
3. **Danger secondary** — cancellation or destructive confirmation.
4. **Icon button** — copy, close, reveal; always has an accessible name.

Rules:

- Use explicit verbs: `Start migration`, `Run preflight`, `Check Azure settings`, `Cancel migration`.
- Non-submit buttons use `type="button"`.
- Loading state keeps the button width stable.
- Disabled actions include visible explanatory text nearby.
- Never use a large red standalone button when a compact danger-secondary action is sufficient.

### 5.4 Form controls

- Every control has a persistent visible label.
- Required fields use a text marker and validation—not color only.
- Helper text appears under or beside the label.
- Errors include border, icon, and text.
- Preserve existing form state, validation, defaults, parsing, and submit payload.
- Do not validate all fields again after every harmless keystroke if the current workflow validates on submit.
- Paths and commands use monospace where it improves scanning.

### 5.5 Status chip

- Compact, readable, and never the only source of meaning.
- Use the exact backend status text unless an existing frontend mapping is already authoritative.
- Preserve unknown states with a neutral fallback; never silently map them to success.

### 5.6 Count badge

Use for artifact counts, phase counts, event counts, and compact metadata.

- Display exact numeric values from the backend.
- `0 artifacts` remains visible.
- Do not hide a count because it is zero.
- Prefer monospace for technical counts.

### 5.7 Empty state

Use a single quiet row or compact message when no action exists.

Examples:

- `No repair attempted yet.`
- `No gate is currently open.`
- `Final report is not available yet.`

Do not reserve a full-height panel for one line of absent data.

### 5.8 Tabs

Job Details tabs group related secondary content:

- Approval Decisions.
- Repair Proposal.
- Proof & Reports.
- Target Dependency Versions.

Requirements:

- Use semantic tab roles and associated tab panels.
- Keyboard navigation must work.
- Active tab is visually and programmatically selected.
- Do not unmount stateful content if that would lose uploads, edits, or backend query state.

### 5.9 Drawer

Evidence and logs use a right-side drawer, hidden by default.

Requirements:

- Trigger is a button with `aria-expanded` and `aria-controls`.
- Drawer has a clear title and close control.
- Escape closes it.
- Focus moves into the drawer and returns to the trigger.
- Background interaction is blocked while modal behavior is active.
- Live updates must not constantly steal focus or create noisy announcements.

### 5.10 Dialog

Use for cancel confirmation and other destructive decisions.

Requirements:

- Clear title and consequence.
- Safe action first, destructive confirmation second.
- Focus remains inside while open.
- Escape closes when safe.
- Focus returns to the invoking control.
- Use existing dialog primitive when available.

### 5.11 Floating assistant

The assistant is secondary to migration execution.

Default:

- Small floating launcher in the lower-right.
- Opens a compact popover.
- Does not occupy a permanent dashboard row.
- Closes when the evidence drawer opens.
- Remains available across relevant Cockpit states.

Preserve the authority note:

> Cannot execute, approve, write files, change the route, or override proof.

Do not hardcode assistant responses. Use the existing assistant hook/service and backend-returned context.

---

## 6. New Migration screen

### 6.1 Goal

Let the user configure and validate a migration with minimal ambiguity before creating the backend job.

### 6.2 Layout

Desktop:

1. Page header and `Save setup`.
2. Migration route board.
3. Main form column.
4. Sticky review/readiness sidebar.

Main form sections:

- Project and paths.
- Java and Maven.
- Source and target.
- Azure and preflight.

Sidebar:

- Review summary.
- Ready-to-start state.
- `Start migration`.

Responsive:

- Sidebar moves below the form.
- Route becomes horizontally scrollable when necessary.
- Fields collapse to one column.
- Actions remain full-width where required.

### 6.3 Required preserved functionality

Preserve every currently implemented field and action, including:

- Environment-block import and parsing.
- Run name.
- Legacy application path.
- Output parent path.
- AI Hub path.
- Proof level.
- Endpoint smoke-test option.
- Java 11, Java 17, and Java 21 locations.
- Maven command.
- Source and target profiles.
- Included, skipped, and excluded stages.
- Save setup.
- Azure settings check.
- Preflight execution and all returned checks.
- Readiness and checksum state.
- Start migration.
- Backend and mutation error handling.

The prototype does not authorize removing additional current fields.

### 6.4 Interaction states

Implement and visually verify:

- Initial.
- Dirty/edited.
- Parsing environment values.
- Parse partial success and failure.
- Azure check idle, loading, pass, and fail.
- Preflight disabled, running, pass, warning, and fail.
- Missing required fields.
- Checksum pending, matched, and mismatch.
- Start disabled and enabled.
- Start loading, success, and error.
- Backend disconnected or unavailable, when currently supported.

### 6.5 Route board

- Route steps are backend-driven and variable length.
- Do not assume exactly four stages.
- Source and target are emphasized.
- Intermediate steps remain compact.
- Long profile names truncate visually but remain available by title, tooltip, or expandable text.
- Included/skipped/excluded text uses backend values.

---

## 7. Migration Cockpit screen

### 7.1 Goal

Answer these questions immediately:

1. What is the migration doing now?
2. What has completed?
3. What is waiting or failed?
4. Does the user need to act?
5. Where can the user inspect evidence or secondary details?

### 7.2 Layout

Desktop order:

1. Job header: title, job ID, overall status, cancel action.
2. Dark command deck:
   - Current execution summary.
   - Route progress.
   - Gate, continuation, approval, and validation facts.
   - Compact Evidence & logs trigger.
3. Full-width **Execution control** panel:
   - Left: route transition/timeline.
   - Right: pipeline status.
   - Bottom: route, gate, and attention context.
4. Job Details tabs.
5. Floating assistant launcher.
6. Evidence drawer, hidden by default.

Do not add a permanent assistant card or permanent logs card.

### 7.3 Pipeline status

The pipeline is backend-driven.

Render:

- Stream state.
- Exact phase order.
- Exact phase name.
- Exact phase status.
- Exact current-action text.
- Exact artifact count.

The current approved example contains:

1. Preflight.
2. Cancellation.
3. Analysis Agent.
4. Planning Agent.
5. Assessment Agent.
6. Human Approval.
7. Transform Agent.
8. Build Agent.
9. Test Validation.
10. Repair/Failure.
11. Result Contract.
12. Final Report.
13. Stage Report.

Do not hardcode this list as the only possible list. It is an example of the backend contract. Render the phases returned by the current implementation.

Pipeline row rules:

- Dense row height.
- Status chip first.
- Phase name next.
- Action text can use up to two lines.
- Artifact count stays visible.
- Critical failure text must not be truncated without a way to reveal it.
- A current or next phase may receive subtle emphasis, but do not invent phase progression.
- Polling or streaming updates should update rows without replaying entrance animations.

### 7.4 Timeline

- Supports 2 to 8+ route steps.
- Completed stages collapse to compact rows.
- Only the current stage or transition expands.
- Pending stages are compact and dimmed.
- Timeline rail scrolls independently when needed.
- Do not infer completion solely from route position; use backend status.

### 7.5 Evidence and live logs

Hidden by default.

The compact trigger may display the current event count. Drawer contents:

- Stream status.
- Route or stage context.
- Timestamp.
- Exact backend event status.
- Exact message.
- Raw-log disclosure.
- Latest/follow action when currently supported.

Do not generate simulated logs in production.

### 7.6 Job Details

#### Approval Decisions

- Preserve auto-approval control and current mutation behavior.
- Preserve stage, decision, reason, checksum, mode, and timestamp.
- Do not show approval as successful until confirmed by the backend.

#### Repair Proposal

- Preserve all proposal, review, checksum, diff, assistant, apply, rebuild, retest, and revision states.
- `No repair attempted yet.` is only for the true empty state.
- Failure and stale-proposal states must remain visible.

#### Proof & Reports

- Preserve existing gate requirements and report-generation conditions.
- Disabled action includes an explanation.
- Do not derive availability from the prototype.

#### Target Dependency Versions

- Preserve CSV/XLSX file validation, upload, comparison, and change behavior.
- Keep the selected file state.
- Do not imply a file was applied before backend confirmation.

### 7.7 Assistant

- Launcher and popover remain visually secondary.
- Use real message history.
- Preserve model unavailable, deterministic fallback, loading, error, idempotency, lease, stale-proposal, and revision states if currently implemented.
- Do not display fake deterministic answers.
- Keep user and assistant messages visually distinct.
- Input composer remains pinned within the popover.

---

## 8. Responsive behavior

Container max width is per-screen, not shared — see §0 (`1280px` New Migration, `1760px` Cockpit).

Verify at minimum:

- 1600–1760 px desktop.
- 1440 px laptop.
- 1280 px compact desktop.
- 1024 px tablet landscape.
- 768 px tablet/mobile boundary.
- 390 px mobile.

Rules:

- No horizontal page overflow.
- Technical route rails may scroll inside their own region.
- Cockpit execution panes stack below the desktop breakpoint.
- Pipeline rows reflow without hiding status or artifact count.
- Drawers become full-width on small screens.
- Floating assistant stays clear of important bottom actions.
- Sticky elements must not overlap the application header.

---

## 9. Accessibility and interaction quality

Minimum requirements:

- Semantic HTML first.
- Visible focus states.
- Logical keyboard order.
- All icon-only buttons have accessible names.
- Labels are connected to controls.
- Status is not communicated by color alone.
- Disclosure controls expose expanded/collapsed state.
- Dialog and drawer focus management is correct.
- Tabs follow the expected keyboard model.
- Errors are announced and visible.
- Reduced-motion preference is respected.
- Contrast is verified for text, status indicators, controls, and focus rings.

Do not add ARIA when native semantics already solve the problem.

---

## 10. Loading, streaming, and error states

- Do not make the page look frozen.
- Keep existing data visible during background refresh when safe.
- Use inline loading for local actions.
- Use skeletons only for meaningful initial content loading if the project already has them.
- Preserve previous pipeline data while reconnecting unless the existing contract requires clearing it.
- Show stream states such as connected, reconnecting, disconnected, or failed when provided.
- Error copy states what failed and what the user can do next.
- Never convert a backend error into an empty state.

---

## 11. Code-quality rules

- Use existing React/Next.js patterns, TypeScript contracts, hooks, query/mutation utilities, and styling system.
- Do not add a second styling framework.
- Do not introduce large dependencies for visual effects.
- Avoid inline styles unless the current codebase standard requires them for dynamic values.
- Avoid duplicating tokens between screens.
- Preserve component boundaries where they encode behavior.
- Memoization is used only where profiling or render behavior justifies it.
- Clean up intervals, subscriptions, listeners, and stream connections.
- Keep test selectors stable where practical.
- Preserve URL state and navigation behavior.

---

## 12. Acceptance checklist

### Functional preservation

- [ ] No backend endpoint or payload changed.
- [ ] No backend-rendered field or state removed.
- [ ] No current action removed.
- [ ] No fake prototype data shipped.
- [ ] Existing routing remains unchanged.
- [ ] Existing polling, streaming, and mutation ownership remains unchanged.
- [ ] Cancellation still works.
- [ ] Approval and repair flows still work.
- [ ] Reports and dependency comparison still work.
- [ ] Assistant still uses the current backend flow.

### New Migration

- [ ] Route, form, validation, review, and readiness match the approved hierarchy.
- [ ] All existing fields remain.
- [ ] Start remains gated by current real validation.
- [ ] Error and loading states are represented.
- [ ] Sidebar stacks correctly on smaller screens.

### Cockpit

- [ ] Command deck shows current backend state.
- [ ] Timeline supports variable route length.
- [ ] Pipeline renders exact backend phases and artifact counts.
- [ ] Evidence drawer is hidden by default.
- [ ] Assistant is a floating popover.
- [ ] Job Details tabs preserve state.
- [ ] Empty states are compact and accurate.
- [ ] No permanent blank cards remain.

### Quality

- [ ] Typecheck passes.
- [ ] Lint passes.
- [ ] Existing tests pass.
- [ ] Production build passes.
- [ ] No console errors.
- [ ] Keyboard navigation works.
- [ ] Responsive widths are verified.
- [ ] Reduced motion is verified.
- [ ] Final diff contains no unrelated backend changes.
