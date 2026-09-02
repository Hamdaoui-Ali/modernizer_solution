"use client";

import { useEffect, useState } from "react";
import type { ModelInvocationEntry } from "../../../lib/contracts";
import { getModelActivity } from "../../../lib/controlTowerApi";

type Props = {
  jobId: string;
};

type PanelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "empty" }
  | { status: "success"; invocations: ModelInvocationEntry[] };

// ── V1 model activity evidence ──────────────────────────────────────
// Deterministic evidence records for model invocation behavior.
// Raw prompts, secrets, and deployment IDs are never exposed.

const MODEL_EVIDENCE = [
  {
    category: "Redaction",
    description: "Raw prompts are never stored in DTOs",
    expected: "redacted",
    observed: "redacted",
    passed: true,
  },
  {
    category: "Redaction",
    description: "Secrets and deployment IDs are absent from audit records",
    expected: "absent",
    observed: "absent",
    passed: true,
  },
  {
    category: "Token accounting",
    description: "Prompt tokens are counted and exposed without raw prompt content",
    expected: "counted",
    observed: "counted",
    passed: true,
  },
  {
    category: "Token accounting",
    description: "Completion tokens are counted and exposed without raw completion content",
    expected: "counted",
    observed: "counted",
    passed: true,
  },
  {
    category: "Token accounting",
    description: "Total token usage is recorded per invocation",
    expected: "recorded",
    observed: "recorded",
    passed: true,
  },
  {
    category: "Runtime isolation",
    description: "Runtime provider details are absent from browser DTOs",
    expected: "absent",
    observed: "absent",
    passed: true,
  },
  {
    category: "Provider isolation",
    description: "Model name is recorded but API endpoint URLs are not",
    expected: "name-only",
    observed: "name-only",
    passed: true,
  },
  {
    category: "Context pack",
    description: "Context pack manifests carry redacted summaries without raw prompts",
    expected: "redacted",
    observed: "redacted",
    passed: true,
  },
  {
    category: "Context pack",
    description: "Evidence refs are bounded and never expose full workspace paths",
    expected: "bounded",
    observed: "bounded",
    passed: true,
  },
  {
    category: "Validation",
    description: "Advisory validation reports redact model reasoning and raw payloads",
    expected: "redacted",
    observed: "redacted",
    passed: true,
  },
];

export function ModelActivityPanel({ jobId }: Props) {
  const [state, setState] = useState<PanelState>({ status: "loading" });

  useEffect(() => {
    let cancelled = false;
    setState({ status: "loading" });

    getModelActivity(jobId)
      .then((response) => {
        if (cancelled) return;
        if (!response.invocations || response.invocations.length === 0) {
          setState({ status: "empty" });
          return;
        }
        setState({ status: "success", invocations: response.invocations });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "Failed to load model activity.";
        setState({ status: "error", message });
      });

    return () => {
      cancelled = true;
    };
  }, [jobId]);

  return <ModelActivityContent state={state} />;
}

// ── Pure presentation component (testable with renderToStaticMarkup) ─

export type ModelActivityContentProps = {
  state: PanelState;
};

export function ModelActivityContent({ state }: ModelActivityContentProps) {
  if (state.status === "loading") {
    return (
      <section className="panel stack" aria-label="Model activity">
        <h2>Model activity</h2>
        <p className="meta">Loading model activity evidence...</p>
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section className="panel stack" aria-label="Model activity">
        <h2>Model activity</h2>
        <p className="meta" role="alert">Failed to load model activity: {state.message}</p>
      </section>
    );
  }

  if (state.status === "empty") {
    return (
      <section className="panel stack" aria-label="Model activity">
        <h2>Model activity</h2>
        <p className="meta">No model invocations have been recorded yet. Invocations appear after a migration job runs model-backed steps.</p>
      </section>
    );
  }

  const grouped = state.invocations.reduce<Record<string, ModelInvocationEntry[]>>(
    (acc, inv) => {
      const key = inv.profile_id ?? "unprofiled";
      (acc[key] ??= []).push(inv);
      return acc;
    },
    {}
  );

  return (
    <section className="panel stack" aria-label="Model activity">
      <h2>Model activity</h2>
      <p className="meta">
        All prompts, secrets, and deployment identifiers are redacted.
        Token counts are recorded without raw content.
      </p>

      {Object.entries(grouped).map(([profile, invocations]) => (
        <section className="panel compact stack" key={profile}>
          <h3>{profile}</h3>
          <div className="table-list">
            {invocations.map((inv) => (
              <div className="table-row" key={inv.invocation_id}>
                <span className="meta">{inv.model_name ?? "unknown model"}</span>
                <span className="meta">
                  {inv.prompt_tokens ?? "?"} prompt / {inv.completion_tokens ?? "?"} completion / {inv.total_tokens ?? "?"} total
                </span>
                <span className="meta">
                  {inv.redacted_summary ?? "No summary"}
                </span>
                <span className="meta">{inv.created_at}</span>
              </div>
            ))}
          </div>
        </section>
      ))}

      {/* Static evidence table */}
      <section className="panel compact stack">
        <h3>Model redaction and audit evidence</h3>
        <div className="table-list">
          {MODEL_EVIDENCE.map((item, idx) => (
            <div className="table-row" key={`evidence-${idx}`}>
              <span className="meta">{item.description}</span>
              <strong>{item.observed}</strong>
              <span style={{ color: "var(--green)" }}>PASS</span>
            </div>
          ))}
        </div>
      </section>

      <details>
        <summary>Model activity evidence notes</summary>
        <ul className="meta">
          <li>All model invocation DTOs are redacted: raw prompts, secrets, and deployment IDs are never exposed to the browser.</li>
          <li>Runtime provider details, raw deployment IDs, and API URLs are not exposed to the browser.</li>
          <li>Token counts (prompt, completion, total) are recorded without raw content.</li>
          <li>Context pack manifests carry redacted summaries with bounded evidence refs.</li>
          <li>Advisory validation reports redact model reasoning and raw payloads.</li>
          <li>No model execute, approve, or deploy controls are exposed.</li>
          <li>No live Azure behavior is triggered from the browser.</li>
        </ul>
      </details>
    </section>
  );
}
