# DEMO3 Roadmap

The roadmap starts from stable baseline `0d9fa7b3b4c386aaebaa7287bebb3f3d2e3cb383` and the docs branch `docs/demo3-f0-f5-prd-stable-0d9fa7b`.

## Delivery Order

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

## Milestones

| Milestone | Verifiable outcome | Parent story |
|---|---|---|
| F0 cleanup | Copilot, TUI, CLI, duplicate orchestration, unused module, and terminology cleanup requirements are implementation-ready. | DEMO3-F0-STORY |
| Foundry/model boundary hardening | Public contracts keep provider/model/deployment/env refs, paths, argv, env, raw commands, and filesystem targets out of product APIs. | DEMO3-F0-STORY |
| F1 checkpoint foundation | Analysis and Planning checkpoint states, decisions, stop conditions, artifact preview/download, and resume behavior are defined. | DEMO3-F1-STORY |
| F2 Analysis reviewer chain | Analysis output follows deterministic artifact, primary LLM, reviewer LLM, final Markdown, stored checkpoint. | DEMO3-F2-STORY |
| F2 Planning reviewer chain | Planning output follows the same mandatory reviewer chain and becomes next-agent input. | DEMO3-F2-STORY |
| F3 target profile | The backend validates source/target profile pairs, routes only required stages, and prevents target overshoot. | DEMO3-F3-STORY |
| F4 current-state start | Source profile detection, manual override, skipped-stage ledger, and resume compatibility are defined. | DEMO3-F4-STORY |
| F5 evidence/proposal | Build/test failure evidence and deterministic failure artifacts feed the Primary Repair LLM. | DEMO3-F5-STORY |
| F5 review/decision | Reviewer LLM validates the exact proposed diff and the user can approve, reject, or request another review with comments. | DEMO3-F5-STORY |
| F5 proof | Backend applies only the exact approved reviewed diff, reruns build/test, records proof, and rolls back when required. | DEMO3-F5-STORY |
| Stage 4/Jackson proof | OpenRewrite/Jackson is one backend-allowlisted proof scenario under generic F5 repair. | DEMO3-F5-STORY |

## Guardrails

- Do not implement F0-F5 from this documentation update.
- Do not start Stage 4 implementation from this documentation update.
- Do not add frontend code from this documentation update.
- Do not create provider-selection UI/API.
- Do not expose provider/model/deployment/env refs, `sandbox_path`, argv, env, raw commands, or filesystem targets as product API fields.
- Do not allow reviewer optionality for supported model-required outputs.
- Do not pass unreviewed primary LLM output to the next agent.
