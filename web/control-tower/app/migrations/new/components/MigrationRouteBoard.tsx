"use client";

import {
  MIGRATION_PROFILE_OPTIONS,
  getRoutePreview,
  type MigrationProfileId,
} from "../../../../lib/contracts";
import styles from "../NewMigrationForm.module.css";

interface MigrationRouteBoardProps {
  sourceProfile: MigrationProfileId;
  targetProfile: MigrationProfileId;
  routeValidationError: string | null;
}

export function MigrationRouteBoard({
  sourceProfile,
  targetProfile,
  routeValidationError,
}: MigrationRouteBoardProps) {
  const sourceLabel = MIGRATION_PROFILE_OPTIONS.find((p) => p.id === sourceProfile)?.label || sourceProfile;
  const targetLabel = MIGRATION_PROFILE_OPTIONS.find((p) => p.id === targetProfile)?.label || targetProfile;
  const preview = routeValidationError ? null : getRoutePreview(sourceProfile, targetProfile);

  const summaryParts: string[] = [];
  if (preview) {
    if (preview.included.length > 0) summaryParts.push(`Included stages: ${preview.included.join(", ")}`);
    if (preview.skipped.length > 0) summaryParts.push(`Skipped: ${preview.skipped.join(", ")}`);
    if (preview.excluded.length > 0) summaryParts.push(`Excluded: ${preview.excluded.join(", ")}`);
  }
  if (routeValidationError) {
    summaryParts.push(routeValidationError);
  }

  const stageNodes = preview
    ? preview.included.map((s) => ({ stage: s, status: "included" as const }))
    : [];

  return (
    <section className={styles["route-board"]} aria-labelledby="routeBoardTitle">
      <div className={styles["route-board__head"]}>
        <strong id="routeBoardTitle">Migration route</strong>
        <span>{summaryParts.length > 0 ? summaryParts.join(" · ") : "Select source and target profiles"}</span>
      </div>

      <div className={`${styles["route-flow"]} ${stageNodes.length === 0 ? styles["route-flow--empty"] : ""}`}>
        <div className={`${styles["route-node"]} ${styles["route-node--edge"]}`}>
          <small>Source</small>
          <strong>{sourceLabel.split(" / ")[0] || sourceLabel}</strong>
          <span>{sourceLabel.split(" / ")[1] || ""}</span>
        </div>

        {stageNodes.map((node) => (
          <div key={node.stage} className={`${styles["route-node"]} ${styles["route-node--stage"]}`}>
            <small>Stage</small>
            <strong>{node.stage}</strong>
          </div>
        ))}

        <div className={`${styles["route-node"]} ${styles["route-node--edge"]}`}>
          <small>Target</small>
          <strong>{targetLabel.split(" / ")[0] || targetLabel}</strong>
          <span>{targetLabel.split(" / ")[1] || ""}</span>
        </div>
      </div>
    </section>
  );
}
