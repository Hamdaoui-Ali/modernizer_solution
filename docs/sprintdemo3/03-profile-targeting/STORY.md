[STORY] Target profile control

As a migration operator,
I want to select the target profile for the migration,
So that the system migrates only to the requested target and does not continue into unnecessary or riskier stages.

---

## Acceptance Criteria

* [ ] Given a migration job is configured, when the user selects `target_profile`, then the backend persists the selected target profile in job configuration.
* [ ] Given `source_profile` and `target_profile` are provided, when the backend validates the pair, then invalid, unsupported, reversed, or unsafe pairs are rejected or handled as explicit no-op cases.
* [ ] Given a valid source/target pair, when stage progression is planned, then only stages required to reach the target profile are included.
* [ ] Given the target profile is reached, when the runner evaluates next stage, then the pipeline stops at the selected target.
* [ ] Given a higher profile exists, when the selected target is lower than that higher profile, then the pipeline does not continue beyond the target.
* [ ] Given checkpoint artifacts are created, when they are stored, then source and target profile metadata are included.
* [ ] Given resume occurs after target reached, when stage progression is evaluated, then target overshoot remains blocked.

---

## Scope

**In scope:** Profile model, profile validation, stage/profile mapping, stop-at-target behavior, safe API fields, artifact/checkpoint metadata, and target-overshoot prevention tests.
**Out of scope:** Provider/model profile selection, arbitrary custom execution stages, user-supplied commands, or duplicate planning/stage progression logic.

---

## Technical Notes

* Files/services to inspect: `migration_factory/control_tower/application/v2_stage_progression.py`, `migration_factory/control_tower/schemas/run_configuration.py`, `migration_factory/control_tower/schemas/pipeline_definition.py`.
* Profile files to inspect: `migration_factory/profiles/`, `migration_factory/profile_reader.py`, `migration_factory/agents/planning_agent/`.
* API files to inspect: `migration_factory/control_tower/adapters/fastapi/app.py`.
* Public contracts may expose `source_profile`, `target_profile`, validation status, and included/excluded stage summaries, but not provider/model/deployment/env refs or execution details.

---

## Definition of Done

* [ ] Code reviewed & merged
* [ ] Unit tests passing
* [ ] AC validated by PO
* [ ] No blocking issues

---

Labels: profile-control migration sprint-DEMO3
Story Points: 8
Priority: High
Epic Link: DEMO3 - Controlled Migration Pipeline and Build/Test Repair Agent Review
