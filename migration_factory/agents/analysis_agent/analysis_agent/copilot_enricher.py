import os
import json
import datetime
import copy
import subprocess
from dataclasses import dataclass

try:
    from migration_factory.agents.planning_agent.assist_config import load_planning_assist_config
except Exception:
    load_planning_assist_config = None


class CopilotConfigError(Exception):
    """Raised when Copilot configuration invalid."""


class CopilotAuthResolver:
    SUPPORTED_AUTH_MODES = {"github_signed_in_user", "oauth_github_app", "token"}

    @staticmethod
    def resolve_auth_mode(config=None):
        mode = (
            os.environ.get("AIMF_COPILOT_AUTH_MODE", "").strip()
            or os.environ.get("MF_PLANNING_ASSIST_AUTH_MODE", "").strip()
            or getattr(config, "auth_mode", "github_signed_in_user")
        )
        if mode not in CopilotAuthResolver.SUPPORTED_AUTH_MODES:
            raise CopilotConfigError(f"Unsupported auth mode: {mode}")
        return mode

    @staticmethod
    def _gh_auth_ready():
        try:
            completed = subprocess.run(
                ["gh", "auth", "status"],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
        except Exception:
            return False
        return completed.returncode == 0

    @staticmethod
    def get_token(auth_mode, config=None):
        if auth_mode == "github_signed_in_user":
            if not CopilotAuthResolver._gh_auth_ready():
                raise PermissionError("Auth Resolver : GitHub CLI signed-in user auth is not ready.")
            return None

        if auth_mode == "oauth_github_app":
            token = (
                os.environ.get("AIMF_GITHUB_APP_OAUTH_TOKEN", "").strip()
                or os.environ.get("MF_PLANNING_ASSIST_TOKEN", "").strip()
                or os.environ.get("GITHUB_TOKEN", "").strip()
                or os.environ.get("GH_TOKEN", "").strip()
            )
            if not token:
                raise PermissionError("Auth Resolver : oauth_github_app token missing.")
            return token

        if auth_mode == "token":
            for name in getattr(config, "token_env_vars", ()):
                token = os.environ.get(name, "").strip()
                if token:
                    return token
            raise PermissionError("Auth Resolver : token missing.")

        raise CopilotConfigError(f"Unsupported auth mode: {auth_mode}")


@dataclass(frozen=True)
class ModelResolutionResult:
    model: str
    source: str
    requested_model: str
    model_verified: bool = False


class ModelResolver:
    @staticmethod
    def _normalize(model):
        return (model or "").strip().lower()

    @staticmethod
    def _ensure_allowed(model, config=None):
        allowed = {
            ModelResolver._normalize(item)
            for item in getattr(config, "allowed_models", ())
            if ModelResolver._normalize(item)
        }
        if not allowed:
            allowed = {"gpt-5-mini", "gpt-4.1", "gpt-4o", "gpt-4o-mini"}
        if not model or model not in allowed:
            raise CopilotConfigError(
                f"model_unavailable: Analysis assist model is not allowed: {model or None}."
            )

    @staticmethod
    def resolve(config=None):
        analysis_override = ModelResolver._normalize(os.environ.get("AIMF_ANALYSIS_COPILOT_MODEL", ""))
        if analysis_override:
            ModelResolver._ensure_allowed(analysis_override, config)
            return ModelResolutionResult(analysis_override, "env_override", analysis_override)

        fallback = ModelResolver._normalize(os.environ.get("COPILOT_ANALYSIS_MODEL", ""))
        if fallback:
            ModelResolver._ensure_allowed(fallback, config)
            return ModelResolutionResult(fallback, "env_override", fallback)

        phase_overrides = getattr(config, "phase_model_overrides", None) or {}
        configured = ModelResolver._normalize(phase_overrides.get("analysis"))
        if configured:
            ModelResolver._ensure_allowed(configured, config)
            return ModelResolutionResult(configured, "phase_override", configured)

        configured = ModelResolver._normalize(getattr(config, "default_model", ""))
        if configured:
            ModelResolver._ensure_allowed(configured, config)
            return ModelResolutionResult(configured, "hub_default", configured)

        raise CopilotConfigError("model_unavailable: Analysis assist model is empty or missing.")


class CopilotSDKWrapper:
    """Boundary for real Copilot SDK usage for aimf-analysis-assist."""

    def __init__(self, model, token):
        self.model = model
        self.token = token

    @staticmethod
    def is_available():
        try:
            import github_copilot_sdk  # type: ignore # noqa: F401
            return True
        except Exception:
            return False

    def enrich(self, report_data):
        raise RuntimeError(
            "Copilot SDK runtime call not configured for aimf-analysis-assist in this build."
        )


class GuardrailValidator:
    _ALLOWED_ADVISORY_KEYS = {
        "risks",
        "unknowns",
        "recommendations",
        "planning_hints",
        "summary_notes",
        "confidence",
        "warnings",
    }

    _FORBIDDEN_MUTATION_PATH_HINTS = (
        "source_stack",
        "target_stack",
        "maven",
        "dependency_graph",
        "test",
        "import",
        "config",
        "module",
        "path",
    )

    @staticmethod
    def _to_json_object(ai_response):
        if isinstance(ai_response, str):
            try:
                ai_response = json.loads(ai_response)
            except json.JSONDecodeError as exc:
                raise ValueError("Guardrail Violation : Invalid Copilot JSON output.") from exc
        if not isinstance(ai_response, dict):
            raise ValueError("Guardrail Violation : Copilot output must be a JSON object.")
        return ai_response

    @staticmethod
    def _validate_confidence(value):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Guardrail Violation : Invalid confidence value.")
        if value < 0 or value > 1:
            raise ValueError("Guardrail Violation : Invalid confidence value.")

    @classmethod
    def _contains_forbidden_mutation(cls, key):
        k = key.lower()
        return any(hint in k for hint in cls._FORBIDDEN_MUTATION_PATH_HINTS)

    @classmethod
    def extract_advisory_fields(cls, ai_response):
        payload = cls._to_json_object(ai_response)
        advisory = {}
        warnings = []

        for key, value in payload.items():
            if key in cls._ALLOWED_ADVISORY_KEYS:
                if key == "confidence":
                    cls._validate_confidence(value)
                advisory[key] = value
                continue

            if cls._contains_forbidden_mutation(key):
                warnings.append(f"Guardrail ignored deterministic mutation attempt: {key}")

        return advisory, warnings

    @staticmethod
    def validate_no_tampering(original_report, enriched_report):
        if original_report.get("source_stack") != enriched_report.get("source_stack"):
            raise ValueError("Guardrail Violation : attempted to modify source_stack.")

        if original_report.get("target_stack") != enriched_report.get("target_stack"):
            raise ValueError("Guardrail Violation : attempted to modify target_stack.")

        if original_report.get("project_metadata", {}).get("import_stats") != enriched_report.get("project_metadata", {}).get("import_stats"):
            raise ValueError("Guardrail Violation : attempted to modify import_stats.")

        return True


def enrich_with_ai(context, report_data):
    original_data_backup = copy.deepcopy(report_data)
    ai_hub_path = getattr(context, "ai_hub_path", "")
    assist_config = (
        load_planning_assist_config(ai_hub_path=ai_hub_path, phase="analysis")
        if load_planning_assist_config
        else None
    )
    ai_assist_enabled = bool(getattr(assist_config, "enabled", False))
    if assist_config is None and os.environ.get("AIMF_AI_ASSIST_ENABLED", "").strip():
        ai_assist_enabled = os.environ.get("AIMF_AI_ASSIST_ENABLED", "").lower() == "true"

    assist_artifact = {
        "run_id": context.run_id,
        "status": "SKIPPED",
        "auth_mode": None,
        "model": None,
        "requested_model": None,
        "resolved_model": None,
        "model_source": None,
        "model_verified": False,
        "input_artifacts": ["analysis_report.json"],
        "suggestions_count": 0,
        "agent": "aimf-analysis-assist",
        "warnings": [],
        "timestamp": datetime.datetime.now().isoformat(),
    }

    if not ai_assist_enabled:
        report_data["ai_enrichment"]["status"] = "SKIPPED"
        assist_artifact["warnings"].append("AI assist disabled by config.")
    else:
        try:
            auth_mode = CopilotAuthResolver.resolve_auth_mode(assist_config)
            token = CopilotAuthResolver.get_token(auth_mode, assist_config)
            model_resolution = ModelResolver.resolve(assist_config)
            model = model_resolution.model

            if not model:
                raise CopilotConfigError("Model Resolver : Empty model not allowed.")

            assist_artifact["auth_mode"] = auth_mode
            assist_artifact["model"] = model
            assist_artifact["requested_model"] = model_resolution.requested_model
            assist_artifact["resolved_model"] = model_resolution.model
            assist_artifact["model_source"] = model_resolution.source
            assist_artifact["model_verified"] = model_resolution.model_verified

            raise ModuleNotFoundError(
                "adapter_unavailable: Analysis assist provider adapter is not configured."
            )

            sdk = CopilotSDKWrapper(model=model, token=token)
            ai_response = sdk.enrich(report_data)
            advisory_fields, advisory_warnings = GuardrailValidator.extract_advisory_fields(ai_response)

            report_data["ai_enrichment"].update(advisory_fields)
            report_data["ai_enrichment"]["status"] = "USED"
            GuardrailValidator.validate_no_tampering(original_data_backup, report_data)

            assist_artifact["warnings"].extend(advisory_warnings)
            assist_artifact["status"] = "USED"
            recs = report_data["ai_enrichment"].get("recommendations", [])
            assist_artifact["suggestions_count"] = len(recs) if isinstance(recs, list) else 0

        except Exception as e:
            report_data = original_data_backup
            report_data["ai_enrichment"]["status"] = "FAILED"
            assist_artifact["status"] = "FAILED"
            assist_artifact["warnings"].append(str(e))

    output_file = context.get_output_path("copilot_assist.json")
    with open(output_file, "w", encoding="utf-8") as f:
        json.dump(assist_artifact, f, indent=4)

    return report_data
