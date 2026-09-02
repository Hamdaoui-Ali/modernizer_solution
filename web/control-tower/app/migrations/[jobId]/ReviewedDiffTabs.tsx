"use client";

import { useState } from "react";
import type { ReviewedDiffProposal, SafeDiffPreview as SafeDiffPreviewType } from "../../../lib/contracts";
import { SafeDiffPreview } from "./SafeDiffPreview";
import { ReviewerVerdictCard } from "./ReviewerVerdictCard";

type TabId = "diff" | "files-changed" | "reviewer-opinion" | "validation";

const TABS: { id: TabId; label: string }[] = [
  { id: "diff", label: "Diff" },
  { id: "files-changed", label: "Files Changed" },
  { id: "reviewer-opinion", label: "Reviewer Notes" },
  { id: "validation", label: "Validation" },
];

export function ReviewedDiffTabs({
  proposal,
  diff,
  onTabChange,
}: {
  proposal: ReviewedDiffProposal;
  diff: SafeDiffPreviewType | null;
  onTabChange?: (tab: TabId) => void;
}) {
  const [activeTab, setActiveTab] = useState<TabId>("diff");

  function handleTabClick(tab: TabId) {
    setActiveTab(tab);
    onTabChange?.(tab);
  }

  return (
    <div className="reviewed-diff-tabs" data-testid="reviewed-diff-tabs">
      <div className="tab-bar" role="tablist">
        {TABS.map((tab) => (
          <button
            key={tab.id}
            role="tab"
            type="button"
            className={activeTab === tab.id ? "tab-active" : ""}
            onClick={() => handleTabClick(tab.id)}
            data-testid={`tab-${tab.id}`}
          >
            {tab.label}
          </button>
        ))}
      </div>
      <div className="tab-content" role="tabpanel">
        {activeTab === "diff" && (
          <div data-testid="tabpanel-diff">
            {diff ? (
              <SafeDiffPreview diff={diff} />
            ) : (
              <div className="safe-diff-missing" data-testid="safe-diff-missing">
                <p className="meta">No diff preview available.</p>
                {proposal.diff_ref && <p className="meta">Diff could not be loaded.</p>}
              </div>
            )}
          </div>
        )}
        {activeTab === "files-changed" && (
          <div data-testid="tabpanel-files-changed">
            {proposal.files_changed.length === 0 ? (
              <p className="meta">No files changed.</p>
            ) : (
              <div className="table-list">
                {proposal.files_changed.map((fc, i) => (
                  <div key={i} className="table-row" data-testid="files-changed-row">
                    <span className="meta">{fc.change_type}</span>
                    <strong>{fc.path}</strong>
                    <span className="meta">+{fc.additions} / -{fc.deletions}</span>
                  </div>
                ))}
              </div>
            )}
          </div>
        )}
        {activeTab === "reviewer-opinion" && (
          <div data-testid="tabpanel-reviewer-opinion">
            <ReviewerVerdictCard verdict={proposal.reviewer_verdict} />
          </div>
        )}
        {activeTab === "validation" && (
          <div data-testid="tabpanel-validation">
            {proposal.status === "approved_applied" ? (
              <p className="meta">Validation passed. Patch was applied to sandbox.</p>
            ) : proposal.apply_status ? (
              <div className="table-list">
                <div className="table-row">
                  <span className="meta">Apply status</span>
                  <strong>{proposal.apply_status || "N/A"}</strong>
                </div>
                <div className="table-row">
                  <span className="meta">Rerun status</span>
                  <strong>{proposal.rerun_status || "N/A"}</strong>
                </div>
              </div>
            ) : (
              <p className="meta">No validation data available yet. Approve the diff to run validation.</p>
            )}
          </div>
        )}
      </div>
    </div>
  );
}

export type { TabId };
