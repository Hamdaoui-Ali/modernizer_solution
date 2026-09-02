"use client";

import { useEffect, useRef, useState } from "react";
import type {
  ArtifactMetadata,
  CommandOutputWindow,
  CommandRepresentation,
  JobRepresentation,
  PublicRunEvent
} from "../../../lib/contracts";
import {
  CONTROL_TOWER_API_BASE_URL,
  allowedStatusCopy,
  eventStreamUrl,
  getArtifacts,
  getCommandOutput,
  getCommands
} from "../../../lib/controlTowerApi";
import { ApprovalPanel } from "./ApprovalPanel";
import { ProofReportPanel } from "./ProofReportPanel";
import { RepairPanel } from "./RepairPanel";
import {
  applyPublicEvent,
  jobStatusCopy,
  latestAppliedSequence,
  shouldRefetchJobProjection
} from "../../../lib/eventReplay";

type Props = {
  initialEvents: PublicRunEvent[];
  initialJob: JobRepresentation;
};

type LogState = {
  stdout: CommandOutputWindow | null;
  stderr: CommandOutputWindow | null;
};

const PUBLIC_EVENT_TYPES = [
  "job_created",
  "command_queued",
  "command_starting",
  "command_running",
  "job_state_changed",
  "command_finalized",
  "artifact_registered"
];

const ACTIVE_COMMAND_STATES = new Set(["QUEUED", "STARTING", "RUNNING", "CANCELLING"]);

export function CurrentRunClient({ initialEvents, initialJob }: Props) {
  const jobId = initialJob.job.job_id;
  const [job, setJob] = useState(initialJob);
  const [commands, setCommands] = useState<CommandRepresentation[]>(
    initialJob.active_command ? [initialJob.active_command] : []
  );
  const [events, setEvents] = useState(initialEvents);
  const [artifacts, setArtifacts] = useState<ArtifactMetadata[]>([]);
  const [logs, setLogs] = useState<LogState>({ stdout: null, stderr: null });
  const [connectionStatus, setConnectionStatus] = useState("Connecting");
  const [error, setError] = useState<string | null>(null);
  const [pendingAction, setPendingAction] = useState<"start" | "cancel" | null>(null);
  const lastAppliedSequenceRef = useRef(latestAppliedSequence(initialEvents));

  const activeCommand = job.active_command;
  const canStart = job.job.state === "CREATED";
  const canCancel = activeCommand ? ACTIVE_COMMAND_STATES.has(activeCommand.status) : false;

  async function refetchJobProjection() {
    const response = await fetch(
      `${CONTROL_TOWER_API_BASE_URL}/v1/jobs/${encodeURIComponent(jobId)}`,
      { cache: "no-store" }
    );
    if (!response.ok) {
      throw new Error("Could not refresh foundation diagnostic job.");
    }
    const etag = response.headers.get("etag") ?? job.etag;
    const next = { ...((await response.json()) as JobRepresentation), etag };
    setJob(next);
    return next;
  }

  async function refreshDetails(currentJob = job) {
    setError(null);
    const [commandResponse, artifactResponse] = await Promise.all([
      getCommands(jobId),
      getArtifacts(jobId)
    ]);
    setCommands(commandResponse.commands);
    setArtifacts(artifactResponse.artifacts);

    const command = currentJob.active_command ?? commandResponse.commands.at(-1);
    if (!command) {
      setLogs({ stdout: null, stderr: null });
      return;
    }
    const [stdout, stderr] = await Promise.all([
      getCommandOutput(jobId, command.command_id, "stdout", logs.stdout?.next_offset ?? 0),
      getCommandOutput(jobId, command.command_id, "stderr", logs.stderr?.next_offset ?? 0)
    ]);
    setLogs((current) => ({
      stdout: mergeLogWindow(current.stdout, stdout),
      stderr: mergeLogWindow(current.stderr, stderr)
    }));
  }

  async function mutateJob(action: "start" | "cancel") {
    setPendingAction(action);
    setError(null);
    try {
      const response = await fetch(
        `${CONTROL_TOWER_API_BASE_URL}/v1/jobs/${encodeURIComponent(jobId)}/${action}`,
        {
          body: JSON.stringify({}),
          headers: {
            "Content-Type": "application/json",
            "Idempotency-Key": crypto.randomUUID(),
            "If-Match": job.etag
          },
          method: "POST"
        }
      );
      if (!response.ok) {
        throw new Error(action === "start" ? "Could not queue diagnostic command." : "Could not cancel diagnostic run.");
      }
      const etag = response.headers.get("etag") ?? job.etag;
      const next = { ...((await response.json()) as JobRepresentation), etag };
      setJob(next);
      await refreshDetails(next);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Control Tower request failed.");
    } finally {
      setPendingAction(null);
    }
  }

  useEffect(() => {
    void refreshDetails().catch((exc) => {
      setError(exc instanceof Error ? exc.message : "Could not load diagnostic details.");
    });
  }, [jobId]);

  useEffect(() => {
    const source = new EventSource(eventStreamUrl(jobId, lastAppliedSequenceRef.current));

    function applyMessage(message: MessageEvent<string>) {
      const event = JSON.parse(message.data) as PublicRunEvent;
      setEvents((currentEvents) => {
        const currentLastApplied = lastAppliedSequenceRef.current;
        if (event.sequence <= currentLastApplied) {
          return currentEvents;
        }
        const next = applyPublicEvent(
          {
            events: currentEvents,
            lastAppliedSequence: currentLastApplied
          },
          event
        );
        lastAppliedSequenceRef.current = next.lastAppliedSequence;
        return next.events;
      });
      if (shouldRefetchJobProjection(event)) {
        void refetchJobProjection()
          .then((next) => refreshDetails(next))
          .catch((exc) => {
            setError(exc instanceof Error ? exc.message : "Could not refresh diagnostic projection.");
          });
      }
    }

    source.onopen = () => setConnectionStatus(allowedStatusCopy.connected);
    source.onerror = () => setConnectionStatus("Reconnecting");
    for (const eventType of PUBLIC_EVENT_TYPES) {
      source.addEventListener(eventType, applyMessage);
    }

    return () => source.close();
  }, [jobId]);

  return (
    <section className="stack">
      <header className="panel stack">
        <div>
          <p className="eyebrow">Foundation diagnostic</p>
          <h1>{jobStatusCopy(job.job)}</h1>
          <p className="meta">{connectionStatus}</p>
        </div>
        {error ? <p role="alert">{error}</p> : null}
        <div className="actions">
          <button className="button" disabled={!canStart || pendingAction !== null} onClick={() => mutateJob("start")} type="button">
            {pendingAction === "start" ? "Starting..." : "Start"}
          </button>
          <button className="button danger" disabled={!canCancel || pendingAction !== null} onClick={() => mutateJob("cancel")} type="button">
            {pendingAction === "cancel" ? "Cancelling..." : "Cancel"}
          </button>
        </div>
      </header>

      <section className="grid">
        <StatusCard label="Job state" value={job.job.state} />
        <StatusCard label="Version" value={String(job.job.version)} />
        <StatusCard label="ETag" value={job.etag} />
        <StatusCard label="Active command" value={activeCommand?.status ?? "None"} />
      </section>

      <section className="panel stack">
        <h2>Command state</h2>
        {commands.length ? (
          <div className="table-list">
            {commands.map((command) => (
              <div className="table-row" key={command.command_id}>
                <span>{command.command_id}</span>
                <strong>{command.status}</strong>
                <span>{command.operation}</span>
                <span>{command.command_manifest_artifact_id ?? "No manifest artifact yet"}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="meta">No command has been queued yet.</p>
        )}
      </section>

      <section className="log-grid">
        <LogViewer title="Stdout" window={logs.stdout} />
        <LogViewer title="Stderr" window={logs.stderr} />
      </section>

      <section className="panel stack">
        <h2>Artifacts</h2>
        {artifacts.length ? (
          <div className="table-list">
            {artifacts.map((artifact) => (
              <div className="table-row" key={artifact.artifact_id}>
                <span>{artifact.artifact_type}</span>
                <strong>{artifact.artifact_id}</strong>
                <span>{artifact.size_bytes} bytes</span>
                <span>{artifact.normalized_relative_path}</span>
              </div>
            ))}
          </div>
        ) : (
          <p className="meta">No terminal artifacts have been registered yet.</p>
        )}
      </section>

      <section className="panel stack" aria-label="Committed public events">
        <h2>Public event timeline</h2>
        <div className="event-list">
          {events.map((event) => (
            <article className="event-row" key={`${event.job_id}-${event.sequence}`}>
              <span className="event-sequence">#{event.sequence}</span>
              <strong>{event.event_type === "command_queued" ? allowedStatusCopy.diagnosticQueued : event.event_type}</strong>
              <span className="meta">{event.created_at}</span>
            </article>
          ))}
        </div>
      </section>

      <ProofReportPanel jobId={jobId} />

      <RepairPanel commandId={activeCommand?.command_id ?? null} />

      <ApprovalPanel jobId={jobId} />
    </section>
  );
}

function StatusCard({ label, value }: { label: string; value: string }) {
  return (
    <div className="panel compact">
      <dt className="meta">{label}</dt>
      <dd>{value}</dd>
    </div>
  );
}

function LogViewer({ title, window }: { title: string; window: CommandOutputWindow | null }) {
  return (
    <section className="panel stack">
      <div>
        <h2>{title}</h2>
        <p className="meta">
          {window ? `${window.start_offset} -> ${window.next_offset} bytes` : "No bytes loaded"}
        </p>
      </div>
      <pre className="log-window">{window?.data || ""}</pre>
      <p className="meta">
        {window
          ? `terminal=${String(window.terminal)} truncated=${String(window.truncated)} replacement_characters=${window.replacement_characters_used}`
          : "Waiting for backend-owned diagnostic output."}
      </p>
    </section>
  );
}

function mergeLogWindow(current: CommandOutputWindow | null, next: CommandOutputWindow): CommandOutputWindow {
  if (!current || next.start_offset === 0) {
    return next;
  }
  if (!next.data) {
    return { ...current, terminal: next.terminal, truncated: current.truncated || next.truncated };
  }
  return {
    ...next,
    data: `${current.data}${next.data}`,
    start_offset: current.start_offset,
    replacement_characters_used: current.replacement_characters_used + next.replacement_characters_used,
    truncated: current.truncated || next.truncated
  };
}
