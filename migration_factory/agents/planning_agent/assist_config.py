import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from typing import Literal

AssistMode = Literal["assist_only"]
AssistProvider = Literal["github_copilot"]
CopilotSdkMode = Literal["suggestion_only"]

_TRUE_VALUES = {"1", "true", "yes", "on"}
_SUPPORTED_AUTH_MODES = ("github_signed_in_user", "oauth_github_app", "token")
_DEFAULT_MODEL = "gpt-5-mini"
_DEFAULT_ALLOWED_MODELS = ("gpt-5-mini", "gpt-4.1", "gpt-4o", "gpt-4o-mini")


@dataclass(frozen=True)
class PlanningAssistConfig:
    enabled: bool = False
    provider: AssistProvider = "github_copilot"
    mode: AssistMode = "assist_only"
    direct_write: bool = False
    model_override: str | None = None
    default_model: str = _DEFAULT_MODEL
    allowed_models: tuple[str, ...] = _DEFAULT_ALLOWED_MODELS
    phase_model_overrides: dict[str, str] | None = None
    auth_mode: str = "github_signed_in_user"
    auth_modes: tuple[str, ...] = _SUPPORTED_AUTH_MODES
    token_env_vars: tuple[str, ...] = (
        "MF_PLANNING_ASSIST_TOKEN",
        "AIMF_COPILOT_TOKEN",
        "GITHUB_COPILOT_TOKEN",
        "GITHUB_TOKEN",
        "GH_TOKEN",
    )
    allowed_phases: tuple[str, ...] = ("analysis_review", "planning_review")
    forbidden_actions: tuple[str, ...] = (
        "source_writes",
        "blocker_changes",
        "executable_changes",
        "approval_changes",
        "tool_changes",
        "unit_changes",
    )


@dataclass(frozen=True)
class AssistPolicy:
    copilot_sdk_allowed: bool
    copilot_sdk_mode: CopilotSdkMode = "suggestion_only"


def _parse_scalar(value: str) -> Any:
    value = value.strip()
    if value in {"true", "false"}:
        return value == "true"
    if value.startswith("[") and value.endswith("]"):
        inner = value[1:-1].strip()
        return [part.strip().strip("'\"") for part in inner.split(",") if part.strip()]
    return value.strip("'\"")


def _load_yaml_mapping(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore

        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception:
        pass

    root: dict[str, Any] = {}
    stack: list[tuple[int, dict[str, Any] | list[Any]]] = [(-1, root)]
    last_key_by_indent: dict[int, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        if not raw_line.strip() or raw_line.lstrip().startswith("#"):
            continue
        indent = len(raw_line) - len(raw_line.lstrip(" "))
        line = raw_line.strip()
        while stack and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if line.startswith("- "):
            if isinstance(parent, list):
                parent.append(_parse_scalar(line[2:]))
            continue
        if ":" not in line or not isinstance(parent, dict):
            continue
        key, raw_value = line.split(":", 1)
        key = key.strip()
        raw_value = raw_value.strip()
        if raw_value:
            parent[key] = _parse_scalar(raw_value)
            last_key_by_indent[indent] = key
            continue
        child: dict[str, Any] = {}
        parent[key] = child
        last_key_by_indent[indent] = key
        stack.append((indent, child))
    return root


def _config_path(ai_hub_path: str | Path | None) -> Path | None:
    if not ai_hub_path:
        return None
    path = Path(ai_hub_path) / "agents" / "copilot-assist.yaml"
    return path if path.exists() else None


def _tuple(value: Any, fallback: tuple[str, ...]) -> tuple[str, ...]:
    if isinstance(value, list):
        return tuple(str(item).strip().lower() for item in value if str(item).strip())
    return fallback


def _phase_overrides(model_config: dict[str, Any]) -> dict[str, str]:
    overrides = model_config.get("phase_overrides", {})
    if not isinstance(overrides, dict):
        return {}
    return {
        str(phase).strip(): str(model).strip().lower()
        for phase, model in overrides.items()
        if str(phase).strip() and str(model).strip()
    }


def load_planning_assist_config(
    ai_hub_path: str | Path | None = None,
    phase: str = "planning",
) -> PlanningAssistConfig:
    config_data: dict[str, Any] = {}
    path = _config_path(ai_hub_path)
    if path:
        config_data = _load_yaml_mapping(path)

    model_config = config_data.get("model", {})
    if not isinstance(model_config, dict):
        model_config = {}
    auth_config = config_data.get("auth", {})
    if not isinstance(auth_config, dict):
        auth_config = {}

    enabled = bool(config_data.get("enabled", False))
    enabled_override = os.getenv("MF_PLANNING_ASSIST_ENABLED", "").strip().lower()
    analysis_enabled_override = os.getenv("AIMF_AI_ASSIST_ENABLED", "").strip().lower()
    if enabled_override:
        enabled = enabled_override in _TRUE_VALUES
    elif phase == "analysis" and analysis_enabled_override:
        enabled = analysis_enabled_override in _TRUE_VALUES

    provider = str(config_data.get("provider", "github_copilot")).strip() or "github_copilot"
    mode = str(config_data.get("mode", "assist_only")).strip() or "assist_only"
    phase_overrides = _phase_overrides(model_config)
    model_override = os.getenv("MF_PLANNING_ASSIST_MODEL", "").strip().lower() or None
    if phase == "analysis":
        model_override = (
            os.getenv("AIMF_ANALYSIS_COPILOT_MODEL", "").strip().lower()
            or os.getenv("COPILOT_ANALYSIS_MODEL", "").strip().lower()
            or model_override
        )
    auth_mode = (
        os.getenv("MF_PLANNING_ASSIST_AUTH_MODE", "").strip()
        or os.getenv("AIMF_COPILOT_AUTH_MODE", "").strip()
        or str(auth_config.get("default_mode", "github_signed_in_user")).strip()
        or "github_signed_in_user"
    )

    if provider != "github_copilot":
        provider = "github_copilot"
    if mode != "assist_only":
        mode = "assist_only"

    return PlanningAssistConfig(
        enabled=enabled,
        provider=provider,  # type: ignore[arg-type]
        mode=mode,  # type: ignore[arg-type]
        direct_write=False,
        model_override=model_override,
        default_model=str(model_config.get("default", _DEFAULT_MODEL)).strip().lower() or _DEFAULT_MODEL,
        allowed_models=_tuple(model_config.get("allowed"), _DEFAULT_ALLOWED_MODELS),
        phase_model_overrides=phase_overrides,
        auth_mode=auth_mode,
        auth_modes=_tuple(auth_config.get("modes"), _SUPPORTED_AUTH_MODES),
        token_env_vars=_tuple(auth_config.get("token_env_vars"), PlanningAssistConfig().token_env_vars),
        allowed_phases=_tuple(config_data.get("allowed_phases"), ("analysis_review", "planning_review")),
        forbidden_actions=_tuple(config_data.get("forbidden_actions"), PlanningAssistConfig().forbidden_actions),
    )


def build_assist_policy(config: PlanningAssistConfig | None = None) -> AssistPolicy:
    planning_config = config or load_planning_assist_config()
    return AssistPolicy(
        copilot_sdk_allowed=bool(planning_config.enabled),
        copilot_sdk_mode="suggestion_only",
    )
