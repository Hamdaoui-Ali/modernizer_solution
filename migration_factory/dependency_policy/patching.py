from __future__ import annotations

import difflib
import json
import os
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

from migration_factory.dependency_policy.models import PolicyReport

APPLY_ENV = "AI_MIGRATION_APPLY_DEPENDENCY_POLICY_FIXES"
TRUE_VALUES = {"1", "true", "yes", "on"}


def apply_policy_patches_if_enabled(
    *,
    sandbox_path: str | Path,
    run_dir: str | Path,
    report: PolicyReport,
    target_plan: dict[str, Any] | None = None,
    env: dict[str, str] | None = None,
) -> dict[str, Any]:
    source = env if env is not None else os.environ
    enabled = str(source.get(APPLY_ENV, "")).strip().lower() in TRUE_VALUES
    assessment_dir = Path(run_dir) / "assessment"
    assessment_dir.mkdir(parents=True, exist_ok=True)
    plan_path = assessment_dir / "policy_patch_plan.json"
    result_path = assessment_dir / "policy_patch_result.json"
    diff_path = assessment_dir / "policy_patch_pom.diff"
    payload = {
        "enabled": enabled,
        "auto_apply_copilot_allowed": False,
        "safe_actions": [],
    }

    pom_path = Path(sandbox_path) / "pom.xml"
    if not enabled or not pom_path.is_file():
        plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        result = {"status": "SKIPPED", "enabled": enabled, "applied": False, "actions_applied": []}
        result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        return {"policy_patch_plan": plan_path, "policy_patch_result": result_path, "policy_patch_applied": False}

    risks = [risk for risk in report.risks if risk.deterministic_fix_available]
    for risk in risks:
        if risk.rule_id in {"DEP-TOMCAT-BOOT3-001", "DEP-TOMCAT-BOOT3-002", "DEP-ZALANDO-BOOT3-001"}:
            payload["safe_actions"].append(
                {
                    "rule_id": risk.rule_id,
                    "file": risk.file,
                    "evidence": risk.evidence,
                    "suggested_fix": risk.suggested_fix,
                }
            )
    before = pom_path.read_text(encoding="utf-8")
    actions = _apply_pom_policy_actions(pom_path, payload["safe_actions"], target_plan or {})
    after = pom_path.read_text(encoding="utf-8")
    if before != after:
        diff = difflib.unified_diff(
            before.splitlines(keepends=True),
            after.splitlines(keepends=True),
            fromfile="pom.xml.before",
            tofile="pom.xml.after",
        )
        diff_path.write_text("".join(diff), encoding="utf-8")
    else:
        diff_path.write_text("", encoding="utf-8")

    plan_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    result = {
        "status": "APPLIED" if actions else "NO_CHANGES",
        "enabled": enabled,
        "applied": bool(actions),
        "actions_applied": actions,
        "diff_ref": str(diff_path),
        "auto_apply_copilot_allowed": False,
    }
    result_path.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "policy_patch_plan": plan_path,
        "policy_patch_result": result_path,
        "policy_patch_diff": diff_path,
        "policy_patch_applied": bool(actions),
    }


def _apply_pom_policy_actions(pom_path: Path, safe_actions: list[dict[str, Any]], target_plan: dict[str, Any]) -> list[dict[str, Any]]:
    if not safe_actions:
        return []
    tree = ET.parse(pom_path)
    root = tree.getroot()
    ns = _namespace(root.tag)
    if ns:
        ET.register_namespace("", ns)
    actions: list[dict[str, Any]] = []
    action_ids = {str(action.get("rule_id")) for action in safe_actions}
    if "DEP-TOMCAT-BOOT3-001" in action_ids:
        props = root.find(_tag("properties", ns))
        prop = props.find(_tag("tomcat.version", ns)) if props is not None else None
        if props is not None and prop is not None and (prop.text or "").strip().startswith("9."):
            props.remove(prop)
            actions.append({"rule_id": "DEP-TOMCAT-BOOT3-001", "action": "removed_property", "property": "tomcat.version"})
    if "DEP-TOMCAT-BOOT3-002" in action_ids:
        for dep in root.iter(_tag("dependency", ns)):
            group = _child_text(dep, "groupId", ns)
            artifact = _child_text(dep, "artifactId", ns)
            version = dep.find(_tag("version", ns))
            if group == "org.apache.tomcat.embed" and artifact.startswith("tomcat-embed-") and version is not None:
                dep.remove(version)
                actions.append({"rule_id": "DEP-TOMCAT-BOOT3-002", "action": "removed_dependency_version", "dependency": f"{group}:{artifact}"})
    if "DEP-ZALANDO-BOOT3-001" in action_ids:
        target = _configured_zalando_target(target_plan)
        if target:
            _set_zalando_version(root, ns, target, actions)
    if actions:
        tree.write(pom_path, encoding="utf-8", xml_declaration=True)
    return actions


def _set_zalando_version(root: ET.Element, ns: str, target: str, actions: list[dict[str, Any]]) -> None:
    props = root.find(_tag("properties", ns))
    if props is not None:
        prop = props.find(_tag("org-zalando.version", ns))
        if prop is not None:
            before = (prop.text or "").strip()
            if before != target:
                prop.text = target
                actions.append({"rule_id": "DEP-ZALANDO-BOOT3-001", "action": "updated_property", "property": "org-zalando.version", "before": before, "after": target})
            return
    for dep in root.iter(_tag("dependency", ns)):
        if _child_text(dep, "groupId", ns) == "org.zalando" and _child_text(dep, "artifactId", ns) == "problem-spring-web":
            version = dep.find(_tag("version", ns))
            if version is not None and not (version.text or "").strip().startswith("${"):
                before = (version.text or "").strip()
                version.text = target
                actions.append({"rule_id": "DEP-ZALANDO-BOOT3-001", "action": "updated_dependency_version", "dependency": "org.zalando:problem-spring-web", "before": before, "after": target})


def _configured_zalando_target(plan: dict[str, Any]) -> str:
    versions = plan.get("configured_safe_versions")
    if isinstance(versions, dict):
        return str(versions.get("org.zalando:problem-spring-web") or "")
    return ""


def _namespace(tag: str) -> str:
    return tag[1:].split("}", 1)[0] if tag.startswith("{") and "}" in tag else ""


def _tag(name: str, ns: str) -> str:
    return f"{{{ns}}}{name}" if ns else name


def _child_text(parent: ET.Element, name: str, ns: str) -> str:
    child = parent.find(_tag(name, ns))
    return child.text.strip() if child is not None and child.text else ""
