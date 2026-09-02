from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from migration_factory.contracts import SCHEMA_VERSION
from migration_factory.dependency_policy.models import PolicyReport, PolicyRisk

JAVAX_PATTERNS = ("javax.servlet", "javax.persistence", "javax.validation", "javax.ws.rs")
TOMCAT_EMBED_PREFIX = "tomcat-embed-"


def scan_dependency_policy(
    *,
    sandbox_path: str | Path,
    target_plan: dict[str, Any] | None = None,
    dependency_tree_text: str = "",
    build_passed: bool = False,
) -> PolicyReport:
    root = Path(sandbox_path)
    plan = dict(target_plan or {})
    target_boot = str(plan.get("target_boot_version") or "")
    target_java = str(plan.get("target_java_version") or "")
    boot3 = _boot_major(target_boot) >= 3
    pom_path = root / "pom.xml"
    risks: list[PolicyRisk] = []
    pom_findings: dict[str, Any] = {
        "properties": {},
        "dependencies": [],
        "explicit_tomcat_embed_versions": [],
    }

    if pom_path.is_file():
        pom = _parse_pom(pom_path)
        pom_findings.update(pom)
        tomcat_version = str(pom["properties"].get("tomcat.version") or "")
        if boot3 and tomcat_version.startswith("9."):
            risks.append(
                PolicyRisk(
                    rule_id="DEP-TOMCAT-BOOT3-001",
                    severity="WARNING" if build_passed else "ERROR",
                    category="TOMCAT_OVERRIDE",
                    file="pom.xml",
                    evidence=f"tomcat.version={tomcat_version}",
                    why_it_matters="Spring Boot 3 uses the Jakarta Servlet 6 baseline; Tomcat 9 is a pre-Jakarta override.",
                    deterministic_fix_available=True,
                    suggested_fix="Remove tomcat.version and let Spring Boot dependency management select Tomcat.",
                    blocks_v1_build_test=False,
                    blocks_v2_runtime=True,
                )
            )
        for dep in pom["dependencies"]:
            if (
                boot3
                and dep["group_id"] == "org.apache.tomcat.embed"
                and dep["artifact_id"].startswith(TOMCAT_EMBED_PREFIX)
                and dep.get("version")
            ):
                pom_findings["explicit_tomcat_embed_versions"].append(dep)
                risks.append(
                    PolicyRisk(
                        rule_id="DEP-TOMCAT-BOOT3-002",
                        severity="WARNING",
                        category="TOMCAT_OVERRIDE",
                        file="pom.xml",
                        evidence=f"{dep['group_id']}:{dep['artifact_id']}:{dep['version']}",
                        why_it_matters="Explicit embedded Tomcat versions can override the Boot 3 managed Servlet 6-compatible stack.",
                        deterministic_fix_available=True,
                        suggested_fix="Remove the explicit tomcat-embed-* dependency version.",
                        blocks_v1_build_test=False,
                        blocks_v2_runtime=True,
                    )
                )
            if dep["group_id"] == "org.zalando" and dep["artifact_id"] == "problem-spring-web":
                version = _resolve_property_version(str(dep.get("version") or ""), pom["properties"])
                if boot3 and _version_lte(version, "0.24.0"):
                    configured = _configured_zalando_target(plan)
                    risks.append(
                        PolicyRisk(
                            rule_id="DEP-ZALANDO-BOOT3-001",
                            severity="WARNING" if build_passed else "ERROR",
                            category="OLD_ZALANDO",
                            file="pom.xml",
                            evidence=f"org.zalando:problem-spring-web:{version or dep.get('version') or 'unknown'}",
                            why_it_matters="problem-spring-web 0.24.x predates the Boot 3/Jakarta baseline and may retain javax/Servlet 4 assumptions.",
                            deterministic_fix_available=bool(configured),
                            suggested_fix=(
                                f"Upgrade to configured target {configured}."
                                if configured
                                else "Request advisory review for a Boot 3/Jakarta-compatible Zalando integration."
                            ),
                            blocks_v1_build_test=False,
                            blocks_v2_runtime=True,
                        )
                    )

    source_findings = _scan_source(root)
    for finding in source_findings["source_imports"]:
        risks.append(
            PolicyRisk(
                rule_id="DEP-JAVAX-DEPS-001",
                severity="ERROR",
                category="JAVAX_SOURCE",
                file=finding["file"],
                evidence=finding["evidence"],
                why_it_matters="Spring Boot 3 requires Jakarta APIs in application source.",
                deterministic_fix_available=False,
                suggested_fix="Migrate source import and API usage from javax.* to jakarta.*.",
                blocks_v1_build_test=True,
                blocks_v2_runtime=True,
            )
        )
    for finding in source_findings["resource_references"]:
        risks.append(
            PolicyRisk(
                rule_id="DEP-JAVAX-DEPS-001",
                severity="INFO",
                category="JAVAX_SOURCE",
                file=finding["file"],
                evidence=finding["evidence"],
                why_it_matters="Resource/logger references may be harmless names, but are tracked separately from Java imports.",
                deterministic_fix_available=False,
                suggested_fix="Review only if the name maps to a real runtime class.",
                blocks_v1_build_test=False,
                blocks_v2_runtime=False,
            )
        )

    tree_findings = _scan_dependency_tree(dependency_tree_text or _read_dependency_tree(root))
    for evidence in tree_findings["javax_dependencies"]:
        risks.append(
            PolicyRisk(
                rule_id="DEP-JAVAX-DEPS-001",
                severity="ERROR",
                category="JAVAX_DEPENDENCY",
                file="dependency-tree",
                evidence=evidence,
                why_it_matters="A runtime dependency that still pulls javax.servlet/persistence/validation/ws.rs can break under Boot 3/Jakarta.",
                deterministic_fix_available=False,
                suggested_fix="Upgrade or replace the dependency with a Jakarta-compatible artifact.",
                blocks_v1_build_test=False,
                blocks_v2_runtime=True,
            )
        )
    for evidence in tree_findings["internal_jars"]:
        risks.append(
            PolicyRisk(
                rule_id="DEP-INTERNAL-JAR-001",
                severity="WARNING",
                category="INTERNAL_JAR",
                file="dependency-tree",
                evidence=evidence,
                why_it_matters="Internal libraries can hide old Spring or javax.servlet APIs from source-only scans.",
                deterministic_fix_available=False,
                suggested_fix="Inspect the internal JAR API surface for Spring 6/Jakarta compatibility.",
                blocks_v1_build_test=False,
                blocks_v2_runtime=True,
            )
        )

    blocked_build = any(risk.blocks_v1_build_test and risk.severity in {"ERROR", "BLOCKER"} for risk in risks)
    blocked_runtime = any(risk.blocks_v2_runtime and risk.severity in {"WARNING", "ERROR", "BLOCKER"} for risk in risks)
    status = "FAIL" if blocked_build else "PASS_WITH_WARNINGS" if risks else "PASS"
    deterministic = tuple(
        {
            "rule_id": risk.rule_id,
            "file": risk.file,
            "suggested_fix": risk.suggested_fix,
        }
        for risk in risks
        if risk.deterministic_fix_available
    )
    advisory = tuple(
        {
            "rule_id": risk.rule_id,
            "severity": risk.severity,
            "evidence": risk.evidence,
            "suggested_fix": risk.suggested_fix,
        }
        for risk in risks
        if risk.severity in {"WARNING", "ERROR", "BLOCKER"} and not risk.deterministic_fix_available
    )
    return PolicyReport(
        schema_version=SCHEMA_VERSION,
        target_boot_version=target_boot,
        target_java_version=target_java,
        status=status,  # type: ignore[arg-type]
        risks=tuple(risks),
        deterministic_actions_available=deterministic,
        copilot_advisory_required=advisory,
        blocked_for_build_test=blocked_build,
        blocked_for_runtime=blocked_runtime,
        source_vs_dependency_jakarta_findings={
            **source_findings,
            **tree_findings,
            "jakarta_imports_ok": source_findings["jakarta_imports"],
        },
        pom_findings=pom_findings,
    )


def load_target_plan(run_dir: str | Path) -> dict[str, Any]:
    path = Path(run_dir) / "planning" / "target_dependency_plan.json"
    if not path.is_file():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return payload if isinstance(payload, dict) else {}


def _parse_pom(pom_path: Path) -> dict[str, Any]:
    tree = ET.parse(pom_path)
    root = tree.getroot()
    ns = _namespace(root.tag)
    properties: dict[str, str] = {}
    props = _find_child(root, "properties", ns)
    if props is not None:
        for child in list(props):
            if child.text is not None:
                properties[_local(child.tag)] = child.text.strip()
    dependencies: list[dict[str, str]] = []
    for dep in root.iter(_tag("dependency", ns)):
        group_id = _child_text(dep, "groupId", ns)
        artifact_id = _child_text(dep, "artifactId", ns)
        if not group_id or not artifact_id:
            continue
        dependencies.append(
            {
                "group_id": group_id,
                "artifact_id": artifact_id,
                "version": _child_text(dep, "version", ns),
                "scope": _child_text(dep, "scope", ns),
            }
        )
    return {"properties": properties, "dependencies": dependencies}


def _scan_source(root: Path) -> dict[str, Any]:
    source_imports: list[dict[str, str]] = []
    resource_references: list[dict[str, str]] = []
    jakarta_imports: list[dict[str, str]] = []
    for path in sorted(root.rglob("*")):
        rel = path.relative_to(root).as_posix()
        if not path.is_file() or _is_ignored_path(rel):
            continue
        if path.suffix == ".java":
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in JAVAX_PATTERNS:
                if re.search(rf"^\s*import\s+{re.escape(pattern)}(?:\.|\b)", text, re.MULTILINE):
                    source_imports.append({"file": rel, "evidence": pattern})
            if re.search(r"^\s*import\s+jakarta\.persistence(?:\.|\b)", text, re.MULTILINE):
                jakarta_imports.append({"file": rel, "evidence": "jakarta.persistence"})
            continue
        if any(part in rel for part in ("/resources/", "src/test/")) or path.name.startswith("logback"):
            text = path.read_text(encoding="utf-8", errors="replace")
            for pattern in JAVAX_PATTERNS:
                if pattern in text:
                    resource_references.append({"file": rel, "evidence": pattern})
    return {
        "source_imports": source_imports,
        "resource_references": resource_references,
        "jakarta_imports": jakarta_imports,
    }


def _scan_dependency_tree(text: str) -> dict[str, Any]:
    javax_dependencies: list[str] = []
    internal_jars: list[str] = []
    for line in text.splitlines():
        clean = line.strip()
        if not clean:
            continue
        if any(pattern in clean for pattern in JAVAX_PATTERNS):
            javax_dependencies.append(clean)
        lowered = clean.lower()
        if any(marker in lowered for marker in ("com.company", "com.mycorp", "corp", "internal")):
            internal_jars.append(clean)
    return {"javax_dependencies": javax_dependencies, "internal_jars": internal_jars}


def _read_dependency_tree(root: Path) -> str:
    candidates = [
        root / ".migration" / "dependency-tree.raw.txt",
        root / "dependency-tree.raw.txt",
    ]
    for path in candidates:
        if path.is_file():
            return path.read_text(encoding="utf-8", errors="replace")
    return ""


def _configured_zalando_target(plan: dict[str, Any]) -> str:
    configured = plan.get("configured_safe_versions")
    if not isinstance(configured, dict):
        return ""
    return str(configured.get("org.zalando:problem-spring-web") or "")


def _resolve_property_version(version: str, properties: dict[str, str]) -> str:
    match = re.fullmatch(r"\$\{([^}]+)\}", version.strip())
    if not match:
        return version
    return str(properties.get(match.group(1)) or version)


def _version_lte(version: str, ceiling: str) -> bool:
    if not version:
        return True
    if version.startswith("${"):
        return True
    return _version_tuple(version) <= _version_tuple(ceiling)


def _version_tuple(value: str) -> tuple[int, ...]:
    nums = re.findall(r"\d+", value)
    return tuple(int(num) for num in nums[:4]) if nums else (0,)


def _boot_major(version: str) -> int:
    try:
        return int(str(version).split(".", 1)[0])
    except (TypeError, ValueError):
        return 0


def _is_ignored_path(rel_path: str) -> bool:
    return any(part in {".git", "target", "build", "node_modules", ".migration"} for part in rel_path.split("/"))


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") and "}" in tag else ""


def _tag(name: str, ns: str) -> str:
    return f"{{{ns}}}{name}" if ns else name


def _local(tag: str) -> str:
    return tag.split("}", 1)[1] if tag.startswith("{") and "}" in tag else tag


def _find_child(parent: ET.Element, name: str, ns: str) -> ET.Element | None:
    return parent.find(_tag(name, ns))


def _child_text(parent: ET.Element, name: str, ns: str) -> str:
    child = _find_child(parent, name, ns)
    return child.text.strip() if child is not None and child.text else ""
