"use client";

import { useEffect, useRef, useCallback } from "react";
import type { V2JobEvent } from "../../../../lib/contracts";
import styles from "../MigrationCockpit.module.css";

export function EvidenceDrawer({
  open,
  evidence,
  rawLogs,
  streamState,
  activeStageIndex,
  onClose,
}: {
  open: boolean;
  evidence: V2JobEvent[];
  rawLogs: V2JobEvent[];
  streamState?: string;
  activeStageIndex?: number;
  onClose: () => void;
}) {
  const logRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (open) {
      const timer = setTimeout(() => closeRef.current?.focus(), 80);
      return () => clearTimeout(timer);
    }
  }, [open]);

  useEffect(() => {
    if (!open) return;
    function handleKey(e: KeyboardEvent) {
      if (e.key === "Escape") onClose();
    }
    document.addEventListener("keydown", handleKey);
    return () => document.removeEventListener("keydown", handleKey);
  }, [open, onClose]);

  const scrollToLatest = useCallback(() => {
    logRef.current?.scrollTo({ top: logRef.current.scrollHeight, behavior: "smooth" });
  }, []);

  useEffect(() => {
    if (open) scrollToLatest();
  }, [evidence.length, open, scrollToLatest]);

  function formatTime(ts: string): string {
    try {
      return new Date(ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit", second: "2-digit" });
    } catch { return ""; }
  }

  function renderLogEvent(event: V2JobEvent, index: number) {
    const tagClass = event.status === "pass" || event.status === "done" || event.status === "completed"
      ? "log-tag--done"
      : event.status === "running" || event.status === "connected" || event.status === "started"
        ? "log-tag--running"
        : "log-tag--pending";
    return (
      <div key={event.event_id ?? index} className="log-line" style={{
        display: "grid", gridTemplateColumns: "66px 64px minmax(0,1fr)",
        gap: 9, alignItems: "start", padding: "4px 10px",
      }}>
        <div style={{ color: "#8995a8", fontFamily: "monospace", fontSize: 9.5 }}>{formatTime(event.created_at)}</div>
        <div>
          <span style={{
            minHeight: 17, display: "inline-flex", alignItems: "center",
            justifyContent: "center", padding: "0 5px",
            borderRadius: 999, fontSize: 8, fontWeight: 830,
            letterSpacing: ".025em",
            color: tagClass === "log-tag--done" ? "#bff3d2" : tagClass === "log-tag--running" ? "#c0dcff" : "#ffe0a1",
            background: tagClass === "log-tag--done" ? "rgba(53,208,127,.15)" : tagClass === "log-tag--running" ? "rgba(76,141,255,.17)" : "rgba(245,166,35,.16)",
          }}>
            {event.status.toUpperCase()}
          </span>
        </div>
        <div style={{ color: "#e4e7ec", wordBreak: "break-word", fontSize: 9.5 }}>{event.message}</div>
      </div>
    );
  }

  return (
    <>
      <div
        className={`${styles.evidenceBackdrop} ${open ? styles.evidenceBackdropOpen : ""}`}
        aria-hidden="true"
        onClick={onClose}
      />
      <aside
        className={`${styles.evidenceDrawer} ${open ? styles.evidenceDrawerOpen : ""}`}
        aria-label="Evidence and live logs"
        aria-hidden={!open}
      >
        <div className={styles.evidenceDrawerHeader}>
          <div className={styles.evidenceDrawerTitle}>
            <strong>Evidence &amp; live logs</strong>
            <span>Hidden by default. Open only when you need operational detail.</span>
          </div>
          <div className={styles.evidenceDrawerActions}>
            {streamState && (
              <span className={`${styles.status} ${streamState === "connected" ? styles.statusRunning : styles.statusPending}`}>
                {streamState}
              </span>
            )}
            <button
              ref={closeRef}
              type="button"
              className={styles.iconButton}
              onClick={onClose}
              aria-label="Close evidence and logs"
            >
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none">
                <path d="M6 6l12 12M18 6 6 18" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round"/>
              </svg>
            </button>
          </div>
        </div>
        <div className={styles.evidenceDrawerBody}>
          <div className={styles.evidenceDrawerToolbar}>
            <div style={{ display: "flex", alignItems: "center", gap: 7, flexWrap: "wrap" }}>
              {activeStageIndex != null && <span className={styles.countBadge}>route_step={activeStageIndex}</span>}
              <span className={styles.countBadge}>{evidence.length} events</span>
            </div>
            <button type="button" className={styles.button} onClick={scrollToLatest}>
              Latest
            </button>
          </div>
          <div style={{
            overflow: "hidden",
            border: "1px solid #2a3445", borderRadius: 11,
            background: "#12161f", color: "#e4e7ec",
          }}>
            <div style={{
              minHeight: 40, display: "flex", alignItems: "center",
              justifyContent: "space-between", gap: 10, padding: "0 11px",
              borderBottom: "1px solid #2a3445",
              background: "rgba(255,255,255,.02)",
              color: "#8b94a3", fontSize: 9.4, fontWeight: 760,
            }}>
              <span>Evidence stream</span>
              <span style={{ fontFamily: "monospace" }}>backend-owned</span>
            </div>
            <div
              ref={logRef}
              style={{
                maxHeight: 430, overflow: "auto", padding: "7px 0",
                fontFamily: '"SFMono-Regular", Consolas, "Liberation Mono", monospace',
                fontSize: 9.5, lineHeight: 1.45,
              }}
            >
              {evidence.length === 0 ? (
                <div style={{ padding: "14px 10px", color: "#8b94a3", fontStyle: "italic", fontSize: 10 }}>
                  No evidence events yet.
                </div>
              ) : (
                evidence.map((event, i) => renderLogEvent(event, i))
              )}
            </div>
          </div>
          {rawLogs.length > 0 && (
            <details style={{
              marginTop: 9,
              border: "1px solid #dce2e9", borderRadius: 8,
              background: "#f8fafc",
            }}>
              <summary style={{
                minHeight: 36, display: "flex", alignItems: "center",
                justifyContent: "space-between", gap: 10, padding: "0 10px",
                cursor: "pointer", listStyle: "none",
                color: "#172033", fontSize: 10.2, fontWeight: 700,
              }}>
                <span>Raw logs</span>
                <span style={{ color: "#667085", fontSize: 8.8 }}>expand</span>
              </summary>
              <div style={{
                padding: 10, borderTop: "1px solid #dce2e9",
                color: "#667085", fontSize: 9.4, lineHeight: 1.55,
                fontFamily: '"SFMono-Regular", Consolas, monospace',
              }}>
                {rawLogs.map((log, i) => (
                  <div key={log.event_id ?? i}>
                    {log.message}
                  </div>
                ))}
              </div>
            </details>
          )}
        </div>
        <div className={styles.evidenceDrawerFooter}>
          Logs remain available without occupying the primary cockpit. Press Escape or the close button to return.
        </div>
      </aside>
    </>
  );
}
