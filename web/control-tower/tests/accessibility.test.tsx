import { renderToStaticMarkup } from "react-dom/server";
import { describe, expect, it } from "vitest";

import { CreateDiagnosticJobForm } from "../app/jobs/new/CreateDiagnosticJobForm";
import { CurrentRunClient } from "../app/jobs/[jobId]/CurrentRunClient";
import type { Catalog, JobRepresentation, PublicRunEvent } from "../lib/contracts";

const catalog: Catalog = {
  runnerProfiles: [
    {
      runner_profile_id: "runner-default",
      runner_profile_version: "2026.06",
      display_name: "Default local runner"
    }
  ],
  pipelines: [
    {
      pipeline_id: "pipeline-default",
      pipeline_version: "2026.06",
      display_name: "Foundation diagnostic pipeline"
    }
  ],
  filesystemRoots: [
    {
      runner_profile_id: "runner-default",
      runner_profile_version: "2026.06",
      root_id: "source-root",
      kind: "source",
      display_name: "source-root"
    },
    {
      runner_profile_id: "runner-default",
      runner_profile_version: "2026.06",
      root_id: "output-root",
      kind: "output",
      display_name: "output-root"
    }
  ]
};

const initialJob: JobRepresentation = {
  job: {
    job_id: "job-1",
    version: 1,
    state: "CREATED",
    created_at: "2026-06-11T00:00:00Z",
    updated_at: "2026-06-11T00:00:00Z"
  },
  active_command: null,
  etag: '"job-job-1-v1"'
};

const initialEvents: PublicRunEvent[] = [];

describe("M2-12 accessibility and wording", () => {
  it("renders labeled form controls and action buttons", () => {
    const markup = renderToStaticMarkup(<CreateDiagnosticJobForm catalog={catalog} />);

    expect(markup).toContain("<form");
    expect(markup).toContain("<label");
    expect(markup).toContain("<select");
    expect(markup).toContain("<input");
    expect(markup).toContain("<button");
    expect(markup).toContain("Runner profile");
    expect(markup).toContain("Pipeline");
    expect(markup).toContain("Source relative path");
    expect(markup).toContain("Output relative path");
    expect(markup).toContain("Create foundation diagnostic job");
  });

  it("renders status text and avoids false migration wording", () => {
    const markup = renderToStaticMarkup(
      <CurrentRunClient initialEvents={initialEvents} initialJob={initialJob} />
    );

    expect(markup).toContain("Foundation diagnostic");
    expect(markup).toContain("Job state");
    expect(markup).toContain("Version");
    expect(markup).toContain("ETag");
    expect(markup).toContain("Active command");
    expect(markup).toContain("Start");
    expect(markup).toContain("Cancel");
    expect(markup).not.toContain("Migration completed");
    expect(markup).not.toContain("Build verified");
    expect(markup).not.toContain("Spring Boot upgraded");
    expect(markup).not.toContain("Proof achieved");
  });
});
