"use client";

import { useNewMigrationForm, EMPTY_FIELDS } from "./hooks/useNewMigrationForm";
import { MigrationRouteBoard } from "./components/MigrationRouteBoard";
import { ProjectPathsSection } from "./components/ProjectPathsSection";
import { JavaMavenSection } from "./components/JavaMavenSection";
import { SourceTargetSection } from "./components/SourceTargetSection";
import { AzurePreflightSection } from "./components/AzurePreflightSection";
import { MigrationReadinessSidebar } from "./components/MigrationReadinessSidebar";
import styles from "./NewMigrationForm.module.css";

import type { MigrationProfileId } from "../../../lib/contracts";

// ── Re-exported display helpers (for test imports) ──────────────
export { getRouteValidationMessage, getStartReadinessCopy, getAzureSmokeCopy, EMPTY_FIELDS } from "./hooks/useNewMigrationForm";
export type {
  ParsedEnvResult,
  SetupResponse,
  PreflightResponse,
  ReadinessResponse,
  SettingsResponse,
  FormFields,
} from "./hooks/useNewMigrationForm";

export function NewMigrationForm() {
  const {
    fields,
    parseResult,
    setupResult,
    preflight,
    readiness,
    azureSettings,
    error,
    loading,
    updateField,
    handleParseEnv,
    handleSaveSetup,
    handleRunPreflight,
    handleLoadSettings,
    handleStart,
    startState,
    azureSmokeCopy,
    startEnabled,
    routeValidationError,
    routePreview,
  } = useNewMigrationForm();

  return (
    <div className={styles["page"]}>
      <div className={styles["breadcrumbs"]}>
        <span>Migrations</span>
        <span>›</span>
        <span>New migration</span>
      </div>

      <header className={styles["page-head"]}>
        <div>
          <h1>Create a local migration</h1>
          <p className={styles["page-head__desc"]}>
            Check the source application, local toolchains, and migration route before the backend creates the migration workspace.
          </p>
        </div>

        <div className={styles["page-head__actions"]}>
          <button
            type="button"
            className={`${styles["btn"]} ${styles["btn--primary"]}`}
            disabled={!!loading || !fields.run_name || !fields.legacy_app_path}
            onClick={handleSaveSetup}
          >
            {loading === "Saving setup..." ? (
              <><span className={styles["spinner"]} /> Saving...</>
            ) : (
              "Save setup"
            )}
          </button>
        </div>
      </header>

      <MigrationRouteBoard
        sourceProfile={fields.sourceProfile as MigrationProfileId}
        targetProfile={fields.targetProfile as MigrationProfileId}
        routeValidationError={routeValidationError}
      />

      {error && (
        <div className={styles["error-banner"]} role="alert">
          {error}
        </div>
      )}

      <div className={styles["layout"]}>
        <div className={styles["main-stack"]}>
          <ProjectPathsSection
            run_name={fields.run_name}
            legacy_app_path={fields.legacy_app_path}
            output_parent_path={fields.output_parent_path}
            ai_hub_path={fields.ai_hub_path}
            envBlock={fields.envBlock}
            parseResult={parseResult}
            loading={loading}
            onFieldChange={updateField}
            onEnvBlockChange={(v) => updateField("envBlock", v)}
            onParse={handleParseEnv}
          />

          <JavaMavenSection
            java11_home={fields.java11_home}
            java17_home={fields.java17_home}
            java21_home={fields.java21_home}
            maven_cmd={fields.maven_cmd}
            proof_level={fields.proof_level}
            skip_endpoint_smoke={fields.skip_endpoint_smoke}
            onFieldChange={updateField}
          />

          <SourceTargetSection
            sourceProfile={fields.sourceProfile as MigrationProfileId}
            targetProfile={fields.targetProfile as MigrationProfileId}
            routeValidationError={routeValidationError}
            onFieldChange={updateField}
          />

          <AzurePreflightSection
            setupResult={setupResult}
            azureSettings={azureSettings}
            preflight={preflight}
            skip_endpoint_smoke={fields.skip_endpoint_smoke}
            loading={loading}
            azureSmokeCopy={azureSmokeCopy}
            onCheckAzure={handleLoadSettings}
            onRunPreflight={handleRunPreflight}
            onFieldChange={updateField}
          />
        </div>

        <MigrationReadinessSidebar
          fields={fields}
          setupResult={setupResult}
          azureSettings={azureSettings}
          preflight={preflight}
          readiness={readiness}
          loading={loading}
          startEnabled={startEnabled}
          startState={startState}
          routeValidationError={routeValidationError}
          onStart={handleStart}
        />
      </div>
    </div>
  );
}
