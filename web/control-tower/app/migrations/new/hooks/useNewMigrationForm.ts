"use client";

import { useState, useCallback } from "react";
import { useRouter } from "next/navigation";

import {
  createV2JobPayload,
  DEFAULT_V2_STAGE_CONTINUATION_POLICY,
  type V2StageContinuationPolicy,
} from "../../../../lib/controlTowerApi";
import {
  MIGRATION_PROFILE_OPTIONS,
  getRoutePreview,
  getRouteValidationError,
  type MigrationProfileId,
} from "../../../../lib/contracts";

// ── Types ──────────────────────────────────────────────────────────

export interface ParsedEnvResult {
  parsed: {
    run_name: string;
    legacy_app_path: string;
    output_parent_path: string;
    ai_hub_path: string;
    java_homes: { java11: string; java17: string; java21: string };
    maven_cmd: string;
    migration_flags: { proof_level: string; skip_endpoint_smoke: boolean | null };
    stage_continuation_policy: string;
  };
  ignored_keys: string[];
  blocked_keys: string[];
}

export interface SetupResponse {
  setup_id: string;
  run_name: string;
  legacy_app_path: string;
  output_parent_path: string;
  ai_hub_path: string;
  java_homes: { java11: string; java17: string; java21: string };
  maven_cmd: string;
  proof_level: string;
  skip_endpoint_smoke: boolean;
  migration_flags: Record<string, unknown>;
  setup_checksum: string;
  created_at: string;
}

export interface PreflightResponse {
  preflight_id: string;
  setup_id: string;
  all_ready: boolean;
  azure_model_ready?: boolean;
  azure_model_failure_reason?: string;
  azure_model_response_snippet?: string;
  azure_model_checked_at?: string;
  readiness: Record<string, boolean>;
  warnings: string[];
  errors: string[];
  checked_at: string;
}

export interface ReadinessResponse {
  ready: boolean;
  setup_checksum: string;
  preflight_checksum_match: boolean;
  gates: Record<string, boolean>;
}

export interface SettingsResponse {
  azure: {
    profile_id: string;
    status: string;
    connection_configured: boolean;
    roles: Record<string, { configured: boolean; enabled: boolean }>;
  };
  local_mode: {
    enabled: boolean;
    allowed_source_roots: string[];
    allowed_output_roots: string[];
  };
}

// ── Form state types ────────────────────────────────────────────────

export interface FormFields {
  envBlock: string;
  run_name: string;
  legacy_app_path: string;
  output_parent_path: string;
  ai_hub_path: string;
  java11_home: string;
  java17_home: string;
  java21_home: string;
  maven_cmd: string;
  proof_level: string;
  skip_endpoint_smoke: boolean;
  stageContinuationPolicy: V2StageContinuationPolicy;
  sourceProfile: MigrationProfileId;
  targetProfile: MigrationProfileId;
}

export const EMPTY_FIELDS: FormFields = {
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
  stageContinuationPolicy: DEFAULT_V2_STAGE_CONTINUATION_POLICY,
  sourceProfile: "springboot-2.7-java11",
  targetProfile: "springboot-3.5-java17",
};

// ── API helpers ────────────────────────────────────────────────────

const API_BASE = process.env.NEXT_PUBLIC_API_BASE_URL || "http://127.0.0.1:8000";

function mutationHeaders(): Record<string, string> {
  return {
    "Content-Type": "application/json",
    Origin: "http://127.0.0.1:3000",
    "X-Control-Tower-Client": "control-tower-frontend",
  };
}

async function parseEnv(envBlock: string): Promise<ParsedEnvResult> {
  const res = await fetch(`${API_BASE}/v1/migration-setups/parse-env`, {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({ env_block: envBlock }),
  });
  if (!res.ok) throw new Error(`Parse failed: ${res.status}`);
  return res.json();
}

async function fetchSettings(): Promise<SettingsResponse> {
  const res = await fetch(`${API_BASE}/v1/settings/ai`, {
    headers: { Host: "127.0.0.1:8000" },
  });
  if (!res.ok) throw new Error(`Settings failed: ${res.status}`);
  return res.json();
}

async function createSetup(payload: Record<string, unknown>): Promise<SetupResponse> {
  const res = await fetch(`${API_BASE}/v1/migration-setups`, {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify(payload),
  });
  if (!res.ok) throw new Error(`Create setup failed: ${res.status}`);
  return res.json();
}

async function runPreflight(setupId: string): Promise<PreflightResponse> {
  const res = await fetch(`${API_BASE}/v1/migration-setups/preflight`, {
    method: "POST",
    headers: mutationHeaders(),
    body: JSON.stringify({ setup_id: setupId }),
  });
  if (!res.ok) throw new Error(`Preflight failed: ${res.status}`);
  return res.json();
}

async function fetchReadiness(setupId: string): Promise<ReadinessResponse> {
  const res = await fetch(`${API_BASE}/v1/migration-setups/${setupId}/readiness`, {
    headers: { Host: "127.0.0.1:8000" },
  });
  if (!res.ok) throw new Error(`Readiness failed: ${res.status}`);
  return res.json();
}

// ── Display helpers (also exported for tests) ─────────────────────

export function getRouteValidationMessage(
  source: MigrationProfileId,
  target: MigrationProfileId,
): string | null {
  return getRouteValidationError(source, target);
}

export function getStartReadinessCopy(
  readiness: ReadinessResponse | null,
): { label: string; ready: boolean } {
  const ready = readiness?.ready === true && readiness?.preflight_checksum_match === true;
  return {
    label: ready ? "READY" : "NOT READY",
    ready,
  };
}

function sanitizeSmokeSnippet(value: string): string {
  return value
    .replace(/sk-[A-Za-z0-9_-]+/g, "[redacted-token]")
    .replace(/Bearer\s+[A-Za-z0-9._-]+/gi, "Bearer [redacted-token]")
    .slice(0, 240);
}

export function getAzureSmokeCopy(
  preflight: PreflightResponse | null,
): { label: string; checkedAt: string; failureReason: string; snippet: string } {
  if (!preflight || preflight.azure_model_ready === undefined) {
    return {
      label: "Azure model smoke: not run",
      checkedAt: "",
      failureReason: "",
      snippet: "",
    };
  }

  if (preflight.azure_model_ready) {
    return {
      label: preflight.azure_model_checked_at ? "Azure model smoke: PASS" : "Azure model smoke: PASS (skipped)",
      checkedAt: preflight.checked_at,
      failureReason: "",
      snippet: "",
    };
  }

  const reason = preflight.azure_model_failure_reason || "invalid_response";
  const snippet = preflight.azure_model_response_snippet
    ? sanitizeSmokeSnippet(preflight.azure_model_response_snippet)
    : "";
  const suffix = snippet ? ` — ${snippet}` : "";
  return {
    label: `Azure model smoke: FAIL — ${reason}${suffix}`,
    checkedAt: preflight.checked_at,
    failureReason: reason,
    snippet,
  };
}

// ── Hook ──────────────────────────────────────────────────────────

export function useNewMigrationForm() {
  const router = useRouter();
  const [fields, setFields] = useState<FormFields>(EMPTY_FIELDS);
  const [parseResult, setParseResult] = useState<ParsedEnvResult | null>(null);
  const [setupResult, setSetupResult] = useState<SetupResponse | null>(null);
  const [preflight, setPreflight] = useState<PreflightResponse | null>(null);
  const [readiness, setReadiness] = useState<ReadinessResponse | null>(null);
  const [azureSettings, setAzureSettings] = useState<SettingsResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState<string | null>(null);

  const updateField = useCallback(
    (key: string, value: string | boolean) => {
      setFields((prev) => ({ ...prev, [key]: value }));
    },
    [],
  );

  const handleParseEnv = useCallback(async () => {
    if (!fields.envBlock.trim()) return;
    setLoading("Parsing env block...");
    setError(null);
    try {
      const result = await parseEnv(fields.envBlock);
      setParseResult(result);
      if (result.blocked_keys.length > 0) {
        setError(`Blocked keys detected: ${result.blocked_keys.join(", ")}. These were not parsed.`);
      }
      const p = result.parsed;
      setFields((prev) => ({
        ...prev,
        run_name: p.run_name || prev.run_name,
        legacy_app_path: p.legacy_app_path || prev.legacy_app_path,
        output_parent_path: p.output_parent_path || prev.output_parent_path,
        ai_hub_path: p.ai_hub_path || prev.ai_hub_path,
        java11_home: p.java_homes?.java11 || prev.java11_home,
        java17_home: p.java_homes?.java17 || prev.java17_home,
        java21_home: p.java_homes?.java21 || prev.java21_home,
        maven_cmd: p.maven_cmd || prev.maven_cmd,
        proof_level: p.migration_flags?.proof_level || prev.proof_level,
        skip_endpoint_smoke: p.migration_flags?.skip_endpoint_smoke ?? prev.skip_endpoint_smoke,
        stageContinuationPolicy: (p.stage_continuation_policy as V2StageContinuationPolicy) || prev.stageContinuationPolicy,
      }));
    } catch (e) {
      setError(e instanceof Error ? e.message : "Parse failed");
    } finally {
      setLoading(null);
    }
  }, [fields.envBlock]);

  const handleSaveSetup = useCallback(async () => {
    setLoading("Saving setup...");
    setError(null);
    try {
      const result = await createSetup({
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
      });
      setSetupResult(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Save failed");
    } finally {
      setLoading(null);
    }
  }, [fields]);

  const handleRunPreflight = useCallback(async () => {
    if (!setupResult?.setup_id) return;
    setLoading("Running preflight...");
    setError(null);
    try {
      const result = await runPreflight(setupResult.setup_id);
      setPreflight(result);
      const readinessResult = await fetchReadiness(setupResult.setup_id);
      setReadiness(readinessResult);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Preflight failed");
    } finally {
      setLoading(null);
    }
  }, [setupResult?.setup_id]);

  const handleLoadSettings = useCallback(async () => {
    setLoading("Loading settings...");
    setError(null);
    try {
      const result = await fetchSettings();
      setAzureSettings(result);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Settings load failed");
    } finally {
      setLoading(null);
    }
  }, []);

  const handleStart = useCallback(async () => {
    if (!setupResult) return;
    setLoading("Starting migration...");
    setError(null);
    try {
      const jobPayload = createV2JobPayload(
        setupResult.setup_id,
        fields.stageContinuationPolicy || DEFAULT_V2_STAGE_CONTINUATION_POLICY,
        {
          sourceProfile: fields.sourceProfile,
          targetProfile: fields.targetProfile,
        },
      );
      const jobRes = await fetch(`${API_BASE}/v1/v2/migration-jobs`, {
        method: "POST",
        headers: mutationHeaders(),
        body: JSON.stringify(jobPayload),
      });
      if (!jobRes.ok) throw new Error(`Job creation failed: ${jobRes.status}`);
      const jobData = await jobRes.json();

      const stageRes = await fetch(`${API_BASE}/v1/v2/migration-jobs/start-stage1`, {
        method: "POST",
        headers: mutationHeaders(),
        body: JSON.stringify({
          job_id: jobData.job_id,
          setup_id: setupResult.setup_id,
        }),
      });
      if (!stageRes.ok) throw new Error(`Stage 1 start failed: ${stageRes.status}`);

      router.push(`/migrations/${jobData.job_id}`);
    } catch (e) {
      setError(e instanceof Error ? e.message : "Start failed");
    } finally {
      setLoading(null);
    }
  }, [setupResult, fields, router]);

  const startState = getStartReadinessCopy(readiness);
  const azureSmokeCopy = getAzureSmokeCopy(preflight);
  const startEnabled = startState.ready;
  const routeValidationError = getRouteValidationMessage(fields.sourceProfile, fields.targetProfile);
  const routePreview = routeValidationError ? null : getRoutePreview(fields.sourceProfile, fields.targetProfile);

  return {
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
    setError,
  };
}
