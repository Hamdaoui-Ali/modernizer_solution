from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migration_factory.contracts import SCHEMA_VERSION
from migration_factory.dependency_policy.models import PolicyReport
from migration_factory.profile_semantics import requires_jakarta_migration


POLICY_RULE_IDS = (
    "DEP-TOMCAT-BOOT3-001",
    "DEP-TOMCAT-BOOT3-002",
    "DEP-ZALANDO-BOOT3-001",
    "DEP-JAVAX-DEPS-001",
    "DEP-INTERNAL-JAR-001",
    "DEP-BOOT-BOM-001",
)


def build_target_dependency_plan(
    *,
    source_boot_version: str = "",
    target_boot_version: str = "",
    target_java_version: str = "",
    profile_id: str = "",
    migration_unit_ids: list[str] | tuple[str, ...] | None = None,
    openrewrite_recipes_expected: list[str] | tuple[str, ...] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "source_boot_version": source_boot_version,
        "target_boot_version": target_boot_version,
        "target_java_version": target_java_version,
        "target_jakarta_required": requires_jakarta_migration(
            source_boot_version=source_boot_version,
            target_boot_version=target_boot_version,
            profile_id=profile_id,
            unit_ids=migration_unit_ids,
        ),
        "openrewrite_recipes_expected": list(openrewrite_recipes_expected or []),
        "blocked_properties": [
            {
                "rule_id": "DEP-TOMCAT-BOOT3-001",
                "property": "tomcat.version",
                "when": "target Spring Boot major >= 3 and value matches 9.*",
                "recommended_deterministic_action": "remove property and use Spring Boot managed Tomcat",
            }
        ],
        "blocked_dependencies": [
            {
                "rule_id": "DEP-ZALANDO-BOOT3-001",
                "dependency": "org.zalando:problem-spring-web",
                "when": "target Spring Boot major >= 3 and version <= 0.24.0",
                "recommended_action": "upgrade to configured Jakarta-compatible target or request Copilot advisory",
            },
            {
                "rule_id": "DEP-JAVAX-DEPS-001",
                "patterns": [
                    "javax.servlet",
                    "javax.persistence",
                    "javax.validation",
                    "javax.ws.rs",
                ],
                "recommended_action": "separate Java source imports from test resource/logger names and dependency tree risk",
            },
        ],
        "required_dependency_updates": [
            {
                "rule_id": "DEP-TOMCAT-BOOT3-002",
                "dependency_pattern": "org.apache.tomcat.embed:tomcat-embed-*",
                "recommended_deterministic_action": "remove explicit versions when Spring Boot dependency management applies",
            },
            {
                "rule_id": "DEP-BOOT-BOM-001",
                "recommended_action": "prefer Spring Boot parent/BOM-managed versions and flag redundant explicit versions",
            },
        ],
        "dependency_scan_rules": [
            "scan pom.xml properties and dependencies",
            "scan Java source imports for javax.* namespaces",
            "scan resources separately for logger/config references",
            "scan dependency tree text or JSON when available",
            "flag internal corporate JARs that may expose javax.servlet or old Spring APIs",
        ],
        "policy_rule_ids": list(POLICY_RULE_IDS),
        "copilot_advisory_enabled": True,
        "auto_apply_copilot_allowed": False,
        "configured_safe_versions": {},
    }


def write_target_dependency_plan(
    *,
    run_dir: str | Path,
    source_boot_version: str = "",
    target_boot_version: str = "",
    target_java_version: str = "",
    profile_id: str = "",
    migration_unit_ids: list[str] | tuple[str, ...] | None = None,
    openrewrite_recipes_expected: list[str] | tuple[str, ...] | None = None,
) -> Path:
    output_path = Path(run_dir) / "planning" / "target_dependency_plan.json"
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = build_target_dependency_plan(
        source_boot_version=source_boot_version,
        target_boot_version=target_boot_version,
        target_java_version=target_java_version,
        profile_id=profile_id,
        migration_unit_ids=migration_unit_ids,
        openrewrite_recipes_expected=openrewrite_recipes_expected,
    )
    output_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return output_path


def write_dependency_policy_artifacts(*, run_dir: str | Path, report: PolicyReport) -> dict[str, Path]:
    assessment_dir = Path(run_dir) / "assessment"
    assessment_dir.mkdir(parents=True, exist_ok=True)
    json_path = assessment_dir / "dependency_policy_report.json"
    md_path = assessment_dir / "dependency_policy_report.md"
    payload = report.to_dict()
    json_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    md_path.write_text(_markdown(payload), encoding="utf-8")
    return {"dependency_policy_report": json_path, "dependency_policy_summary": md_path}


def _markdown(payload: dict[str, Any]) -> str:
    risks = list(payload.get("risks", []) or [])
    lines = [
        "# Dependency Policy Report",
        "",
        f"- Status: {payload.get('status', '')}",
        f"- Target Spring Boot: {payload.get('target_boot_version', '')}",
        f"- Target Java: {payload.get('target_java_version', '')}",
        f"- Blocked for build/test: {str(payload.get('blocked_for_build_test', False)).lower()}",
        f"- Blocked for runtime: {str(payload.get('blocked_for_runtime', False)).lower()}",
        f"- Risks: {len(risks)}",
    ]
    if risks:
        lines.extend(["", "## Risks", ""])
        for risk in risks:
            lines.append(
                f"- {risk.get('severity')} {risk.get('rule_id')} "
                f"{risk.get('category')}: {risk.get('evidence')}"
            )
    return "\n".join(lines) + "\n"
