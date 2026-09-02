from dataclasses import dataclass, field
from typing import Any, Literal

AssistResultStatus = Literal["USED", "SKIPPED", "UNAVAILABLE", "ERROR"]


@dataclass(frozen=True)
class PlanningAssistRequest:
    run_id: str
    agent: str
    phase: str
    model: str | None
    prompt: str
    context: dict[str, Any]
    allowed_fields: list[str] = field(default_factory=list)
    forbidden_fields: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class PlanningAssistResult:
    status: AssistResultStatus
    missing_warnings: list[str] = field(default_factory=list)
    approval_summary_improvements: list[str] = field(default_factory=list)
    operator_notes: list[str] = field(default_factory=list)
    risk_explanations: list[str] = field(default_factory=list)
    confidence: float | None = None
    warnings: list[str] = field(default_factory=list)
    error: str | None = None
    requested_model: str | None = None
    resolved_model: str | None = None
    model_source: str | None = None
    model_verified: bool = False
