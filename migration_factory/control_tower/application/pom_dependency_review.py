"""F14 — Stage 3 dependency review service.

Loads evidence, detects baseline, classifies dependencies into
buckets using the generic policy layer. Read-only — no writes.
"""

from __future__ import annotations

import json
import re as _re
from pathlib import Path
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.application.pom_change_models import (
    PomBaseline,
    PomDependencyFinding,
    PomDependencyReview,
    PomChangeTarget,
)
from migration_factory.control_tower.application.pom_dependency_policy import (
    PomDependencyPolicy,
    DependencyControlMode,
    DEPENDENCY_BUCKETS,
)
from migration_factory.control_tower.domain.checksums import utc_now_text


class PomDependencyReviewer:
    """Read-only Stage 3 dependency review.

    Classifies dependencies into buckets using the generic
    dependency policy. Does not write files.
    """

    def __init__(
        self,
        policy: PomDependencyPolicy | None = None,
    ) -> None:
        self._policy = policy or PomDependencyPolicy()

    def review(
        self,
        *,
        job_id: str,
        stage: int,
        pom_content: str,
        pom_path: str,
        pom_deps_data: dict[str, Any] | None = None,
        target_dependency_plan: dict[str, Any] | None = None,
        dependency_policy_report: dict[str, Any] | None = None,
    ) -> PomDependencyReview:
        """Produce a full Stage 3 dependency review.

        Args:
            job_id: The V2 job ID
            stage: Must be 3 for apply, but review is allowed at any stage
            pom_content: Raw POM XML content
            pom_path: Path to root_pom (for reference, not exposed raw)
            pom_deps_data: Pre-parsed POM dependency metadata
            target_dependency_plan: Optional target dependency plan artifact
            dependency_policy_report: Optional dependency policy report
        """

        # Detect baseline
        baseline = self._detect_baseline(pom_content, target_dependency_plan)

        # Parse dependencies from POM content
        deps_data = pom_deps_data or self._parse_pom_deps(pom_content)

        # Build a fresh policy with this POM data
        policy = PomDependencyPolicy(pom_deps_data=deps_data)

        # Classify dependencies into buckets
        buckets: dict[str, list[PomDependencyFinding]] = {
            bucket: [] for bucket in DEPENDENCY_BUCKETS
        }

        all_findings: list[PomDependencyFinding] = []

        # Process direct dependencies
        for dep in deps_data.get("dependencies", []):
            finding = self._classify_dependency(
                dep=dep,
                policy=policy,
                baseline=baseline,
            )
            bucket = finding.bucket
            if bucket in buckets:
                buckets[bucket].append(finding)
            all_findings.append(finding)

        # Process plugins
        for plugin in deps_data.get("plugins", []):
            finding = self._classify_plugin(
                plugin=plugin,
                policy=policy,
                baseline=baseline,
            )
            bucket = finding.bucket
            if bucket in buckets:
                buckets[bucket].append(finding)
            all_findings.append(finding)

        # Process dependency management entries
        for entry in deps_data.get("dependency_management", []):
            finding = self._classify_dependency_management(
                entry=entry,
                policy=policy,
                baseline=baseline,
            )
            bucket = finding.bucket
            if bucket in buckets:
                buckets[bucket].append(finding)
            all_findings.append(finding)

        # Determine evidence
        evidence_loaded = ["root_pom"]
        evidence_missing: list[str] = []

        if target_dependency_plan:
            evidence_loaded.append("target_dependency_plan")
        else:
            evidence_missing.append("target_dependency_plan")

        if dependency_policy_report:
            evidence_loaded.append("dependency_policy_report")
        else:
            evidence_missing.append("dependency_policy_report")

        # Generate warnings
        warnings: list[str] = []
        if not deps_data.get("dependencies"):
            warnings.append("No <dependencies> section detected in root POM.")
        if evidence_missing:
            warnings.append(f"Missing evidence: {', '.join(evidence_missing)}")

        return PomDependencyReview(
            job_id=job_id,
            stage=stage,
            baseline=baseline,
            buckets=buckets,
            findings=tuple(all_findings),
            evidence_loaded=tuple(evidence_loaded),
            evidence_missing=tuple(evidence_missing),
            warnings=tuple(warnings),
            created_at=utc_now_text(),
        )

    # ── Baseline detection ──────────────────────────────────────────

    def _detect_baseline(
        self,
        pom_content: str,
        target_dependency_plan: dict[str, Any] | None,
    ) -> PomBaseline:
        """Detect Java/Spring Boot baseline from POM evidence."""
        detected_from: list[str] = ["root_pom"]
        java_version = ""
        boot_version = ""
        boot_location = "unknown"

        # Try parent spring-boot-starter-parent
        parent_match = _re.search(
            r"<parent>.*?<artifactId>spring-boot-starter-parent</artifactId>.*?<version>([^<]+)</version>.*?</parent>",
            pom_content, _re.DOTALL,
        )
        if parent_match:
            boot_version = parent_match.group(1)
            boot_location = "parent"

        # Try dependencyManagement BOM
        if not boot_version:
            bom_match = _re.search(
                r"<artifactId>spring-boot-dependencies</artifactId>.*?<version>([^<]+)</version>",
                pom_content, _re.DOTALL,
            )
            if bom_match:
                boot_version = bom_match.group(1)
                boot_location = "bom"

        # Try spring-boot.version property
        if not boot_version:
            prop_match = _re.search(
                r"<spring-boot\.version>([^<]+)</spring-boot\.version>",
                pom_content,
            )
            if prop_match:
                boot_version = prop_match.group(1)
                boot_location = "property"

        # Try target_dependency_plan
        if not boot_version and target_dependency_plan:
            bp = target_dependency_plan.get("spring_boot_version")
            if bp:
                boot_version = str(bp)
                boot_location = "target_dependency_plan"
                detected_from.append("target_dependency_plan")

        # java.version property
        java_match = _re.search(
            r"<java\.version>([^<]+)</java\.version>",
            pom_content,
        )
        if java_match:
            java_version = java_match.group(1)

        if not java_version:
            mvn_match = _re.search(
                r"<maven\.compiler\.(?:source|release)>([^<]+)</maven\.compiler\.(?:source|release)>",
                pom_content,
            )
            if mvn_match:
                java_version = mvn_match.group(1)

        return PomBaseline(
            java_version=java_version,
            spring_boot_version=boot_version,
            spring_boot_version_location=boot_location,
            detected_from=tuple(detected_from),
        )

    # ── Dependency parsing ──────────────────────────────────────────

    def _parse_pom_deps(self, pom_content: str) -> dict[str, Any]:
        """Minimal POM parser for dependency metadata."""
        result: dict[str, Any] = {
            "properties": {},
            "dependencies": [],
            "dependency_management": [],
            "plugins": [],
            "parent": {},
        }

        # Parse properties
        props_match = _re.search(
            r"<properties>(.*?)</properties>", pom_content, _re.DOTALL,
        )
        if props_match:
            props_block = props_match.group(1)
            for m in _re.finditer(r"<(\S+?)>([^<]*)</\1>", props_block):
                result["properties"][m.group(1)] = m.group(2)

        # Parse parent
        parent_match = _re.search(
            r"<parent>(.*?)</parent>", pom_content, _re.DOTALL,
        )
        if parent_match:
            parent_block = parent_match.group(1)
            pg = _re.search(r"<groupId>([^<]+)</groupId>", parent_block)
            pa = _re.search(r"<artifactId>([^<]+)</artifactId>", parent_block)
            pv = _re.search(r"<version>([^<]+)</version>", parent_block)
            result["parent"] = {
                "groupId": pg.group(1) if pg else "",
                "artifactId": pa.group(1) if pa else "",
                "version": pv.group(1) if pv else "",
            }

        # Parse dependencies
        for dep_match in _re.finditer(
            r"<dependency>(.*?)</dependency>", pom_content, _re.DOTALL,
        ):
            dep_block = dep_match.group(1)
            dg = _re.search(r"<groupId>([^<]+)</groupId>", dep_block)
            da = _re.search(r"<artifactId>([^<]+)</artifactId>", dep_block)
            dv = _re.search(r"<version>([^<]+)</version>", dep_block)
            ds = _re.search(r"<scope>([^<]+)</scope>", dep_block)
            result["dependencies"].append({
                "groupId": dg.group(1) if dg else "",
                "artifactId": da.group(1) if da else "",
                "version": dv.group(1) if dv else "",
                "scope": ds.group(1) if ds else "compile",
            })

        # Parse dependencyManagement
        dm_match = _re.search(
            r"<dependencyManagement>.*?<dependencies>(.*?)</dependencies>.*?</dependencyManagement>",
            pom_content, _re.DOTALL,
        )
        if dm_match:
            for dep_match in _re.finditer(
                r"<dependency>(.*?)</dependency>", dm_match.group(1), _re.DOTALL,
            ):
                dep_block = dep_match.group(1)
                dg = _re.search(r"<groupId>([^<]+)</groupId>", dep_block)
                da = _re.search(r"<artifactId>([^<]+)</artifactId>", dep_block)
                dv = _re.search(r"<version>([^<]+)</version>", dep_block)
                result["dependency_management"].append({
                    "groupId": dg.group(1) if dg else "",
                    "artifactId": da.group(1) if da else "",
                    "version": dv.group(1) if dv else "",
                })

        # Parse plugins
        plugins_match = _re.search(
            r"<build>.*?<plugins>(.*?)</plugins>.*?</build>",
            pom_content, _re.DOTALL,
        )
        if plugins_match:
            for plugin_match in _re.finditer(
                r"<plugin>(.*?)</plugin>", plugins_match.group(1), _re.DOTALL,
            ):
                plugin_block = plugin_match.group(1)
                pg = _re.search(r"<groupId>([^<]+)</groupId>", plugin_block)
                pa = _re.search(r"<artifactId>([^<]+)</artifactId>", plugin_block)
                pv = _re.search(r"<version>([^<]+)</version>", plugin_block)
                result["plugins"].append({
                    "groupId": pg.group(1) if pg else "",
                    "artifactId": pa.group(1) if pa else "",
                    "version": pv.group(1) if pv else "",
                })

        return result

    def parse_pom_deps(self, pom_content: str) -> dict[str, Any]:
        """Parse dependency metadata from live POM content.

        Public wrapper used by the editor/proposer so policy decisions are
        based on the current backend-resolved POM when callers do not provide
        pre-parsed metadata.
        """
        return self._parse_pom_deps(pom_content)

    # ── Classification helpers ──────────────────────────────────────

    def _classify_dependency(
        self,
        dep: dict[str, Any],
        policy: PomDependencyPolicy,
        baseline: PomBaseline,
    ) -> PomDependencyFinding:
        gid = dep.get("groupId", "")
        aid = dep.get("artifactId", "")
        version = dep.get("version", "")
        scope = dep.get("scope", "compile")
        name = f"{gid}:{aid}"

        bucket = policy.get_dependency_bucket(group_id=gid, artifact_id=aid)
        control_mode = policy._classify_dependency_control(gid, aid, None)

        # Determine risk and action
        risk = "low"
        can_apply = True
        action = "review"
        reason = ""

        if control_mode == DependencyControlMode.DIRECT_DEPENDENCY_VERSION:
            risk = "low"
            can_apply = True
            action = "can_apply" if version else "no_version"
            reason = f"Explicit dependency at version {version}" if version else "No version specified (may be BOM-managed)"
        elif control_mode == DependencyControlMode.PROPERTY_MANAGED_VERSION:
            risk = "low"
            can_apply = True
            action = "property_managed"
            reason = f"Version managed via property: {version}"
        elif control_mode == DependencyControlMode.SPRING_BOOT_BOM_MANAGED:
            risk = "medium"
            can_apply = True
            action = "bom_managed"
            reason = "Version managed by Spring Boot BOM"
        elif control_mode == DependencyControlMode.DEPENDENCY_MANAGEMENT_ENTRY:
            risk = "medium"
            can_apply = True
            action = "dependency_management"
            reason = "Version in dependencyManagement"
        elif control_mode == DependencyControlMode.NOT_PRESENT:
            risk = "high"
            can_apply = False
            action = "not_present"
            reason = "Dependency not found in POM"
        else:
            risk = "medium"
            can_apply = False
            action = "unknown"
            reason = f"Control mode: {control_mode.value}"

        return PomDependencyFinding(
            dependency_name=name,
            current_version=version,
            source_location="pom.xml:dependencies",
            bucket=bucket,
            control_mode=control_mode.value,
            risk=risk,
            recommended_action=action,
            can_apply_now=can_apply,
            reason=reason,
            evidence_source="root_pom",
        )

    def _classify_plugin(
        self,
        plugin: dict[str, Any],
        policy: PomDependencyPolicy,
        baseline: PomBaseline,
    ) -> PomDependencyFinding:
        gid = plugin.get("groupId", "")
        aid = plugin.get("artifactId", "")
        version = plugin.get("version", "")
        name = f"{gid}:{aid}"

        return PomDependencyFinding(
            dependency_name=name,
            current_version=version,
            source_location="pom.xml:plugins",
            bucket="build_plugins",
            control_mode=DependencyControlMode.PLUGIN_VERSION.value,
            risk="medium",
            recommended_action="review_plugin" if version else "no_version",
            can_apply_now=bool(version),
            reason=f"Build plugin {'at version ' + version if version else 'without explicit version'}",
            evidence_source="root_pom",
        )

    def _classify_dependency_management(
        self,
        entry: dict[str, Any],
        policy: PomDependencyPolicy,
        baseline: PomBaseline,
    ) -> PomDependencyFinding:
        gid = entry.get("groupId", "")
        aid = entry.get("artifactId", "")
        version = entry.get("version", "")
        name = f"{gid}:{aid}"

        bucket = policy.get_dependency_bucket(group_id=gid, artifact_id=aid)

        return PomDependencyFinding(
            dependency_name=name,
            current_version=version,
            source_location="pom.xml:dependencyManagement",
            bucket=bucket,
            control_mode=DependencyControlMode.DEPENDENCY_MANAGEMENT_ENTRY.value,
            risk="medium",
            recommended_action="review_managed",
            can_apply_now=False,  # DM changes require explicit action
            reason=f"Managed dependency at version {version}" if version else "Managed dependency without explicit version",
            evidence_source="root_pom",
        )
