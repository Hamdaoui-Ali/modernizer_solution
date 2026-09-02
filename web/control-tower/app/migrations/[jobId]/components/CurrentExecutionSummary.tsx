"use client";

import type { V2PipelineRow, V2RouteStepEntry } from "../../../../lib/contracts";
import type { Stage } from "../MigrationCockpit";
import styles from "./CurrentExecutionSummary.module.css";

export interface CurrentExecutionSummaryProps {
  routeEntries: Array<V2RouteStepEntry | Stage>;
  activeStageIndex: number | null;
  pipelineRows: V2PipelineRow[];
  sourceProfile: string;
  targetProfile: string;
  streamState: string;
}

function isRouteStep(entry: V2RouteStepEntry | Stage): entry is V2RouteStepEntry {
  return "route_step_index" in entry;
}

export function CurrentExecutionSummary({
  routeEntries,
  activeStageIndex,
  pipelineRows,
  sourceProfile,
  targetProfile,
  streamState,
}: CurrentExecutionSummaryProps) {
  const activePipelineRow = pipelineRows.find(
    (r) => r.status === "running" || r.status === "blocked",
  );

  const currentRouteEntry = routeEntries.find((entry) => {
    if (isRouteStep(entry)) {
      return entry.status === "running" || entry.status === "blocked";
    }
    return entry.chain_status === "running" || entry.chain_status === "blocked";
  });

  const activeRouteStepIndex = currentRouteEntry && isRouteStep(currentRouteEntry)
    ? currentRouteEntry.route_step_index
    : null;

  const totalRouteSteps = routeEntries.filter(isRouteStep).length;
  const lastRouteStep = routeEntries.filter(isRouteStep).pop() as V2RouteStepEntry | undefined;

  return (
    <div className={styles.summary}>
      <div className={styles.main}>
        <div className={styles.header}>
          <small>Current execution</small>
          {activePipelineRow && (
            <span className={`${styles.status} ${activePipelineRow.status === "blocked" ? styles.statusBlocked : styles.statusRunning}`}>
              {activePipelineRow.status === "running" ? "RUNNING" : "BLOCKED"}
            </span>
          )}
        </div>

        {currentRouteEntry && isRouteStep(currentRouteEntry) ? (
          <>
            <div className={styles.routeStep}>
              <span className={styles.routeStepLabel}>
                Route step {currentRouteEntry.route_step_index + 1} of {totalRouteSteps}
              </span>
              <div className={styles.transition}>
                <span className={styles.profile}>{currentRouteEntry.source_profile}</span>
                <span className={styles.arrow}>&rarr;</span>
                <span className={styles.profile}>{currentRouteEntry.target_profile}</span>
              </div>
            </div>

            {activePipelineRow && (
              <div className={styles.phase}>
                <strong>{activePipelineRow.label}</strong>
                <span className={styles.phaseStatus}>{activePipelineRow.status.toUpperCase()}</span>
                <span className={styles.phaseMessage}>{activePipelineRow.latest_message}</span>
              </div>
            )}
          </>
        ) : currentRouteEntry && !isRouteStep(currentRouteEntry) ? (
          <div className={styles.routeStep}>
            <span className={styles.routeStepLabel}>
              {currentRouteEntry.pipeline_stage}
            </span>
            {activePipelineRow && (
              <div className={styles.phase}>
                <span className={styles.phaseStatus}>{activePipelineRow.status.toUpperCase()}</span>
                <span className={styles.phaseMessage}>{activePipelineRow.latest_message}</span>
              </div>
            )}
          </div>
        ) : (
          <div className={styles.routeStep}>
            <span className={styles.routeStepLabel}>Awaiting execution</span>
          </div>
        )}
      </div>

      <div className={styles.overallTarget}>
        <small>Overall target</small>
        <strong>{targetProfile}</strong>
      </div>
    </div>
  );
}
