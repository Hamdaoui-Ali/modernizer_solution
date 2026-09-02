"use client";

import styles from "../NewMigrationForm.module.css";

interface JavaMavenSectionProps {
  java11_home: string;
  java17_home: string;
  java21_home: string;
  maven_cmd: string;
  proof_level: string;
  skip_endpoint_smoke: boolean;
  onFieldChange: (key: string, value: string | boolean) => void;
}

const PROOF_LEVELS = [
  { value: "analyzed", label: "Analyzed" },
  { value: "build_test_verified", label: "Build & Test Verified" },
  { value: "runtime_verified", label: "Runtime Verified" },
];

export function JavaMavenSection({
  java11_home,
  java17_home,
  java21_home,
  maven_cmd,
  proof_level,
  skip_endpoint_smoke,
  onFieldChange,
}: JavaMavenSectionProps) {
  return (
    <section className={styles["card"]}>
      <div className={styles["card__head"]}>
        <div>
          <h2>Java and Maven</h2>
          <p>Set the local executables used by the migration stages.</p>
        </div>
      </div>

      <div className={styles["card__body"]}>
        <div className={styles["field-grid"]}>
          <div className={styles["field"]}>
            <div className={styles["field__label"]}><label htmlFor="java11">JAVA11_HOME <span className={styles["required"]}>*</span></label></div>
            <input
              id="java11"
              type="text"
              value={java11_home}
              onChange={(e) => onFieldChange("java11_home", e.target.value)}
              placeholder="C:\Tools\jdk-11"
            />
          </div>

          <div className={styles["field"]}>
            <div className={styles["field__label"]}><label htmlFor="java17">JAVA17_HOME <span className={styles["required"]}>*</span></label></div>
            <input
              id="java17"
              type="text"
              value={java17_home}
              onChange={(e) => onFieldChange("java17_home", e.target.value)}
              placeholder="C:\Tools\jdk-17"
            />
          </div>

          <div className={styles["field"]}>
            <div className={styles["field__label"]}><label htmlFor="java21">JAVA21_HOME <span className={styles["required"]}>*</span></label></div>
            <input
              id="java21"
              type="text"
              value={java21_home}
              onChange={(e) => onFieldChange("java21_home", e.target.value)}
              placeholder="C:\Tools\jdk-21"
            />
          </div>

          <div className={styles["field"]}>
            <div className={styles["field__label"]}><label htmlFor="mavenCommand">Maven command <span className={styles["required"]}>*</span></label></div>
            <input
              id="mavenCommand"
              type="text"
              value={maven_cmd}
              onChange={(e) => onFieldChange("maven_cmd", e.target.value)}
              placeholder="C:\Tools\apache-maven-3.9.15\bin\mvn.cmd"
            />
          </div>
        </div>

        <div className={styles["section-divider"]} />

        <div className={styles["field-grid"]}>
          <div className={styles["field"]}>
            <div className={styles["field__label"]}><label htmlFor="proofLevel">Proof level</label></div>
            <select
              id="proofLevel"
              value={proof_level}
              onChange={(e) => onFieldChange("proof_level", e.target.value)}
            >
              {PROOF_LEVELS.map((pl) => (
                <option key={pl.value} value={pl.value}>{pl.label}</option>
              ))}
            </select>
          </div>

          <div className={styles["field"]}>
            <div className={styles["field__label"]}><label>Endpoint smoke test</label></div>
            <label className={styles["checkbox"]}>
              <input
                id="skipSmoke"
                type="checkbox"
                checked={skip_endpoint_smoke}
                onChange={(e) => onFieldChange("skip_endpoint_smoke", e.target.checked)}
              />
              <span>Skip endpoint smoke test</span>
            </label>
          </div>
        </div>
      </div>
    </section>
  );
}
