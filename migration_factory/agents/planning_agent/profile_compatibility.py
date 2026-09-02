from dataclasses import dataclass, field
from typing import Any
import re

from migration_factory.agents.planning_agent.artifact_reader import LoadedAnalysisArtifacts
from migration_factory.agents.planning_agent.profile_reader import LoadedMigrationProfile


@dataclass(frozen=True)
class StackFingerprint:
    build_tool: str | None = None
    java: str | None = None
    spring_boot: str | None = None
    spring_framework: str | None = None


@dataclass(frozen=True)
class ProfileCompatibilityResult:
    ok: bool
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    source_stack: StackFingerprint = field(default_factory=StackFingerprint)
    target_stack: StackFingerprint = field(default_factory=StackFingerprint)


def validate_profile_compatibility(
    loaded_artifacts: LoadedAnalysisArtifacts,
    loaded_profile: LoadedMigrationProfile,
) -> ProfileCompatibilityResult:
    errors: list[str] = []
    warnings: list[str] = []

    source_stack = _extract_source_stack(loaded_artifacts)
    target_stack = _extract_target_stack(loaded_profile)

    allowed_build_tools = _profile_allowed_build_tools(loaded_profile)
    if source_stack.build_tool:
        if allowed_build_tools:
            if source_stack.build_tool.lower() not in allowed_build_tools:
                errors.append(
                    "Source build tool incompatible with selected profile: "
                    f"{source_stack.build_tool}. Expected one of: {', '.join(sorted(allowed_build_tools))}."
                )
        elif source_stack.build_tool.lower() != "maven":
            errors.append(
                "Source build tool incompatible with planning target. Expected Maven-compatible source build metadata."
            )
    else:
        warnings.append("Source build tool unknown from analysis artifacts.")

    allowed_java = _profile_allowed_java_versions(loaded_profile)
    if source_stack.java:
        if source_stack.java not in allowed_java:
            errors.append(
                "Source Java version unsupported for selected profile: "
                f"{source_stack.java}. Expected one of: {', '.join(sorted(allowed_java))}."
            )
    else:
        warnings.append("Source Java version missing or unknown in analysis artifacts.")

    allowed_boot_prefixes = _profile_allowed_spring_boot_prefixes(loaded_profile)
    if source_stack.spring_boot:
        if not _matches_any_prefix(source_stack.spring_boot, allowed_boot_prefixes):
            errors.append(
                "Source Spring Boot version unsupported for selected profile: "
                f"{source_stack.spring_boot}. Expected prefixes: {', '.join(allowed_boot_prefixes)}."
            )
    else:
        warnings.append("Source Spring Boot version missing or unknown in analysis artifacts.")

    if not target_stack.java:
        errors.append("Target Java missing from selected profile.")
    if not target_stack.spring_boot:
        errors.append("Target Spring Boot missing from selected profile.")
    if target_stack.build_tool and target_stack.build_tool != "maven":
        errors.append(
            f"Target build tool unsupported for this planning path: {target_stack.build_tool}. Expected maven."
        )

    warnings.extend(_profile_risk_warnings(loaded_profile, target_stack))

    return ProfileCompatibilityResult(
        ok=not errors,
        errors=errors,
        warnings=warnings,
        source_stack=source_stack,
        target_stack=target_stack,
    )


def _extract_source_stack(loaded_artifacts: LoadedAnalysisArtifacts) -> StackFingerprint:
    candidates: list[dict[str, Any]] = []

    for obj in (
        loaded_artifacts.required.get("analysis_report.json"),
        loaded_artifacts.required.get("dependency_graph.json"),
        loaded_artifacts.optional.get("config_inventory.json"),
    ):
        if isinstance(obj, dict):
            candidates.append(obj)

    build_tool = _first_string(
        candidates,
        [
            "source_stack.build_tool",
            "source_stack.build.tool",
            "build_tool",
            "build.tool",
            "source.build_tool",
            "source.build.tool",
            "metadata.build_tool",
            "project_metadata.build_tool",
            "project_metadata.build.tool",
            "inventory.build_tool",
        ],
    )
    if build_tool is None:
        build_tool = _infer_build_tool_from_metadata(candidates)

    java_raw = _first_string(
        candidates,
        [
            "source_stack.java",
            "source_stack.java_version",
            "java",
            "java_version",
            "source.java",
            "source.java_version",
            "runtime.java",
            "metadata.java",
            "metadata.java_version",
            "inventory.java",
            "inventory.java_version",
        ],
    )
    spring_raw = _first_string(
        candidates,
        [
            "source_stack.spring_boot",
            "source_stack.spring_boot_version",
            "spring_boot",
            "spring_boot_version",
            "spring.boot",
            "source.spring_boot",
            "source.spring_boot_version",
            "metadata.spring_boot",
            "metadata.spring_boot_version",
            "inventory.spring_boot",
            "inventory.spring_boot_version",
        ],
    )

    return StackFingerprint(
        build_tool=_normalize_build_tool(build_tool),
        java=_normalize_java(java_raw),
        spring_boot=_normalize_spring_boot(spring_raw),
        spring_framework=None,
    )


def _extract_target_stack(loaded_profile: LoadedMigrationProfile) -> StackFingerprint:
    target = loaded_profile.profile.get("target")
    if not isinstance(target, dict):
        return StackFingerprint()

    build_tool = (
        target.get("build")
        or target.get("build_tool")
        or target.get("buildTool")
    )
    return StackFingerprint(
        build_tool=_normalize_build_tool(build_tool),
        java=_normalize_java(target.get("java")),
        spring_boot=_normalize_spring_boot(target.get("spring_boot")),
        spring_framework=_normalize_version_text(target.get("spring_framework")),
    )


def _profile_allowed_build_tools(loaded_profile: LoadedMigrationProfile) -> set[str]:
    source = loaded_profile.profile.get("source")
    if not isinstance(source, dict):
        return set()
    build = source.get("build")
    values: list[Any] = []
    if isinstance(build, dict):
        raw = build.get("allowed_tools")
        values = raw if isinstance(raw, list) else []
    elif build:
        values = [build]
    return {
        normalized
        for value in values
        if (normalized := _normalize_build_tool(value))
    }


def _profile_allowed_java_versions(loaded_profile: LoadedMigrationProfile) -> set[str]:
    source = loaded_profile.profile.get("source")
    values: list[Any] = []
    if isinstance(source, dict):
        java = source.get("java")
        if isinstance(java, dict):
            raw = java.get("allowed_versions")
            values = raw if isinstance(raw, list) else []
        elif java:
            values = [java]
    allowed = {normalized for value in values if (normalized := _normalize_java(value))}
    return allowed or {"8", "11"}


def _profile_allowed_spring_boot_prefixes(loaded_profile: LoadedMigrationProfile) -> tuple[str, ...]:
    source = loaded_profile.profile.get("source")
    values: list[Any] = []
    if isinstance(source, dict):
        spring_boot = source.get("spring_boot")
        if isinstance(spring_boot, dict):
            raw = spring_boot.get("allowed_version_prefixes")
            values = raw if isinstance(raw, list) else []
        elif spring_boot:
            values = [spring_boot]
    prefixes = tuple(str(value).strip() for value in values if str(value).strip())
    return prefixes or ("2.7",)


def _matches_any_prefix(value: str, prefixes: tuple[str, ...]) -> bool:
    return any(value == prefix or value.startswith(f"{prefix}.") or value.startswith(prefix) for prefix in prefixes)


def _profile_risk_warnings(
    loaded_profile: LoadedMigrationProfile,
    target_stack: StackFingerprint,
) -> list[str]:
    if _major_version(target_stack.spring_boot) != 4:
        return []

    warnings = [
        "Spring Boot 4 target requires Spring Framework 7.x.",
        "Spring Boot 4 target carries Jakarta EE 11 / Servlet 6.1 baseline risk.",
        "Boot 3 deprecated APIs removed in Boot 4 must be reviewed.",
        "Spring Cloud compatibility must be reviewed before sandbox execution.",
        "Spring Security, Spring Data, Hibernate, and custom starter compatibility risk requires human review.",
        "javax.* leftovers must be treated as blockers for Boot 4 readiness.",
        "Maven version and Java runtime must match Boot 4 / Java 21 validation gates.",
        "Official Spring Boot guidance prefers upgrading to the latest 3.5.x before Boot 4; direct migration is sandbox-only and should fall back if unstable.",
    ]
    fallback = loaded_profile.profile.get("fallback_profile")
    if fallback:
        warnings.append(f"Fallback profile configured for direct Boot 4 risk: {fallback}.")
    return warnings


def _first_string(candidates: list[dict[str, Any]], paths: list[str]) -> str | None:
    for candidate in candidates:
        for path in paths:
            value = _get_by_path(candidate, path)
            if isinstance(value, str) and value.strip():
                return value.strip()
            if isinstance(value, int):
                return str(value)
    return None


def _get_by_path(obj: dict[str, Any], path: str) -> Any:
    current: Any = obj
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current


def _infer_build_tool_from_metadata(candidates: list[dict[str, Any]]) -> str | None:
    for candidate in candidates:
        for path in (
            "dependency_graph.tool",
            "dependency_graph.build_tool",
            "dependency_graph.format",
            "build_metadata.tool",
            "build_metadata.build_tool",
            "format",
            "warning",
        ):
            value = _get_by_path(candidate, path)
            if isinstance(value, str) and "maven" in value.lower():
                return "maven"
    return None


def _normalize_build_tool(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    lower = text.lower()
    if "maven" in lower or lower == "mvn":
        return "maven"
    if "gradle" in lower:
        return "gradle"
    return lower


def _normalize_java(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    if not text:
        return None
    if text.startswith("1.8"):
        return "8"
    if text.startswith("8"):
        return "8"
    if text.startswith("11"):
        return "11"
    if text.startswith("17"):
        return "17"
    if text.startswith("21"):
        return "21"
    return text


def _normalize_spring_boot(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parts = text.split(".")
    if len(parts) >= 2 and parts[0].isdigit() and parts[1].isdigit():
        if parts[0] == "2" and parts[1] == "7":
            return "2.7"
        if parts[0] == "3" and parts[1] == "5":
            return "3.5.14" if text == "3.5.14" else f"{parts[0]}.{parts[1]}"
        if parts[0] == "4" and parts[1] == "0":
            return f"{parts[0]}.{parts[1]}"
    return text


def _normalize_version_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _major_version(value: Any) -> int | None:
    match = re.match(r"^\D*(\d+)", str(value or ""))
    return int(match.group(1)) if match else None
