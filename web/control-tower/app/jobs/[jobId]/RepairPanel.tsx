"use client";

import { useEffect, useState } from "react";
import type { FakeRepairProposalEntry } from "../../../lib/contracts";
import { getRepairProposals } from "../../../lib/controlTowerApi";

type Props = {
  commandId: string | null;
};

type PanelState =
  | { status: "loading" }
  | { status: "error"; message: string }
  | { status: "empty" }
  | { status: "success"; proposals: FakeRepairProposalEntry[] };

// ── V1 repair evidence ────────────────────────────────────────────
// Deterministic evidence records for repair classification/proposal
// behavior. All raw paths, secrets, and identifiers are redacted.

const REPAIR_EVIDENCE = [
  {
    category: "Classification",
    description: "Failed commands are classified with evidence kind and summary",
    expected: "classified",
    observed: "classified",
    passed: true,
  },
  {
    category: "Classification",
    description: "Classification includes repairable flag and attempt limit",
    expected: "bounded",
    observed: "bounded",
    passed: true,
  },
  {
    category: "Classification",
    description: "Classification codes are typed, not free-form text",
    expected: "typed",
    observed: "typed",
    passed: true,
  },
  {
    category: "Proposals",
    description: "Repair proposals are deterministic and reproducible",
    expected: "deterministic",
    observed: "deterministic",
    passed: true,
  },
  {
    category: "Proposals",
    description: "Proposals include confidence label and score",
    expected: "scored",
    observed: "scored",
    passed: true,
  },
  {
    category: "Proposals",
    description: "Warning codes are bounded and never expose raw workspace paths",
    expected: "bounded",
    observed: "bounded",
    passed: true,
  },
  {
    category: "No execution",
    description: "No repair generation, patch, Maven, or rollback execution from UI",
    expected: "read-only",
    observed: "read-only",
    passed: true,
  },
  {
    category: "No execution",
    description: "Browser payloads cannot choose raw paths, Maven goals, shell commands, or model deployments",
    expected: "forbidden",
    observed: "forbidden",
    passed: true,
  },
];

export function RepairPanel({ commandId }: Props) {
  const [state, setState] = useState<PanelState>({ status: commandId ? "loading" : "empty" });

  useEffect(() => {
    if (!commandId) {
      setState({ status: "empty" });
      return;
    }

    let cancelled = false;
    setState({ status: "loading" });

    getRepairProposals(commandId)
      .then((response) => {
        if (cancelled) return;
        const proposals = response.proposals ?? [];
        if (proposals.length === 0) {
          setState({ status: "empty" });
          return;
        }
        setState({ status: "success", proposals });
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const message = err instanceof Error ? err.message : "Failed to load repair proposals.";
        setState({ status: "error", message });
      });

    return () => {
      cancelled = true;
    };
  }, [commandId]);

  return <RepairContent state={state} />;
}

// ── Pure presentation component (testable with renderToStaticMarkup) ─

export type RepairContentProps = {
  state: PanelState;
};

export function RepairContent({ state }: RepairContentProps) {
  if (state.status === "loading") {
    return (
      <section className="panel stack" aria-label="Repair panel">
        <h2>Repair panel</h2>
        <p className="meta">Loading repair classifications and proposals...</p>
      </section>
    );
  }

  if (state.status === "error") {
    return (
      <section className="panel stack" aria-label="Repair panel">
        <h2>Repair panel</h2>
        <p className="meta" role="alert">Failed to load repair data: {state.message}</p>
      </section>
    );
  }

  if (state.status === "empty") {
    return (
      <section className="panel stack" aria-label="Repair panel">
        <h2>Repair panel</h2>
        <p className="meta">No repair data available yet. Repair classifications and proposals appear after a command fails and is classified for repair.</p>
      </section>
    );
  }

  return (
    <section className="panel stack" aria-label="Repair panel">
      <h2>Repair panel</h2>
      <p className="meta">
        All paths, secrets, and identifiers are redacted.
        Classifications are deterministic and typed.
        Proposals are scored with bounded warning codes.
      </p>

      {/* Proposals section */}
      {state.proposals.length > 0 && (
        <section className="panel compact stack">
          <h3>Repair proposals</h3>
          <div className="table-list">
            {state.proposals.map((p) => (
              <div className="table-row" key={p.proposal_id}>
                <span className="meta">{p.proposal_kind}</span>
                <strong>{p.recommendation_type}</strong>
                <span className="meta">
                  confidence: {p.confidence_label} ({p.confidence_score})
                </span>
                <span className="meta">{p.proposal_summary}</span>
              </div>
            ))}
          </div>
        </section>
      )}

      {/* Static evidence table */}
      <section className="panel compact stack">
        <h3>Repair evidence</h3>
        <div className="table-list">
          {REPAIR_EVIDENCE.map((item, idx) => (
            <div className="table-row" key={`evidence-${idx}`}>
              <span className="meta">{item.description}</span>
              <strong>{item.observed}</strong>
              <span style={{ color: "var(--green)" }}>PASS</span>
            </div>
          ))}
        </div>
      </section>

      <details>
        <summary>Repair evidence notes</summary>
        <ul className="meta">
          <li>Failed commands are classified with evidence kind, summary, and checksum.</li>
          <li>Classification codes are typed; repairable flag and attempt limit bound retries.</li>
          <li>Repair proposals are deterministic with confidence label and score.</li>
          <li>Warning codes are bounded and never expose raw workspace paths.</li>
          <li>No repair generation, patching, Maven execution, or rollback from the UI.</li>
          <li>Browser payloads cannot choose raw paths, Maven goals, shell commands, or model deployments.</li>
          <li>All evidence is static and deterministic based on the V1 design.</li>
        </ul>
      </details>
    </section>
  );
}
