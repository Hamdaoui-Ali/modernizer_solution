"use client";

import { useState } from "react";
import type { ParsedEnvResult } from "../hooks/useNewMigrationForm";
import styles from "../NewMigrationForm.module.css";

interface EnvironmentImportDisclosureProps {
  envBlock: string;
  onEnvBlockChange: (value: string) => void;
  onParse: () => void;
  parseResult: ParsedEnvResult | null;
  loading: string | null;
}

export function EnvironmentImportDisclosure({
  envBlock,
  onEnvBlockChange,
  onParse,
  parseResult,
  loading,
}: EnvironmentImportDisclosureProps) {
  const [open, setOpen] = useState(false);

  return (
    <details
      className={styles["import-box"]}
      open={open}
      onToggle={(e) => setOpen((e.target as HTMLDetailsElement).open)}
    >
      <summary>
        <span className={styles["import-box__copy"]}>
          <strong>Import environment values</strong>
          <span>Paste the PowerShell block you already use. Supported variables fill the matching fields.</span>
        </span>
        <span className={`${styles["import-box__chevron"]} ${open ? "is-open" : ""}`} aria-hidden="true">
          <svg width="17" height="17" viewBox="0 0 24 24" fill="none">
            <path d="m7 10 5 5 5-5" stroke="currentColor" strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round" />
          </svg>
        </span>
      </summary>

      <div className={styles["import-box__content"]}>
        <textarea
          id="envBlock"
          spellCheck={false}
          aria-label="PowerShell environment block"
          placeholder={`$env:MIGRATION_RUN_NAME = "my-app"\n$env:LEGACY_APP_PATH = "C:\\work\\apps\\my-app"\n$env:JAVA11_HOME = "C:\\Tools\\jdk-11"\n...`}
          value={envBlock}
          onChange={(e) => onEnvBlockChange(e.target.value)}
        />

        <div className={styles["import-box__actions"]}>
          <button
            type="button"
            className={`${styles["btn"]} ${styles["btn--soft"]}`}
            disabled={!!loading || !envBlock.trim()}
            onClick={onParse}
          >
            {loading === "Parsing env block..." ? (
              <><span className={styles["spinner"]} /> Applying...</>
            ) : (
              "Apply values"
            )}
          </button>
          <button
            type="button"
            className={styles["btn"]}
            disabled={!!loading || !envBlock.trim()}
            onClick={() => onEnvBlockChange("")}
          >
            Clear
          </button>
        </div>

        {parseResult && (
          <div className={`${styles["parse-message"]} is-visible`} aria-live="polite">
            <strong>{Object.values(parseResult.parsed).filter(Boolean).length} values applied.</strong>
            {parseResult.ignored_keys.length > 0 && (
              <span className={styles["parse-message__detail"]}>
                Ignored: {parseResult.ignored_keys.join(", ")}
              </span>
            )}
            {parseResult.blocked_keys.length > 0 && (
              <span className={`${styles["parse-message__detail"]} ${styles["parse-message__detail--warn"]}`}>
                Blocked keys: {parseResult.blocked_keys.join(", ")}
              </span>
            )}
          </div>
        )}
      </div>
    </details>
  );
}
