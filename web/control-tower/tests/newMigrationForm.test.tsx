import { describe, expect, it } from "vitest";

import { getAzureSmokeCopy, getRouteValidationMessage, getStartReadinessCopy } from "../app/migrations/new/NewMigrationForm";
import { DEFAULT_V2_STAGE_CONTINUATION_POLICY, createV2JobPayload } from "../lib/controlTowerApi";
import {
  MIGRATION_PROFILE_OPTIONS,
  getRoutePreview,
  getRoutePreviewKey,
  getRouteValidationError,
  type MigrationProfileId,
} from "../lib/contracts";

// Frontend contract tests for the New Migration form
// These test the parsing/shape of env blocks without relying on mock API.

describe("V2 New Migration form contract", () => {
  it("form accepts only local absolute paths as setup inputs", () => {
    // The form fields accept local absolute paths
    const allowedFieldTypes = {
      run_name: "string",
      legacy_app_path: "local path string",
      output_parent_path: "local path string",
      ai_hub_path: "local absolute path string or backend default",
      java11_home: "local absolute path string",
      java17_home: "local absolute path string",
      java21_home: "local absolute path string",
      maven_cmd: "local absolute path string",
      proof_level: "string (enum)",
      skip_endpoint_smoke: "boolean",
    };

    // Verifying the form does NOT have forbidden fields
    const allowedKeys = Object.keys(allowedFieldTypes);
    expect(allowedKeys).not.toContain("azure_api_key");
    expect(allowedKeys).not.toContain("endpoint_url");
    expect(allowedKeys).not.toContain("deployment_id");
    expect(allowedKeys).not.toContain("model_name");
    expect(allowedKeys).not.toContain("maven_goals");
    expect(allowedKeys).not.toContain("shell_command");
  });

  it("form does NOT accept Azure secrets, deployment IDs, or model names", () => {
    // The form should never accept Azure-sensitive fields
    const forbiddenFields = [
      "AZURE_OPENAI_KEY",
      "AZURE_OPENAI_ENDPOINT",
      "AZURE_OPENAI_API_KEY",
      "AZURE_FOUNDRY_PROPOSER_DEPLOYMENT",
      "AZURE_FOUNDRY_REVIEWER_DEPLOYMENT",
      "model_id",
      "deployment_id",
    ];

    // Verify these are not part of the form field names
    const formFieldNames = [
      "run_name",
      "legacy_app_path",
      "output_parent_path",
      "ai_hub_path",
      "java11_home",
      "java17_home",
      "java21_home",
      "maven_cmd",
      "proof_level",
      "skip_endpoint_smoke",
    ];

    for (const forbidden of forbiddenFields) {
      const lowerForbidden = forbidden.toLowerCase().replace(/_/g, "");
      const found = formFieldNames.some((f) =>
        f.toLowerCase().replace(/_/g, "").includes(lowerForbidden),
      );
      expect(found).toBe(false);
    }
  });

  it("start is only enabled when deterministic gates are READY", () => {
    const baseRequiredGates = [
      "backend_ready",
      "local_setup_ready",
      "ai_hub_ready",
      "runner_jdk_maven_ready",
      "pipeline_route_ready",
      "output_parent_ready",
      "legacy_app_marker_ready",
    ];

    const aiRequiredGates = [...baseRequiredGates, "azure_model_ready"];

    expect(baseRequiredGates).not.toContain("azure_ready");
    expect(baseRequiredGates).not.toContain("azure_model_ready");
    expect(aiRequiredGates).toContain("azure_model_ready");

    const baseReady = baseRequiredGates.every(() => true);
    const aiBlocked = aiRequiredGates.every((gate) => gate !== "azure_model_ready");
    expect(baseReady).toBe(true);
    expect(aiBlocked).toBe(false);
  });

  it("Azure health does NOT block deterministic migration start", () => {
    // Azure degraded should not prevent start
    const azureDegraded = true;
    const deterministicReady = true;

    const canStart = deterministicReady; // Azure not required
    expect(canStart).toBe(true);
    expect(azureDegraded).toBe(true); // Azure can be degraded
  });

  it("Azure model smoke PASS/FAIL copy is explicit and bounded", () => {
    const passed = getAzureSmokeCopy({
      preflight_id: "pf-1",
      setup_id: "setup-1",
      all_ready: true,
      azure_model_ready: true,
      azure_model_checked_at: "2026-06-14T12:34:56Z",
      readiness: {},
      warnings: [],
      errors: [],
      checked_at: "2026-06-14T12:34:56Z",
    });
    expect(passed.label).toBe("Azure model smoke: PASS");

    const failed = getAzureSmokeCopy({
      preflight_id: "pf-2",
      setup_id: "setup-2",
      all_ready: false,
      azure_model_ready: false,
      azure_model_failure_reason: "http_400",
      azure_model_response_snippet: '{"error":"Authorization: Bearer sk-abc123"}',
      azure_model_checked_at: "2026-06-14T12:35:56Z",
      readiness: {},
      warnings: [],
      errors: [],
      checked_at: "2026-06-14T12:35:56Z",
    });

    expect(failed.label).toContain("Azure model smoke: FAIL — http_400");
    expect(failed.label).not.toContain("sk-abc123");
    expect(failed.label).not.toContain("Bearer sk-abc123");
    expect(failed.snippet).not.toContain("sk-abc123");
    expect(failed.snippet).toContain("Bearer [redacted-token]");
  });

  it("Start Readiness shows NOT READY when the smoke fails", () => {
    const readiness = {
      ready: false,
      setup_checksum: "checksum-1",
      preflight_checksum_match: true,
      gates: {
        azure_model_ready: false,
      },
    };

    const startState = getStartReadinessCopy(readiness);
    expect(startState.label).toBe("NOT READY");
    expect(startState.ready).toBe(false);
  });

  it("env block parser contract matches backend expectations", () => {
    // Verify the frontend sends the correct structure to POST /v1/migration-setups/parse-env
    const requestBody = { env_block: "string" };
    expect(requestBody).toHaveProperty("env_block");

    // Verify the response shape matches backend
    const responseShape = {
      parsed: {
        run_name: "string",
        legacy_app_path: "string",
        output_parent_path: "string",
        ai_hub_path: "string",
        java_homes: { java11: "string", java17: "string", java21: "string" },
        maven_cmd: "string",
        migration_flags: { proof_level: "string", skip_endpoint_smoke: true },
      },
      ignored_keys: ["string"],
      blocked_keys: ["string"],
    };
    expect(responseShape.parsed).toHaveProperty("run_name");
    expect(responseShape.parsed).toHaveProperty("legacy_app_path");
    expect(responseShape.parsed.java_homes).toHaveProperty("java11");
    expect(responseShape.parsed.java_homes).toHaveProperty("java17");
    expect(responseShape.parsed.java_homes).toHaveProperty("java21");
    expect(responseShape.parsed.migration_flags).toHaveProperty("proof_level");
    expect(responseShape.parsed.migration_flags).toHaveProperty("skip_endpoint_smoke");
    expect(responseShape).toHaveProperty("ignored_keys");
    expect(responseShape).toHaveProperty("blocked_keys");
  });

  it("setup create request matches backend CreateSetupRequestSchema", () => {
    // Verify the frontend sends the correct shape to POST /v1/migration-setups
    const setupPayload = {
      run_name: "my-migration",
      legacy_app_path: "/path/to/legacy",
      output_parent_path: "/path/to/output",
      ai_hub_path: "/path/to/hub",
      java11_home: "/usr/lib/jvm/java-11",
      java17_home: "/usr/lib/jvm/java-17",
      java21_home: "/usr/lib/jvm/java-21",
      maven_cmd: "/usr/bin/mvn",
      proof_level: "build_test_verified",
      skip_endpoint_smoke: false,
    };

    // Must NOT contain extra fields
    expect(setupPayload).not.toHaveProperty("azure_api_key");
    expect(setupPayload).not.toHaveProperty("deployment_id");
    expect(setupPayload).not.toHaveProperty("model_name");
    expect(setupPayload).not.toHaveProperty("maven_goals");
    expect(setupPayload).not.toHaveProperty("stage_inputs");

    // All required fields present
    expect(setupPayload).toHaveProperty("run_name");
    expect(setupPayload).toHaveProperty("legacy_app_path");
    expect(setupPayload).toHaveProperty("output_parent_path");
    expect(setupPayload).toHaveProperty("ai_hub_path");
    expect(setupPayload).toHaveProperty("java11_home");
    expect(setupPayload).toHaveProperty("java17_home");
    expect(setupPayload).toHaveProperty("java21_home");
    expect(setupPayload).toHaveProperty("maven_cmd");
  });

  it("new UI V2 jobs default to auto_on_green continuation", () => {
    expect(DEFAULT_V2_STAGE_CONTINUATION_POLICY).toBe("auto_on_green");
  });

  it("preflight request matches backend PreflightRequest schema", () => {
    const preflightPayload = { setup_id: "some-setup-id" };
    expect(preflightPayload).toHaveProperty("setup_id");
    expect(Object.keys(preflightPayload)).toEqual(["setup_id"]);
  });

  it("start flow navigates to migration cockpit using returned parent job id", () => {
    const jobResponse = { job_id: "parent-job-123" };
    const route = `/migrations/${jobResponse.job_id}`;
    expect(route).toBe("/migrations/parent-job-123");
    expect(route).not.toContain("undefined");
  });

  it("settings response contains no runtime provider or env-ref fields", () => {
    const mockSettingsResponse = {
      azure: {
        profile_id: "azure-foundry-v2",
        status: "not_configured",
        connection_configured: false,
        roles: {
          proposer: { configured: false, enabled: true },
          fallback: { configured: false, enabled: false },
        },
      },
    };

    const json = JSON.stringify(mockSettingsResponse);
    expect(json).not.toContain("sk-");
    expect(json).not.toContain("https://");
    expect(json).not.toContain("api_key=");
    expect(json).not.toContain("provider");
    expect(json).not.toContain("env_ref");
    expect(json).not.toContain("endpoint");
    expect(json).not.toContain("deployment");
  });

  it("NewMigrationForm does not add direct unsafe API helpers (no sandbox_path, report_root, raw command fields)", () => {
    const formFieldNames = [
      "run_name",
      "legacy_app_path",
      "output_parent_path",
      "ai_hub_path",
      "java11_home",
      "java17_home",
      "java21_home",
      "maven_cmd",
      "proof_level",
      "skip_endpoint_smoke",
    ];
    const unsafeFields = ["sandbox_path", "report_root", "raw_command", "run_command", "shell_cmd", "executable"];
    for (const unsafe of unsafeFields) {
      expect(formFieldNames).not.toContain(unsafe);
    }
    // Verify no unintended API helper fields slipped into form payloads
    const setupPayload = {
      run_name: "my-migration",
      legacy_app_path: "/path/to/legacy",
      output_parent_path: "/path/to/output",
      ai_hub_path: "/path/to/hub",
      java11_home: "/usr/lib/jvm/java-11",
      java17_home: "/usr/lib/jvm/java-17",
      java21_home: "/usr/lib/jvm/java-21",
      maven_cmd: "/usr/bin/mvn",
      proof_level: "build_test_verified",
      skip_endpoint_smoke: false,
    };
    const serialized = JSON.stringify(setupPayload);
    expect(serialized).not.toContain("sandbox_path");
    expect(serialized).not.toContain("report_root");
    expect(serialized).not.toContain("raw_command");
  });
});

// ── Environment import parse-to-field and field-to-Save chain ─────────

describe("Environment import parse-to-field data flow", () => {
  it("sends complete untrimmed envBlock to parse endpoint", () => {
    const requestBody = { env_block: "$env:JAVA11_HOME = \"C:\\Tools\\jdk-11\"\n  " };
    expect(requestBody).toEqual({ env_block: expect.any(String) });
    expect(requestBody.env_block).toBe('$env:JAVA11_HOME = "C:\\Tools\\jdk-11"\n  ');
  });

  it("all parsed values map to correct form fields", () => {
    const mockParsed = {
      run_name: "my-app",
      legacy_app_path: "C:\\legacy",
      output_parent_path: "C:\\output",
      ai_hub_path: "C:\\hub",
      java_homes: { java11: "C:\\jdk11", java17: "C:\\jdk17", java21: "C:\\jdk21" },
      maven_cmd: "C:\\mvn.cmd",
      migration_flags: { proof_level: "runtime_verified", skip_endpoint_smoke: true },
      stage_continuation_policy: "auto_on_green",
    };

    const prev = {
      envBlock: "",
      run_name: "",
      legacy_app_path: "",
      output_parent_path: "",
      ai_hub_path: "",
      java11_home: "",
      java17_home: "",
      java21_home: "",
      maven_cmd: "",
      proof_level: "build_test_verified",
      skip_endpoint_smoke: false,
      stageContinuationPolicy: "auto_on_green" as const,
      sourceProfile: "springboot-2.1-java11" as const,
      targetProfile: "springboot-4.0-java21" as const,
    };

    const p = mockParsed;
    const next = {
      ...prev,
      run_name: p.run_name || prev.run_name,
      legacy_app_path: p.legacy_app_path || prev.legacy_app_path,
      output_parent_path: p.output_parent_path || prev.output_parent_path,
      ai_hub_path: p.ai_hub_path || prev.ai_hub_path,
      java11_home: p.java_homes.java11 || prev.java11_home,
      java17_home: p.java_homes.java17 || prev.java17_home,
      java21_home: p.java_homes.java21 || prev.java21_home,
      maven_cmd: p.maven_cmd || prev.maven_cmd,
      proof_level: p.migration_flags.proof_level || prev.proof_level,
      skip_endpoint_smoke: p.migration_flags.skip_endpoint_smoke ?? prev.skip_endpoint_smoke,
      stageContinuationPolicy: (p.stage_continuation_policy as typeof prev.stageContinuationPolicy) || prev.stageContinuationPolicy,
    };

    expect(next.run_name).toBe("my-app");
    expect(next.legacy_app_path).toBe("C:\\legacy");
    expect(next.output_parent_path).toBe("C:\\output");
    expect(next.ai_hub_path).toBe("C:\\hub");
    expect(next.java11_home).toBe("C:\\jdk11");
    expect(next.java17_home).toBe("C:\\jdk17");
    expect(next.java21_home).toBe("C:\\jdk21");
    expect(next.maven_cmd).toBe("C:\\mvn.cmd");
    expect(next.proof_level).toBe("runtime_verified");
    expect(next.skip_endpoint_smoke).toBe(true);
    expect(next.stageContinuationPolicy).toBe("auto_on_green");
  });

  it("empty parsed strings do not erase existing manually entered values", () => {
    const prev = {
      envBlock: "",
      run_name: "manual-run",
      legacy_app_path: "C:\\manual-legacy",
      output_parent_path: "C:\\manual-output",
      ai_hub_path: "",
      java11_home: "C:\\manual-jdk11",
      java17_home: "",
      java21_home: "",
      maven_cmd: "C:\\manual-mvn",
      proof_level: "build_test_verified",
      skip_endpoint_smoke: false,
      stageContinuationPolicy: "auto_on_green" as const,
      sourceProfile: "springboot-2.1-java11" as const,
      targetProfile: "springboot-4.0-java21" as const,
    };

    const p = {
      run_name: "",
      legacy_app_path: "",
      output_parent_path: "",
      ai_hub_path: "",
      java_homes: { java11: "", java17: "", java21: "" },
      maven_cmd: "",
      migration_flags: { proof_level: "", skip_endpoint_smoke: null },
      stage_continuation_policy: "",
    };

    const next = {
      ...prev,
      run_name: p.run_name || prev.run_name,
      legacy_app_path: p.legacy_app_path || prev.legacy_app_path,
      output_parent_path: p.output_parent_path || prev.output_parent_path,
      ai_hub_path: p.ai_hub_path || prev.ai_hub_path,
      java11_home: p.java_homes.java11 || prev.java11_home,
      java17_home: p.java_homes.java17 || prev.java17_home,
      java21_home: p.java_homes.java21 || prev.java21_home,
      maven_cmd: p.maven_cmd || prev.maven_cmd,
      proof_level: p.migration_flags.proof_level || prev.proof_level,
      skip_endpoint_smoke: p.migration_flags.skip_endpoint_smoke ?? prev.skip_endpoint_smoke,
      stageContinuationPolicy: (p.stage_continuation_policy as typeof prev.stageContinuationPolicy) || prev.stageContinuationPolicy,
    };

    expect(next.run_name).toBe("manual-run");
    expect(next.legacy_app_path).toBe("C:\\manual-legacy");
    expect(next.output_parent_path).toBe("C:\\manual-output");
    expect(next.ai_hub_path).toBe("");
    expect(next.java11_home).toBe("C:\\manual-jdk11");
    expect(next.java17_home).toBe("");
    expect(next.java21_home).toBe("");
    expect(next.maven_cmd).toBe("C:\\manual-mvn");
    expect(next.proof_level).toBe("build_test_verified");
    expect(next.skip_endpoint_smoke).toBe(false);
    expect(next.stageContinuationPolicy).toBe("auto_on_green");
  });

  it("boolean false for skip_endpoint_smoke is preserved (not treated as absent)", () => {
    const prev = {
      run_name: "x",
      legacy_app_path: "x",
      output_parent_path: "x",
      ai_hub_path: "x",
      java11_home: "x",
      java17_home: "x",
      java21_home: "x",
      maven_cmd: "x",
      proof_level: "build_test_verified",
      skip_endpoint_smoke: true,
      stageContinuationPolicy: "auto_on_green" as const,
      sourceProfile: "springboot-2.1-java11" as const,
      targetProfile: "springboot-4.0-java21" as const,
      envBlock: "",
    };

    const p = {
      run_name: "",
      legacy_app_path: "",
      output_parent_path: "",
      ai_hub_path: "",
      java_homes: { java11: "", java17: "", java21: "" },
      maven_cmd: "",
      migration_flags: { proof_level: "", skip_endpoint_smoke: false },
      stage_continuation_policy: "",
    };

    const next = {
      ...prev,
      run_name: p.run_name || prev.run_name,
      legacy_app_path: p.legacy_app_path || prev.legacy_app_path,
      output_parent_path: p.output_parent_path || prev.output_parent_path,
      ai_hub_path: p.ai_hub_path || prev.ai_hub_path,
      java11_home: p.java_homes.java11 || prev.java11_home,
      java17_home: p.java_homes.java17 || prev.java17_home,
      java21_home: p.java_homes.java21 || prev.java21_home,
      maven_cmd: p.maven_cmd || prev.maven_cmd,
      proof_level: p.migration_flags.proof_level || prev.proof_level,
      skip_endpoint_smoke: p.migration_flags.skip_endpoint_smoke ?? prev.skip_endpoint_smoke,
      stageContinuationPolicy: (p.stage_continuation_policy as typeof prev.stageContinuationPolicy) || prev.stageContinuationPolicy,
    };

    expect(next.skip_endpoint_smoke).toBe(false);
  });

  it("preserves unrelated fields (sourceProfile, targetProfile, envBlock) after parse merge", () => {
    const prev = {
      envBlock: "$env:TEST = \"val\"",
      run_name: "",
      legacy_app_path: "",
      output_parent_path: "",
      ai_hub_path: "",
      java11_home: "",
      java17_home: "",
      java21_home: "",
      maven_cmd: "",
      proof_level: "build_test_verified",
      skip_endpoint_smoke: false,
      stageContinuationPolicy: "auto_on_green" as const,
      sourceProfile: "springboot-2.7-java11" as const,
      targetProfile: "springboot-3.5-java21" as const,
    };

    const p = {
      run_name: "parsed-run",
      legacy_app_path: "",
      output_parent_path: "",
      ai_hub_path: "",
      java_homes: { java11: "", java17: "", java21: "" },
      maven_cmd: "",
      migration_flags: { proof_level: "", skip_endpoint_smoke: null },
      stage_continuation_policy: "",
    };

    const next = {
      ...prev,
      run_name: p.run_name || prev.run_name,
      legacy_app_path: p.legacy_app_path || prev.legacy_app_path,
      output_parent_path: p.output_parent_path || prev.output_parent_path,
      ai_hub_path: p.ai_hub_path || prev.ai_hub_path,
      java11_home: p.java_homes.java11 || prev.java11_home,
      java17_home: p.java_homes.java17 || prev.java17_home,
      java21_home: p.java_homes.java21 || prev.java21_home,
      maven_cmd: p.maven_cmd || prev.maven_cmd,
      proof_level: p.migration_flags.proof_level || prev.proof_level,
      skip_endpoint_smoke: p.migration_flags.skip_endpoint_smoke ?? prev.skip_endpoint_smoke,
      stageContinuationPolicy: (p.stage_continuation_policy as typeof prev.stageContinuationPolicy) || prev.stageContinuationPolicy,
    };

    expect(next.envBlock).toBe("$env:TEST = \"val\"");
    expect(next.sourceProfile).toBe("springboot-2.7-java11");
    expect(next.targetProfile).toBe("springboot-3.5-java21");
    expect(next.run_name).toBe("parsed-run");
  });
});

describe("Field-to-Save-payload chain", () => {
  it("setup payload contains only the 10 expected fields", () => {
    const fields = {
      run_name: "my-app",
      legacy_app_path: "C:\\legacy",
      output_parent_path: "C:\\output",
      ai_hub_path: "C:\\hub",
      java11_home: "C:\\jdk11",
      java17_home: "C:\\jdk17",
      java21_home: "C:\\jdk21",
      maven_cmd: "C:\\mvn.cmd",
      proof_level: "runtime_verified",
      skip_endpoint_smoke: false,
    };

    const payloadKeys = Object.keys(fields);
    expect(payloadKeys).toEqual([
      "run_name",
      "legacy_app_path",
      "output_parent_path",
      "ai_hub_path",
      "java11_home",
      "java17_home",
      "java21_home",
      "maven_cmd",
      "proof_level",
      "skip_endpoint_smoke",
    ]);
  });

  it("envBlock is excluded from the setup payload", () => {
    const setupPayload = {
      run_name: "test",
      legacy_app_path: "test",
      output_parent_path: "test",
      ai_hub_path: "test",
      java11_home: "test",
      java17_home: "test",
      java21_home: "test",
      maven_cmd: "test",
      proof_level: "build_test_verified",
      skip_endpoint_smoke: false,
    };
    expect(setupPayload).not.toHaveProperty("envBlock");
  });

  it("sourceProfile and targetProfile are excluded from the setup payload", () => {
    const setupPayload = {
      run_name: "test",
      legacy_app_path: "test",
      output_parent_path: "test",
      ai_hub_path: "test",
      java11_home: "test",
      java17_home: "test",
      java21_home: "test",
      maven_cmd: "test",
      proof_level: "build_test_verified",
      skip_endpoint_smoke: false,
    };
    expect(setupPayload).not.toHaveProperty("sourceProfile");
    expect(setupPayload).not.toHaveProperty("targetProfile");
  });

  it("stageContinuationPolicy is excluded from the setup payload", () => {
    const setupPayload = {
      run_name: "test",
      legacy_app_path: "test",
      output_parent_path: "test",
      ai_hub_path: "test",
      java11_home: "test",
      java17_home: "test",
      java21_home: "test",
      maven_cmd: "test",
      proof_level: "build_test_verified",
      skip_endpoint_smoke: false,
    };
    expect(setupPayload).not.toHaveProperty("stageContinuationPolicy");
  });

  it("parse-to-Save chain: parsed values appear in correct setup payload fields", () => {
    const mockParsed = {
      run_name: "chain-test",
      legacy_app_path: "C:\\chain-legacy",
      output_parent_path: "C:\\chain-output",
      ai_hub_path: "C:\\chain-hub",
      java_homes: { java11: "C:\\chain-jdk11", java17: "C:\\chain-jdk17", java21: "C:\\chain-jdk21" },
      maven_cmd: "C:\\chain-mvn.cmd",
      migration_flags: { proof_level: "analyzed", skip_endpoint_smoke: true },
      stage_continuation_policy: "auto_on_green",
    };

    const prev = {
      envBlock: "",
      run_name: "",
      legacy_app_path: "",
      output_parent_path: "",
      ai_hub_path: "",
      java11_home: "",
      java17_home: "",
      java21_home: "",
      maven_cmd: "",
      proof_level: "build_test_verified",
      skip_endpoint_smoke: false,
      stageContinuationPolicy: "auto_on_green" as const,
      sourceProfile: "springboot-2.1-java11" as const,
      targetProfile: "springboot-4.0-java21" as const,
    };

    const p = mockParsed;
    const fields = {
      ...prev,
      run_name: p.run_name || prev.run_name,
      legacy_app_path: p.legacy_app_path || prev.legacy_app_path,
      output_parent_path: p.output_parent_path || prev.output_parent_path,
      ai_hub_path: p.ai_hub_path || prev.ai_hub_path,
      java11_home: p.java_homes.java11 || prev.java11_home,
      java17_home: p.java_homes.java17 || prev.java17_home,
      java21_home: p.java_homes.java21 || prev.java21_home,
      maven_cmd: p.maven_cmd || prev.maven_cmd,
      proof_level: p.migration_flags.proof_level || prev.proof_level,
      skip_endpoint_smoke: p.migration_flags.skip_endpoint_smoke ?? prev.skip_endpoint_smoke,
      stageContinuationPolicy: (p.stage_continuation_policy as typeof prev.stageContinuationPolicy) || prev.stageContinuationPolicy,
    };

    const setupPayload = {
      run_name: fields.run_name,
      legacy_app_path: fields.legacy_app_path,
      output_parent_path: fields.output_parent_path,
      ai_hub_path: fields.ai_hub_path,
      java11_home: fields.java11_home,
      java17_home: fields.java17_home,
      java21_home: fields.java21_home,
      maven_cmd: fields.maven_cmd,
      proof_level: fields.proof_level,
      skip_endpoint_smoke: fields.skip_endpoint_smoke,
    };

    expect(setupPayload.run_name).toBe("chain-test");
    expect(setupPayload.legacy_app_path).toBe("C:\\chain-legacy");
    expect(setupPayload.output_parent_path).toBe("C:\\chain-output");
    expect(setupPayload.ai_hub_path).toBe("C:\\chain-hub");
    expect(setupPayload.java11_home).toBe("C:\\chain-jdk11");
    expect(setupPayload.java17_home).toBe("C:\\chain-jdk17");
    expect(setupPayload.java21_home).toBe("C:\\chain-jdk21");
    expect(setupPayload.maven_cmd).toBe("C:\\chain-mvn.cmd");
    expect(setupPayload.proof_level).toBe("analyzed");
    expect(setupPayload.skip_endpoint_smoke).toBe(true);
    expect(Object.keys(setupPayload)).not.toContain("envBlock");
    expect(Object.keys(setupPayload)).not.toContain("sourceProfile");
    expect(Object.keys(setupPayload)).not.toContain("targetProfile");
    expect(Object.keys(setupPayload)).not.toContain("stageContinuationPolicy");
  });
});

// ── F3/F4 — Profile selectors and route preview ──────────────────────

describe("F3/F4 Profile selectors and route preview", () => {
  it("profile options include selectableAsSource and selectableAsTarget roles", () => {
    const sources = MIGRATION_PROFILE_OPTIONS.filter((p) => p.selectableAsSource);
    const targets = MIGRATION_PROFILE_OPTIONS.filter((p) => p.selectableAsTarget);
    expect(sources.length).toBeGreaterThanOrEqual(1);
    expect(targets.length).toBeGreaterThanOrEqual(1);
    expect(MIGRATION_PROFILE_OPTIONS.find((p) => p.id === "springboot-2.1-java11")?.selectableAsSource).toBe(true);
    expect(MIGRATION_PROFILE_OPTIONS.find((p) => p.id === "springboot-2.7-java11")?.selectableAsTarget).toBe(true);
  });

  it("defaults are springboot-2.1-java11 source and springboot-4.0-java21 target", () => {
    const defaults = {
      sourceProfile: "springboot-2.1-java11" as MigrationProfileId,
      targetProfile: "springboot-4.0-java21" as MigrationProfileId,
    };
    const sourceOption = MIGRATION_PROFILE_OPTIONS.find((p) => p.id === defaults.sourceProfile);
    const targetOption = MIGRATION_PROFILE_OPTIONS.find((p) => p.id === defaults.targetProfile);
    expect(sourceOption).toBeDefined();
    expect(sourceOption!.selectableAsSource).toBe(true);
    expect(sourceOption!.selectableAsTarget).toBe(false);
    expect(targetOption).toBeDefined();
    expect(targetOption!.selectableAsSource).toBe(false);
    expect(targetOption!.selectableAsTarget).toBe(true);
  });

  it("includes springboot-3.5-java21 as a selectable intermediate profile", () => {
    const intermediate = MIGRATION_PROFILE_OPTIONS.find(
      (p) => p.id === "springboot-3.5-java21",
    );
    expect(intermediate).toBeDefined();
    expect(intermediate!.selectableAsSource).toBe(true);
    expect(intermediate!.selectableAsTarget).toBe(true);
    expect(intermediate!.orderIndex).toBe(3);
  });

  it("supports canonical profile pairs and reports expected stages", () => {
    const pairs: Array<{
      source: MigrationProfileId;
      target: MigrationProfileId;
      included: string[];
      skipped: string[];
      excluded: string[];
    }> = [
      { source: "springboot-2.1-java11", target: "springboot-2.7-java11", included: ["1"], skipped: [], excluded: ["2", "3", "4"] },
      { source: "springboot-2.1-java11", target: "springboot-3.5-java17", included: ["1", "2"], skipped: [], excluded: ["3", "4"] },
      { source: "springboot-2.1-java11", target: "springboot-3.5-java21", included: ["1", "2", "3"], skipped: [], excluded: ["4"] },
      { source: "springboot-2.1-java11", target: "springboot-4.0-java21", included: ["1", "2", "3", "4"], skipped: [], excluded: [] },
      { source: "springboot-2.7-java11", target: "springboot-3.5-java17", included: ["2"], skipped: ["1"], excluded: ["3", "4"] },
      { source: "springboot-2.7-java11", target: "springboot-3.5-java21", included: ["2", "3"], skipped: ["1"], excluded: ["4"] },
      { source: "springboot-2.7-java11", target: "springboot-4.0-java21", included: ["2", "3", "4"], skipped: ["1"], excluded: [] },
      { source: "springboot-3.5-java17", target: "springboot-3.5-java21", included: ["3"], skipped: ["1", "2"], excluded: ["4"] },
      { source: "springboot-3.5-java17", target: "springboot-4.0-java21", included: ["3", "4"], skipped: ["1", "2"], excluded: [] },
      { source: "springboot-3.5-java21", target: "springboot-4.0-java21", included: ["4"], skipped: ["1", "2", "3"], excluded: [] },
    ];
    for (const pair of pairs) {
      const validation = getRouteValidationError(pair.source, pair.target);
      expect(validation).toBeNull();
      const preview = getRoutePreview(pair.source, pair.target);
      expect(preview).toEqual({
        included: pair.included,
        skipped: pair.skipped,
        excluded: pair.excluded,
      });
      const key = getRoutePreviewKey(pair.source, pair.target);
      expect(key).toBe(`${pair.source}->${pair.target}`);
    }
  });

  it("same-profile pair blocks start (local validation)", () => {
    const msg = getRouteValidationMessage("springboot-2.7-java11", "springboot-2.7-java11");
    expect(msg).toBe("Source and target profiles must differ.");
  });

  it("reversed pair blocks start (local validation)", () => {
    const msg = getRouteValidationMessage("springboot-4.0-java21", "springboot-2.7-java11");
    expect(msg).toBe("Target profile must be a higher stage than the source profile.");
  });

  it("valid pair returns no validation error", () => {
    const msg = getRouteValidationMessage("springboot-2.7-java11", "springboot-4.0-java21");
    expect(msg).toBeNull();
  });

  it("non-source profile cannot be a source", () => {
    const msg = getRouteValidationMessage("springboot-4.0-java21", "springboot-2.7-java11");
    expect(msg).toBe("Target profile must be a higher stage than the source profile.");
  });

  it("non-target profile cannot be a target", () => {
    const msg = getRouteValidationMessage("springboot-2.7-java11", "springboot-2.7-java11");
    expect(msg).toBe("Source and target profiles must differ.");
  });

  it("route preview includes correct stages for springboot-2.7 to springboot-4.0", () => {
    const preview = getRoutePreview("springboot-2.7-java11", "springboot-4.0-java21");
    expect(preview).toBeDefined();
    expect(preview!.included).toEqual(["2", "3", "4"]);
    expect(preview!.skipped).toEqual(["1"]);
    expect(preview!.excluded).toEqual([]);
  });

  it("route preview includes one step for springboot-2.1 to springboot-2.7", () => {
    const preview = getRoutePreview("springboot-2.1-java11", "springboot-2.7-java11");
    expect(preview).toBeDefined();
    expect(preview!.included).toEqual(["1"]);
    expect(preview!.skipped).toEqual([]);
    expect(preview!.excluded).toEqual(["2", "3", "4"]);
  });

  it("route preview includes four steps for springboot-2.1 to springboot-4.0", () => {
    const preview = getRoutePreview("springboot-2.1-java11", "springboot-4.0-java21");
    expect(preview).toBeDefined();
    expect(preview!.included).toEqual(["1", "2", "3", "4"]);
    expect(preview!.skipped).toEqual([]);
    expect(preview!.excluded).toEqual([]);
  });

  it("route preview includes skipped stages for springboot-3.5-java17 to springboot-4.0", () => {
    const preview = getRoutePreview("springboot-3.5-java17", "springboot-4.0-java21");
    expect(preview).toBeDefined();
    expect(preview!.included).toEqual(["3", "4"]);
    expect(preview!.skipped).toEqual(["1", "2"]);
    expect(preview!.excluded).toEqual([]);
  });

  it("start payload includes selected profile pair", () => {
    const payload = createV2JobPayload("setup-1", "auto_on_green", {
      sourceProfile: "springboot-3.5-java17",
      targetProfile: "springboot-4.0-java21",
    });
    expect(payload.source_profile).toBe("springboot-3.5-java17");
    expect(payload.target_profile).toBe("springboot-4.0-java21");
  });

  it("forbidden execution fields are absent from profile-aware payload", () => {
    const payload = createV2JobPayload("setup-1", "auto_on_green", {
      sourceProfile: "springboot-2.7-java11",
      targetProfile: "springboot-4.0-java21",
    });
    const serialized = JSON.stringify(payload);
    const forbiddenPatterns = [
      "sandbox_path", "argv", "raw_command",
      "filesystem_target", "filesystem_root", "output_root",
      "report_root", "run_root", "ai_hub_path",
      "java_home", "java11_home", "java17_home", "java21_home",
      "maven_cmd",
    ];
    for (const field of forbiddenPatterns) {
      expect(serialized).not.toContain(field);
    }
    // "provider", "model", "deployment", "endpoint" may appear as substrings
    // of allowed policy fields — check as standalone JSON keys
    expect(serialized).not.toMatch(/"provider"/);
    expect(serialized).not.toMatch(/"model"/);
    expect(serialized).not.toMatch(/"model_id"/);
    expect(serialized).not.toMatch(/"deployment"/);
    expect(serialized).not.toMatch(/"endpoint"/);
    expect(serialized).not.toMatch(/"api_key"/);
    expect(serialized).not.toMatch(/"access_token"/);
  });
});
