"use client";

import type {
  ReadinessResponse,
  FormFields,
} from "../hooks/useNewMigrationForm";
import {
  MIGRATION_PROFILE_OPTIONS,
} from "../../../../lib/contracts";
import styles from "../NewMigrationForm.module.css";

interface MigrationReadinessSidebarProps {
  fields: FormFields;
  setupResult: { setup_id: string } | null;
  azureSettings: { azure: { connection_configured: boolean } } | null;
  preflight: unknown;
  readiness: ReadinessResponse | null;
  loading: string | null;
  startEnabled: boolean;
  startState: { label: string; ready: boolean };
  routeValidationError: string | null;
  onStart: () => void;
}

export function MigrationReadinessSidebar({
  fields,
  setupResult,
  azureSettings,
  readiness,
  loading,
  startEnabled,
  startState,
  routeValidationError,
  onStart,
}: MigrationReadinessSidebarProps) {
  const sourceLabel = MIGRATION_PROFILE_OPTIONS.find((p) => p.id === fields.sourceProfile)?.label || fields.sourceProfile;
  const targetLabel = MIGRATION_PROFILE_OPTIONS.find((p) => p.id === fields.targetProfile)?.label || fields.targetProfile;

  const fieldsComplete = fields.run_name.trim().length > 0 && fields.legacy_app_path.trim().length > 0;
  const azureOk = azureSettings?.azure.connection_configured === true;
  const preflightRun = readiness !== null;
  const checksumMatch = readiness?.preflight_checksum_match === true;
  const readinessReady = readiness?.ready === true;

  const disabledReason = routeValidationError
    ? "Select a valid source/target profile pair"
    : !setupResult
      ? "Save the setup first"
      : !readiness
        ? "Run preflight first"
        : !readinessReady
          ? "Fix errors above"
          : !checksumMatch
            ? "Preflight is stale — run preflight again"
            : null;

  return (
    <aside className={styles["sidebar"]}>
      <section className={styles["review-card"]}>
        <div className={styles["review-card__head"]}>
          <h2>Review</h2>
          <p>Current values used when the migration starts.</p>
        </div>

        <div className={styles["review-card__body"]}>
          <dl className={styles["summary-list"]}>
            <div className={styles["summary-row"]}>
              <dt>Run</dt>
              <dd>{fields.run_name || "Not set"}</dd>
            </div>
            <div className={styles["summary-row"]}>
              <dt>Source</dt>
              <dd>{sourceLabel}</dd>
            </div>
            <div className={styles["summary-row"]}>
              <dt>Target</dt>
              <dd>{targetLabel}</dd>
            </div>
            <div className={styles["summary-row"]}>
              <dt>Proof</dt>
              <dd>{fields.proof_level === "analyzed" ? "Analyzed" : fields.proof_level === "build_test_verified" ? "Build & Test Verified" : fields.proof_level === "runtime_verified" ? "Runtime Verified" : fields.proof_level}</dd>
            </div>
            <div className={styles["summary-row"]}>
              <dt>Output</dt>
              <dd>{fields.output_parent_path || "Not set"}</dd>
            </div>
          </dl>
        </div>
      </section>

      <section className={styles["review-card"]}>
        <div className={styles["review-card__head"]}>
          <h2>Ready to start</h2>
          <p>Complete each check before creating the migration workspace.</p>
        </div>

        <div className={styles["review-card__body"]}>
          <div className={`${styles["ready-banner"]} ${startEnabled ? "is-ready" : ""}`}>
            <span className={styles["ready-banner__icon"]}>
              {startEnabled ? "✓" : "!"}
            </span>
            <span>{startEnabled ? "Ready to start" : disabledReason || "Validation required"}</span>
          </div>

          <div className={styles["readiness-list"]}>
            <div className={styles["readiness-row"]}>
              <span>Required fields</span>
              <strong className={fieldsComplete ? "is-pass" : "is-warn"}>
                {fieldsComplete ? "Complete" : "Missing fields"}
              </strong>
            </div>
            <div className={styles["readiness-row"]}>
              <span>Save setup</span>
              <strong className={setupResult ? "is-pass" : "is-warn"}>
                {setupResult ? "Saved" : "Not saved"}
              </strong>
            </div>
            <div className={styles["readiness-row"]}>
              <span>Azure settings</span>
              <strong className={azureSettings ? (azureOk ? "is-pass" : "is-warn") : ""}>
                {azureSettings ? (azureOk ? "PASS" : "FAIL") : "Not checked"}
              </strong>
            </div>
            <div className={styles["readiness-row"]}>
              <span>Preflight</span>
              <strong className={preflightRun ? (readinessReady ? "is-pass" : "is-warn") : ""}>
                {preflightRun ? (readinessReady ? "Ready" : "Failed") : "Not run"}
              </strong>
            </div>
            <div className={styles["readiness-row"]}>
              <span>Checksum</span>
              <strong className={checksumMatch ? "is-pass" : "is-warn"}>
                {readiness ? (checksumMatch ? "Matched" : "Mismatch") : "Pending"}
              </strong>
            </div>
          </div>

          <button
            type="button"
            className={`${styles["btn"]} ${styles["btn--primary"]} ${styles["btn--large"]} ${styles["btn--block"]} ${styles["start-button"]}`}
            disabled={!startEnabled || !!loading}
            title={disabledReason || "Start migration"}
            onClick={onStart}
          >
            {loading === "Starting migration..." ? (
              <><span className={styles["spinner"]} /> Starting...</>
            ) : (
              "Start migration"
            )}
          </button>
          <p className={styles["start-note"]}>
            Starting creates a new migration workspace. The legacy application remains unchanged.
          </p>
        </div>
      </section>
    </aside>
  );
}
