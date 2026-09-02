"use client";

import { useState } from "react";
import type { Catalog } from "../../../lib/contracts";
import { createDiagnosticJobPayload, postJson } from "../../../lib/controlTowerApi";

type Props = {
  catalog: Catalog;
};

export function CreateDiagnosticJobForm({ catalog }: Props) {
  const sourceRoots = catalog.filesystemRoots.filter((root) => root.kind === "source");
  const outputRoots = catalog.filesystemRoots.filter((root) => root.kind === "output");
  const [error, setError] = useState<string | null>(null);
  const [pending, setPending] = useState(false);

  async function submit(formData: FormData) {
    setPending(true);
    setError(null);
    try {
      const payload = createDiagnosticJobPayload({
        runnerProfileKey: String(formData.get("runnerProfileKey") ?? ""),
        pipelineKey: String(formData.get("pipelineKey") ?? ""),
        legacySourceRootId: String(formData.get("legacySourceRootId") ?? ""),
        legacySourceRelativePath: String(formData.get("legacySourceRelativePath") ?? ""),
        outputRootId: String(formData.get("outputRootId") ?? ""),
        outputRelativePath: String(formData.get("outputRelativePath") ?? "")
      });
      const body = await postJson<{ job: { job_id: string } }>("/v1/jobs", payload, {
        "Idempotency-Key": crypto.randomUUID()
      });
      window.location.assign(`/jobs/${encodeURIComponent(body.job.job_id)}`);
    } catch (exc) {
      setError(exc instanceof Error ? exc.message : "Could not create foundation diagnostic job.");
      setPending(false);
    }
  }

  return (
    <form action={submit} className="panel stack">
      <div className="grid">
        <label className="field">
          <span>Runner profile</span>
          <select className="input" name="runnerProfileKey" required>
            {catalog.runnerProfiles.map((profile) => (
              <option
                key={`${profile.runner_profile_id}@${profile.runner_profile_version}`}
                value={`${profile.runner_profile_id}@${profile.runner_profile_version}`}
              >
                {profile.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Pipeline</span>
          <select className="input" name="pipelineKey" required>
            {catalog.pipelines.map((pipeline) => (
              <option
                key={`${pipeline.pipeline_id}@${pipeline.pipeline_version}`}
                value={`${pipeline.pipeline_id}@${pipeline.pipeline_version}`}
              >
                {pipeline.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Source root</span>
          <select className="input" name="legacySourceRootId" required>
            {sourceRoots.map((root) => (
              <option key={root.root_id} value={root.root_id}>
                {root.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Source relative path</span>
          <input className="input" name="legacySourceRelativePath" placeholder="src" required />
        </label>
        <label className="field">
          <span>Output root</span>
          <select className="input" name="outputRootId" required>
            {outputRoots.map((root) => (
              <option key={root.root_id} value={root.root_id}>
                {root.display_name}
              </option>
            ))}
          </select>
        </label>
        <label className="field">
          <span>Output relative path</span>
          <input className="input" name="outputRelativePath" placeholder="out" required />
        </label>
      </div>
      {error ? <p role="alert">{error}</p> : null}
      <button className="button" disabled={pending} type="submit">
        {pending ? "Creating..." : "Create foundation diagnostic job"}
      </button>
    </form>
  );
}
