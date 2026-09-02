"use client";

import { useState, useEffect, useRef, useCallback, useMemo } from "react";
import type { V2AssistantMessageResponse } from "../../../../lib/contracts";

interface OptimisticEntry {
  clientId: string;
  role: "user" | "assistant";
  content: string;
  isThinking?: boolean;
  isError?: boolean;
  errorDetail?: string;
  retryQuestion?: string;
}

function getMessageKey(m: V2AssistantMessageResponse, index: number): string {
  if (m.message_id && m.message_id !== "null") return `srv-${m.message_id}`;
  if (m.correlation_id) return `srv-corr-${m.correlation_id}`;
  return `srv-idx-${index}-${m.role}-${m.content.slice(0, 40)}`;
}

function getModelStatusLabel(
  model: { status: string; source: string; failure_reason?: string } | null,
): string {
  if (!model) return "Model status unavailable";
  if (model.status === "live_ok") return "Live model";
  if (model.source === "deterministic" && model.status !== "live_ok") return "Deterministic fallback";
  if (model.failure_reason) return `${model.status} — ${model.failure_reason}`;
  return model.status;
}

export function FloatingMigrationAssistant({
  assistantModel,
  messages,
  assistantError,
  assistantQuestion,
  assistantBusy,
  approvalReviewOpen,
  onQuestionChange,
  onAsk,
  onRetry,
}: {
  assistantModel: { status: string; source: string; provider: string; role: string; failure_reason?: string } | null;
  messages: V2AssistantMessageResponse[];
  assistantError: string | null;
  assistantQuestion: string;
  assistantBusy: boolean;
  approvalReviewOpen: boolean;
  onQuestionChange: (value: string) => void;
  onAsk: () => void;
  onRetry: (question: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const logRef = useRef<HTMLDivElement>(null);
  const prevMessagesLenRef = useRef(messages.length);
  const [optimisticEntries, setOptimisticEntries] = useState<OptimisticEntry[]>([]);
  const lastSubmittedRef = useRef<string>("");
  const [retryBusy, setRetryBusy] = useState(false);
  const [internalError, setInternalError] = useState<string | null>(null);

  useEffect(() => {
    if (!open) return;
    const timer = setTimeout(() => inputRef.current?.focus(), 120);
    return () => clearTimeout(timer);
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") {
        setOpen(false);
        launcherRef.current?.focus();
      }
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open]);

  useEffect(() => {
    if (assistantBusy) {
      setOptimisticEntries((prev) => {
        const hasThinking = prev.some((e) => e.isThinking);
        if (hasThinking) return prev;
        return [...prev, {
          clientId: crypto.randomUUID(),
          role: "assistant" as const,
          content: "",
          isThinking: true,
        }];
      });
    } else {
      setOptimisticEntries((prev) => prev.filter((e) => !e.isThinking));
    }
  }, [assistantBusy]);

  useEffect(() => {
    if (assistantModel?.failure_reason === "database is locked" && assistantModel?.status === "busy") {
      setInternalError("database is locked");
    } else {
      setInternalError(null);
    }
  }, [assistantModel?.failure_reason, assistantModel?.status]);

  useEffect(() => {
    if (messages.length > prevMessagesLenRef.current) {
      const newCount = messages.length - prevMessagesLenRef.current;
      const newMessages = messages.slice(-newCount);
      const lastUserMsg = lastSubmittedRef.current;

      const hasBusyFallback = newMessages.some(
        (m) => m.role === "assistant"
          && (!m.message_id || m.message_id === "null")
          && m.content === "The orchestrator is busy right now. Retry shortly.",
      );

      if (!hasBusyFallback && lastUserMsg && newMessages.some((m) => m.role === "user" && m.content === lastUserMsg)) {
        setOptimisticEntries([]);
        lastSubmittedRef.current = "";
      }
      prevMessagesLenRef.current = messages.length;
    }
  }, [messages]);

  useEffect(() => {
    const effectiveError = assistantError || internalError;
    if (effectiveError) {
      const question = lastSubmittedRef.current;
      setOptimisticEntries((prev) => {
        const withoutThinking = prev.filter((e) => !e.isThinking);
        const hasError = withoutThinking.some((e) => e.isError);
        if (hasError) return withoutThinking;
        const displayDetail = effectiveError.includes("database is locked")
          ? "The assistant is busy. Retry shortly."
          : effectiveError.includes("fetch")
            ? "Unable to reach the assistant."
            : "The assistant is busy. Retry shortly.";
        return [...withoutThinking, {
          clientId: crypto.randomUUID(),
          role: "assistant" as const,
          content: displayDetail,
          isError: true,
          errorDetail: effectiveError,
          retryQuestion: question || undefined,
        }];
      });
    } else {
      setOptimisticEntries((prev) => prev.filter((e) => !e.isError));
    }
  }, [assistantError, internalError]);

  const handleSend = useCallback(() => {
    const question = assistantQuestion.trim();
    if (!question || assistantBusy || retryBusy) return;
    lastSubmittedRef.current = question;
    const clientId = crypto.randomUUID();
    setOptimisticEntries((prev) => [
      ...prev,
      { clientId, role: "user", content: question },
    ]);
    setOptimisticEntries((prev) => [
      ...prev,
      { clientId: crypto.randomUUID(), role: "assistant", content: "", isThinking: true },
    ]);
    onQuestionChange("");
    onAsk();
  }, [assistantQuestion, assistantBusy, retryBusy, onQuestionChange, onAsk]);

  const handleRetry = useCallback(() => {
    const errorEntry = optimisticEntries.find((e) => e.isError);
    if (!errorEntry?.retryQuestion || retryBusy) return;
    setRetryBusy(true);
    setOptimisticEntries((prev) => prev.filter((e) => !e.isError && !e.isThinking));
    setInternalError(null);
    lastSubmittedRef.current = errorEntry.retryQuestion;
    onQuestionChange(errorEntry.retryQuestion);
    onRetry(errorEntry.retryQuestion);
    setRetryBusy(false);
  }, [optimisticEntries, retryBusy, onQuestionChange, onRetry]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      handleSend();
    }
  }, [handleSend]);

  useEffect(() => {
    if (logRef.current) {
      logRef.current.scrollTop = logRef.current.scrollHeight;
    }
  }, [messages, optimisticEntries]);

  const toggleOpen = useCallback(() => setOpen((prev) => !prev), []);
  const close = useCallback(() => { setOpen(false); launcherRef.current?.focus(); }, []);

  const modelLabel = getModelStatusLabel(assistantModel);

  const filteredMessages = useMemo(() => {
    const result: V2AssistantMessageResponse[] = [];
    for (const m of messages) {
      const isBusyAssistant = m.role === "assistant"
        && (!m.message_id || m.message_id === "null")
        && m.content === "The orchestrator is busy right now. Retry shortly.";
      if (isBusyAssistant) {
        if (result.length > 0) {
          const last = result[result.length - 1];
          if (last.role === "user" && (!last.message_id || last.message_id === "null")) {
            result.pop();
          }
        }
        continue;
      }
      result.push(m);
    }
    return result;
  }, [messages]);

  const hasConversation = filteredMessages.length > 0 || optimisticEntries.length > 0;

  const allEntries: Array<{ key: string; optimistic?: OptimisticEntry } & (
    | { source: "server"; message: V2AssistantMessageResponse }
    | { source: "optimistic"; entry: OptimisticEntry }
  )> = [];

  {
    let serverIdx = 0;
    filteredMessages.forEach((m) => {
      allEntries.push({
        key: getMessageKey(m, serverIdx),
        source: "server" as const,
        message: m,
      });
      serverIdx++;
    });
    optimisticEntries.forEach((e) => {
      allEntries.push({
        key: `opt-${e.clientId}`,
        source: "optimistic" as const,
        entry: e,
      });
    });
  }

  return (
    <>
      <button
        ref={launcherRef}
        type="button"
        className="assistant-launcher"
        onClick={toggleOpen}
        aria-label="Open migration assistant"
        aria-expanded={open}
        style={{
          position: "fixed", right: 22, bottom: 22, zIndex: 100,
          width: 58, height: 58, display: "grid", placeItems: "center",
          color: "#173770", border: "1px solid rgba(255,255,255,.72)",
          borderRadius: 20, cursor: "pointer",
          background: "linear-gradient(145deg, rgba(255,255,255,.78), rgba(224,236,255,.56))",
          boxShadow: "inset 0 1px 0 rgba(255,255,255,.9), 0 14px 40px rgba(43,86,164,.24)",
          backdropFilter: "blur(20px) saturate(1.45)",
          transition: "transform .18s ease, box-shadow .18s ease",
        }}
      >
        <svg width="23" height="23" viewBox="0 0 24 24" fill="none">
          <path d="M7 17.5 4.5 20v-4.2A7.5 7.5 0 1 1 19.5 12 7.5 7.5 0 0 1 7 17.5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/>
          <path d="M9 10.5h6M9 13.5h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
        </svg>
        {filteredMessages.length > 0 && (
          <span style={{
            position: "absolute", top: -5, right: -5,
            minWidth: 19, height: 19, display: "grid", placeItems: "center",
            padding: "0 5px", color: "white", border: "2px solid white",
            borderRadius: 999, background: "#3157d5",
            fontSize: 8, fontWeight: 820,
          }}>
            {filteredMessages.length}
          </span>
        )}
      </button>

      <aside
        className="assistant-popover"
        aria-label="Migration assistant"
        role="dialog"
        aria-modal={open}
        style={{
          position: "fixed", right: 22, bottom: 92, zIndex: 101,
          width: "min(410px, calc(100vw - 28px))",
          height: "min(590px, calc(100vh - 126px))",
          display: "grid", gridTemplateRows: "auto minmax(0, 1fr) auto",
          overflow: "hidden",
          border: "1px solid rgba(255,255,255,.74)",
          borderRadius: 22, cursor: "default",
          background: "linear-gradient(160deg, rgba(255,255,255,.88), rgba(244,248,255,.78))",
          boxShadow: "inset 0 1px 0 rgba(255,255,255,.96), 0 28px 80px rgba(23,45,84,.24)",
          backdropFilter: "blur(26px) saturate(1.4)",
          opacity: open ? 1 : 0,
          pointerEvents: open ? "auto" as const : "none" as const,
          transform: open ? "translateY(0) scale(1)" : "translateY(14px) scale(.96)",
          transformOrigin: "bottom right",
          transition: "opacity .18s ease, transform .18s ease",
        }}
      >
        <div style={{
          minHeight: 62, display: "flex", alignItems: "center",
          justifyContent: "space-between", gap: 12,
          padding: "11px 12px 10px 14px",
          borderBottom: "1px solid rgba(199,208,219,.74)",
          background: "rgba(255,255,255,.44)",
        }}>
          <div style={{ display: "flex", alignItems: "center", gap: 9, minWidth: 0 }}>
            <span aria-hidden="true" style={{
              width: 34, height: 34, flex: "none", display: "grid", placeItems: "center",
              color: "#3157d5", border: "1px solid rgba(255,255,255,.7)",
              borderRadius: 12, background: "rgba(237,244,255,.8)",
              boxShadow: "inset 0 1px 0 rgba(255,255,255,.95)",
            }}>
              <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
                <path d="M7 17.5 4.5 20v-4.2A7.5 7.5 0 1 1 19.5 12 7.5 7.5 0 0 1 7 17.5Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/>
                <path d="M9 10.5h6M9 13.5h4" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
              </svg>
            </span>
            <div>
              <strong style={{ display: "block", color: "#0b0e14", fontSize: 11 }}>Migration assistant</strong>
              <span style={{ display: "block", marginTop: 2, color: "#667085", fontSize: 8.8 }}>
                {modelLabel}
                {assistantBusy && " · Thinking"}
              </span>
            </div>
          </div>
          <button
            type="button"
            onClick={close}
            aria-label="Close assistant"
            style={{
              width: 24, height: 24, flex: "none", display: "grid", placeItems: "center",
              color: "#667085", border: 0, borderRadius: 7, background: "transparent",
              cursor: "pointer",
            }}
          >
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
              <path d="M6 6l12 12M18 6 6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
            </svg>
          </button>
        </div>

        <div
          ref={logRef}
          role="log"
          aria-live="polite"
          aria-relevant="additions text"
          style={{
            minHeight: 0, overflow: "auto", display: "grid", alignContent: "start",
            gap: 10, padding: 13,
            background: "linear-gradient(180deg, rgba(255,255,255,.46), transparent 28%)",
          }}
        >
          {!hasConversation && !assistantError ? (
            <div style={{
              minHeight: 200, display: "grid", placeItems: "center", padding: 18,
              textAlign: "center",
              border: "1px dashed rgba(169,182,199,.7)",
              borderRadius: 16, background: "rgba(255,255,255,.36)",
            }}>
              <div>
                <div aria-hidden="true" style={{
                  width: 42, height: 42, display: "grid", placeItems: "center",
                  margin: "0 auto 9px", color: "#3157d5",
                  borderRadius: 15, background: "rgba(237,244,255,.82)",
                  boxShadow: "inset 0 1px 0 rgba(255,255,255,.95)",
                }}>
                  <svg width="18" height="18" viewBox="0 0 24 24" fill="none">
                    <path d="M12 17v.01M8.2 9a4 4 0 1 1 7.6 2c-.8 1-1.8 1.6-2.4 2.5-.35.5-.4.9-.4 1.5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round"/>
                  </svg>
                </div>
                <strong style={{ display: "block", color: "#0b0e14", fontSize: 11.2 }}>Ask what is happening</strong>
                <p style={{ margin: "5px 0 0", color: "#667085", fontSize: 9.5, lineHeight: 1.5 }}>
                  Ask about the current stage, evidence, approvals, gates, or why the pipeline is waiting.
                </p>
              </div>
            </div>
          ) : (
            allEntries.map((entry) => {
              if (entry.source === "server") {
                const m = entry.message;
                return (
                  <div key={entry.key}
                    style={{
                      maxWidth: 330,
                      display: "flex", alignItems: "flex-start", gap: 8,
                      flexDirection: m.role === "user" ? "row-reverse" : "row",
                      justifySelf: m.role === "user" ? "end" : "start",
                    }}
                  >
                    <span aria-hidden="true" style={{
                      width: 25, height: 25, flex: "none", display: "grid", placeItems: "center",
                      color: m.role === "user" ? "#475467" : "#3157d5",
                      border: "1px solid rgba(255,255,255,.7)",
                      borderRadius: "50%",
                      background: m.role === "user" ? "rgba(240,243,247,.88)" : "rgba(237,244,255,.85)",
                      fontSize: 10,
                    }}>
                      {m.role === "user" ? "U" : "A"}
                    </span>
                    <div style={{
                      padding: "9px 10px",
                      border: m.role === "assistant" ? "1px solid rgba(173,201,247,.75)" : "1px solid rgba(199,208,219,.7)",
                      borderRadius: 13,
                      background: m.role === "assistant" ? "rgba(244,249,255,.78)" : "rgba(247,248,250,.78)",
                      boxShadow: "0 4px 14px rgba(16,24,40,.04)",
                    }}>
                      <div style={{
                        marginBottom: 4, color: "#667085",
                        fontSize: 8.2, fontWeight: 760,
                      }}>
                        {m.role === "user" ? "You" : "Assistant"}
                      </div>
                      <div style={{
                        color: "#172033", fontSize: 9.8, lineHeight: 1.5,
                        whiteSpace: "pre-wrap",
                      }}>
                        {m.content}
                      </div>
                    </div>
                  </div>
                );
              }

              const opt = entry.entry;
              if (opt.isThinking) {
                return (
                  <div key={entry.key} role="status" aria-label="Assistant is thinking"
                    style={{
                      maxWidth: 330, display: "flex", alignItems: "flex-start", gap: 8,
                      justifySelf: "start",
                    }}
                  >
                    <span aria-hidden="true" style={{
                      width: 25, height: 25, flex: "none", display: "grid", placeItems: "center",
                      color: "#3157d5",
                      border: "1px solid rgba(255,255,255,.7)",
                      borderRadius: "50%",
                      background: "rgba(237,244,255,.85)",
                      fontSize: 10,
                    }}>A</span>
                    <div style={{
                      padding: "9px 10px",
                      border: "1px solid rgba(173,201,247,.75)",
                      borderRadius: 13,
                      background: "rgba(244,249,255,.78)",
                      boxShadow: "0 4px 14px rgba(16,24,40,.04)",
                    }}>
                      <div style={{
                        marginBottom: 4, color: "#667085",
                        fontSize: 8.2, fontWeight: 760,
                      }}>Assistant</div>
                      <div style={{
                        color: "#667085", fontSize: 9.8, fontStyle: "italic",
                      }}>
                        <span className="thinking-dots">Thinking</span>
                      </div>
                    </div>
                  </div>
                );
              }

              if (opt.isError) {
                return (
                  <div key={entry.key} role="alert"
                    style={{
                      maxWidth: 330, display: "flex", alignItems: "flex-start", gap: 8,
                      justifySelf: "start",
                    }}
                  >
                    <span aria-hidden="true" style={{
                      width: 25, height: 25, flex: "none", display: "grid", placeItems: "center",
                      color: "#956100",
                      border: "1px solid rgba(255,255,255,.7)",
                      borderRadius: "50%",
                      background: "#fff6e3",
                      fontSize: 10,
                    }}>A</span>
                    <div style={{
                      padding: "9px 10px",
                      border: "1px solid #f1daa0",
                      borderRadius: 13,
                      background: "#fff6e3",
                      boxShadow: "0 4px 14px rgba(16,24,40,.04)",
                    }}>
                      <div style={{
                        marginBottom: 4, color: "#956100",
                        fontSize: 8.2, fontWeight: 760,
                      }}>Assistant busy</div>
                      <div style={{
                        color: "#956100", fontSize: 9.8, lineHeight: 1.5,
                        whiteSpace: "pre-wrap",
                      }}>
                        {opt.content}
                      </div>
                      {opt.retryQuestion && (
                        <button
                          type="button"
                          onClick={handleRetry}
                          disabled={retryBusy}
                          style={{
                            marginTop: 7, padding: "4px 10px",
                            color: "#3157d5", border: "1px solid #cfe0ff",
                            borderRadius: 7, background: "#edf4ff",
                            fontSize: 9, fontWeight: 720, cursor: "pointer",
                          }}
                        >
                          {retryBusy ? "Retrying..." : "Retry"}
                        </button>
                      )}
                    </div>
                  </div>
                );
              }

              return (
                <div key={entry.key}
                  style={{
                    maxWidth: 330,
                    display: "flex", alignItems: "flex-start", gap: 8,
                    flexDirection: opt.role === "user" ? "row-reverse" : "row",
                    justifySelf: opt.role === "user" ? "end" : "start",
                  }}
                >
                  <span aria-hidden="true" style={{
                    width: 25, height: 25, flex: "none", display: "grid", placeItems: "center",
                    color: opt.role === "user" ? "#475467" : "#3157d5",
                    border: "1px solid rgba(255,255,255,.7)",
                    borderRadius: "50%",
                    background: opt.role === "user" ? "rgba(240,243,247,.88)" : "rgba(237,244,255,.85)",
                    fontSize: 10,
                  }}>
                    {opt.role === "user" ? "U" : "A"}
                  </span>
                  <div style={{
                    padding: "9px 10px",
                    border: opt.role === "assistant" ? "1px solid rgba(173,201,247,.75)" : "1px solid rgba(199,208,219,.7)",
                    borderRadius: 13,
                    background: opt.role === "assistant" ? "rgba(244,249,255,.78)" : "rgba(247,248,250,.78)",
                    boxShadow: "0 4px 14px rgba(16,24,40,.04)",
                  }}>
                    <div style={{
                      marginBottom: 4, color: "#667085",
                      fontSize: 8.2, fontWeight: 760,
                    }}>
                      {opt.role === "user" ? "You" : "Assistant"}
                    </div>
                    <div style={{
                      color: "#172033", fontSize: 9.8, lineHeight: 1.5,
                      whiteSpace: "pre-wrap",
                    }}>
                      {opt.content}
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>

        <div style={{
          padding: "10px 12px 11px",
          borderTop: "1px solid rgba(199,208,219,.74)",
          background: "rgba(255,255,255,.44)",
        }}>
          <div style={{
            display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto",
            gap: 8, alignItems: "end",
            padding: "6px 6px 6px 10px",
            border: "1px solid rgba(184,196,211,.75)",
            borderRadius: 15,
            background: "rgba(255,255,255,.62)",
            boxShadow: "inset 0 1px 0 rgba(255,255,255,.85)",
          }}>
            <textarea
              ref={inputRef}
              value={assistantQuestion}
              onChange={(e) => onQuestionChange(e.target.value)}
              onKeyDown={handleKeyDown}
              placeholder="Ask about this migration"
              disabled={assistantBusy && !assistantError}
              rows={1}
              aria-label="Ask assistant"
              style={{
                width: "100%", minHeight: 34, maxHeight: 92,
                padding: "7px 0", resize: "none", color: "#172033",
                border: 0, outline: "none", background: "transparent",
                fontFamily: "inherit", fontSize: 9.9, lineHeight: 1.4,
              }}
            />
            <button
              type="button"
              onClick={handleSend}
              disabled={assistantBusy || retryBusy || !assistantQuestion.trim()}
              aria-label="Send message"
              style={{
                width: 34, height: 34, display: "grid", placeItems: "center",
                color: "white", border: 0, borderRadius: 11,
                background: "#3157d5", cursor: "pointer",
                boxShadow: "0 6px 14px rgba(49,87,213,.22)",
                opacity: assistantBusy || retryBusy || !assistantQuestion.trim() ? 0.5 : 1,
              }}
            >
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                <path d="m4 4 16 8-16 8 3-8-3-8Z" stroke="currentColor" strokeWidth="1.8" strokeLinejoin="round"/>
                <path d="M7 12h13" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
              </svg>
            </button>
          </div>
          <div style={{ marginTop: 7, color: "#667085", fontSize: 8.4, lineHeight: 1.4 }}>
            {approvalReviewOpen && (
              <p style={{ margin: 0 }}>Pre-transform review is open. Use the assistant to review evidence, request changes, and confirm checksums.</p>
            )}
            <p style={{ margin: "4px 0 0" }}>Cannot execute, approve, write files, change the route, or override proof.</p>
          </div>
        </div>
      </aside>
    </>
  );
}
