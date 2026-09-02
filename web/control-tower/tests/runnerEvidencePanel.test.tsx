import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { RunnerEvidencePanel } from "../app/jobs/[jobId]/RunnerEvidencePanel";

describe("V1-18A Runner operations evidence panel", () => {
  it("renders panel with heading and counts", () => {
    const markup = renderToStaticMarkup(<RunnerEvidencePanel />);

    expect(markup).toContain("Runner operations evidence");
    expect(markup).toContain("Deterministic evidence records");
    expect(markup).toContain("redacted");
  });

  it("renders all evidence categories", () => {
    const markup = renderToStaticMarkup(<RunnerEvidencePanel />);

    expect(markup).toContain("Readiness");
    expect(markup).toContain("Launch");
    expect(markup).toContain("Output limit");
    expect(markup).toContain("Cancellation");
    expect(markup).toContain("Timeout");
    expect(markup).toContain("Restart");
  });

  it("renders key evidence descriptions", () => {
    const markup = renderToStaticMarkup(<RunnerEvidencePanel />);

    expect(markup).toContain("JDK 11 detected");
    expect(markup).toContain("JDK 17 detected");
    expect(markup).toContain("JDK 21 detected");
    expect(markup).toContain("Maven detected at backend-owned path");
    expect(markup).toContain("Worker process starts");
    expect(markup).toContain("Worker manifest checksum");
    expect(markup).toContain("Stdout truncated");
    expect(markup).toContain("Stderr truncated");
    expect(markup).toContain("Running worker terminates");
    expect(markup).toContain("Cancelled command marked");
    expect(markup).toContain("Command timed out");
    expect(markup).toContain("Worker relaunch creates new PID");
    expect(markup).toContain("Restarted worker picks up");
  });

  it("renders PASS status for all evidence items", () => {
    const markup = renderToStaticMarkup(<RunnerEvidencePanel />);

    const passCount = (markup.match(/PASS/g) || []).length;
    expect(passCount).toBe(13); // 13 evidence items
  });

  it("renders redacted label for all evidence items", () => {
    const markup = renderToStaticMarkup(<RunnerEvidencePanel />);

    const redactedCount = (markup.match(/redacted/g) || []).length;
    // All 13 items have "redacted" plus the notes section
    expect(redactedCount).toBeGreaterThanOrEqual(13);
  });

  it("shows empty state for unknown category", () => {
    const markup = renderToStaticMarkup(
      <RunnerEvidencePanel category="UnknownCategory" />
    );

    expect(markup).toContain("No evidence records found");
    expect(markup).toContain("UnknownCategory");
  });

  it("filters by category when provided", () => {
    const markup = renderToStaticMarkup(
      <RunnerEvidencePanel category="Readiness" />
    );

    expect(markup).toContain("Readiness");
    expect(markup).toContain("JDK 11 detected");
    expect(markup).not.toContain("Launch");
    expect(markup).not.toContain("Worker process");
  });

  it("renders details notes section", () => {
    const markup = renderToStaticMarkup(<RunnerEvidencePanel />);

    expect(markup).toContain("Evidence notes");
    expect(markup).toContain("All absolute paths");
    expect(markup).toContain("truncated at 1MB");
  });

  // ── No dangerous controls evidence ─────────────────────────────────

  it("has no dangerous input controls", () => {
    const markup = renderToStaticMarkup(<RunnerEvidencePanel />);

    expect(markup).not.toContain("<input");
    expect(markup).not.toContain("<select");
    expect(markup).not.toContain("<textarea");
    expect(markup).not.toContain("<button");
    expect(markup).not.toContain("<form");
  });

  it("has no raw shell, Maven, path, or model controls", () => {
    const markup = renderToStaticMarkup(<RunnerEvidencePanel />);

    // No executable editors
    expect(markup).not.toContain("mvn");
    expect(markup).not.toContain("raw path");
    expect(markup).not.toContain("goal");
    expect(markup).not.toContain("cmd");
    // No deployment/model selectors
    expect(markup).not.toContain("deployment");
    expect(markup).not.toContain("model");
    // No approve/execute
    expect(markup).not.toContain("Approve");
    expect(markup).not.toContain("Reject");
    expect(markup).not.toContain("Execute");
  });
});
