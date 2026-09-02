from __future__ import annotations

import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path


ALLOWED_RULE_IDS = {
    "DEPENDENCY_ADD_H2_RUNTIME",
    "DEPENDENCY_ADD_VALIDATION_STARTER",
    "DEPENDENCY_REMOVE_TOMCAT9_OVERRIDE_BOOT3",
    "DEPENDENCY_REPLACE_JAVAX_SERVLET_API_WITH_JAKARTA",
    "DEPENDENCY_REPLACE_JAVAX_VALIDATION_WITH_JAKARTA",
    "DEPENDENCY_UPGRADE_ZALANDO_PROBLEM_SPRING_WEB_0291",
    "H2_SMOKE_CONFIG_ONLY",
    "JAKARTA_IMPORT_MECHANICAL_SOURCE",
}


@dataclass(frozen=True)
class RuleDecision:
    allowed: bool
    reason: str
    human_review_required: bool = False


def evaluate_rule(
    *,
    rule_id: str,
    sandbox_path: str | Path,
    touched_paths: list[str],
    unified_diff: str,
    failure_classification: dict | None = None,
    h2_required: bool = False,
) -> RuleDecision:
    if rule_id not in ALLOWED_RULE_IDS:
        return RuleDecision(False, f"rule id is not allowlisted: {rule_id}", human_review_required=True)

    sandbox = Path(sandbox_path)
    if rule_id == "DEPENDENCY_ADD_H2_RUNTIME":
        return _dependency_add_h2(sandbox, touched_paths, unified_diff, h2_required=h2_required)
    if rule_id == "DEPENDENCY_ADD_VALIDATION_STARTER":
        return _dependency_add_validation_starter(sandbox, touched_paths, unified_diff, failure_classification or {})
    if rule_id == "DEPENDENCY_REMOVE_TOMCAT9_OVERRIDE_BOOT3":
        return _remove_tomcat9_override(sandbox, touched_paths, unified_diff)
    if rule_id == "DEPENDENCY_REPLACE_JAVAX_SERVLET_API_WITH_JAKARTA":
        return _replace_javax_servlet(touched_paths, unified_diff)
    if rule_id == "DEPENDENCY_REPLACE_JAVAX_VALIDATION_WITH_JAKARTA":
        return _replace_javax_validation(touched_paths, unified_diff)
    if rule_id == "DEPENDENCY_UPGRADE_ZALANDO_PROBLEM_SPRING_WEB_0291":
        return _upgrade_zalando_problem(sandbox, touched_paths, unified_diff)
    if rule_id == "H2_SMOKE_CONFIG_ONLY":
        return _h2_smoke_config_only(touched_paths)
    if rule_id == "JAKARTA_IMPORT_MECHANICAL_SOURCE":
        return _jakarta_import_mechanical(touched_paths, unified_diff)
    return RuleDecision(False, f"rule id is not implemented: {rule_id}", human_review_required=True)


def _dependency_add_h2(sandbox: Path, paths: list[str], diff: str, *, h2_required: bool) -> RuleDecision:
    if not h2_required:
        return RuleDecision(False, "H2 runtime dependency can only be auto-applied for H2 smoke validation")
    if paths != ["pom.xml"]:
        return RuleDecision(False, "H2 dependency rule must touch only pom.xml")
    pom_text = _read(sandbox / "pom.xml")
    if _has_dependency(pom_text, "com.h2database", "h2"):
        return RuleDecision(False, "pom.xml already contains com.h2database:h2")
    added = _added_lines(diff)
    added_text = "\n".join(added)
    if "com.h2database" not in added_text or "<artifactId>h2</artifactId>" not in added_text:
        return RuleDecision(False, "patch does not add com.h2database:h2")
    if "<scope>runtime</scope>" not in added_text and "<scope>test</scope>" not in added_text:
        return RuleDecision(False, "H2 dependency must use runtime or test scope")
    return RuleDecision(True, "H2 runtime dependency POM-only patch is allowed")


def _dependency_add_validation_starter(
    sandbox: Path,
    paths: list[str],
    diff: str,
    failure: dict,
) -> RuleDecision:
    if paths != ["pom.xml"]:
        return RuleDecision(False, "validation starter rule must touch only pom.xml")
    failure_text = str(failure).lower()
    if "validation" not in failure_text and "validator" not in failure_text:
        return RuleDecision(False, "failure evidence does not indicate missing validation dependency")
    pom_text = _read(sandbox / "pom.xml")
    boot_version = _spring_boot_version(pom_text)
    if boot_version and not (boot_version.startswith("2.7.") or boot_version.startswith("3.")):
        return RuleDecision(False, "validation starter rule supports only Boot 2.7 or Boot 3 targets")
    added_text = "\n".join(_added_lines(diff))
    if "spring-boot-starter-validation" not in added_text:
        return RuleDecision(False, "patch does not add spring-boot-starter-validation")
    return RuleDecision(True, "validation starter POM-only patch is allowed")


def _remove_tomcat9_override(sandbox: Path, paths: list[str], diff: str) -> RuleDecision:
    if paths != ["pom.xml"]:
        return RuleDecision(False, "Tomcat override rule must touch only pom.xml")
    pom_text = _read(sandbox / "pom.xml")
    boot_version = _spring_boot_version(pom_text)
    if not boot_version.startswith("3."):
        return RuleDecision(False, "Tomcat 9 override removal is allowed only for Spring Boot 3 targets")
    removed_text = "\n".join(_removed_lines(diff))
    added_text = "\n".join(_added_lines(diff))
    if "tomcat" not in removed_text.lower() or "9." not in removed_text:
        return RuleDecision(False, "patch does not remove an explicit Tomcat 9 override")
    if "tomcat" in added_text.lower() and "<version>" in added_text:
        return RuleDecision(False, "patch adds a custom Tomcat version")
    return RuleDecision(True, "explicit Tomcat 9 override removal is allowed")


def _replace_javax_servlet(paths: list[str], diff: str) -> RuleDecision:
    if paths != ["pom.xml"]:
        return RuleDecision(False, "servlet API replacement must touch only pom.xml")
    removed = "\n".join(_removed_lines(diff))
    added = "\n".join(_added_lines(diff))
    if "javax.servlet" not in removed or "jakarta.servlet" not in added:
        return RuleDecision(False, "patch must replace javax servlet coordinate with jakarta servlet coordinate")
    return RuleDecision(True, "servlet API coordinate replacement is allowed")


def _replace_javax_validation(paths: list[str], diff: str) -> RuleDecision:
    if paths != ["pom.xml"]:
        return RuleDecision(False, "validation API replacement must touch only pom.xml")
    removed = "\n".join(_removed_lines(diff))
    added = "\n".join(_added_lines(diff))
    if "javax.validation" not in removed or "jakarta.validation" not in added:
        return RuleDecision(False, "patch must replace javax validation coordinate with jakarta validation coordinate")
    return RuleDecision(True, "validation API coordinate replacement is allowed")


def _upgrade_zalando_problem(sandbox: Path, paths: list[str], diff: str) -> RuleDecision:
    if paths != ["pom.xml"]:
        return RuleDecision(False, "Zalando upgrade rule must touch only pom.xml")
    pom_text = _read(sandbox / "pom.xml")
    if "org.zalando" not in pom_text or "problem-spring-web" not in pom_text:
        return RuleDecision(False, "pom.xml does not contain a Zalando problem-spring-web dependency")
    removed_versions = re.findall(r"<version>\s*([^<]+)\s*</version>", "\n".join(_removed_lines(diff)))
    if not removed_versions or not any(_version_lt(version, "0.29.1") for version in removed_versions):
        return RuleDecision(False, "patch does not upgrade a version below 0.29.1")
    if "0.29.1" not in "\n".join(_added_lines(diff)):
        return RuleDecision(False, "patch must update to 0.29.1")
    return RuleDecision(True, "Zalando problem-spring-web 0.29.1 upgrade is allowed")


def _h2_smoke_config_only(paths: list[str]) -> RuleDecision:
    allowed_prefixes = ("runtime/h2/", "repairs/", "failures/")
    if all(path.startswith(allowed_prefixes) for path in paths):
        return RuleDecision(True, "H2 smoke config-only patch is allowed")
    return RuleDecision(False, "H2 smoke config rule may touch only generated run-dir H2 config or repair metadata")


def _jakarta_import_mechanical(paths: list[str], diff: str) -> RuleDecision:
    if any(not path.endswith(".java") for path in paths):
        return RuleDecision(False, "Jakarta import mechanical rule may touch only Java source files")
    if any(_looks_security_path(path) for path in paths):
        return RuleDecision(False, "Jakarta import mechanical rule cannot touch security files", human_review_required=True)
    for line in _changed_lines(diff):
        body = line[1:].strip()
        if not (body.startswith("import javax.") or body.startswith("import jakarta.") or body.startswith("package javax.") or body.startswith("package jakarta.")):
            return RuleDecision(False, "source diff is not import/package-only")
    return RuleDecision(True, "Jakarta import/package-only source patch is allowed")


def _added_lines(diff: str) -> list[str]:
    return [line[1:] for line in diff.splitlines() if line.startswith("+") and not line.startswith("+++")]


def _removed_lines(diff: str) -> list[str]:
    return [line[1:] for line in diff.splitlines() if line.startswith("-") and not line.startswith("---")]


def _changed_lines(diff: str) -> list[str]:
    return [
        line
        for line in diff.splitlines()
        if (line.startswith("+") and not line.startswith("+++")) or (line.startswith("-") and not line.startswith("---"))
    ]


def _read(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except OSError:
        return ""


def _has_dependency(pom_text: str, group_id: str, artifact_id: str) -> bool:
    return f"<groupId>{group_id}</groupId>" in pom_text and f"<artifactId>{artifact_id}</artifactId>" in pom_text


def _spring_boot_version(pom_text: str) -> str:
    try:
        root = ET.fromstring(pom_text)
    except ET.ParseError:
        root = None
    if root is not None:
        ns = {"m": root.tag.split("}", 1)[0].strip("{")} if root.tag.startswith("{") else {}
        parent = root.find("m:parent" if ns else "parent", ns)
        if parent is not None:
            artifact = parent.findtext("m:artifactId" if ns else "artifactId", default="", namespaces=ns)
            version = parent.findtext("m:version" if ns else "version", default="", namespaces=ns)
            if artifact == "spring-boot-starter-parent":
                return version
    match = re.search(r"<spring-boot.version>\s*([^<]+)\s*</spring-boot.version>", pom_text)
    return match.group(1).strip() if match else ""


def _version_lt(current: str, target: str) -> bool:
    def parts(value: str) -> tuple[int, ...]:
        return tuple(int(part) for part in re.findall(r"\d+", value)[:4])

    return parts(current) < parts(target)


def _looks_security_path(path: str) -> bool:
    lowered = path.lower()
    return "security" in lowered or "auth" in lowered or "jwt" in lowered
