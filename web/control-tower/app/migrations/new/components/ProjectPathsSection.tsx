"use client";

import { EnvironmentImportDisclosure } from "./EnvironmentImportDisclosure";
import type { ParsedEnvResult } from "../hooks/useNewMigrationForm";
import styles from "../NewMigrationForm.module.css";

interface ProjectPathsSectionProps {
  run_name: string;
  legacy_app_path: string;
  output_parent_path: string;
  ai_hub_path: string;
  envBlock: string;
  parseResult: ParsedEnvResult | null;
  loading: string | null;
  onFieldChange: (key: string, value: string | boolean) => void;
  onEnvBlockChange: (value: string) => void;
  onParse: () => void;
}

export function ProjectPathsSection({
  run_name,
  legacy_app_path,
  output_parent_path,
  ai_hub_path,
  envBlock,
  parseResult,
  loading,
  onFieldChange,
  onEnvBlockChange,
  onParse,
}: ProjectPathsSectionProps) {
  return (
    <section className={styles["card"]}>
      <div className={styles["card__head"]}>
        <div>
          <h2>Project and paths</h2>
          <p>Name the run and locate the source, output, and AI Hub folders.</p>
        </div>
        <span className={styles["hint"]}>Required fields are marked *</span>
      </div>

      <div className={styles["card__body"]}>
        <EnvironmentImportDisclosure
          envBlock={envBlock}
          onEnvBlockChange={onEnvBlockChange}
          onParse={onParse}
          parseResult={parseResult}
          loading={loading}
        />

        <div className={styles["field-grid"]}>
          <div className={`${styles["field"]} ${styles["field--full"]}`}>
            <div className={styles["field__label"]}>
              <label htmlFor="runName">Run name <span className={styles["required"]}>*</span></label>
              <span className={styles["hint"]}>Used for the workspace and migration records</span>
            </div>
            <input
              id="runName"
              type="text"
              value={run_name}
              onChange={(e) => onFieldChange("run_name", e.target.value)}
              placeholder="my-app-v2"
              autoComplete="off"
            />
          </div>

          <div className={`${styles["field"]} ${styles["field--full"]}`}>
            <div className={styles["field__label"]}>
              <label htmlFor="legacyPath">Legacy app path <span className={styles["required"]}>*</span></label>
              <span className={styles["hint"]}>The source application is not modified</span>
            </div>
            <div className={styles["path-field"]}>
              <input
                id="legacyPath"
                type="text"
                value={legacy_app_path}
                onChange={(e) => onFieldChange("legacy_app_path", e.target.value)}
                placeholder="C:\work\apps\legacy-service"
              />
              <span className={styles["path-field__icon"]} aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path d="M4 8a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z" stroke="currentColor" strokeWidth="1.7" />
                </svg>
              </span>
            </div>
          </div>

          <div className={styles["field"]}>
            <div className={styles["field__label"]}>
              <label htmlFor="outputPath">Output parent path <span className={styles["required"]}>*</span></label>
            </div>
            <div className={styles["path-field"]}>
              <input
                id="outputPath"
                type="text"
                value={output_parent_path}
                onChange={(e) => onFieldChange("output_parent_path", e.target.value)}
                placeholder="C:\work\modernized"
              />
              <span className={styles["path-field__icon"]} aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path d="M4 8a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z" stroke="currentColor" strokeWidth="1.7" />
                </svg>
              </span>
            </div>
          </div>

          <div className={styles["field"]}>
            <div className={styles["field__label"]}>
              <label htmlFor="aiHubPath">AI Hub path <span className={styles["required"]}>*</span></label>
            </div>
            <div className={styles["path-field"]}>
              <input
                id="aiHubPath"
                type="text"
                value={ai_hub_path}
                onChange={(e) => onFieldChange("ai_hub_path", e.target.value)}
                placeholder="C:\Users\me\modernizer-solution-ai-hub"
              />
              <span className={styles["path-field__icon"]} aria-hidden="true">
                <svg width="16" height="16" viewBox="0 0 24 24" fill="none">
                  <path d="M4 8a2 2 0 0 1 2-2h4l2 2h6a2 2 0 0 1 2 2v7a2 2 0 0 1-2 2H6a2 2 0 0 1-2-2V8Z" stroke="currentColor" strokeWidth="1.7" />
                </svg>
              </span>
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
