"use client";

import { useEffect, useRef, useState } from "react";
import type {
  AssistantMessageData,
  AssistantStreamEvent,
  AssistantToolResultData,
} from "../../../lib/contracts";
import { assistantStreamUrl } from "../../../lib/controlTowerApi";

// ── Read-only tool allowlist evidence (V1-16A) ──────────────────────
// These are the ONLY tools the assistant may call. Each is read-only:
// no command execution, no approval, no file writes, no state mutation.

const READ_ONLY_TOOL_ALLOWLIST: { name: string; description: string }[] = [
  { name: "get_job_status", description: "Read migration job status" },
  { name: "get_context_pack", description: "Read context pack manifest" },
  { name: "list_context_packs", description: "List context packs" },
  { name: "get_command_output_window", description: "Read command output" },
  { name: "list_artifacts", description: "List registered artifacts" },
  { name: "list_model_invocations", description: "Read model invocations" },
  { name: "get_pipeline_info", description: "Read pipeline definition" },
  { name: "get_stage_chain", description: "Read stage chain ledger" },
  { name: "list_audit_records", description: "Read audit log" },
  { name: "retrieve_evidence", description: "Bound evidence retrieval" },
];

type Props = {
  jobId: string;
  initialMessages?: AssistantMessageData[];
};

type AssistantMessage = {
  id: string;
  role: "user" | "assistant" | "tool_result";
  content: string;
  toolName?: string;
  timestamp: number;
};

export function AssistantPanel({ jobId, initialMessages = [] }: Props) {
  const [messages, setMessages] = useState<AssistantMessage[]>(
    initialMessages.map((m) => ({
      id: m.message_id,
      role: m.role,
      content: m.content,
      timestamp: Date.now(),
    }))
  );
  const [connectionStatus, setConnectionStatus] = useState<
    "idle" | "connecting" | "connected" | "error"
  >("idle");
  const [error, setError] = useState<string | null>(null);
  const eventSourceRef = useRef<EventSource | null>(null);
  const messagesEndRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    return () => {
      eventSourceRef.current?.close();
    };
  }, []);

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: "smooth" });
  }, [messages]);

  function connect() {
    setError(null);
    setConnectionStatus("connecting");

    const source = new EventSource(assistantStreamUrl(jobId));
    eventSourceRef.current = source;

    source.onopen = () => setConnectionStatus("connected");

    source.onerror = () => {
      setConnectionStatus("error");
      setError("Assistant stream connection lost. Reconnect to retry.");
    };

    source.addEventListener("message", (e: MessageEvent<string>) => {
      try {
        const eventData = JSON.parse(e.data) as AssistantStreamEvent;
        if (eventData.event_type === "message") {
          const data = JSON.parse(eventData.data_json) as AssistantMessageData;
          setMessages((prev) => [
            ...prev,
            {
              id: data.message_id,
              role: data.role,
              content: data.content,
              toolName: data.role === "tool_result" ? data.tool_call_id : undefined,
              timestamp: Date.now(),
            },
          ]);
        } else if (eventData.event_type === "tool_result") {
          const data = JSON.parse(eventData.data_json) as AssistantToolResultData;
          setMessages((prev) => [
            ...prev,
            {
              id: `tool-${data.tool_call_id}`,
              role: "tool_result",
              content: `[Tool: ${data.tool_name}] ${data.result}${data.truncated ? " (truncated)" : ""} (${data.duration_ms}ms)`,
              toolName: data.tool_name,
              timestamp: Date.now(),
            },
          ]);
        }
      } catch {
        // Silently skip unparseable events
      }
    });
  }

  function disconnect() {
    eventSourceRef.current?.close();
    eventSourceRef.current = null;
    setConnectionStatus("idle");
  }

  const isConnected = connectionStatus === "connected";

  return (
    <section className="panel stack" aria-label="Assistant panel">
      <header className="stack">
        <h2>Assistant panel</h2>
        <p className="meta">
          Status:{" "}
          {connectionStatus === "idle"
            ? "Disconnected"
            : connectionStatus === "connecting"
              ? "Connecting..."
              : connectionStatus === "connected"
                ? "Connected"
                : "Error"}
        </p>
        {!isConnected ? (
          <button
            className="button"
            disabled={connectionStatus === "connecting"}
            onClick={connect}
            type="button"
          >
            {connectionStatus === "connecting" ? "Connecting..." : "Connect"}
          </button>
        ) : (
          <button className="button danger" onClick={disconnect} type="button">
            Disconnect
          </button>
        )}
        {error ? <p role="alert">{error}</p> : null}
      </header>

      {/* Guardrail evidence: tool allowlist */}
      <section className="panel compact stack">
        <h3>Read-only tool allowlist</h3>
        <p className="meta">
          The assistant may only invoke the following bounded, read-only tools.
          No command execution, no approvals, no file writes, and no state
          mutation are permitted.
        </p>
        <div className="table-list">
          {READ_ONLY_TOOL_ALLOWLIST.map((tool) => (
            <div className="table-row" key={tool.name}>
              <code>{tool.name}</code>
              <span className="meta">{tool.description}</span>
            </div>
          ))}
        </div>
      </section>

      {/* Redaction evidence */}
      <section className="panel compact">
        <h3>Redaction and guardrails</h3>
        <p className="meta">
          All assistant tool outputs are redacted using the V1-00D redaction
          baseline. Absolute paths, environment variable values, deployment
          identifiers, secret keywords, and raw prompt content are replaced
          with safe placeholders before reaching the browser. The stream is
          read-only: no tool can execute shell commands, write files, approve
          actions, or mutate database state.
        </p>
      </section>

      {/* Streamed messages */}
      <section className="panel stack">
        <h3>Messages</h3>
        {messages.length === 0 ? (
          <p className="meta">
            No assistant messages yet. Click Connect to start the stream.
          </p>
        ) : (
          <div className="event-list">
            {messages.map((msg) => (
              <article
                className={`event-row ${msg.role === "assistant" ? "" : msg.role === "tool_result" ? "tool-result" : "user-message"}`}
                key={msg.id}
              >
                <strong>
                  {msg.role === "assistant"
                    ? "Assistant"
                    : msg.role === "tool_result"
                      ? `Tool: ${msg.toolName ?? "unknown"}`
                      : "User"}
                </strong>
                <pre className="log-window">{msg.content}</pre>
              </article>
            ))}
            <div ref={messagesEndRef} />
          </div>
        )}
      </section>
    </section>
  );
}
