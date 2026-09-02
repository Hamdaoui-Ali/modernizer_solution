"""F14 — Server-side POM change validation and proposal.

Validates user requests before the backend writes. Integrates with
the generic dependency policy layer and POM detection.
"""

from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.application.pom_change_models import (
    PomChangePlan,
    PomChangeProposal,
    PomChangeTarget,
    ALLOWED_POM_OPERATIONS,
)
from migration_factory.control_tower.application.pom_dependency_policy import (
    PomDependencyPolicy,
    DependencyPolicyDecision,
    DependencyControlMode,
    _is_vague_request,
    _is_latest_request,
)
from migration_factory.control_tower.application.pom_dependency_review import PomDependencyReviewer
from migration_factory.control_tower.application.pom_xml_patcher import PomXmlPatcher
from migration_factory.control_tower.domain.checksums import utc_now_text


class PomChangeProposer:
    """Server-side change proposal validation.

    Validates user requests, integrates with policy layer,
    and produces validated change plans. Never writes files.
    """

    def __init__(
        self,
        policy: PomDependencyPolicy | None = None,
        patcher: PomXmlPatcher | None = None,
    ) -> None:
        self._policy = policy or PomDependencyPolicy()
        self._patcher = patcher or PomXmlPatcher()
        self._reviewer = PomDependencyReviewer()

    def propose(
        self,
        *,
        job_id: str,
        user_request: str,
        stage: int,
        pom_content: str,
        pom_deps_data: dict[str, Any] | None = None,
    ) -> PomChangeProposal:
        """Propose a POM change from a user request.

        Returns a read-only proposal. No file is written.
        """

        live_deps_data = pom_deps_data or self._reviewer.parse_pom_deps(pom_content)

        # Parse the user request to extract target and operation
        parsed = self._parse_user_request(user_request, pom_content, live_deps_data)

        target = parsed["target"]
        operation = parsed["operation"]
        requested_version = _clean_requested_version(parsed.get("requested_version", ""))

        # Build fresh policy with current POM data
        policy = PomDependencyPolicy(pom_deps_data=live_deps_data)

        # Evaluate through policy
        decision: DependencyPolicyDecision = policy.evaluate_change(
            target_kind=target.kind,
            group_id=target.group_id,
            artifact_id=target.artifact_id,
            property_name=target.property_name,
            requested_version=requested_version,
            user_request=user_request,
            stage=stage,
        )

        # Build the validated plan
        plan = PomChangePlan(
            intent="apply_dependency_change",
            stage=stage,
            operation=operation,
            target=target,
            requested_version=requested_version,
            risk=decision.risk,
            control_mode=decision.control_mode.value,
            requires_validation=True,
            evidence=("root_pom",),
            rationale=f"User-requested change: {user_request}",
        )

        # Extract current version from POM
        current_version = ""
        if target.kind == "property" and target.property_name:
            current_version = self._patcher.extract_current_version(
                pom_content=pom_content,
                target_kind=target.kind,
                property_name=target.property_name,
            )
        elif target.kind == "dependency" and target.group_id and target.artifact_id:
            current_version = self._patcher.extract_current_version(
                pom_content=pom_content,
                target_kind=target.kind,
                group_id=target.group_id,
                artifact_id=target.artifact_id,
            )
        elif target.kind == "plugin":
            current_version = self._patcher.extract_current_version(
                pom_content=pom_content,
                target_kind=target.kind,
                group_id=target.group_id,
                artifact_id=target.artifact_id,
                plugin_group_id=target.plugin_group_id,
                plugin_artifact_id=target.plugin_artifact_id,
            )

        # Build proposal
        plan_preview = plan.to_public_dict()
        plan_preview["current_version"] = current_version

        return PomChangeProposal(
            proposal_id=uuid4().hex,
            server_validated_plan_preview=plan_preview,
            risk=decision.risk,
            can_apply=decision.can_apply,
            warnings=decision.warnings,
            applied=False,
            control_mode=decision.control_mode.value,
            created_at=utc_now_text(),
        )

    def revalidate(
        self,
        *,
        proposal: PomChangeProposal,
        pom_content: str,
        pom_deps_data: dict[str, Any] | None = None,
    ) -> DependencyPolicyDecision:
        """Revalidate a proposal against current POM state.

        Used before applying to ensure the proposal is still valid.
        """
        plan = proposal.server_validated_plan_preview
        target_data = plan.get("target", {})
        target = PomChangeTarget(
            kind=target_data.get("kind", ""),
            group_id=target_data.get("group_id"),
            artifact_id=target_data.get("artifact_id"),
            property_name=target_data.get("property_name"),
            plugin_group_id=target_data.get("plugin_group_id"),
            plugin_artifact_id=target_data.get("plugin_artifact_id"),
        )

        policy = PomDependencyPolicy(pom_deps_data=pom_deps_data)
        return policy.evaluate_change(
            target_kind=target.kind,
            group_id=target.group_id,
            artifact_id=target.artifact_id,
            property_name=target.property_name,
            requested_version=_clean_requested_version(plan.get("requested_version", "")),
            user_request="revalidate",
            stage=3,
        )


    # ── User request parsing ────────────────────────────────────────

    def _parse_user_request(
        self,
        user_request: str,
        pom_content: str,
        pom_deps_data: dict[str, Any] | None,
    ) -> dict[str, Any]:
        """Parse a natural-language user request into structured target/operation.

        Extracts:
        - target kind (dependency, property, plugin)
        - groupId:artifactId or property name
        - requested version
        - operation type
        """
        import re

        lowered = user_request.lower().strip()

        # Detect operation
        operation = "update_dependency_version"
        if "property" in lowered and ("version" in lowered or "change" in lowered):
            operation = "update_property_version"
        elif "remove" in lowered and ("version" in lowered):
            operation = "remove_dependency_version"
        elif "plugin" in lowered:
            operation = "update_plugin_version"
        elif "dependencymanagement" in lowered or "dependency management" in lowered:
            operation = "add_or_update_dependency_management_entry"
        elif "add" in lowered and "dependency" in lowered:
            operation = "add_dependency"
        elif "remove" in lowered and "dependency" in lowered:
            operation = "remove_dependency"

        # Try property changes before dependency changes so *.version names are property targets.
        explicit_prop_pattern = re.compile(
            r"""(?:update|updating|change|changing|set|bump|bumping)\s+property\s+
            ([\w.\-]+)\s+
            (?:to|version)\s+
            ([\w.\-]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = explicit_prop_pattern.search(user_request)
        if m:
            return {
                "target": PomChangeTarget(
                    kind="property",
                    property_name=m.group(1).strip(),
                ),
                "operation": "update_property_version",
                "requested_version": m.group(2).strip(),
            }

        dot_ver_prop_pattern = re.compile(
            r"""(?:update|updating|change|changing|set|bump|bumping)\s+
            ([\w.\-]+)\.version\s+
            (?:to|version)\s+
            ([\w.\-]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = dot_ver_prop_pattern.search(user_request)
        if m:
            return {
                "target": PomChangeTarget(
                    kind="property",
                    property_name=m.group(1).strip() + ".version",
                ),
                "operation": "update_property_version",
                "requested_version": m.group(2).strip(),
            }

        # ── GAV patterns (groupId:artifactId with colon) ──
        gav_advisory_pattern = re.compile(
            r"""(?:can\s+i|should\s+i|what\s+do\s+you\s+think\s+about)\s+
            (?:chang(?:ing|e)|updat(?:ing|e)|upgrad(?:ing|e)|set(?:ting)?)\s+
            (?:dependency\s+)?
            ([\w.\-]+):([\w.\-]+)
            \s+(?:to\s+|version\s+)?
            ([\w.\-]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = gav_advisory_pattern.search(user_request)
        if m:
            return {
                "target": PomChangeTarget(
                    kind="dependency",
                    group_id=m.group(1),
                    artifact_id=m.group(2),
                ),
                "operation": operation,
                "requested_version": _clean_version_token(m.group(3)),
            }

        # Pattern A: "propose/suggest/draft changing/updating GROUP:ARTIFACT to VERSION"
        gav_propose_pattern = re.compile(
            r"""(?:propose|suggest|draft|recommend)\s+
            (?:chang(?:ing|e)|updat(?:ing|e)|upgrad(?:ing|e))\s+
            (?:dependency\s+)?
            ([\w.\-]+):([\w.\-]+)
            \s+(?:to\s+|version\s+)?
            ([\w.\-]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = gav_propose_pattern.search(user_request)
        if m:
            return {
                "target": PomChangeTarget(
                    kind="dependency",
                    group_id=m.group(1),
                    artifact_id=m.group(2),
                ),
                "operation": operation,
                "requested_version": _clean_version_token(m.group(3)),
            }

        # Pattern B: "update/change/set dependency GROUP:ARTIFACT to VERSION"
        gav_dep_pattern = re.compile(
            r"""(?:update|updat(?:ing|e)|chang(?:ing|e)|set|bump)\s+
            dependency\s+
            ([\w.\-]+):([\w.\-]+)
            \s+(?:to\s+|version\s+)?
            ([\w.\-]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = gav_dep_pattern.search(user_request)
        if m:
            return {
                "target": PomChangeTarget(
                    kind="dependency",
                    group_id=m.group(1),
                    artifact_id=m.group(2),
                ),
                "operation": operation,
                "requested_version": _clean_version_token(m.group(3)),
            }

        # Pattern C: "apply this ... update dependency GROUP:ARTIFACT to VERSION"
        gav_apply_dep_pattern = re.compile(
            r"""apply\s+this.*?
            (?:update|updat(?:ing|e)|chang(?:ing|e)|set)\s+
            dependency\s+
            ([\w.\-]+):([\w.\-]+)
            \s+(?:to\s+|version\s+)?
            ([\w.\-]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = gav_apply_dep_pattern.search(user_request)
        if m:
            return {
                "target": PomChangeTarget(
                    kind="dependency",
                    group_id=m.group(1),
                    artifact_id=m.group(2),
                ),
                "operation": operation,
                "requested_version": _clean_version_token(m.group(3)),
            }

        # Try to extract groupId:artifactId (existing simple GAV pattern)
        gav_pattern = re.compile(
            r"""(?:change|update|upgrade|downgrade|set)\s+
            ([\w.\-]+):([\w.\-]+)
            \s+(?:to\s+|version\s+)?
            ([\w.\-]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = gav_pattern.search(user_request)
        if m:
            return {
                "target": PomChangeTarget(
                    kind="dependency",
                    group_id=m.group(1),
                    artifact_id=m.group(2),
                ),
                "operation": operation,
                "requested_version": m.group(3),
            }

        # Try "change X to Y" (dependency by artifactId alone with version)
        short_pattern = re.compile(
            r"""(?:change|update|upgrade|set)\s+
            ([\w.\-]+)\s+
            (?:to|version)\s+
            ([\w.\-]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = short_pattern.search(user_request)
        if m:
            artifact_id = m.group(1)
            version = m.group(2)

            # Try to find the groupId from POM data
            group_id = None
            if pom_deps_data:
                for dep in pom_deps_data.get("dependencies", []):
                    if dep.get("artifactId", "").lower() == artifact_id.lower():
                        group_id = dep.get("groupId")
                        break
                if not group_id:
                    for plugin in pom_deps_data.get("plugins", []):
                        if plugin.get("artifactId", "").lower() == artifact_id.lower():
                            group_id = plugin.get("groupId")
                            operation = "update_plugin_version"
                            break

            if group_id:
                return {
                    "target": PomChangeTarget(
                        kind="plugin" if operation == "update_plugin_version" else "dependency",
                        group_id=group_id,
                        artifact_id=artifact_id,
                    ),
                    "operation": operation,
                    "requested_version": version,
                }

        # Try property change — explicit "update property NAME to VERSION"
        explicit_prop_pattern = re.compile(
            r"""(?:update|change|set|bump)\s+property\s+
            ([\w.\-]+)\s+
            (?:to|version)\s+
            ([\d.]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = explicit_prop_pattern.search(user_request)
        if m:
            return {
                "target": PomChangeTarget(
                    kind="property",
                    property_name=m.group(1).strip(),
                ),
                "operation": "update_property_version",
                "requested_version": m.group(2).strip(),
            }

        # Try X.version to Y.Z pattern: "update example.version to 1.2.3"
        dot_ver_prop_pattern = re.compile(
            r"""(?:update|change|set|bump)\s+
            ([\w.\-]+)\.version\s+
            (?:to|version)\s+
            ([\d.]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = dot_ver_prop_pattern.search(user_request)
        if m:
            return {
                "target": PomChangeTarget(
                    kind="property",
                    property_name=m.group(1).strip() + ".version",
                ),
                "operation": "update_property_version",
                "requested_version": m.group(2).strip(),
            }

        # Try property change — generic
        prop_pattern = re.compile(
            r"""(?:change|update|set)\s+
            ([\w.\-]+)\s+
            (?:property\s+|version\s+)?
            (?:to\s+)?
            ([\w.\-]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = prop_pattern.search(user_request)
        if m:
            prop_name = m.group(1)
            version = m.group(2)
            if "version" in prop_name.lower() or prop_name in pom_deps_data.get("properties", {}) if pom_deps_data else True:
                return {
                    "target": PomChangeTarget(
                        kind="property",
                        property_name=prop_name,
                    ),
                    "operation": "update_property_version",
                    "requested_version": version,
                }

        # Fallback: try basic artifactId + version extraction
        basic_pattern = re.compile(
            r"""([\w.\-]+)\s+
            (?:to|version)\s+
            ([\w.\-]+)""",
            re.VERBOSE | re.IGNORECASE,
        )
        m = basic_pattern.search(user_request)
        if m:
            artifact_id = m.group(1)
            version = m.group(2)
            group_id = None
            if pom_deps_data:
                for dep in pom_deps_data.get("dependencies", []):
                    if dep.get("artifactId", "").lower() == artifact_id.lower():
                        group_id = dep.get("groupId")
                        break
            return {
                "target": PomChangeTarget(
                    kind="dependency",
                    group_id=group_id or "unknown",
                    artifact_id=artifact_id,
                ),
                "operation": operation,
                "requested_version": version,
            }

        # Generic fallback
        return {
            "target": PomChangeTarget(
                kind="dependency",
                group_id="unknown",
                artifact_id="unknown",
            ),
            "operation": "update_dependency_version",
            "requested_version": "",
        }


def _clean_requested_version(value: Any) -> str:
    return str(value or "").strip().rstrip(".,;:")


def _clean_version_token(value: str) -> str:
    """Clean a version token, stripping trailing punctuation like 1.2.3. -> 1.2.3"""
    return str(value or "").strip().rstrip(".,;:")
