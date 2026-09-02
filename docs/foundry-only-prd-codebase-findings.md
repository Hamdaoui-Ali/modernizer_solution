# Foundry-Only PRD / Codebase Alignment Findings

## 1. Executive Verdict
- Branch: `docs/foundry-only-prd-alignment`
- Commit: `ea6d5db`
- Worktree: Clean before documentation branch; documentation-only edits in progress.
- Base branch: `stable`
- Recommended first implementation branch: `fnd/01-copilot-quarantine-foundry-boundary`
- Verdict: Not aligned for DEMO3 implementation until Foundry-only foundation work is complete.
- Main conclusion: Azure AI Foundry must be the only LLM runtime boundary, but the current stable codebase still contains reachable Copilot runtime paths, Azure OpenAI-specific model plumbing, public provider/config DTO leakage, deterministic fallback semantics that can look like model output, and non-content-derived context-pack checksums.

## 2. Codebase Reality
| Area | File:line | Finding | Category | PRD impact |
|---|---|---|---|---|
| Backend model client | `migration_factory/control_tower/application/v2_assistant_model_client.py:49` | Client identifies provider as `azure_openai`. | A | PRD must require Azure OpenAI naming/config to be hidden behind Foundry adapter. |
| Backend model client | `migration_factory/control_tower/application/v2_assistant_model_client.py:56` | Reads `AZURE_OPENAI_ENDPOINT`, key, and deployment directly. | B | Blocks DEMO3 model work until application services use Foundry adapter. |
| Backend model client | `migration_factory/control_tower/application/v2_assistant_model_client.py:242` | Invocation path directly reads Azure OpenAI endpoint/key. | B | Requires backend-owned Foundry adapter/config contract. |
| Backend model client | `migration_factory/control_tower/application/v2_assistant_model_client.py:892` | `_fallback_result` returns deterministic content with `Model: fallback`. | A | PRD must fail closed for model-required operations and label non-model assistance. |
| Settings projection | `migration_factory/control_tower/application/v2_settings.py:63` | Foundry settings default to `azure_openai` and `AZURE_OPENAI_*` env refs. | A | PRD must treat this as implementation debt unless encapsulated. |
| Settings projection | `migration_factory/control_tower/application/v2_settings.py:117` | Public `AzureFoundryProjection` includes `provider`, endpoint, auth, roles. | A | PRD must forbid provider/config leakage in public DTOs. |
| Role router | `migration_factory/control_tower/application/v2_model_role_router.py:69` | Router resolves role deployment env refs and fallback deployment from env. | B | Role routing must become backend adapter capability, not public/provider contract. |
| Role router | `migration_factory/control_tower/application/v2_model_role_router.py:111` | Fallback deployment can be invoked after primary failure. | B | PRD must forbid provider fallback for model-required DEMO3 operations. |
| FastAPI | `migration_factory/control_tower/adapters/fastapi/app.py:2210` | Assistant event payload exposes `provider: azure_openai`. | A | Public/product events must not leak provider internals. |
| FastAPI | `migration_factory/control_tower/adapters/fastapi/app.py:3106` | Model-profile APIs expose `provider_kind`. | A | PRD must forbid provider selection/leakage from UI/API. |
| FastAPI | `migration_factory/control_tower/adapters/fastapi/app.py:3128` | Public response includes `model_env_ref`, `endpoint_env_ref`, `deployment_env_ref`. | A | PRD must forbid env/deployment refs in public DTOs. |
| FastAPI | `migration_factory/control_tower/adapters/fastapi/app.py:10401` | Readiness check reads `AZURE_OPENAI_*` directly. | B | Readiness belongs behind Foundry adapter. |
| Context pack | `migration_factory/control_tower/application/v2_model_schemas.py:471` | Context pack uses random UUID-derived ID. | B | PRD must require content-derived context checksums and policy binding. |
| Context pack | `migration_factory/control_tower/application/v2_model_schemas.py:481` | Context checksum is `cp-{pack_id[:8]}` rather than canonical content hash. | B | Blocks model audit/proof for DEMO3. |
| Frontend contracts | `web/control-tower/lib/contracts.ts:565` | Frontend settings contract includes provider and env refs. | A | PRD must forbid provider internals in frontend bundle/contracts. |
| Frontend cockpit | `web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx:201` | UI displays `Live Azure OpenAI`. | A | Product terminology must be Foundry/provider-neutral. |

## 3. Copilot Runtime / Product Prohibition Findings
| File:line | Finding | Required PRD rule |
|---|---|---|
| `migration_factory/orchestrator/state.py:19` | Default Copilot assist mode is `failures`. | GitHub Copilot must not be a DEMO3 runtime or fallback path. |
| `migration_factory/orchestrator/state.py:20` | Copilot report generation is enabled by default. | Copilot must not generate DEMO3 reports. |
| `migration_factory/orchestrator/state.py:21` | Default provider is `copilot_cli`. | Copilot CLI must be quarantined from DEMO3 runtime. |
| `migration_factory/orchestrator/graph.py:75` | Graph registers `copilot_phase_assist`. | Orchestrator must not route to Copilot assist. |
| `migration_factory/orchestrator/graph.py:86` | Graph registers `copilot_final_report`. | Final reporting must not invoke Copilot. |
| `migration_factory/orchestrator/graph.py:224` | Failure/warning routes can select Copilot assist. | Copilot must not be reachable from failure recovery. |
| `migration_factory/orchestrator/preflight.py:68` | Preflight probes Copilot availability. | TUI/preflight/status probing of Copilot is prohibited for DEMO3. |
| `migration_factory/orchestrator/summary.py:172` | Summary may generate Copilot final report/docs. | Copilot is not a report generator or hidden execution path. |
| `migration_factory/final_report/copilot.py:17` | Final report module resolves Copilot CLI executable. | Copilot CLI/SDK invocation must be quarantined. |
| `migration_factory/agents/planning_agent/copilot_assist_client.py:13` | Planning assist client wraps Copilot provider review. | Copilot must not be planning provider. |
| `migration_factory/agents/analysis_agent/analysis_agent/copilot_enricher.py:127` | Analysis enrichment defines Copilot SDK boundary. | Copilot must not be analysis enrichment provider. |
| `migration_factory/transform_v1_after_approval.py:648` | Transform path can invoke dependency Copilot advisory. | Copilot must not be repair/advisory fallback after approval. |
| `migration_factory/assessment/writer.py:172` | Assessment artifacts expose `copilot` status. | Public reports must clean terminology; legacy readability only. |
| `migration_factory/tui/copilot_status.py:149` | TUI status can call Copilot CLI detector. | TUI must not probe Copilot for DEMO3. |
| `web/control-tower/app/migrations/[jobId]/MigrationCockpit.tsx:769` | Cockpit displays `Copilot:` status. | Frontend must not display Copilot runtime status. |

## 4. Azure OpenAI / Provider Leakage Findings
| File:line | Finding | Required PRD rule |
|---|---|---|
| `migration_factory/control_tower/application/v2_settings.py:64` | `azure_foundry_provider` value is `azure_openai`. | Azure OpenAI-specific values are implementation debt behind Foundry adapter only. |
| `migration_factory/control_tower/application/v2_settings.py:65` | Publicly projected endpoint env ref is `AZURE_OPENAI_ENDPOINT`. | Frontend/public API must never receive env refs. |
| `migration_factory/control_tower/application/v2_settings.py:175` | Settings exposes fallback role env ref and enabled flag. | Provider fallback must not be a public or runtime DEMO3 contract. |
| `migration_factory/control_tower/adapters/fastapi/app.py:3157` | Public model profile accepts `azure_openai` as `provider_kind`. | No provider switching from UI/API. |
| `migration_factory/control_tower/adapters/fastapi/app.py:10029` | Assistant text says Azure OpenAI is configured. | Product text should say Azure AI Foundry or provider-neutral AI. |
| `migration_factory/control_tower/adapters/fastapi/app.py:10330` | Prompt context maps model status to `azure_openai` or deterministic fallback. | Provider details stay internal and deterministic fallback cannot satisfy model output. |
| `web/control-tower/lib/contracts.ts:503` | Assistant model contract carries `provider`. | Public DTOs must omit provider internals. |
| `web/control-tower/app/migrations/new/NewMigrationForm.tsx:508` | Setup UI displays provider value. | Frontend must not expose provider internals or selection. |

## 5. Foundry-Only Foundation Features
| Feature | Purpose | Must happen before |
|---|---|---|
| FND-01 Disable/quarantine Copilot runtime paths | Make Copilot unreachable from Control Tower, orchestrator, repair, report, TUI, public API, and frontend paths. | Stage 4/DEMO3 runtime work |
| FND-02 Azure AI Foundry adapter contract | Establish the sole backend model invocation/config/readiness/audit boundary. | Any model-backed assistant, proposer, reviewer, or repair work |
| FND-03 Remove public provider/config leakage | Remove provider names, env refs, deployments, credentials, and fallback details from public DTOs/frontend contracts. | Public DEMO3 APIs and cockpit work |
| FND-04 UI/report/docs terminology cleanup | Align current product language to Foundry/provider-neutral AI and quarantine legacy names. | Demo-facing UX/report work |
| FND-05 Context-pack enforcement | Bind model calls to redacted, bounded, content-checksummed, policy-versioned context. | LLM diagnosis/candidate/review |
| FND-06 Legacy compatibility mapping | Keep historical names readable without runtime reachability or public leakage. | Migration of existing artifacts/contracts |

## 6. PRD Updates Made
- Added `1.1 Foundry-only LLM runtime boundary`.
- Added `1.2 Copilot prohibition`.
- Expanded DEMO3 goals with Copilot quarantine, DTO cleanup, and context-pack binding.
- Expanded non-goals with Copilot integration prohibition, provider switching prohibition, Azure OpenAI leakage prohibition, and no Copilot fallback.
- Updated authority model to bind LLM authority to Azure AI Foundry only.
- Added Foundation 0 to required delivery order and implementation phases.
- Added Foundry-only foundation acceptance criteria.
- Added Foundry adapter, Copilot quarantine, public DTO leakage, frontend text/contract, context-pack, no-live-model, and forbidden-term regression tests.

## 7. Sprint Docs Updates Made
- `docs/sprintdemo3/INDEX.md`: added Foundation 0 before MVP-A/MVP-B and listed FND-01 through FND-06.
- `docs/sprintdemo3/ROADMAP.md`: made Foundation 0 a prerequisite and added its exit condition.
- `docs/sprintdemo3/ARCHITECTURE.md`: added Foundation 0 boundary requirements and Azure OpenAI/provider leakage rule.
- `docs/sprintdemo3/RISKS.md`: added risks for skipped foundation, Copilot reachability, Azure OpenAI leakage, and content-pack binding.
- `docs/sprintdemo3/TASKS.md`: added FND-01 through FND-06 task entries.

## 8. Remaining Implementation Blockers
- Blocker: Copilot runtime paths are still present and enabled/reachable in defaults, graph routes, preflight, summary, final report, agent wrappers, transform advisory, TUI, and frontend status.
- Why it blocks DEMO3: The product prohibition says Copilot must not be a runtime, fallback, planning engine, repair provider, reviewer, report generator, proposer, TUI probe, or hidden execution path.
- Suggested first implementation branch: `fnd/01-copilot-quarantine-foundry-boundary`

- Blocker: Azure OpenAI-specific configuration and direct env reads are in application/API layers.
- Why it blocks DEMO3: Foundry-only requires one backend-owned adapter boundary and no direct provider env reads by application services.
- Suggested first implementation branch: `fnd/01-copilot-quarantine-foundry-boundary`

- Blocker: Public API/frontend contracts expose provider names, provider_kind, env refs, deployment refs, fallback status, and Copilot fields.
- Why it blocks DEMO3: DEMO3 must not expose provider internals or provider switching in UI/API.
- Suggested first implementation branch: `fnd/01-copilot-quarantine-foundry-boundary`

- Blocker: Context-pack checksums are UUID-derived rather than content-derived and policy-versioned.
- Why it blocks DEMO3: Model audit cannot prove the exact redacted context supplied to Foundry.
- Suggested first implementation branch: `fnd/01-copilot-quarantine-foundry-boundary`

## 9. Final Recommendation
DEMO3 implementation should not start until Foundry-only foundation blockers are implemented. Documentation can proceed, but runtime work should begin with `fnd/01-copilot-quarantine-foundry-boundary` and complete FND-01 through FND-06 before Stage 4/DEMO3 feature implementation.
