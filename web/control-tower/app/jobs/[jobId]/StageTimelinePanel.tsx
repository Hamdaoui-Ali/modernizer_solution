"use client";

import { useEffect, useState } from "react";
import type { StageChainEntry } from "../../../lib/contracts";
import { getStageChain } from "../../../lib/controlTowerApi";

type Props = {
  jobId: string;
};

type PanelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "empty" }
  | { status: "success"; stages: StageChainEntry[] };

// ── V1 stage timeline evidence ──────────────────────────────────────
// Deterministic evidence records for the V1 stage pipeline:
//   Stage 1: Java 11 / Spring Boot 2.7.18 from legacy source
//   Stage 2: Java 17 / Spring Boot 3.5.6 from Stage 1 sandbox
//   Stage 3: Java 21 / Spring Boot 3.5.6 from Stage 2 sandbox

const STAGE_EVIDENCE: Record<number, { jdk: string; boot: string; description: string }> = {
  1: { jdk: "Java 11 (java11)", boot: "Spring Boot 2.7.18", description: "Legacy source → Stage 1" },
  2: { jdk: "Java 17 (java17)", boot: "Spring Boot 3.5.6", description: "Stage 1 sandbox → Stage 2" },
  3: { jdk: "Java 21 (java21)", boot: "Spring Boot 3.5.6", description: "Stage 2 sandbox → Stage 3" },
};

export function StageTimelinePanel({ jobId }: Props) {
  const [state, setState] = useState<PanelState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });

    getStageChain(jobId)
      .then((response) => {
        if (cancelled) return;
        if (!response.stages || response.stages.length === 0) {
          setState({ status: "empty" });
          return;
        }
        setState({ status: "success", stages: response.stages });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "Failed to load stage timeline.";
        setState({ status: "error", message });
      });

    return () => {
      cancelled = true;
    };
  }, [jobId]);

  return <StageTimelineContent state={state} />;
}

// ── Pure presentation component (testable with renderToStaticMarkup) ─

export type StageTimelineContentProps = {
  state: PanelState;
};

export function StageTimelineContent({ state }: StageTimelineContentProps) {
  if (state.status === "loading") {
    return (
      <section className="panel stack" aria-label="Stage timeline">
        <h2>Stage timeline</h2>
        <p className="meta">Loading stage chain evidence...</p>
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section className="panel stack" aria-label="Stage timeline">
        <h2>Stage timeline</h2>
        <p className="meta" role="alert">Failed to load stage timeline: {state.message}</p>
      </section>
    );
  }

  if (state.status === "empty") {
    return (
      <section className="panel stack" aria-label="Stage timeline">
        <h2>Stage timeline</h2>
        <p className="meta">No stage chain entries have been registered yet. Stages appear after a migration job is created.</p>
      </section>
    );
  }

  return (
    <section className="panel stack" aria-label="Stage timeline">
      <h2>Stage timeline</h2>
      <p className="meta">
        V1 pipeline: <code>springboot-216-to-356-java21-three-stage</code>
      </p>
      <p className="meta">
        Boot 4 is not selectable. <code>3.5.14</code> is not execution-relevant in V1.
        All paths, secrets, and identifiers are redacted.
      </p>

      <div className="table-list">
        {state.stages.map((stage) => {
          const evidence = STAGE_EVIDENCE[stage.stage_index];
          return (
            <div className="table-row" key={stage.ledger_id}>
              <span className="meta">
                Stage {stage.stage_index} — {evidence?.description ?? stage.input_source_kind}
              </span>
              <strong>{stage.chain_status}</strong>
              {evidence ? (
                <span className="meta">
                  {evidence.boot} / {evidence.jdk}
                </span>
              ) : null}
              <span className="meta">
                input: {stage.input_source_kind === "legacy_source" ? "legacy source" : "previous stage sandbox"}
              </span>
              <span className="meta">
                {stage.output_artifact_id ? "Artifact registered" : "No output yet"}
              </span>
            </div>
          );
        })}
      </div>

      <details>
        <summary>Stage pipeline evidence notes</summary>
        <ul className="meta">
          <li>Pipeline locked to <code>springboot-216-to-356-java21-three-stage</code>.</li>
          <li>Stage 1 runs Java 11 (java11) with Spring Boot 2.7.18 from legacy source.</li>
          <li>Stage 2 runs Java 17 (java17) with Spring Boot 3.5.6 from Stage 1 sandbox.</li>
          <li>Stage 3 runs Java 21 (java21) with Spring Boot 3.5.6 from Stage 2 sandbox.</li>
          <li>Boot 4 is not available.</li>
          <li><code>3.5.14</code> is not execution-relevant for V1.</li>
          <li>Stage ordering is enforced by the backend; browser cannot reorder stages.</li>
          <li>All stage chain evidence is read-only. No approve, reject, or execute controls.</li>
        </ul>
      </details>
    </section>
  );
}
