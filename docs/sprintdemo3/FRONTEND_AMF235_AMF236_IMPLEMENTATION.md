# Frontend Implementation Plan

AMF-235 / DEMO3-F3-STORY and AMF-236 / DEMO3-F4-STORY

## Status

This is an implementation plan, not the completed frontend implementation.
Jira status for this document should be "Planning complete / Ready for implementation," not "Done."
AMF-235 + AMF-236 should only be marked Done after frontend code, tests, type-check, and build pass.

## Purpose

Bring the frontend cockpit up to date with the backend-owned profile routing and current-state detection workflow.

The backend stays authoritative. The frontend only:

- previews valid profile choices before job creation,
- renders backend-returned route state after job creation,
- displays source-profile detection evidence,
- sends checksum-bound human decisions,
- avoids any execution/runtime control fields.

Implementation should happen on a feature branch created from latest `demov3`, not directly on `demov3`.

Recommended branch:

- `demo3/amf235-236-frontend-profile-routing`

## Story Scope

### AMF-235 / F3 - Target profile control

Expose target-profile selection in the new migration form and show the derived route in the cockpit.

### AMF-236 / F4 - Start from current app state

Expose source-profile detection evidence and a governed human override action in the cockpit.

## Source Of Truth

Backend files already define the contract the frontend must mirror:

- `migration_factory/control_tower/adapters/fastapi/app.py`
- `migration_factory/control_tower/application/v2_job_service.py`
- `migration_factory/control_tower/application/v2_stage_progression.py`
- `migration_factory/control_tower/application/v2_gate_action_service.py`
- `migration_factory/control_tower/application/v2_phase_gate_service.py`
- `migration_factory/control_tower/schemas/profile_model.py`
- `migration_factory/control_tower/schemas/profile_validation.py`
- `migration_factory/control_tower/schemas/profile_checkpoint_metadata.py`
- `migration_factory/control_tower/schemas/source_profile_override.py`
- `migration_factory/control_tower/schemas/run_configuration.py`

The gate detail endpoint already returns phase-specific evidence packs from the backend evidence pack builder:

- `pack_id`
- `pack_type`
- `gate_id`
- `gate_phase`
- `summary`
- `artifacts[]`
- `missing_refs[]`
- `checksum_mismatches[]`
- `failure_message`
- `resolved_artifact_count`
- `total_artifact_count`
- `redaction_status`
- `created_at`

That is the correct frontend projection boundary. The UI should not invent a second evidence path.

## Governance Rule

Backend remains the authority.
Frontend previews before creation only.
After job creation, cockpit displays backend-returned route state only.
No duplicate routing engine in the cockpit.
No assistant/system/API actor can override source profile.
Only a human checksum-bound gate action can override.

## Maintainability Note

`MigrationCockpit.tsx` is already large. Prefer small helper functions or small local components for:

- `MigrationRoutePanel`
- `SourceProfileDetectionPanel`
- `SourceProfileOverrideForm`

Do not dump all parsing and rendering logic inline if it makes the cockpit harder to maintain.

## Current Frontend Gaps

### Gap 1 - New migration form does not expose profile routing

File:

- `web/control-tower/app/migrations/new/NewMigrationForm.tsx`

Missing UI/state:

- `source_profile`
- `target_profile`
- route preview
- included stages preview
- skipped stages preview
- excluded stages preview
- invalid pair copy

### Gap 2 - Frontend V2 job response is too narrow

File:

- `web/control-tower/lib/contracts.ts`

`V2MigrationJobResponse` currently omits backend-returned route metadata:

- `source_profile`
- `target_profile`
- `validation_status`
- `validation_reason`
- `included_stages`
- `excluded_stages`
- `skipped_stages`
- `stage_continuation_policy`
- `run_configuration_id`

### Gap 3 - Job creation helper cannot send profile pair

File:

- `web/control-tower/lib/controlTowerApi.ts`

`createV2JobPayload` currently only sends `setup_id` and `policy`.

It must also allow:

- `source_profile`
- `target_profile`

### Gap 4 - Cockpit does not render the migration route

File:

- `web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx`

Missing cockpit projection:

- source profile
- target profile
- validation status
- validation reason
- included stages
- excluded stages
- skipped stages
- run configuration ID
- stage continuation policy

### Gap 5 - Gate contract is missing source-profile override support

Files:

- `web/control-tower/lib/contracts.ts`
- `web/control-tower/lib/controlTowerApi.ts`

Current `GateDecision` and `GateActionRequest` do not fully express the backend action:

- `override_source_profile`
- `detection_artifact_ref`
- `detected_source_profile`
- `requested_source_profile`
- `target_profile`
- `expected_detection_artifact_checksum`
- `comments`

### Gap 6 - Gate evidence type is too specific

File:

- `web/control-tower/lib/contracts.ts`

The current `RepairGateEvidence` type is too narrow for the backend's generic evidence packs.

Use a generic safe evidence type instead, aligned with the backend payload:

```ts
export type GateEvidenceArtifact = {
  kind: string;
  checksum_verified: boolean;
  content: string;
  size_bytes: number;
  truncated: boolean;
};

export type GateEvidencePack = {
  pack_id: string;
  pack_type: string;
  gate_id: string;
  gate_phase: string;
  summary: string;
  artifacts: GateEvidenceArtifact[];
  missing_refs: string[];
  checksum_mismatches: string[];
  failure_message: string | null;
  resolved_artifact_count: number;
  total_artifact_count: number;
  redaction_status: string;
  created_at: string;
};
```

## Implementation Plan

### Phase 1 - Update frontend contracts and API helpers

File:

- `web/control-tower/lib/contracts.ts`

Add:

- `MigrationProfileId`
- `MIGRATION_PROFILE_OPTIONS`
- route-aware job response fields
- gate override payload fields
- `GateEvidencePack`

Keep these rules:

- no `sandbox_path`
- no `argv`
- no `env`
- no `raw_command`
- no `filesystem_target`
- no `provider`
- no `model`
- no `deployment`
- no `endpoint`
- no access tokens or API keys

File:

- `web/control-tower/lib/controlTowerApi.ts`

Update:

- `CreateV2JobRequest`
- `createV2JobPayload(setupId, stageContinuationPolicy, options?)`
- `createV2Job(setupId, options?)`
- `postV2GateAction` typing

Important:

- `createV2JobPayload` should accept `sourceProfile` and `targetProfile`
- `createV2JobPayload` must not start accepting runtime controls
- `postV2GateAction` must send only checksum-bound, backend-owned decision data
- add a safe idempotency helper:

```ts
export function createIdempotencyKey(): string {
  if (globalThis.crypto?.randomUUID) {
    return globalThis.crypto.randomUUID();
  }
  return `idempotency-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}
```

`crypto.randomUUID()` is preferred, but tests and some dev environments may need the fallback.

### Phase 2 - Add profile selectors and route preview to New Migration

File:

- `web/control-tower/app/migrations/new/NewMigrationForm.tsx`

Add form state:

- `source_profile`
- `target_profile`

Recommended defaults:

- source: `springboot-2.7-java11`
- target: `springboot-4.0-java21`

Add a `Migration Route` fieldset that shows:

- source profile label
- target profile label
- included stages
- skipped stages
- excluded stages
- validation message for invalid pairs

Local validation rules:

- same source and target is blocked locally
- reversed pair is blocked locally
- source must be selectable as a source
- target must be selectable as a target

Do not treat local validation as backend authority. It is only a UX guard.

When starting the migration, send:

- `source_profile`
- `target_profile`

Do not send any unsafe execution inputs.

Required order for implementation:

1. Contract/API type alignment
2. `NewMigrationForm` profile selectors + route preview
3. `createV2JobPayload` sends `source_profile` and `target_profile`
4. Cockpit `Migration Route` panel
5. Stage timeline included/skipped/excluded labels
6. `analysis_review` source-profile detection evidence display
7. checksum-bound `override_source_profile` form
8. frontend tests
9. type-check/test/build verification

### Phase 3 - Add a migration route panel to the cockpit

File:

- `web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx`

Add a route panel that renders backend-returned values from `data.job`:

- source profile
- target profile
- validation status
- validation reason
- included stages
- excluded stages
- skipped stages
- run configuration ID
- stage continuation policy

Rule for the cockpit:

- after the job exists, display backend values only
- do not recompute the route locally for the live cockpit

Update stage timeline rendering so each stage can show one of:

- included
- skipped by source profile
- excluded by target profile

Use backend arrays directly:

- `included_stages`
- `excluded_stages`
- `skipped_stages`

### Phase 4 - Render source-profile detection evidence

File:

- `web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx`

In the open gate panel, when the gate is `analysis_review`, show source-profile detection evidence from the gate detail payload.

Display:

- detected source profile
- target profile
- confidence
- uncertainty notes
- evidence refs
- evidence checksums
- evidence summary

Use the backend gate detail evidence pack first.
Parse detection artifact JSON defensively only for display.
Do not use parsed artifact content as execution input.
Do not fetch artifacts by raw path.
If the existing payload is insufficient, add only the smallest backend-safe projection for source-profile detection evidence.

The UI should show a message like: "Source-profile detection evidence is unavailable; refresh the gate or rerun analysis."

### Phase 5 - Add the manual source-profile override form

Files:

- `web/control-tower/lib/contracts.ts`
- `web/control-tower/lib/controlTowerApi.ts`
- `web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx`

Show the override form only when:

- there is an open gate
- the gate phase is `analysis_review`
- `override_source_profile` is present in `available_actions`
- source-profile detection evidence is available

Form fields:

- requested source profile
- reason
- comments

Hidden backend-bound fields:

- `job_id`
- `gate_id`
- `expected_gate_checksum`
- `detection_artifact_ref`
- `detected_source_profile`
- `target_profile`
- `expected_detection_artifact_checksum`
- `decided_by`
- `actor_type = "human"`
- `idempotency_key`

Submit to:

- `POST /v1/v2/jobs/{job_id}/gates/{gate_id}/actions`

Payload action:

- `override_source_profile`

Rules:

- only the human operator can submit this action
- the assistant must not expose a direct override path
- checksum mismatch should force a refresh
- if `detection_artifact_ref` or `expected_detection_artifact_checksum` cannot be extracted from backend gate detail evidence, disable the override submit button
- do not submit empty strings or invented values for backend-bound checksum/artifact fields

After success:

- refresh gate state
- refresh job state
- refresh stages
- refresh pipeline
- re-render the route/skipped-stage view

### Phase 6 - Add and update frontend tests

Files:

- `web/control-tower/tests/controlTowerApi.test.ts`
- `web/control-tower/tests/newMigrationForm.test.tsx`
- `web/control-tower/tests/migrationCockpit.test.tsx`

#### `controlTowerApi.test.ts`

Cover:

- `createV2JobPayload` includes source and target profile
- `createV2JobPayload` does not include forbidden execution fields
- `GateDecision` includes `override_source_profile`
- `GateActionRequest` includes override fields
- `GateEvidencePack` shape matches the backend evidence pack
- explicit tests prove these fields are not present in requests or rendered as frontend controls:
  - `sandbox_path`
  - `argv`
  - `env`
  - `raw_command`
  - `filesystem_target`
  - `filesystem_root`
  - `output_root`
  - `report_root`
  - `run_root`
  - `ai_hub_path`
  - `java_home`
  - `java11_home`
  - `java17_home`
  - `java21_home`
  - `maven_cmd`
  - `provider`
  - `model`
  - `model_id`
  - `deployment`
  - `endpoint`
  - `api_key`
  - `access_token`

#### `newMigrationForm.test.tsx`

Cover:

- profile selectors exist
- defaults are source `springboot-2.7-java11` and target `springboot-4.0-java21`
- same-profile pair blocks start
- reversed pair blocks start
- start payload includes selected profile pair
- forbidden fields are absent

#### `migrationCockpit.test.tsx`

Cover:

- cockpit displays source and target profile from backend job data
- cockpit displays included/excluded/skipped stages
- skipped stage cards render with skipped state
- analysis_review gate renders detection evidence
- override form appears only for analysis_review with override action available
- override submit posts a checksum-bound `override_source_profile` action
- required reason/comments validation is enforced
- assistant cannot override source profile
- forbidden execution fields are absent from rendered copy and request payloads
- detection evidence unavailable state disables override submit and shows the refresh/rerun message

## Recommended UX Copy

### New Migration form

Migration Route

- Source: Spring Boot 2.7 / Java 11
- Target: Spring Boot 4.0 / Java 21
- Included stages: 2, 3, 4
- Skipped stages: none
- Excluded stages: none

### Cockpit

Migration Route

- Source profile: `springboot-3.5-java17`
- Target profile: `springboot-4.0-java21`
- Validation: valid
- Included stages: 3, 4
- Skipped stages: 2
- Excluded stages: none

### Analysis review gate

- Detected source profile
- Confidence
- Evidence checksum
- Uncertainty notes

### Override form

- Requested profile
- Reason
- Comments

## Non-Goals

- No backend governance redesign
- No new execution controls in the frontend
- No raw command, env, path, or sandbox inputs
- No duplicate routing engine in the UI
- No direct assistant override path
- No resume/import UI beyond the backend-backed cockpit state

## Verification Order

1. Update contracts and API helpers.
2. Add New Migration route controls.
3. Add cockpit route panel.
4. Add source-profile detection evidence display.
5. Add override form.
6. Update tests.
7. Run focused frontend checks:

```powershell
cd web/control-tower
npm run type-check
npm test
npm run build
```

## Backend Contingency

If the cockpit cannot render the source-profile detection artifact from the existing gate detail payload, add the smallest possible backend-safe projection for detection evidence only.

Do not add any path-based fetch or any raw execution fields.
Do not submit empty strings or invented backend-bound values while waiting on that projection.

## Final Acceptance Summary

This implementation is complete when:

- the new migration form can select source and target profiles,
- the cockpit shows the backend route and skipped stages,
- analysis gates show source-profile detection evidence,
- human override is checksum-bound and limited to the analysis gate,
- tests prove no unsafe execution fields are exposed.

Accepted only when:

- `cd web/control-tower`
- `npm run type-check`
- `npm test`
- `npm run build`

all pass.

If backend projection changes are made, also run focused backend tests:

```powershell
py -m pytest tests/control_tower/test_profile_api_contract.py tests/control_tower/test_source_profile_detection.py tests/control_tower/test_source_profile_override.py -q
```
