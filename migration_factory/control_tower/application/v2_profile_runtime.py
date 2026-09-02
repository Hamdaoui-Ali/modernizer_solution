"""Route-aware runtime profile resolution for DEMO3 V2 jobs."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


class RouteRuntimeProfileUnavailableError(ValueError):
    """Raised when a persisted route has no concrete AI Hub runtime profile."""

    code = "ROUTE_RUNTIME_PROFILE_UNAVAILABLE"
    public_message = (
        "Route runtime profile is unavailable for the selected source/target profile."
    )

    def __init__(
        self,
        message: str,
        *,
        public_message: str | None = None,
    ) -> None:
        super().__init__(message)
        self.public_message = public_message or self.public_message


_ROUTE_RUNTIME_PROFILE_MAP: dict[tuple[str, str], str] = {
    # Exact route profiles that exist in the checked-in AI Hub.
    ("springboot-2.1-java11", "springboot-2.7-java11"): "springboot-2.1.6-to-2.7-java11",
    ("springboot-2.7-java11", "springboot-3.5-java17"): "springboot-2.7-to-3.5-java17",
    ("springboot-3.5-java17", "springboot-3.5-java21"): "springboot-3.5-java17-to-java21",
    ("springboot-3.5-java21", "springboot-4.0-java21"): "springboot-3.5-java21-to-4.0-java21",
    # Multi-stage routes reuse the first concrete route profile available in repo.
    ("springboot-2.1-java11", "springboot-3.5-java17"): "springboot-2.1.6-to-2.7-java11",
    ("springboot-2.1-java11", "springboot-3.5-java21"): "springboot-2.1.6-to-2.7-java11",
    ("springboot-2.1-java11", "springboot-4.0-java21"): "springboot-2.1.6-to-2.7-java11",
    ("springboot-2.7-java11", "springboot-3.5-java21"): "springboot-2.7-to-3.5-java17",
    ("springboot-2.7-java11", "springboot-4.0-java21"): "springboot-2.7-to-3.5-java17",
    ("springboot-3.5-java17", "springboot-4.0-java21"): "springboot-3.5-java17-to-java21",
}

_ROUTE_RUNTIME_PROFILE_IDS: frozenset[str] = frozenset(_ROUTE_RUNTIME_PROFILE_MAP.values())

_ROUTE_RUNTIME_PROFILE_CATALOG_MAP: dict[str, str] = {
    "springboot-2.1.6-to-2.7-java11": "springboot-2.1.6-to-2.7-java11",
    "springboot-2.7-to-3.5-java17": "springboot-3.5-java17",
    "springboot-3.5-java17-to-java21": "springboot-3.5-java17-to-java21",
    "springboot-3.5-java21-to-4.0-java21": "springboot-3.5-java21-to-4.0-java21",
}

_RUNTIME_PROFILE_EXECUTION_JDK_ENV_MAP: dict[str, str] = {
    "springboot-2.1.6-to-2.7-java11": "JAVA11_HOME",
    "springboot-2.7-to-3.5-java17": "JAVA17_HOME",
    "springboot-3.5-java17-to-java21": "JAVA21_HOME",
    "springboot-3.5-java21-to-4.0-java21": "JAVA21_HOME",
}

_RUNTIME_PROFILE_EXECUTION_JDK_ID_MAP: dict[str, str] = {
    "springboot-2.1.6-to-2.7-java11": "java11",
    "springboot-2.7-to-3.5-java17": "java17",
    "springboot-3.5-java17-to-java21": "java21",
    "springboot-3.5-java21-to-4.0-java21": "java21",
}


def resolve_runtime_profile_for_route(source_profile: str, target_profile: str) -> str:
    """Resolve the backend-owned AI Hub profile for a persisted route."""
    source = str(source_profile or "").strip()
    target = str(target_profile or "").strip()
    if not source or not target:
        raise RouteRuntimeProfileUnavailableError(
            "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: source_profile and target_profile are required"
        )

    profile_id = _ROUTE_RUNTIME_PROFILE_MAP.get((source, target))
    if profile_id is None:
        raise RouteRuntimeProfileUnavailableError(
            "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: no runtime profile mapping exists for "
            f"source={source!r} target={target!r}"
        )
    return profile_id


def resolve_runtime_profile_for_run_configuration(run_configuration: Any) -> str:
    """Resolve a runtime profile from a persisted run-configuration record."""
    source_profile, target_profile = extract_profile_route(run_configuration)
    return resolve_runtime_profile_for_route(source_profile, target_profile)


def resolve_execution_jdk_env_var_for_runtime_profile(profile_id: str) -> str:
    """Resolve the required JAVA*_HOME env var for a runtime profile."""
    runtime_profile = str(profile_id or "").strip()
    if not runtime_profile:
        raise RouteRuntimeProfileUnavailableError(
            "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: runtime profile is required"
        )

    env_var = _RUNTIME_PROFILE_EXECUTION_JDK_ENV_MAP.get(runtime_profile)
    if env_var is None:
        raise RouteRuntimeProfileUnavailableError(
            "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: no execution JDK mapping exists for "
            f"runtime profile {runtime_profile!r}"
        )
    return env_var


def resolve_execution_jdk_id_for_runtime_profile(profile_id: str) -> str:
    """Resolve the backend-owned JDK id for a runtime profile."""
    runtime_profile = str(profile_id or "").strip()
    if not runtime_profile:
        raise RouteRuntimeProfileUnavailableError(
            "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: runtime profile is required"
        )

    execution_jdk = _RUNTIME_PROFILE_EXECUTION_JDK_ID_MAP.get(runtime_profile)
    if execution_jdk is None:
        raise RouteRuntimeProfileUnavailableError(
            "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: no execution JDK mapping exists for "
            f"runtime profile {runtime_profile!r}"
        )
    return execution_jdk


def resolve_catalog_for_runtime_profile(profile_id: str) -> str:
    """Resolve the backend-owned catalog id for a runtime profile."""
    runtime_profile = str(profile_id or "").strip()
    if not runtime_profile:
        raise RouteRuntimeProfileUnavailableError(
            "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: runtime profile is required"
        )

    catalog = _ROUTE_RUNTIME_PROFILE_CATALOG_MAP.get(runtime_profile)
    if catalog is None:
        raise RouteRuntimeProfileUnavailableError(
            "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: no catalog mapping exists for "
            f"runtime profile {runtime_profile!r}"
        )
    return catalog


def resolve_runtime_profile_for_state(state: Any) -> str:
    """Resolve a runtime profile from a persisted orchestration state payload."""
    source_profile, target_profile = extract_profile_route(state)
    if source_profile and target_profile:
        return resolve_runtime_profile_for_route(source_profile, target_profile)

    profile_id = ""
    if isinstance(state, dict):
        profile_id = str(state.get("profile_id") or "").strip()
    else:
        profile_id = str(getattr(state, "profile_id", "") or "").strip()

    if profile_id in _ROUTE_RUNTIME_PROFILE_IDS:
        return profile_id

    raise RouteRuntimeProfileUnavailableError(
        "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: unable to resolve a runtime profile "
        "from the persisted orchestration state"
    )


def extract_profile_route(run_configuration: Any) -> tuple[str, str]:
    """Extract source/target profiles from a run-configuration-like object."""
    source_profile = ""
    target_profile = ""

    if isinstance(run_configuration, dict):
        source_profile = str(run_configuration.get("source_profile") or "").strip()
        target_profile = str(run_configuration.get("target_profile") or "").strip()
        if not (source_profile and target_profile):
            source_profile, target_profile = _extract_from_nested_profile_metadata(run_configuration)
        if not (source_profile and target_profile):
            source_profile, target_profile = _extract_from_payload_json(run_configuration.get("payload_json"))
        return source_profile, target_profile

    source_profile = str(getattr(run_configuration, "source_profile", "") or "").strip()
    target_profile = str(getattr(run_configuration, "target_profile", "") or "").strip()
    if source_profile and target_profile:
        return source_profile, target_profile

    payload_json = getattr(run_configuration, "payload_json", "") or ""
    if payload_json:
        payload_source, payload_target = _extract_from_payload_json(payload_json)
        source_profile = source_profile or payload_source
        target_profile = target_profile or payload_target

    return source_profile, target_profile


def ensure_runtime_profile_available(ai_hub_path: str | Path, profile_id: str) -> Path:
    """Verify that the resolved runtime profile exists in the checked-in AI Hub."""
    profile_path = Path(ai_hub_path) / "profiles" / f"{profile_id}.yaml"
    if not profile_path.is_file():
        raise RouteRuntimeProfileUnavailableError(
            "ROUTE_RUNTIME_PROFILE_UNAVAILABLE: runtime profile file missing at "
            f"{profile_path}"
    )
    return profile_path


def public_runtime_profile_error_message(exc: BaseException) -> str:
    """Return a sanitized message safe for API and event surfaces."""
    if isinstance(exc, RouteRuntimeProfileUnavailableError):
        return exc.code
    return RouteRuntimeProfileUnavailableError.code


def _extract_from_payload_json(payload_json: Any) -> tuple[str, str]:
    if not isinstance(payload_json, str) or not payload_json.strip():
        return "", ""
    try:
        payload = json.loads(payload_json)
    except (json.JSONDecodeError, TypeError, ValueError):
        return "", ""
    if not isinstance(payload, dict):
        return "", ""
    return (
        str(payload.get("source_profile") or "").strip(),
        str(payload.get("target_profile") or "").strip(),
    )


def _extract_from_nested_profile_metadata(value: Any) -> tuple[str, str]:
    if isinstance(value, dict):
        candidate = value.get("profile_metadata")
        if isinstance(candidate, dict):
            source_profile = str(candidate.get("source_profile") or "").strip()
            target_profile = str(candidate.get("target_profile") or "").strip()
            if source_profile and target_profile:
                return source_profile, target_profile
        for nested in value.values():
            source_profile, target_profile = _extract_from_nested_profile_metadata(nested)
            if source_profile and target_profile:
                return source_profile, target_profile
    elif isinstance(value, list):
        for item in value:
            source_profile, target_profile = _extract_from_nested_profile_metadata(item)
            if source_profile and target_profile:
                return source_profile, target_profile
    return "", ""
