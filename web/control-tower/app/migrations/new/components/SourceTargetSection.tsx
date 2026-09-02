"use client";

import {
  MIGRATION_PROFILE_OPTIONS,
  getRoutePreview,
  type MigrationProfileId,
} from "../../../../lib/contracts";
import styles from "../NewMigrationForm.module.css";

interface RouteDetailProps {
  label: string;
  stages: string[];
}

function RouteDetail({ label, stages }: RouteDetailProps) {
  return (
    <div className={styles["route-detail"]}>
      <small>{label}</small>
      <strong>{stages.length > 0 ? stages.join(", ") : "none"}</strong>
    </div>
  );
}

interface SourceTargetSectionProps {
  sourceProfile: MigrationProfileId;
  targetProfile: MigrationProfileId;
  routeValidationError: string | null;
  onFieldChange: (key: string, value: string | boolean) => void;
}

export function SourceTargetSection({
  sourceProfile,
  targetProfile,
  routeValidationError,
  onFieldChange,
}: SourceTargetSectionProps) {
  const preview = routeValidationError ? null : getRoutePreview(sourceProfile, targetProfile);

  return (
    <section className={styles["card"]}>
      <div className={styles["card__head"]}>
        <div>
          <h2>Source and target</h2>
          <p>Choose the migration profiles and confirm the calculated route.</p>
        </div>
      </div>

      <div className={styles["card__body"]}>
        <div className={styles["field-grid"]}>
          <div className={styles["field"]}>
            <div className={styles["field__label"]}><label htmlFor="sourceProfile">Source profile</label></div>
            <select
              id="sourceProfile"
              value={sourceProfile}
              onChange={(e) => onFieldChange("sourceProfile", e.target.value)}
              data-testid="source-profile-select"
            >
              {MIGRATION_PROFILE_OPTIONS.filter((p) => p.selectableAsSource).map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </div>

          <div className={styles["field"]}>
            <div className={styles["field__label"]}><label htmlFor="targetProfile">Target profile</label></div>
            <select
              id="targetProfile"
              value={targetProfile}
              onChange={(e) => onFieldChange("targetProfile", e.target.value)}
              data-testid="target-profile-select"
            >
              {MIGRATION_PROFILE_OPTIONS.filter((p) => p.selectableAsTarget).map((p) => (
                <option key={p.id} value={p.id}>{p.label}</option>
              ))}
            </select>
          </div>
        </div>

        {routeValidationError && (
          <p className={styles["field-error"]} data-testid="route-validation-error">{routeValidationError}</p>
        )}

        {routeValidationError === null && preview && (
          <>
            <div className={styles["section-divider"]} />
            <div className={styles["route-details"]} data-testid="route-preview">
              <RouteDetail label="Included stages" stages={preview.included} />
              <RouteDetail label="Skipped stages" stages={preview.skipped} />
              <RouteDetail label="Excluded stages" stages={preview.excluded} />
            </div>
          </>
        )}
      </div>
    </section>
  );
}
