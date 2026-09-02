"use client";

import type { V2PipelineRow } from "../../../../lib/contracts";
import styles from "./PipelineStatusList.module.css";

export interface PipelineStatusListProps {
  rows: V2PipelineRow[];
  streamState: string;
}

type PipelineTone = "done" | "running" | "pending" | "failed" | "neutral";

interface PipelineRowViewModel {
  row: V2PipelineRow;
  displayStatus: string;
  tone: PipelineTone;
  visibleInMainList: boolean;
  accessibilityLabel: string;
}

const CANCELLATION_KEYS = new Set(["cancellation", "Cancellation"]);

function buildStatusTone(status: string): PipelineTone {
  switch (status) {
    case "pass": return "done";
    case "running": return "running";
    case "pending": return "pending";
    case "failed": return "failed";
    case "cancelled": return "failed";
    case "blocked": return "neutral";
    case "skipped": return "neutral";
    default: return "neutral";
  }
}

function buildViewModel(row: V2PipelineRow): PipelineRowViewModel {
  const raw = row.status;
  const isCancellation = CANCELLATION_KEYS.has(row.key);

  let displayStatus = raw.toUpperCase();
  let tone = buildStatusTone(raw);
  let visibleInMainList = true;

  if (row.key.toLowerCase() === "preflight") {
    visibleInMainList = false;
  }

  if (isCancellation && raw === "pending") {
    const msg = (row.latest_message || "").toLowerCase();
    const isInactive = msg.includes("no cancellation") || msg.includes("waiting for") || msg.includes("no request") || msg.length === 0;
    if (isInactive) {
      visibleInMainList = false;
    }
  }

  const accessibilityLabel = `${row.label}: ${displayStatus}. ${row.latest_message || ""}`.trim();

  return {
    row,
    displayStatus,
    tone,
    visibleInMainList,
    accessibilityLabel,
  };
}

export function PipelineStatusList({ rows, streamState }: PipelineStatusListProps) {
  const viewModels = rows.map(buildViewModel);
  const visibleRows = viewModels.filter((vm) => vm.visibleInMainList);

  const doneCount = visibleRows.filter((r) => r.tone === "done").length;
  const runningCount = visibleRows.filter((r) => r.tone === "running").length;
  const waitingCount = visibleRows.filter((r) => r.tone !== "done" && r.tone !== "running" && r.tone !== "failed").length;
  const failedCount = visibleRows.filter((r) => r.tone === "failed").length;

  const summaryParts: string[] = [];
  if (doneCount > 0) summaryParts.push(`${doneCount} completed`);
  if (runningCount > 0) summaryParts.push(`${runningCount} running`);
  if (waitingCount > 0) summaryParts.push(`${waitingCount} waiting`);
  if (failedCount > 0) summaryParts.push(`${failedCount} failed`);
  const summary = summaryParts.join(" · ") || "No phases";

  return (
    <div className={styles.container}>
      <div className={styles.head}>
        <div className={styles.headLeft}>
          <strong>Pipeline Status</strong>
          <span className={styles.streamIndicator}>
            <span className={styles.streamDot} />
            Stream: {streamState}
          </span>
        </div>
        <span className={styles.summaryBadge}>{summary}</span>
      </div>
      <div className={styles.list} role="list" aria-label="Pipeline status list">
        {visibleRows.map((vm) => (
          <div
            key={vm.row.key}
            className={styles.row}
            role="listitem"
            aria-label={vm.accessibilityLabel}
          >
            <span className={`${styles.status} ${styles[`tone${vm.tone.charAt(0).toUpperCase() + vm.tone.slice(1)}`]}`}>
              {vm.displayStatus}
            </span>
            <div className={styles.rowName}>{vm.row.label}</div>
            <div className={styles.rowMessage} title={vm.row.latest_message}>
              {vm.row.latest_message}
            </div>
            <span className={styles.artifactBadge}>{vm.row.artifact_count} artifacts</span>
          </div>
        ))}
      </div>
    </div>
  );
}
