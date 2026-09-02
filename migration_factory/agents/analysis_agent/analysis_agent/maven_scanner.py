import hashlib
import json
from pathlib import Path
import re
import xml.etree.ElementTree as ET

import yaml

from migration_factory.control_tower.schemas.profile_model import (
    SourceProfileDetectionArtifact,
    SourceProfileEvidenceRef,
    SourceProfileFacts,
    SourceProfileSignal,
)


DEFAULT_TARGET_STACK = {
    "java": "17",
    "spring_boot": "3.5.14",
}


def _default_scan_result(target, warning):
    target_stack_payload = {
        "java": str(target.get("java", "17")),
        "spring_boot": str(target.get("spring_boot", "3.5.14")),
    }
    for optional_key in ("spring_framework", "build"):
        if target.get(optional_key):
            target_stack_payload[optional_key] = str(target[optional_key])

    return {
        "source_stack": {
            "java": "unknown",
            "spring_boot": "unknown",
            "build_tool": "unknown",
        },
        "project_structure": {
            "modules": [],
            "module_count": 0,
        },
        "target_stack": target_stack_payload,
        "warnings": [warning],
    }


def load_profile_target_stack(ai_hub_path, profile_id):
    if not ai_hub_path or not profile_id:
        return DEFAULT_TARGET_STACK.copy()

    profile_path = Path(ai_hub_path) / "profiles" / f"{profile_id}.yaml"
    if not profile_path.is_file():
        return DEFAULT_TARGET_STACK.copy()

    profile = yaml.safe_load(profile_path.read_text(encoding="utf-8")) or {}
    target = profile.get("target") if isinstance(profile, dict) else None
    if not isinstance(target, dict):
        return DEFAULT_TARGET_STACK.copy()

    stack = DEFAULT_TARGET_STACK.copy()
    for key in ("java", "spring_boot", "spring_framework", "build"):
        if target.get(key):
            stack[key] = str(target[key])
    return stack


def scan_root_pom(file_path, target_stack=None):
    ns = {"mvn": "http://maven.apache.org/POM/4.0.0"}
    target = dict(target_stack or DEFAULT_TARGET_STACK)
    pom_path = Path(file_path)

    try:
        tree = ET.parse(file_path)
        root = tree.getroot()

        properties = _maven_properties(root, ns)
        spring_boot = _detect_spring_boot_version(root, ns, properties)

        java_ver_elem = root.find(".//mvn:properties/mvn:java.version", ns)
        compiler_source_elem = root.find(".//mvn:properties/mvn:maven.compiler.source", ns)
        compiler_release_elem = root.find(".//mvn:properties/mvn:maven.compiler.release", ns)
        java_version = "unknown"
        for candidate in (java_ver_elem, compiler_release_elem, compiler_source_elem):
            if candidate is not None and candidate.text:
                java_version = candidate.text
                break

        modules = [m.text for m in root.findall(".//mvn:modules/mvn:module", ns)]
        target_stack_payload = {
            "java": str(target.get("java", "17")),
            "spring_boot": str(target.get("spring_boot", "3.5.14")),
        }
        for optional_key in ("spring_framework", "build"):
            if target.get(optional_key):
                target_stack_payload[optional_key] = str(target[optional_key])

        return {
            "source_stack": {
                "java": java_version,
                "spring_boot": spring_boot,
                "build_tool": "maven" if pom_path.name == "pom.xml" else "unknown",
            },
            "project_structure": {
                "modules": modules,
                "module_count": len(modules),
            },
            "target_stack": target_stack_payload,
            "warnings": _target_warnings(target, java_version, spring_boot),
        }
    except Exception as e:
        result = _default_scan_result(target, f"Unable to parse root pom.xml: {e}")
        if pom_path.name == "pom.xml" and pom_path.exists():
            result["source_stack"]["build_tool"] = "maven"
        return result


def build_source_profile_detection_for_root_pom(
    file_path,
    *,
    job_id,
    created_at,
    target_profile=None,
    checkpoint_id=None,
    artifact_revision_id=None,
    artifact_id=None,
    artifact_ref="analysis:source-profile-detection",
    evidence_ref="analysis:maven-root-pom",
    target_stack=None,
):
    scan_result = scan_root_pom(file_path, target_stack=target_stack)
    checksum = _file_sha256_checksum(Path(file_path))
    return build_source_profile_detection(
        scan_result,
        job_id=job_id,
        created_at=created_at,
        target_profile=target_profile,
        checkpoint_id=checkpoint_id,
        artifact_revision_id=artifact_revision_id,
        artifact_id=artifact_id,
        artifact_ref=artifact_ref,
        evidence_ref=evidence_ref,
        evidence_checksum=checksum,
    )


def build_source_profile_detection(
    scan_result,
    *,
    job_id,
    created_at,
    target_profile=None,
    checkpoint_id=None,
    artifact_revision_id=None,
    artifact_id=None,
    artifact_ref="analysis:source-profile-detection",
    evidence_ref="analysis:maven-root-pom",
    evidence_checksum=None,
):
    source_stack = scan_result.get("source_stack", {}) if isinstance(scan_result, dict) else {}
    project_structure = (
        scan_result.get("project_structure", {}) if isinstance(scan_result, dict) else {}
    )
    java_version = str(source_stack.get("java", "unknown"))
    spring_boot_version = str(source_stack.get("spring_boot", "unknown"))
    build_tool = str(source_stack.get("build_tool", "unknown"))
    modules = tuple(str(module) for module in project_structure.get("modules", ()) or ())
    module_count = int(project_structure.get("module_count", len(modules)) or 0)

    detected_profile, confidence, uncertainty_notes = infer_source_profile_from_stack(
        java_version=java_version,
        spring_boot_version=spring_boot_version,
    )
    facts = SourceProfileFacts(
        java_version=java_version,
        spring_boot_version=spring_boot_version,
        build_tool=build_tool,
        module_count=module_count,
        modules=modules,
    )
    checksum = evidence_checksum or _checksum_text({
        "source_stack": source_stack,
        "project_structure": project_structure,
    })
    evidence = SourceProfileEvidenceRef(
        evidence_ref=evidence_ref,
        evidence_type="maven_root_pom",
        checksum=checksum,
        description="Root Maven POM facts inspected by Analysis",
    )
    signals = _source_profile_signals(
        evidence_ref=evidence_ref,
        java_version=java_version,
        spring_boot_version=spring_boot_version,
        build_tool=build_tool,
    )
    artifact_payload = {
        "artifact_id": artifact_id or f"{job_id}:source-profile-detection",
        "artifact_kind": "source_profile_detection",
        "artifact_ref": artifact_ref,
        "job_id": job_id,
        "stage_index": 1,
        "checkpoint_id": checkpoint_id,
        "artifact_revision_id": artifact_revision_id,
        "detected_source_profile": detected_profile,
        "target_profile": target_profile,
        "confidence": confidence,
        "uncertainty_notes": uncertainty_notes,
        "evidence_refs": (evidence,),
        "evidence_checksums": (checksum,),
        "profile_signals": signals,
        "profile_facts": facts,
        "created_at": created_at,
        "produced_by": "analysis",
    }
    checksum_payload = {
        key: (
            value.model_dump(mode="json")
            if hasattr(value, "model_dump")
            else [item.model_dump(mode="json") if hasattr(item, "model_dump") else item for item in value]
            if isinstance(value, tuple)
            else value
        )
        for key, value in artifact_payload.items()
    }
    artifact_checksum = _checksum_text(checksum_payload)
    return SourceProfileDetectionArtifact(
        artifact_checksum=artifact_checksum,
        **artifact_payload,
    )


def infer_source_profile_from_stack(*, java_version, spring_boot_version):
    java_major = _major_version(java_version)
    boot_major = _major_version(spring_boot_version)
    spring_boot_text = str(spring_boot_version or "").strip()

    notes = []
    if boot_major == 2:
        confidence = 0.9 if java_major == 11 else 0.7
        if java_major not in (None, 11):
            notes.append("Spring Boot 2 detected with a Java version outside the canonical Java 11 profile.")
        if spring_boot_text.startswith("2.1"):
            return "springboot-2.1-java11", confidence, tuple(notes)
        if spring_boot_text.startswith("2.7"):
            return "springboot-2.7-java11", confidence, tuple(notes)
        notes.append("Spring Boot 2 detected and normalized to the canonical Spring Boot 2.7 source profile.")
        return "springboot-2.7-java11", confidence, tuple(notes)

    if boot_major == 3:
        if java_major is None:
            return (
                "springboot-3.5-java17",
                0.65,
                ("Spring Boot 3 detected but Java version was not resolved.",),
            )
        if java_major >= 21:
            return "springboot-3.5-java21", 0.9, tuple(notes)
        if java_major >= 17:
            return "springboot-3.5-java17", 0.9, tuple(notes)
        return (
            "springboot-3.5-java17",
            0.6,
            ("Spring Boot 3 detected with Java below the expected Java 17 baseline.",),
        )

    if boot_major == 4:
        return (
            "springboot-3.5-java21",
            0.4,
            ("Spring Boot 4 was detected, but it is not selectable as a source profile yet.",),
        )

    return (
        "springboot-2.7-java11",
        0.2,
        ("Spring Boot version was not resolved; default source profile is uncertain.",),
    )


def _target_warnings(target, source_java, source_boot):
    warnings = []
    target_boot = str(target.get("spring_boot", ""))
    target_java = str(target.get("java", ""))
    if target_boot.startswith("4."):
        warnings.extend(
            [
                "Spring Boot 4 requires Spring Framework 7.x.",
                "Spring Boot 4 uses Jakarta EE 11 / Servlet 6.1 baseline.",
                "Boot 3 deprecated APIs removed in Boot 4 must be reviewed.",
                "Spring Cloud compatibility must be reviewed.",
                "Spring Security, Spring Data, Hibernate, and custom starter risk requires human review.",
                "javax.* leftovers must be eliminated before Boot 4 readiness.",
                "Maven version and Java runtime must match Boot 4 target validation gates.",
                "Official Boot guidance prefers latest 3.5.x before Boot 4; direct migration should fall back if unstable.",
            ]
        )
    if target_java.startswith("21") and not str(source_java).startswith("21"):
        warnings.append("Target Java 21 requires a Java 21-capable runtime during target validation.")
    if str(source_boot).startswith("2.") and target_boot.startswith("4."):
        warnings.append("Direct Spring Boot 2.x to 4.x migration is sandbox-only and high risk.")
    return warnings


def _source_profile_signals(*, evidence_ref, java_version, spring_boot_version, build_tool):
    return (
        SourceProfileSignal(
            signal_name="spring_boot_version",
            value=spring_boot_version,
            evidence_ref=evidence_ref,
            confidence_weight=0.55 if spring_boot_version != "unknown" else 0.1,
        ),
        SourceProfileSignal(
            signal_name="java_version",
            value=java_version,
            evidence_ref=evidence_ref,
            confidence_weight=0.35 if java_version != "unknown" else 0.1,
        ),
        SourceProfileSignal(
            signal_name="build_tool",
            value=build_tool,
            evidence_ref=evidence_ref,
            confidence_weight=0.1 if build_tool != "unknown" else 0.0,
        ),
    )


def _file_sha256_checksum(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _checksum_text(value):
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return f"sha256:{hashlib.sha256(payload.encode('utf-8')).hexdigest()}"


def _major_version(value):
    text = str(value or "").strip()
    if text.startswith("1."):
        text = text[2:]
    match = re.search(r"\d+", text)
    if not match:
        return None
    return int(match.group(0))


def _maven_properties(root, ns):
    properties = {}
    properties_elem = root.find("./mvn:properties", ns)
    if properties_elem is None:
        return properties

    for child in list(properties_elem):
        key = _strip_namespace(child.tag)
        value = _text(child)
        if key and value:
            properties[key] = value
    return properties


def _detect_spring_boot_version(root, ns, properties):
    for property_name in ("spring-boot.version", "spring.boot.version"):
        version = _resolve_property_placeholders(properties.get(property_name, ""), properties)
        if version:
            return version

    parent_group = root.find("./mvn:parent/mvn:groupId", ns)
    parent_artifact = root.find("./mvn:parent/mvn:artifactId", ns)
    parent_version = root.find("./mvn:parent/mvn:version", ns)
    if (
        _text(parent_group) == "org.springframework.boot"
        and _text(parent_artifact) == "spring-boot-starter-parent"
    ):
        version = _resolve_property_placeholders(_text(parent_version), properties)
        if version:
            return version

    for dependency in root.findall("./mvn:dependencyManagement/mvn:dependencies/mvn:dependency", ns):
        if (
            _text(dependency.find("./mvn:groupId", ns)) == "org.springframework.boot"
            and _text(dependency.find("./mvn:artifactId", ns)) == "spring-boot-dependencies"
        ):
            version = _resolve_property_placeholders(
                _text(dependency.find("./mvn:version", ns)),
                properties,
            )
            if version:
                return version

    if parent_group is None and parent_artifact is None:
        version = _resolve_property_placeholders(_text(parent_version), properties)
        if version:
            return version

    return "unknown"


def _resolve_property_placeholders(value, properties):
    value = str(value or "").strip()
    if not value:
        return ""

    def replace(match):
        property_name = match.group(1)
        return str(properties.get(property_name, match.group(0))).strip()

    resolved = re.sub(r"\$\{([^}]+)\}", replace, value)
    return resolved.strip()


def _text(element):
    return element.text.strip() if element is not None and element.text else ""


def _strip_namespace(tag):
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag
