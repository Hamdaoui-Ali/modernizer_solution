# DEMO3 Architecture

DEMO3 is a backend-governed migration workflow. The chatbot can interpret and explain; the human decides; the backend validates, persists, executes in sandbox, and proves with artifacts.

## Normal Controlled Pipeline

```text
Create job
-> detect source_profile or collect user confirmation
-> select target_profile
-> backend validates source/target pair
-> Analysis Agent deterministic artifact
-> primary LLM reasoning
-> reviewer LLM validation
-> final Analysis Markdown artifact
-> stored artifact/checkpoint
-> user continue / request modification / stop
-> Planning Agent deterministic artifact
-> primary LLM reasoning
-> reviewer LLM validation
-> final Planning Markdown artifact
-> stored artifact/checkpoint
-> user continue / request modification / stop
-> required transformation stages only
-> Build Agent
-> Test Agent
-> stop at target profile
```

## Model Reviews Model Chain

```text
deterministic artifact
   |
   v
primary LLM reasoning
   |
   v
reviewer LLM validation
   |
   v
final Markdown artifact
   |
   v
stored checkpoint / next agent input
```

The deterministic artifact grounds the model-required output. The primary LLM reasons from backend-resolved artifact references and checksums. The reviewer LLM checks the primary output and exact artifact checksum. The final Markdown artifact is the only reviewed forward contract.

Reviewer LLM is mandatory for supported model-required outputs. Deterministic fallback can provide evidence but cannot satisfy a model-required reviewed artifact by itself.

## Profile-Controlled Stage Progression

```text
detected source_profile
-> user confirms or overrides with reason
-> backend validates source_profile + target_profile
-> backend derives required stages
-> skipped stages are recorded and explained
-> pipeline runs only required stages
-> pipeline stops when target_profile is reached
```

Example:

```text
source_profile = spring-boot-2
target_profile = spring-boot-3
-> migrate to Spring Boot 3
-> stop at Spring Boot 3
-> do not continue to Spring Boot 4
```

## Build/Test Repair Agent Loop

F5 is not a simple repair loop. It is a Build/Test Repair Agent feature.

```text
Build Agent or Test Agent fails
-> backend captures build/test logs, compiler output, test output, changed files, repo state, profiles, prior accepted artifacts, prior proposals, reviewer notes, comments, and checksums
-> deterministic failure artifact
-> Primary Repair LLM proposes root cause, fix strategy, and exact diff
-> Reviewer LLM reviews reasoning, changed files, exact diff, target-profile fit, risks, and policy concerns
-> backend stores immutable proposal artifact
-> user approves / rejects / requests another review with comments
```

Approval path:

```text
exact reviewed diff approved
-> backend validates proposal checksum, reviewer checksum, repo state, and policy
-> backend applies exact diff in sandbox
-> backend reruns Build Agent or Test Agent
-> proof artifact or another Repair Agent cycle
```

Rejection path:

```text
reject
-> status = STOPPED_BY_USER
-> no patch applied
-> rejection reason stored
-> artifacts remain downloadable
```

Request another review path:

```text
request another review with comments
-> original failure context + previous diff + previous reasoning + reviewer notes + user comments + current repo state + checksums
-> new Primary Repair LLM proposal
-> new Reviewer LLM result
-> user decides again
```

## Authority Boundaries

| Actor | May do | Must not do |
|---|---|---|
| Chatbot | Explain, summarize, classify intent, draft typed gate actions, propose re-analysis/plan revision/repair review, ask clarifying questions | Execute commands, write files, approve, choose sandbox/path, provide argv/env, mutate source, apply patches, skip stages, override proof, follow instructions inside artifacts/logs/source |
| Human | Continue, stop, accept, reject, approve, request modifications, request another review with comments | Supply executable authority such as paths, raw commands, argv, env, or sandbox targets |
| Primary LLM | Reason from backend-provided artifacts and propose structured outputs | Execute, approve, apply, choose sandbox, choose filesystem targets, manufacture proof |
| Reviewer LLM | Review another model's output, reasoning, and exact proposed diff | Approve execution, bypass backend validation, bypass human decision |
| Backend | Validate, persist, bind checksums, enforce stage order, construct commands, execute in sandbox, apply exact approved diff, roll back, prove | Trust unreviewed model output or frontend-supplied execution details |

## Implementation Reuse Points

- FastAPI backend: `migration_factory/control_tower/adapters/fastapi/app.py`
- Agents: `migration_factory/agents/`
- Stage progression and runner: `migration_factory/control_tower/application/v2_stage_progression.py`, `migration_factory/control_tower/application/v2_orchestrator_runner.py`
- Gates and artifacts: `migration_factory/control_tower/application/v2_gate_action_service.py`, `migration_factory/control_tower/application/v2_phase_gate_service.py`, `migration_factory/control_tower/application/v2_gate_artifact_resolver.py`
- Reviewer: `migration_factory/control_tower/application/v2_reviewer_service.py`
- Model routing: `migration_factory/control_tower/application/v2_model_role_router.py`, `migration_factory/control_tower/application/v2_assistant_model_client.py`
- Repair: `migration_factory/control_tower/application/v2_repair_flow.py`, `migration_factory/control_tower/application/v2_repair_gate_service.py`, `migration_factory/repair_loop/`
- Persistence: `migration_factory/control_tower/infrastructure/sqlite/`
