"""F14 — Generic dependency compatibility policy layer.

Evaluates any requested dependency/property/plugin/BOM/parent/
dependencyManagement change before the backend writes. Not Tomcat-specific.
"""

from __future__ import annotations

import re as _re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any


# ── Control modes ──────────────────────────────────────────────────

class DependencyControlMode(Enum):
    """How a dependency version is controlled in the current POM."""
    DIRECT_DEPENDENCY_VERSION = "direct_dependency_version"
    PROPERTY_MANAGED_VERSION = "property_managed_version"
    DEPENDENCY_MANAGEMENT_ENTRY = "dependency_management_entry"
    PARENT_BOM_MANAGED = "parent_bom_managed"
    SPRING_BOOT_BOM_MANAGED = "spring_boot_bom_managed"
    PLUGIN_VERSION = "plugin_version"
    TRANSITIVE_ONLY = "transitive_only"
    NOT_PRESENT = "not_present"
    MULTI_MODULE_INHERITED = "multi_module_inherited"
    UNKNOWN = "unknown"


class RiskLevel(Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    BLOCKED = "blocked"
    EVIDENCE_INSUFFICIENT = "evidence_insufficient"


class ExecutionMode(Enum):
    POM_SPAN_PATCH = "pom_span_patch"
    OPENREWRITE = "openrewrite"
    REPAIR_LOOP_PATCH = "repair_loop_patch"
    PROPOSAL_ONLY = "proposal_only"
    REFUSE = "refuse"


# ── Dependency buckets ─────────────────────────────────────────────

DEPENDENCY_BUCKETS = frozenset({
    "boot_managed",
    "jakarta_platform",
    "app_specific_third_party",
    "build_plugins",
    "transitive_or_bom_managed_risk",
})


# ── Policy decision ────────────────────────────────────────────────

@dataclass(frozen=True)
class DependencyPolicyDecision:
    """Decision object after evaluating a dependency change request."""
    control_mode: DependencyControlMode
    risk: str  # "low" | "medium" | "high" | "blocked" | "evidence_insufficient"
    can_apply: bool
    execution_mode: str  # "pom_span_patch" | "openrewrite" | "repair_loop_patch" | "proposal_only" | "refuse"
    warnings: tuple[str, ...]
    reason: str
    requires_explicit_high_risk: bool
    suggested_next_action: str


# ── Bean component keywords (examples, not hardcoded targets) ──────

_JAKARTA_PLATFORM_PREFIXES = (
    "jakarta.",
    "javax.",
)

_SPRING_BOOT_PREFIXES = (
    "org.springframework.boot",
    "org.springframework",
)

_SPRING_BOOT_BOM_ARTIFACTS = (
    "spring-boot-dependencies",
    "spring-boot-starter-parent",
)

_BUILD_PLUGIN_ARTIFACT_PATTERNS = (
    _re.compile(r"maven-.*-plugin", _re.IGNORECASE),
    _re.compile(r".*-maven-plugin", _re.IGNORECASE),
)


# ── PomDependencyPolicy classifier ─────────────────────────────────


class PomDependencyPolicy:
    """Generic dependency compatibility policy.

    Evaluates any requested dependency/property/plugin/BOM change
    against evidence-loaded POM state. Decides control mode, risk
    level, and whether the operation can proceed.

    No hardcoded Java/Spring Boot versions. Baseline is passed in
    from evidence, not stored in this class.
    """

    def __init__(self, *, pom_deps_data: dict[str, Any] | None = None) -> None:
        """pom_deps_data: pre-parsed POM dependency metadata mapping.

        Structure expected:
        {
            "properties": {"java.version": "17", ...},
            "dependencies": [
                {"groupId": "com.example", "artifactId": "library-name", "version": "1.2.3", ...},
                ...
            ],
            "dependency_management": [
                {"groupId": "org.springframework.boot", "artifactId": "spring-boot-dependencies", ...},
                ...
            ],
            "plugins": [
                {"groupId": "org.apache.maven.plugins", "artifactId": "maven-compiler-plugin", "version": "3.11.0", ...},
                ...
            ],
            "parent": {"groupId": "...", "artifactId": "...", "version": "..."},
            "baseline": {"java_version": "17", "spring_boot_version": "3.5.14", ...},
        }
        """
        self._data = pom_deps_data or {}

    # ── Public API ──────────────────────────────────────────────────

    def evaluate_change(
        self,
        *,
        target_kind: str,
        group_id: str | None,
        artifact_id: str | None,
        property_name: str | None,
        requested_version: str,
        user_request: str,
        stage: int,
    ) -> DependencyPolicyDecision:
        """Evaluate a dependency change request.

        Returns a policy decision that tells the caller whether to
        proceed, block, or propose-only.
        """

        # Stage gate
        if stage != 3:
            return DependencyPolicyDecision(
                control_mode=DependencyControlMode.UNKNOWN,
                risk=RiskLevel.BLOCKED.value,
                can_apply=False,
                execution_mode=ExecutionMode.PROPOSAL_ONLY.value,
                warnings=("Stage 3 is required for POM dependency changes.",),
                reason="POM changes can only be applied in Stage 3.",
                requires_explicit_high_risk=False,
                suggested_next_action="Complete Stage 3 setup before applying dependency changes.",
            )

        # Detect control mode from the current POM
        control_mode = self._detect_control_mode(
            target_kind=target_kind,
            group_id=group_id,
            artifact_id=artifact_id,
            property_name=property_name,
        )

        # Classify risk
        risk, warnings = self._classify_risk(
            control_mode=control_mode,
            target_kind=target_kind,
            group_id=group_id,
            artifact_id=artifact_id,
            requested_version=requested_version,
            user_request=user_request,
        )

        # Determine if apply is allowed
        can_apply, execution_mode, reason = self._determine_execution(
            control_mode=control_mode,
            risk=risk,
            user_request=user_request,
            requested_version=requested_version,
        )
        if risk == RiskLevel.BLOCKED.value and warnings:
            reason = warnings[0]

        requires_explicit_high_risk = risk in (RiskLevel.HIGH.value,)
        if risk == RiskLevel.BLOCKED.value:
            can_apply = False

        return DependencyPolicyDecision(
            control_mode=control_mode,
            risk=risk,
            can_apply=can_apply,
            execution_mode=execution_mode,
            warnings=warnings,
            reason=reason,
            requires_explicit_high_risk=requires_explicit_high_risk,
            suggested_next_action=self._suggest_next(can_apply, risk, execution_mode),
        )

    def get_dependency_bucket(
        self,
        *,
        group_id: str,
        artifact_id: str,
    ) -> str:
        """Classify a dependency into a review bucket."""
        gid = (group_id or "").lower()
        aid = (artifact_id or "").lower()

        # Jakarta platform
        if any(gid.startswith(p) for p in _JAKARTA_PLATFORM_PREFIXES):
            return "jakarta_platform"

        # Boot-managed
        if any(gid.startswith(p) for p in _SPRING_BOOT_PREFIXES):
            return "boot_managed"
        if aid in _SPRING_BOOT_BOM_ARTIFACTS:
            return "boot_managed"

        # Build plugins
        if target_kind_is_plugin(group_id or "", artifact_id or ""):
            return "build_plugins"

        # App-specific third-party
        # If we have dependency_management data and this dependency
        # is managed by a BOM or parent, it's transitive/bom risk
        if self._is_managed_by_bom(group_id, artifact_id):
            return "transitive_or_bom_managed_risk"

        return "app_specific_third_party"

    # ── Internal detection ──────────────────────────────────────────

    def _detect_control_mode(
        self,
        *,
        target_kind: str,
        group_id: str | None,
        artifact_id: str | None,
        property_name: str | None,
    ) -> DependencyControlMode:
        """Detect how the target is controlled in the current POM."""

        if target_kind == "property":
            return DependencyControlMode.PROPERTY_MANAGED_VERSION

        if target_kind == "plugin":
            return DependencyControlMode.PLUGIN_VERSION

        if target_kind == "dependency_management":
            return DependencyControlMode.DEPENDENCY_MANAGEMENT_ENTRY

        if target_kind == "parent" or target_kind == "bom":
            return DependencyControlMode.PARENT_BOM_MANAGED

        if target_kind == "dependency":
            return self._classify_dependency_control(group_id, artifact_id, property_name)

        # Check if property_name maps to a known property
        if property_name:
            props = self._data.get("properties", {})
            if property_name in props:
                return DependencyControlMode.PROPERTY_MANAGED_VERSION

        return DependencyControlMode.UNKNOWN

    def _classify_dependency_control(
        self,
        group_id: str | None,
        artifact_id: str | None,
        property_name: str | None,
    ) -> DependencyControlMode:
        """Classify how a specific dependency is controlled."""

        gid = (group_id or "").lower()
        aid = (artifact_id or "").lower()

        # Check if it exists in direct dependencies
        deps = self._data.get("dependencies", [])
        for dep in deps:
            dg = (dep.get("groupId", "") or "").lower()
            da = (dep.get("artifactId", "") or "").lower()
            if dg == gid and da == aid:
                version = dep.get("version", "")
                # If version references a property like ${foo.version}
                if version and version.startswith("${") and version.endswith("}"):
                    return DependencyControlMode.PROPERTY_MANAGED_VERSION
                if version:
                    return DependencyControlMode.DIRECT_DEPENDENCY_VERSION
                # No version = probably managed by BOM/dependencyManagement
                return DependencyControlMode.SPRING_BOOT_BOM_MANAGED

        # Check dependency management
        dm_entries = self._data.get("dependency_management", [])
        for entry in dm_entries:
            eg = (entry.get("groupId", "") or "").lower()
            ea = (entry.get("artifactId", "") or "").lower()
            if eg == gid and ea == aid:
                return DependencyControlMode.DEPENDENCY_MANAGEMENT_ENTRY

        # Check parent
        parent = self._data.get("parent", {})
        pg = (parent.get("groupId", "") or "").lower()
        pa = (parent.get("artifactId", "") or "").lower()
        if pg == gid and pa == aid:
            return DependencyControlMode.PARENT_BOM_MANAGED

        # Not found in any POM location
        return DependencyControlMode.NOT_PRESENT

    def _is_managed_by_bom(self, group_id: str, artifact_id: str) -> bool:
        """Check if dependency appears to be managed by a BOM."""
        dm_entries = self._data.get("dependency_management", [])
        if not dm_entries:
            return False
        # If there are dependency management entries at all and
        # the dependency is not in direct deps with an explicit version,
        # it may be transitively managed
        deps = self._data.get("dependencies", [])
        gid_lower = group_id.lower()
        aid_lower = artifact_id.lower()
        for dep in deps:
            dg = (dep.get("groupId", "") or "").lower()
            da = (dep.get("artifactId", "") or "").lower()
            if dg == gid_lower and da == aid_lower:
                version = dep.get("version", "")
                if not version:
                    return True  # No version = likely BOM-managed
                return False  # Has explicit version
        return False

    # ── Risk classification ─────────────────────────────────────────

    def _classify_risk(
        self,
        *,
        control_mode: DependencyControlMode,
        target_kind: str,
        group_id: str | None,
        artifact_id: str | None,
        requested_version: str,
        user_request: str,
    ) -> tuple[str, tuple[str, ...]]:
        """Classify risk level and produce warnings."""

        gid = (group_id or "").lower()

        # Vague requests are blocked
        if _is_vague_request(user_request):
            return (
                RiskLevel.BLOCKED.value,
                (
                    "Vague request cannot be applied. Please specify exact target and version.",
                ),
            )

        # "latest" without evidence is blocked
        if _is_latest_request(requested_version) and not _has_version_evidence():
            return (
                RiskLevel.BLOCKED.value,
                (
                    '"latest" version cannot be used without project policy or evidence providing the exact version.',
                ),
            )

        # High-risk control modes
        high_risk_modes = {
            DependencyControlMode.PARENT_BOM_MANAGED,
            DependencyControlMode.TRANSITIVE_ONLY,
            DependencyControlMode.MULTI_MODULE_INHERITED,
        }
        if control_mode in high_risk_modes:
            return (
                RiskLevel.HIGH.value,
                (
                    f"{control_mode.value} changes are high-risk and may require additional code changes.",
                    "Review the full impact before proceeding.",
                ),
            )

        # Not present
        if control_mode == DependencyControlMode.NOT_PRESENT:
            return (
                RiskLevel.MEDIUM.value if target_kind != "dependency" else RiskLevel.BLOCKED.value,
                (
                    "Target is not present in the current POM. Update operations require an existing direct target.",
                ),
            )

        # Unknown
        if control_mode == DependencyControlMode.UNKNOWN:
            return (
                RiskLevel.EVIDENCE_INSUFFICIENT.value,
                (
                    "Cannot determine how this target is controlled in the POM.",
                ),
            )

        # Medium risk
        medium_risk_modes = {
            DependencyControlMode.DEPENDENCY_MANAGEMENT_ENTRY,
            DependencyControlMode.PLUGIN_VERSION,
        }
        if control_mode in medium_risk_modes:
            return (
                RiskLevel.MEDIUM.value,
                (
                    f"{control_mode.value} changes may affect multiple modules or transitive consumers.",
                ),
            )

        # Jakarta/javax coordinate migration is high risk
        if any(gid.startswith(p) for p in _JAKARTA_PLATFORM_PREFIXES):
            return (
                RiskLevel.HIGH.value,
                (
                    "Jakarta/javax coordinate migration may require code changes. Ensure all source references are updated.",
                ),
            )

        # Low risk by default
        return (RiskLevel.LOW.value, ())

    # ── Execution determination ─────────────────────────────────────

    def _determine_execution(
        self,
        *,
        control_mode: DependencyControlMode,
        risk: str,
        user_request: str,
        requested_version: str,
    ) -> tuple[bool, str, str]:
        """Determine if apply is allowed and how to execute."""

        # Blocked = no apply
        if risk == RiskLevel.BLOCKED.value:
            return (False, ExecutionMode.REFUSE.value, "Request cannot be applied. See warnings for details.")

        # Evidence insufficient = propose only
        if risk == RiskLevel.EVIDENCE_INSUFFICIENT.value:
            return (False, ExecutionMode.PROPOSAL_ONLY.value, "Insufficient evidence to apply. A proposal has been generated.")

        # Direct/property low risk = pom_span_patch
        if risk == RiskLevel.LOW.value and control_mode in (
            DependencyControlMode.DIRECT_DEPENDENCY_VERSION,
            DependencyControlMode.PROPERTY_MANAGED_VERSION,
        ):
            return (True, ExecutionMode.POM_SPAN_PATCH.value, "Change approved for targeted POM patch application.")

        # Medium risk with explicit request = pom_span_patch with warnings
        if risk == RiskLevel.MEDIUM.value:
            return (True, ExecutionMode.POM_SPAN_PATCH.value, "Change approved with medium risk. Validation is required.")

        # High-risk control modes are proposal-only until a supported executor exists.
        if risk == RiskLevel.HIGH.value:
            return (False, ExecutionMode.PROPOSAL_ONLY.value, "Change is not executable by the targeted POM patcher for this control mode. A proposal has been generated for review.")

        # Default for direct dependency: apply
        if control_mode in (DependencyControlMode.DIRECT_DEPENDENCY_VERSION,):
            return (True, ExecutionMode.POM_SPAN_PATCH.value, "Change approved for application.")

        return (False, ExecutionMode.PROPOSAL_ONLY.value, "A proposal has been generated for review.")

    def _suggest_next(self, can_apply: bool, risk: str, execution_mode: str) -> str:
        if can_apply:
            return "apply_this_change"
        if risk == RiskLevel.BLOCKED.value:
            return "provide_more_details"
        if risk == RiskLevel.HIGH.value:
            return "review_proposal"
        return "review_proposal"


# ── Helper functions ───────────────────────────────────────────────

def target_kind_is_plugin(group_id: str, artifact_id: str) -> bool:
    """Check if a GAV looks like a Maven plugin."""
    for pat in _BUILD_PLUGIN_ARTIFACT_PATTERNS:
        if pat.search(artifact_id):
            return True
    if group_id.startswith("org.apache.maven.plugins"):
        return True
    return False


def _is_vague_request(user_request: str) -> bool:
    """Check if the user request is too vague to apply."""
    vague_patterns = (
        r"\bfix\s+(all|everything|dependencies)\b",
        r"\bupgrade?\s+(all|everything|dependencies)\b",
        r"\bmake\s+(it|things?)\s+(better|work)\b",
        r"\bimprove\s+(dependencies?|things?)\b",
    )
    import re
    lowered = user_request.lower()
    for pat in vague_patterns:
        if re.search(pat, lowered):
            return True
    return False


def _is_latest_request(version: str | None) -> bool:
    """Check if version is 'latest' or equivalent."""
    if version is None:
        return False
    return version.lower().strip() in ("latest", "latest.release", "release", "latest.integration")


def _has_version_evidence() -> bool:
    """Check if external evidence provides an exact version for 'latest'."""
    # In production, this would check project policy or evidence artifacts
    return False


def _is_explicit_high_risk_request(user_request: str) -> bool:
    """Check if user wording explicitly signals high-risk intent."""
    high_risk_keywords = (
        "i understand the risk",
        "apply high-risk change",
        "accept risk",
        "proceed with high risk",
        "apply despite risk",
        "i confirm",
        "apply anyway",
        "force apply",
    )
    lowered = user_request.lower()
    return any(kw in lowered for kw in high_risk_keywords)
