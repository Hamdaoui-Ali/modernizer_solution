from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migration_factory.contracts import SCHEMA_VERSION


CLASSIFIERS: tuple[tuple[str, tuple[str, ...], str, str], ...] = (
    ("DEPENDENCY_RESOLUTION_FAILURE", ("Could not resolve dependencies", "Failed to collect dependencies", "Could not find artifact"), "BLOCKER", "Resolve Maven dependency coordinates/repositories."),
    ("JAKARTA_CLASS_NOT_FOUND", ("ClassNotFoundException: jakarta.", "NoClassDefFoundError: jakarta/", "package jakarta."), "BLOCKER", "Add or align Jakarta runtime dependencies."),
    ("JAVAX_LEFTOVER", ("package javax.", "import javax.", "ClassNotFoundException: javax.", "NoClassDefFoundError: javax/"), "BLOCKER", "Complete javax to jakarta migration."),
    ("MISSING_RUNTIME_DEPENDENCY", ("ClassNotFoundException", "NoClassDefFoundError"), "BLOCKER", "Add missing runtime dependency or fix classpath."),
    ("SPRING_SECURITY_6_BREAK", ("WebSecurityConfigurerAdapter", "authorizeRequests", "SecurityFilterChain", "requestMatchers", "AccessDeniedException"), "BLOCKER", "Human review Spring Security 6 migration."),
    ("OPENREWRITE_BAD_REMOVAL", ("deleted non-generated", "bad removal", "missing bean", "NoSuchBeanDefinitionException"), "BLOCKER", "Review OpenRewrite removals before applying fixes."),
    ("H2_STARTUP_FAILURE", ("H2_STARTUP_FAILED", "jdbc:h2", "missing h2"), "BLOCKER", "Review optional H2 smoke startup evidence."),
    ("SQL_INIT_BLOCKED_FOR_SMOKE", ("schema.sql", "data.sql", "spring.sql.init", "ScriptStatementFailedException"), "WARNING", "Keep SQL init disabled for H2 smoke unless explicitly reviewed."),
    ("BEAN_CREATION_FAILURE", ("BeanCreationException", "UnsatisfiedDependencyException"), "BLOCKER", "Repair Spring bean creation failure."),
    ("AUTO_CONFIGURATION_FAILURE", ("auto-configuration", "AutoConfiguration", "ConditionEvaluationReport"), "BLOCKER", "Review Spring Boot auto-configuration failure."),
    ("JAVA_VERSION_MISMATCH", ("UnsupportedClassVersionError", "release version", "invalid target release"), "BLOCKER", "Align Java runtime and compiler target."),
    ("MAVEN_PROFILE_CLASSPATH_MISMATCH", ("profile", "classpath", "activated profiles", "Could not find or load main class"), "BLOCKER", "Review Maven profile classpath selection."),
)
SECURITY_ENV_TOKENS = ("keystore", "jwt", "jwk", "secret", "certificate", "private key")


def classify_failure(
    *,
    run_id: str,
    evidence_text: str = "",
    openrewrite_report: dict[str, Any] | None = None,
    h2_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    combined = evidence_text
    if openrewrite_report:
        combined += "\n" + json.dumps(openrewrite_report)
    if h2_report:
        combined += "\n" + json.dumps(h2_report)
    lowered = combined.replace('\\"', '"').replace("\\'", "'").lower()
    security_warning_detected = any(token in lowered for token in SECURITY_ENV_TOKENS)
    related_warnings = ["SECURITY_ENV_WARNING"] if security_warning_detected else []

    if _is_caching_config_missing_property(lowered, h2_report or {}):
        return _payload(
            run_id,
            failure_type="H2_STARTUP_FAILURE",
            severity="BLOCKER",
            migration_blocker=True,
            security_env_warning=False,
            likely_root_cause="cachingConfig is missing a runtime property required for H2 startup.",
            evidence=_runtime_config_evidence(lowered, h2_report or {}),
            recommended_next_step="Identify the required cachingConfig property and add a smoke-safe runtime configuration value.",
            send_to_copilot=False,
            requires_human_review=False,
            related_warnings=related_warnings,
            root_cause="RUNTIME_CONFIG_MISSING_PROPERTY",
        )

    if security_warning_detected:
        h2_required = bool((h2_report or {}).get("required"))
        h2_failed = (h2_report or {}).get("h2_status") == "H2_STARTUP_FAILED"
        if not (h2_required and h2_failed):
            return _payload(
                run_id,
                failure_type="SECURITY_ENV_WARNING",
                severity="WARNING",
                migration_blocker=False,
                security_env_warning=True,
                likely_root_cause="Missing local keystore/JWT/secret material for runtime startup.",
                evidence=["security environment warning detected"],
                recommended_next_step="Provide non-production local secrets for runtime smoke or review manually.",
                send_to_copilot=False,
                requires_human_review=True,
                root_cause="security environment missing common config",
            )

    for failure_type, tokens, severity, next_step in CLASSIFIERS:
        if any(token.lower() in lowered for token in tokens):
            root_cause = tokens[0]
            return _payload(
                run_id,
                failure_type=failure_type,
                severity=severity,
                migration_blocker=severity == "BLOCKER",
                security_env_warning=False,
                likely_root_cause=root_cause,
                evidence=[token for token in tokens if token.lower() in lowered][:5],
                recommended_next_step=next_step,
                send_to_copilot=severity == "BLOCKER",
                requires_human_review=failure_type in {"SPRING_SECURITY_6_BREAK", "OPENREWRITE_BAD_REMOVAL"},
                related_warnings=related_warnings,
                root_cause=root_cause,
            )

    return _payload(
        run_id,
        failure_type="UNKNOWN_MIGRATION_FAILURE",
        severity="UNKNOWN",
        migration_blocker=True,
        security_env_warning=False,
        likely_root_cause="No deterministic classifier matched the available evidence.",
        evidence=[],
        recommended_next_step="Collect build/test/runtime logs and review manually.",
        send_to_copilot=True,
        requires_human_review=True,
    )


def write_failure_classification(*, run_dir: str | Path, report: dict[str, Any]) -> Path:
    path = Path(run_dir) / "failures" / "failure_classification.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _payload(
    run_id: str,
    *,
    failure_type: str,
    severity: str,
    migration_blocker: bool,
    security_env_warning: bool,
    likely_root_cause: str,
    evidence: list[str],
    recommended_next_step: str,
    send_to_copilot: bool,
    requires_human_review: bool,
    related_warnings: list[str] | None = None,
    root_cause: str | None = None,
) -> dict[str, Any]:
    payload = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "failure_type": failure_type,
        "severity": severity,
        "migration_blocker": migration_blocker,
        "security_env_warning": security_env_warning,
        "likely_root_cause": likely_root_cause,
        "evidence": evidence,
        "recommended_next_step": recommended_next_step,
        "send_to_copilot": send_to_copilot,
        "requires_human_review": requires_human_review,
    }
    if related_warnings:
        payload["related_warnings"] = related_warnings
    if root_cause:
        payload["root_cause"] = root_cause
    return payload


def _is_caching_config_missing_property(lowered: str, h2_report: dict[str, Any]) -> bool:
    findings = h2_report.get("runtime_config_findings")
    if isinstance(findings, list):
        for finding in findings:
            if isinstance(finding, dict) and finding.get("type") == "RUNTIME_CONFIG_MISSING_PROPERTY":
                return True
    return (
        "beancreationexception" in lowered
        and "cachingconfig" in lowered
        and (
            "properties.get(object) returned null" in lowered
            or "java.util.properties.get(object) returned null" in lowered
            or "return value of \"java.util.properties.get(object)\" is null" in lowered
            or "return value of 'java.util.properties.get(object)' is null" in lowered
        )
    )


def _runtime_config_evidence(lowered: str, h2_report: dict[str, Any]) -> list[str]:
    evidence = ["BeanCreationException", "cachingConfig"]
    if (
        "properties.get(object) returned null" in lowered
        or "return value of \"java.util.properties.get(object)\" is null" in lowered
        or "return value of 'java.util.properties.get(object)' is null" in lowered
    ):
        evidence.append("Properties.get(Object) returned null")
    for finding in h2_report.get("runtime_config_findings", []) or []:
        if isinstance(finding, dict) and finding.get("property_key"):
            evidence.append(f"property_key={finding['property_key']}")
    return evidence[:5]
