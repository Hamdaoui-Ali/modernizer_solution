from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from migration_factory.contracts import SCHEMA_VERSION


COPILOT_RESPONSE_TEMPLATE = {
    "schema_version": "1.0.0",
    "repair_summary": "",
    "failure_classification": "H2_STARTUP_FAILURE",
    "skills_claimed": [],
    "wrapper_checklist": {
        "legacy_source_not_modified": True,
        "sandbox_only": True,
        "no_deployment": True,
        "no_pr_creation": True,
        "no_security_weakening": True,
        "h2_only_runtime_scope": True,
        "sql_server_out_of_scope": True,
        "endpoint_smoke_out_of_scope": True,
    },
    "patch_proposals": [],
    "security_review_required": False,
    "confidence": "LOW",
    "refusals": [],
    "limitations": [],
}


def repair_response_instruction() -> str:
    return (
        "Use the inline REQUEST_JSON, RESPONSE_SCHEMA_JSON, and RESPONSE_TEMPLATE_JSON evidence in this prompt.\n"
        "Return only the filled RESPONSE_TEMPLATE_JSON shape.\n"
        "Do not add new top-level fields.\n"
        "Do not rename fields.\n"
        "Do not use markdown.\n"
        "Do not include prose.\n"
        "Do not include comments.\n"
        "Return exactly one JSON object.\n"
        "Propose repairs only; do not apply patches.\n"
        "If unsure, return the template with confidence=\"LOW\", patch_proposals=[], "
        "and limitations explaining uncertainty."
    )


def build_repair_request(
    *,
    run_dir: str | Path,
    run_id: str,
    failure_classification: dict[str, Any],
    artifact_refs: dict[str, str] | None = None,
    openrewrite_diff_safety: dict[str, Any] | None = None,
    h2_startup_report: dict[str, Any] | None = None,
) -> dict[str, Any]:
    request = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id,
        "mode": "proposal_only",
        "failure_classification": failure_classification,
        "openrewrite_diff_safety": openrewrite_diff_safety or {},
        "h2_startup_report": h2_startup_report or {},
        "artifact_refs": dict(artifact_refs or {}),
        "guardrails": {
            "legacy_source_not_modified": True,
            "sandbox_only": True,
            "no_auto_patch_apply": True,
            "no_deployment": True,
            "no_pr_creation": True,
            "no_security_weakening": True,
            "h2_only_runtime_scope": True,
            "sql_server_out_of_scope": True,
            "endpoint_smoke_out_of_scope": True,
        },
        "required_response": repair_response_instruction(),
    }
    failures_dir = Path(run_dir) / "failures"
    failures_dir.mkdir(parents=True, exist_ok=True)
    (failures_dir / "copilot_repair_request.json").write_text(
        json.dumps(request, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    return request
