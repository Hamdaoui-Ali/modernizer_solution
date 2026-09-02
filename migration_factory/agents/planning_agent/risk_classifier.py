from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from migration_factory.agents.planning_agent.artifact_reader import LoadedAnalysisArtifacts
from migration_factory.agents.planning_agent.profile_compatibility import StackFingerprint
from migration_factory.profile_semantics import should_openrewrite_impact_be_fatal

RiskSeverity = Literal["BLOCKER", "WARNING", "INFO"]


@dataclass(frozen=True)
class PlanningRiskItem:
    code: str
    severity: RiskSeverity
    message: str
    source: str


@dataclass(frozen=True)
class PlanningRiskResult:
    ok: bool
    risks: list[PlanningRiskItem] = field(default_factory=list)


def classify_planning_risks(
    loaded_artifacts: LoadedAnalysisArtifacts,
    source_stack: StackFingerprint,
    *,
    profile_id: str | None = None,
    migration_units: Sequence[str] | None = None,
) -> PlanningRiskResult:
    risks: list[PlanningRiskItem] = []

    if _has_unreadable_or_invalid_build_metadata(loaded_artifacts):
        risks.append(
            PlanningRiskItem(
                code="UNREADABLE_BUILD_METADATA",
                severity="BLOCKER",
                message="Build metadata unreadable or invalid from analysis artifacts.",
                source="analysis",
            )
        )

    if source_stack.java is None:
        risks.append(
            PlanningRiskItem(
                code="UNKNOWN_SOURCE_JAVA",
                severity="WARNING",
                message="Source Java version unknown in analysis artifacts.",
                source="analysis",
            )
        )

    if source_stack.spring_boot is None:
        risks.append(
            PlanningRiskItem(
                code="UNKNOWN_SOURCE_SPRING_BOOT",
                severity="WARNING",
                message="Source Spring Boot version unknown in analysis artifacts.",
                source="analysis",
            )
        )

    javax_count = _extract_javax_count(loaded_artifacts)
    if javax_count is not None and javax_count > 0:
        risks.append(
            PlanningRiskItem(
                code="JAKARTA_MIGRATION_REQUIRED",
                severity="WARNING",
                message=f"Detected javax usage count: {javax_count}.",
                source="analysis",
            )
        )

    risks.extend(
        _classify_openrewrite_impact(
            loaded_artifacts,
            profile_id=profile_id,
            migration_units=migration_units,
        )
    )

    has_blocker = any(r.severity == "BLOCKER" for r in risks)
    return PlanningRiskResult(ok=not has_blocker, risks=risks)


def _classify_openrewrite_impact(
    loaded_artifacts: LoadedAnalysisArtifacts,
    *,
    profile_id: str | None = None,
    migration_units: Sequence[str] | None = None,
) -> list[PlanningRiskItem]:
    impact_summary = loaded_artifacts.optional.get("rewrite_impact_summary.json")
    if not isinstance(impact_summary, dict):
        return []

    risks: list[PlanningRiskItem] = []
    raw_impact = impact_summary.get("overall_impact")
    if raw_impact is None:
        risks.append(
            PlanningRiskItem(
                code="OPENREWRITE_IMPACT_SCHEMA_MISMATCH",
                severity="WARNING",
                message="OpenRewrite impact artifact is missing overall_impact.",
                source="openrewrite",
            )
        )
    impact = raw_impact.strip().upper() if isinstance(raw_impact, str) else "UNKNOWN"

    if impact not in {"LOW", "MEDIUM", "HIGH", "BLOCKED", "UNKNOWN"}:
        impact = "UNKNOWN"

    fatal_blocked = should_openrewrite_impact_be_fatal(
        profile_id=profile_id,
        unit_ids=migration_units,
    )
    severity_by_impact: dict[str, RiskSeverity] = {
        "LOW": "INFO",
        "MEDIUM": "WARNING",
        "HIGH": "WARNING",
        "BLOCKED": "BLOCKER",
        "UNKNOWN": "WARNING",
    }
    message_by_impact = {
        "LOW": "OpenRewrite impact is low.",
        "MEDIUM": "OpenRewrite impact is medium.",
        "HIGH": "OpenRewrite impact is high; manual review is required before execution.",
        "BLOCKED": "OpenRewrite impact is blocked; planning output is not executable.",
        "UNKNOWN": "OpenRewrite impact is unknown or missing.",
    }
    severity = severity_by_impact[impact]
    message = message_by_impact[impact]
    if impact == "BLOCKED" and not fatal_blocked:
        severity = "WARNING"
        message = (
            "OpenRewrite impact is blocked for a Java 21 runtime-validation route; "
            "continuing to runtime validation gate."
        )

    risks.append(
        PlanningRiskItem(
            code=f"OPENREWRITE_IMPACT_{impact}",
            severity=severity,
            message=message,
            source="openrewrite",
        )
    )

    high_risk_files = impact_summary.get("high_risk_files")
    if isinstance(high_risk_files, list) and high_risk_files:
        risks.append(
            PlanningRiskItem(
                code="OPENREWRITE_HIGH_RISK_FILES",
                severity="WARNING",
                message=f"OpenRewrite reported high-risk files: {len(high_risk_files)}.",
                source="openrewrite",
            )
        )

    migration_signals = impact_summary.get("migration_signals")
    if isinstance(migration_signals, dict):
        if migration_signals.get("security_config_touched") is True:
            risks.append(
                PlanningRiskItem(
                    code="OPENREWRITE_SECURITY_CONFIG_TOUCHED",
                    severity="WARNING",
                    message="OpenRewrite migration signals indicate security configuration was touched.",
                    source="openrewrite",
                )
            )
        if migration_signals.get("datasource_config_touched") is True:
            risks.append(
                PlanningRiskItem(
                    code="OPENREWRITE_DATASOURCE_CONFIG_TOUCHED",
                    severity="WARNING",
                    message="OpenRewrite migration signals indicate datasource configuration was touched.",
                    source="openrewrite",
                )
            )

    return risks


def _has_unreadable_or_invalid_build_metadata(
    loaded_artifacts: LoadedAnalysisArtifacts,
) -> bool:
    errors_text = "\n".join(loaded_artifacts.errors).lower()
    if "pom" in errors_text:
        return True

    for obj in _iter_dict_candidates(loaded_artifacts):
        for key in (
            "pom_readable",
            "pom_valid",
            "build_metadata_readable",
            "build_metadata_valid",
        ):
            value = _get_by_path(obj, key)
            if value is False:
                return True

        for key in (
            "pom_error",
            "pom_parse_error",
            "build_metadata_error",
            "build_metadata_parse_error",
        ):
            value = _get_by_path(obj, key)
            if isinstance(value, str) and value.strip():
                return True

    return False


def _extract_javax_count(loaded_artifacts: LoadedAnalysisArtifacts) -> int | None:
    for obj in _iter_dict_candidates(loaded_artifacts):
        for key in (
            "javax_count",
            "jakarta.javax_count",
            "inventory.javax_count",
            "source.javax_count",
        ):
            value = _get_by_path(obj, key)
            if isinstance(value, int):
                return value
            if isinstance(value, str) and value.strip().isdigit():
                return int(value.strip())
    return None


def _iter_dict_candidates(loaded_artifacts: LoadedAnalysisArtifacts) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for obj in loaded_artifacts.required.values():
        if isinstance(obj, dict):
            out.append(obj)
    for obj in loaded_artifacts.optional.values():
        if isinstance(obj, dict):
            out.append(obj)
    return out


def _get_by_path(obj: dict[str, Any], path: str) -> Any:
    current: Any = obj
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            return None
        current = current[part]
    return current
