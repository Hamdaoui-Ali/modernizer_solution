# DEMO3 Risks

| Risk | Impact | Mitigation | Owner story |
|---|---|---|---|
| Reviewer LLM treated as optional | Supported model-required artifacts become unreviewed and unsafe to pass forward. | Fail closed unless reviewer validates the exact artifact checksum. | F2, F5 |
| Unreviewed primary output passed forward | Next agent consumes draft reasoning rather than a reviewed final artifact. | Make final reviewed Markdown the only forward contract. | F2 |
| Target profile overshoot | User requests one target and the pipeline migrates beyond it. | Persist `target_profile`, validate route, stop when target is reached, test resume-after-target. | F3 |
| Current app state misdetected | The pipeline starts from the wrong source profile and may run obsolete or invalid stages. | Emit source-profile evidence, confidence, uncertainty, and require validated override for correction. | F4 |
| Skipped stages not auditable | Already-modernized apps skip work without a durable explanation. | Record skipped-stage ledger entries with source/target profile, evidence refs, reasons, and checksums. | F4 |
| Stale diff applied | Patch applies to a repository state that no longer matches the reviewed proposal. | Bind approval to exact proposal checksum, reviewer checksum, repo state checksum, and artifact/checkpoint checksums. | F5 |
| User comments ignored in repeated review | Repair Agent repeats the same flawed proposal or misses operator intent. | Include comments, previous diff, prior reasoning, reviewer notes, current repo state, and checksums in the next repair context. | F5 |
| Copilot/TUI path remains reachable | Product control surface remains ambiguous and non-auditable. | F0 inventory, quarantine/removal decisions, and cleanup report. | F0 |
| Provider/model runtime leaks | Frontend/API becomes a runtime-control surface. | Public contracts expose IDs, statuses, decisions, profiles, artifact refs, and checksums only. | F0, F2 |
| Vendor recipe not backend-allowlisted | LLM indirectly chooses executable repair behavior. | Only backend-allowlisted repair modes can execute after review, approval, and checksum validation. | F5 |
| F5 overfits Jackson instead of generic build/test repair | Repair becomes a special-case Stage 4 feature instead of a reusable Build/Test Repair Agent. | Keep Stage 4/Jackson as one proof scenario under generic F5 flow. | F5 |
| Checkpoint resume accepts stale artifact | User decision applies to superseded Analysis or Planning output. | Bind decisions to artifact revision IDs and checksums; reject stale/foreign/incompatible checkpoints. | F1 |
| Source/target validation duplicates planning logic | Divergent routing behavior appears across services. | Reuse V2 stage progression, run configuration, pipeline definitions, and profile reader concepts. | F3, F4 |
| Cleanup removes compatibility code without decision | Historical support breaks unexpectedly. | Separate removed, quarantined, retained, and follow-up items in F0 cleanup report. | F0 |
| Proof relies on LLM assertion | Repair is marked successful without deterministic validation. | Build/test rerun proof and backend ledger control success. | F5 |
