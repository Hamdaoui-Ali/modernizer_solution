"use client";

import { useState, useCallback } from "react";
import styles from "../MigrationCockpit.module.css";
import type {
  V2ApprovalResponse,
  V2AssistantMessageResponse,
  V2FinalReportResponse,
  V2JobEvent,
  V2MigrationJobResponse,
  V2PipelineResponse,
  V2RouteStepEntry,
  GateDetailResponse,
  GateRepresentation,
} from "../../../../lib/contracts";

const TAB_IDS = ["approvals", "repair", "reports", "dependencies"] as const;
type TabId = (typeof TAB_IDS)[number];

const TAB_LABELS: Record<TabId, string> = {
  approvals: "Approval Decisions",
  repair: "Repair Proposal",
  reports: "Proof & Reports",
  dependencies: "Target Dependency Versions",
};

export function JobDetailsTabs({
  activeTab: externalTab,
  onTabChange,
  approvalChildren,
  repairChildren,
  reportsChildren,
  dependenciesChildren,
}: {
  activeTab?: string;
  onTabChange?: (tab: string) => void;
  approvalChildren: React.ReactNode;
  repairChildren: React.ReactNode;
  reportsChildren: React.ReactNode;
  dependenciesChildren: React.ReactNode;
}) {
  const [internalTab, setInternalTab] = useState<TabId>("approvals");
  const activeTab = (externalTab ?? internalTab) as TabId;

  const handleTabClick = useCallback((tab: TabId) => {
    setInternalTab(tab);
    onTabChange?.(tab);
  }, [onTabChange]);

  const handleKeyDown = useCallback((e: React.KeyboardEvent, currentIndex: number) => {
    let newIndex = currentIndex;
    switch (e.key) {
      case "ArrowRight":
        newIndex = (currentIndex + 1) % TAB_IDS.length;
        break;
      case "ArrowLeft":
        newIndex = (currentIndex - 1 + TAB_IDS.length) % TAB_IDS.length;
        break;
      case "Home":
        newIndex = 0;
        break;
      case "End":
        newIndex = TAB_IDS.length - 1;
        break;
      default:
        return;
    }
    e.preventDefault();
    handleTabClick(TAB_IDS[newIndex]);
    const tabEl = document.getElementById(`job-tab-${TAB_IDS[newIndex]}`);
    tabEl?.focus();
  }, [handleTabClick]);

  return (
    <section className={styles.panel}>
      <div className={styles.panelHeader}>
        <div>
          <h2>Job details</h2>
          <p>Approvals, repair, reports, and dependency comparison remain available without competing with the live execution surface.</p>
        </div>
      </div>
      <div className={styles.tabs} role="tablist" aria-label="Job details sections">
        {TAB_IDS.map((id, i) => (
          <button
            key={id}
            id={`job-tab-${id}`}
            type="button"
            role="tab"
            aria-selected={activeTab === id}
            aria-controls={`job-panel-${id}`}
            className={`${styles.tab} ${activeTab === id ? styles.tabActive : ""}`}
            onClick={() => handleTabClick(id)}
            onKeyDown={(e) => handleKeyDown(e, i)}
          >
            {TAB_LABELS[id]}
          </button>
        ))}
      </div>

      <div
        id="job-panel-approvals"
        role="tabpanel"
        aria-labelledby="job-tab-approvals"
        className={styles.tabPanel}
        hidden={activeTab !== "approvals"}
      >
        {approvalChildren}
      </div>

      <div
        id="job-panel-repair"
        role="tabpanel"
        aria-labelledby="job-tab-repair"
        className={styles.tabPanel}
        hidden={activeTab !== "repair"}
      >
        {repairChildren}
      </div>

      <div
        id="job-panel-reports"
        role="tabpanel"
        aria-labelledby="job-tab-reports"
        className={styles.tabPanel}
        hidden={activeTab !== "reports"}
      >
        {reportsChildren}
      </div>

      <div
        id="job-panel-dependencies"
        role="tabpanel"
        aria-labelledby="job-tab-dependencies"
        className={styles.tabPanel}
        hidden={activeTab !== "dependencies"}
      >
        {dependenciesChildren}
      </div>
    </section>
  );
}
