from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

Severity = Literal["INFO", "WARNING", "ERROR", "BLOCKER"]
PolicyStatus = Literal["PASS", "PASS_WITH_WARNINGS", "FAIL"]


@dataclass(frozen=True)
class PolicyRisk:
    rule_id: str
    severity: Severity
    category: str
    file: str
    evidence: str
    why_it_matters: str
    deterministic_fix_available: bool
    suggested_fix: str
    blocks_v1_build_test: bool
    blocks_v2_runtime: bool

    def to_dict(self) -> dict[str, Any]:
        return {
            "rule_id": self.rule_id,
            "severity": self.severity,
            "category": self.category,
            "file": self.file,
            "evidence": self.evidence,
            "why_it_matters": self.why_it_matters,
            "deterministic_fix_available": self.deterministic_fix_available,
            "suggested_fix": self.suggested_fix,
            "blocks_v1_build_test": self.blocks_v1_build_test,
            "blocks_v2_runtime": self.blocks_v2_runtime,
        }


@dataclass(frozen=True)
class PolicyReport:
    schema_version: str
    target_boot_version: str
    target_java_version: str
    status: PolicyStatus
    risks: tuple[PolicyRisk, ...] = field(default_factory=tuple)
    deterministic_actions_available: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    copilot_advisory_required: tuple[dict[str, Any], ...] = field(default_factory=tuple)
    blocked_for_build_test: bool = False
    blocked_for_runtime: bool = False
    source_vs_dependency_jakarta_findings: dict[str, Any] = field(default_factory=dict)
    pom_findings: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "target_boot_version": self.target_boot_version,
            "target_java_version": self.target_java_version,
            "status": self.status,
            "risks": [risk.to_dict() for risk in self.risks],
            "deterministic_actions_available": list(self.deterministic_actions_available),
            "copilot_advisory_required": list(self.copilot_advisory_required),
            "blocked_for_build_test": self.blocked_for_build_test,
            "blocked_for_runtime": self.blocked_for_runtime,
            "source_vs_dependency_jakarta_findings": dict(self.source_vs_dependency_jakarta_findings),
            "pom_findings": dict(self.pom_findings),
        }
