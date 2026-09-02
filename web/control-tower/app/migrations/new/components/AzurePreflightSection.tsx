"use client";

import type { SettingsResponse, PreflightResponse } from "../hooks/useNewMigrationForm";
import styles from "../NewMigrationForm.module.css";

interface AzurePreflightSectionProps {
  setupResult: { setup_id: string } | null;
  azureSettings: SettingsResponse | null;
  preflight: PreflightResponse | null;
  skip_endpoint_smoke: boolean;
  loading: string | null;
  azureSmokeCopy: { label: string; checkedAt: string; failureReason: string; snippet: string };
  onCheckAzure: () => void;
  onRunPreflight: () => void;
  onFieldChange: (key: string, value: string | boolean) => void;
}

export function AzurePreflightSection({
  setupResult,
  azureSettings,
  preflight,
  skip_endpoint_smoke,
  loading,
  azureSmokeCopy,
  onCheckAzure,
  onRunPreflight,
  onFieldChange,
}: AzurePreflightSectionProps) {
  const azureChecked = azureSettings !== null;
  const azureOk = azureSettings?.azure.connection_configured === true;
  const preflightDisabled = !setupResult || loading !== null;

  return (
    <section className={styles["card"]}>
      <div className={styles["card__head"]}>
        <div>
          <h2>Azure and preflight</h2>
          <p>Confirm the configured model, then validate the local migration prerequisites.</p>
        </div>
      </div>

      <div className={styles["card__body"]}>
        <div className={styles["azure-row"]}>
          <div className={styles["azure-row__copy"]}>
            <strong>Azure settings</strong>
            <span>
              {azureOk
                ? "Endpoint configured. Run the check to confirm the current model configuration."
                : "Check the current Azure model endpoint and role configuration."}
            </span>
          </div>

          <div className={styles["azure-row__actions"]}>
            <span className={`${styles["state"]} ${azureChecked ? (azureOk ? styles["state--pass"] : styles["state--warn"]) : ""}`}>
              {azureChecked ? (azureOk ? "PASS" : "FAIL") : "Not checked"}
            </span>
            <button
              type="button"
              className={styles["btn"]}
              disabled={!!loading}
              onClick={onCheckAzure}
            >
              {loading === "Loading settings..." ? (
                <><span className={styles["spinner"]} /> Checking</>
              ) : (
                azureChecked ? "Check again" : "Check Azure settings"
              )}
            </button>
          </div>
        </div>

        {azureSettings && (
          <div className={styles["azure-detail"]}>
            <div className={styles["check-row"]}>
              <span className={styles["check-row__key"]}>Status:</span>
              <code>{azureSettings.azure.status}</code>
            </div>
            <div className={styles["check-row"]}>
              <span className={styles["check-row__key"]}>Endpoint:</span>
              <code>{azureSettings.azure.connection_configured ? "✓ Configured" : "✗ Not Configured"}</code>
            </div>
            <p className={styles["meta"]} style={{ marginTop: 8 }}>
              Endpoint configured is not smoke evidence. Run preflight to get a PASS or FAIL verdict.
            </p>
          </div>
        )}

        <div className={styles["preflight-toolbar"]}>
          <p id="preflightMessage">
            {preflightDisabled && !setupResult
              ? "Save the setup before running preflight."
              : preflight
                ? "Preflight completed. Review results below."
                : "Run preflight after the Azure settings check passes."}
          </p>
          <button
            type="button"
            className={`${styles["btn"]} ${!preflightDisabled ? styles["btn--primary"] : ""}`}
            disabled={!!preflightDisabled}
            onClick={onRunPreflight}
          >
            {loading === "Running preflight..." ? (
              <><span className={styles["spinner"]} /> Running</>
            ) : (
              "Run preflight"
            )}
          </button>
        </div>

        {preflight && (
          <>
            <div className={styles["check-groups"]}>
              <section className={styles["check-group"]}>
                <div className={styles["check-group__title"]}>All ready</div>
                <div className={styles["check-list"]}>
                  <div className={`${styles["check-item"]} ${preflight.all_ready ? "is-pass" : "is-warn"}`}>
                    <span className={styles["check-item__icon"]}>{preflight.all_ready ? "✓" : "✗"}</span>
                    <span>{preflight.all_ready ? "All prerequisites ready" : "Some checks failed"}</span>
                  </div>
                </div>
              </section>

              {Object.keys(preflight.readiness || {}).length > 0 && (
                <section className={styles["check-group"]}>
                  <div className={styles["check-group__title"]}>Readiness checks</div>
                  <div className={styles["check-list"]}>
                    {Object.entries(preflight.readiness).map(([key, val]) => (
                      <div key={key} className={`${styles["check-item"]} ${val ? "is-pass" : "is-warn"}`}>
                        <span className={styles["check-item__icon"]}>{val ? "✓" : "✗"}</span>
                        <span>{key}</span>
                      </div>
                    ))}
                  </div>
                </section>
              )}
            </div>

            {preflight.warnings.length > 0 && (
              <div className={`${styles["message-box"]} ${styles["message-box--warning"]}`}>
                <strong>Warnings:</strong>
                <ul>
                  {preflight.warnings.map((w, i) => (
                    <li key={i}>{w}</li>
                  ))}
                </ul>
              </div>
            )}

            {preflight.errors.length > 0 && (
              <div className={`${styles["message-box"]} ${styles["message-box--error"]}`}>
                <strong>Errors:</strong>
                <ul>
                  {preflight.errors.map((e, i) => (
                    <li key={i}>{e}</li>
                  ))}
                </ul>
              </div>
            )}

            <div className={styles["info-box"]}>
              <p className={styles["check-row"]}>
                <span className={styles["check-row__key"]}>Azure smoke:</span>
                <span>{azureSmokeCopy.label}</span>
              </p>
              <p className={styles["meta"]} style={{ marginTop: 4 }}>
                Smoke checked at: <code>{azureSmokeCopy.checkedAt || preflight.checked_at}</code>
              </p>
              {azureSmokeCopy.failureReason && (
                <p className={`${styles["check-row"]} ${styles["check-row--warn"]}`} style={{ marginTop: 4 }}>
                  <span className={styles["check-row__key"]}>Failure reason:</span>
                  <code>{azureSmokeCopy.failureReason}</code>
                </p>
              )}
              {azureSmokeCopy.snippet && (
                <p className={styles["meta"]} style={{ marginTop: 4 }}>
                  Evidence: <code>{azureSmokeCopy.snippet}</code>
                </p>
              )}
            </div>
          </>
        )}
      </div>
    </section>
  );
}
