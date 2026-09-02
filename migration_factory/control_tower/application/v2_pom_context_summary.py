"""V2 POM Intelligence Summary (F04).

Creates a bounded PomContextSummary from existing Maven/POM tools so the LLM
can select and justify a governed POM repair path without becoming a free-form
POM rewrite engine.

The PomContextSummary is a backend-owned artifact JSON. It is not a V2 model
schema — it is evidence that enriches the ContextPack metadata via
pom_summary_ref.

Responsibilities:
1. Resolve sandbox POM path from backend state.
2. Call existing scan_root_pom(), detect_spring_boot_version(), and
   build command detection helpers.
3. Determine target Boot/Java from V2 stage/profile/command state
   (not scanner defaults).
4. Map observed issue to rule_registry.ALLOWED_RULE_IDS.
5. Emit pom_summary_created event and attach pom_summary_ref to
   ContextPack metadata (event emission, not artifact persistence).
6. Allow LLM to recommend deterministic rule id or POM patch intent
   with rationale only (backend never applies patches in F04).

Non-goals:
- New POM parser, POM agent, POM repair engine, or free-form POM rewrite.
- Patch application — backend must not apply any patch in F04.
"""

from __future__ import annotations

import re as _re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text

# Existing POM/Maven tools this service reuses
from migration_factory.agents.analysis_agent.analysis_agent.maven_scanner import (
    scan_root_pom,
    load_profile_target_stack,
    DEFAULT_TARGET_STACK,
)
from migration_factory.agents.transformation_agent.pom_patches import (
    detect_spring_boot_version as pom_detect_boot_version,
    SpringBootVersionDetection,
)
from migration_factory.agents.build_agent.detection import (
    full_validation_command,
    detect_java_project,
    BuildTool,
)
from migration_factory.repair_loop.rule_registry import (
    ALLOWED_RULE_IDS,
)


# ── Data types ────────────────────────────────────────────────────


@dataclass(frozen=True)
class PomContextSummary:
    """Backend-owned POM context summary artifact.

    Produced from existing Maven/POM tools. Not a model schema —
    this is evidence attached to ContextPack metadata via pom_summary_ref.

    The pom_summary_ref is a stable artifact reference that can be
    passed to F02/ContextPack metadata and persisted via
    pom_summary_created events.
    """

    pom_summary_ref: str
    pom_path: str
    spring_boot_version: str
    spring_boot_version_location: str  # parent, bom, property, plugin, unknown
    java_version_property: str
    maven_compiler_release: str
    maven_compiler_source: str
    maven_compiler_target: str
    target_stage_boot: str
    target_stage_java: str
    candidate_deterministic_rules: tuple[str, ...]
    validation_command: str
    warnings: tuple[str, ...]
    created_at: str


# ── Summary builder ────────────────────────────────────────────────


class PomContextSummaryBuilder:
    """Build PomContextSummary artifacts from sandbox POM and V2 stage state.

    Reuses existing Maven scanner, pom_patches detection, build command
    detection, and rule_registry.
    """

    @staticmethod
    def build_summary(
        *,
        sandbox_path: str | Path,
        target_boot: str = "3.5.14",
        target_java: str = "17",
        ai_hub_path: str | None = None,
        profile_id: str | None = None,
    ) -> PomContextSummary:
        """Build a PomContextSummary from a sandbox POM.

        Args:
            sandbox_path: Path to the sandbox directory containing pom.xml.
            target_boot: Target Spring Boot version from V2 stage/profile state.
            target_java: Target Java version from V2 stage/profile state.
            ai_hub_path: Optional AI Hub path for profile-based target stack.
            profile_id: Optional profile id for target stack resolution.

        Returns:
            A PomContextSummary artifact with POM analysis results.
        """
        sandbox = Path(sandbox_path)
        pom_file = sandbox / "pom.xml"

        # 1. Resolve target stack from profile if available, else use explicit params
        target_stack = PomContextSummaryBuilder._resolve_target_stack(
            target_boot=target_boot,
            target_java=target_java,
            ai_hub_path=ai_hub_path,
            profile_id=profile_id,
        )

        # Use resolved target values (profile overrides may change them)
        resolved_target_boot = str(target_stack.get("spring_boot", target_boot))
        resolved_target_java = str(target_stack.get("java", target_java))

        # 2. Scan POM with existing scanner
        scan_result = scan_root_pom(str(pom_file), target_stack=target_stack)

        # 3. Detect Spring Boot version location with pom_patches helper
        boot_detection = pom_detect_boot_version(sandbox)
        boot_version = ""
        boot_location = "unknown"
        if boot_detection is not None:
            boot_version = boot_detection.version
            if boot_detection.location == "parent":
                boot_location = "parent"
            elif boot_detection.location == "bom":
                boot_location = "bom"
            elif boot_detection.location == "property":
                boot_location = "property"
            elif boot_detection.location == "plugin":
                boot_location = "plugin"
            elif boot_detection.detected_locations:
                boot_location = boot_detection.detected_locations[0]
        else:
            # Fall back to scanner result
            boot_version = scan_result.get("source_stack", {}).get("spring_boot", "unknown")

        # 4. Extract Java and compiler settings from scan result
        source_java = scan_result.get("source_stack", {}).get("java", "unknown")

        # 5. Determine compiler properties from the parsed POM XML directly
        compiler_release = ""
        compiler_source = ""
        compiler_target = ""

        try:
            ns = {"mvn": "http://maven.apache.org/POM/4.0.0"}
            tree = ET.parse(str(pom_file))
            root = tree.getroot()
            props_elem = root.find(".//mvn:properties", ns)
            if props_elem is not None:
                for child in list(props_elem):
                    tag = _strip_ns(child.tag)
                    text = str(child.text or "").strip()
                    if tag == "maven.compiler.release":
                        compiler_release = text
                    elif tag == "maven.compiler.source":
                        compiler_source = text
                    elif tag == "maven.compiler.target":
                        compiler_target = text
        except Exception:
            pass

        # 6. Build validation command
        validation_cmd = PomContextSummaryBuilder._build_validation_command(
            sandbox_path=sandbox,
        )

        # 7. Determine candidate deterministic rules based on POM analysis
        #     Use resolved target values (profile may override originals).
        candidate_rules = PomContextSummaryBuilder._find_candidate_rules(
            boot_version=boot_version,
            target_boot=resolved_target_boot,
            source_java=source_java,
            target_java=resolved_target_java,
        )

        # 8. Build warnings list
        warnings_list = list(scan_result.get("warnings", []))

        ref = f"pom-summary:{uuid4().hex}"
        return PomContextSummary(
            pom_summary_ref=ref,
            pom_path=str(pom_file),
            spring_boot_version=boot_version,
            spring_boot_version_location=boot_location,
            java_version_property=source_java if source_java != "unknown" else "",
            maven_compiler_release=compiler_release,
            maven_compiler_source=compiler_source,
            maven_compiler_target=compiler_target,
            target_stage_boot=resolved_target_boot,
            target_stage_java=resolved_target_java,
            candidate_deterministic_rules=tuple(candidate_rules),
            validation_command=validation_cmd,
            warnings=tuple(warnings_list),
            created_at=utc_now_text(),
        )

    # ── Internal helpers ───────────────────────────────────────────

    @staticmethod
    def _resolve_target_stack(
        *,
        target_boot: str,
        target_java: str,
        ai_hub_path: str | None = None,
        profile_id: str | None = None,
    ) -> dict[str, str]:
        """Resolve target stack from profile or explicit params.

        The target Boot/Java must come from V2 stage/profile/command state,
        not scanner defaults.
        """
        if ai_hub_path and profile_id:
            profile_stack = load_profile_target_stack(ai_hub_path, profile_id)
            if profile_stack:
                return {
                    "java": str(profile_stack.get("java", target_java)),
                    "spring_boot": str(profile_stack.get("spring_boot", target_boot)),
                }
        return {
            "java": target_java,
            "spring_boot": target_boot,
        }

    @staticmethod
    def _build_validation_command(*, sandbox_path: str | Path) -> str:
        """Build validation command using existing build detection helpers."""
        try:
            info = detect_java_project(sandbox_path)
            cmd = full_validation_command(info.base_command, info.build_tool)
            return " ".join(cmd)
        except Exception:
            return "mvn clean compile"

    @staticmethod
    def _find_candidate_rules(
        *,
        boot_version: str,
        target_boot: str,
        source_java: str,
        target_java: str,
    ) -> list[str]:
        """Identify candidate allowlisted rules based on POM analysis.

        Maps observed POM patterns to ALLOWED_RULE_IDS entries.
        This is heuristic — the LLM makes the final recommendation.
        """
        candidates: list[str] = []

        # Check if Boot version needs upgrade (Boot 2.x -> 3.x implies Jakarta migration)
        if boot_version and boot_version.startswith("2."):
            candidates.append("DEPENDENCY_REPLACE_JAVAX_SERVLET_API_WITH_JAKARTA")
            candidates.append("DEPENDENCY_REPLACE_JAVAX_VALIDATION_WITH_JAKARTA")

        # Check if Boot version is below target
        if boot_version and target_boot:
            if _version_lt(boot_version, target_boot):
                candidates.append("DEPENDENCY_ADD_H2_RUNTIME")

        # Check if Java version needs upgrade
        if source_java and source_java not in ("unknown", "") and target_java:
            src_ver = _extract_major(source_java)
            tgt_ver = _extract_major(target_java)
            if src_ver is not None and tgt_ver is not None and src_ver < tgt_ver:
                candidates.append("H2_SMOKE_CONFIG_ONLY")

        # Add general candidates if none matched
        if not candidates:
            candidates.append("H2_SMOKE_CONFIG_ONLY")

        # Filter to only allowlisted rules
        return [r for r in candidates if r in ALLOWED_RULE_IDS]

    @staticmethod
    def build_and_emit(
        *,
        sandbox_path: str | Path,
        target_boot: str = "3.5.14",
        target_java: str = "17",
        ai_hub_path: str | None = None,
        profile_id: str | None = None,
        job_id: str | None = None,
        stage_index: int | None = None,
        command_id: str | None = None,
        event_sink: Callable[[str, int | None, str, str, str, dict[str, Any] | None], None] | None = None,
    ) -> PomContextSummary:
        """Build a PomContextSummary and optionally emit a pom_summary_created event.

        This is an event emission (not artifact persistence). The
        pom_summary_created event carries the summary as payload so
        downstream consumers (F02 ContextPack metadata, cockpit,
        final report) can discover it by pom_summary_ref.

        When event_sink is provided, job_id and stage_index are required.
        Callers must supply real backend context — placeholder values
        are rejected.

        Args:
            sandbox_path: Path to the sandbox directory containing pom.xml.
            target_boot: Target Spring Boot version from V2 stage/profile state.
            target_java: Target Java version from V2 stage/profile state.
            ai_hub_path: Optional AI Hub path for profile-based target stack.
            profile_id: Optional profile id for target stack resolution.
            job_id: Required when event_sink is provided. The migration job id.
            stage_index: Required when event_sink is provided. The stage index.
            command_id: Optional failed command id for traceability.
            event_sink: Optional event sink matching the
                (job_id, stage, event_type, status, message, payload) signature.

        Returns:
            A PomContextSummary with a stable pom_summary_ref.

        Raises:
            ValueError: If event_sink is provided but job_id or stage_index
                        is missing, empty, or a placeholder.
        """
        summary = PomContextSummaryBuilder.build_summary(
            sandbox_path=sandbox_path,
            target_boot=target_boot,
            target_java=target_java,
            ai_hub_path=ai_hub_path,
            profile_id=profile_id,
        )
        if event_sink is not None:
            # Fail closed: require real backend context
            if not job_id or not isinstance(job_id, str) or job_id.strip() == "":
                raise ValueError(
                    "build_and_emit requires a non-empty job_id when event_sink is provided"
                )
            if stage_index is None or not isinstance(stage_index, int) or stage_index < 0:
                raise ValueError(
                    "build_and_emit requires a non-negative stage_index when event_sink is provided"
                )

            event_payload = PomContextSummaryBuilder.summary_to_dict(summary)
            # Attach caller-provided context for traceability
            # NOTE: sandbox_path is intentionally excluded from event payload
            # to avoid exposing raw absolute paths before redaction.
            # pom_path in summary_to_dict provides the POM artifact reference.
            if command_id:
                event_payload["command_id"] = command_id
            if profile_id:
                event_payload["profile_id"] = profile_id

            event_sink(
                job_id,
                stage_index,
                "pom_summary_created",
                "completed",
                f"POM context summary created: {summary.spring_boot_version}",
                event_payload,
            )
        return summary

    @staticmethod
    def summary_to_dict(summary: PomContextSummary) -> dict[str, Any]:
        """Convert PomContextSummary to a dict for artifact JSON."""
        return {
            "pom_summary_ref": summary.pom_summary_ref,
            "pom_path": summary.pom_path,
            "spring_boot_version": summary.spring_boot_version,
            "spring_boot_version_location": summary.spring_boot_version_location,
            "java_version_property": summary.java_version_property,
            "maven_compiler_release": summary.maven_compiler_release,
            "maven_compiler_source": summary.maven_compiler_source,
            "maven_compiler_target": summary.maven_compiler_target,
            "target_stage_boot": summary.target_stage_boot,
            "target_stage_java": summary.target_stage_java,
            "candidate_deterministic_rules": list(summary.candidate_deterministic_rules),
            "validation_command": summary.validation_command,
            "warnings": list(summary.warnings),
            "created_at": summary.created_at,
        }


# ── Helper functions ──────────────────────────────────────────────


def _extract_major(version: str) -> int | None:
    """Extract major version number from a version string."""
    match = _re.match(r"(\d+)", str(version or "").strip())
    return int(match.group(1)) if match else None


def _version_lt(current: str, target: str) -> bool:
    """Compare two semver-like version strings."""
    def parts(value: str) -> tuple[int, ...]:
        return tuple(int(p) for p in _re.findall(r"\d+", value)[:4])

    return parts(current) < parts(target)


def _strip_ns(tag: str) -> str:
    """Strip namespace prefix from an XML tag."""
    return tag.rsplit("}", 1)[-1] if "}" in tag else tag
