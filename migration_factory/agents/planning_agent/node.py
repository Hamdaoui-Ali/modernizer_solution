from pathlib import Path

from migration_factory.agents.planning_agent.assist_config import (
    load_planning_assist_config,
)
from migration_factory.agents.planning_agent.artifact_reader import (
    load_analysis_artifacts,
)
from migration_factory.agents.planning_agent.analysis_validator import (
    validate_analysis_completeness,
)
from migration_factory.agents.planning_agent.copilot_assist_client import (
    CopilotPlanningAssistClient,
)
from migration_factory.agents.planning_agent.profile_compatibility import (
    validate_profile_compatibility,
)
from migration_factory.agents.planning_agent.plan_writer import (
    MigrationPlanPayload,
    write_migration_plan,
    write_migration_units,
)
from migration_factory.agents.planning_agent.approval_writer import (
    ApprovalRequestPayload,
    write_approval_request,
)
from migration_factory.agents.planning_agent.summary_writer import (
    PlanSummaryPayload,
    write_plan_summary,
)
from migration_factory.agents.planning_agent.output_validator import (
    validate_planning_outputs,
)
from migration_factory.agents.planning_agent.risk_classifier import (
    classify_planning_risks,
)
from migration_factory.agents.planning_agent.unit_builder import (
    build_migration_units,
)
from migration_factory.agents.planning_agent.profile_reader import (
    load_migration_profile,
)
from migration_factory.agents.planning_agent.assist_merge import (
    merge_advisory_assist_suggestions,
)
from migration_factory.agents.planning_agent.assist_artifact_writer import (
    CopilotAssistArtifactPayload,
    write_copilot_assist_artifact,
)
from migration_factory.agents.planning_agent.assist_output_validator import (
    validate_assist_output_for_merge,
)
from migration_factory.agents.planning_agent.paths import (
    get_planning_output_artifact_paths,
)
from migration_factory.contracts.planning_assist import (
    PlanningAssistRequest,
    PlanningAssistResult,
)
from migration_factory.dependency_policy import write_target_dependency_plan
from migration_factory.orchestrator.state import MigrationState


def _get_profile_id(state: MigrationState) -> str:
    return state.get("profile") or state.get("profile_id", "")


def planning_node(state: MigrationState) -> MigrationState:
    profile_id = _get_profile_id(state)
    output_paths = {
        name: str(path)
        for name, path in get_planning_output_artifact_paths(
            modernized_app_path=state.get("modernized_app_path", ""),
            run_id=state.get("run_id", ""),
        ).items()
    }

    def _safe_write_assist_artifact(payload: CopilotAssistArtifactPayload) -> None:
        try:
            write_copilot_assist_artifact(
                modernized_app_path=state.get("modernized_app_path", ""),
                payload=payload,
                run_id=state.get("run_id", ""),
            )
        except Exception:
            # Artifact write is audit-only. Never block deterministic planning.
            return

    loaded_artifacts = load_analysis_artifacts(
        modernized_app_path=state.get("modernized_app_path", ""),
        run_id=state.get("run_id", ""),
    )
    validation = validate_analysis_completeness(loaded_artifacts)
    if not validation.ok:
        errors = [*validation.errors]
        if validation.non_executable_reason:
            errors.append(f"Analysis not executable: {validation.non_executable_reason}")
        return {
            "planning_status": "FAIL",
            "current_unit": "planning",
            "errors": errors,
            "blockers": errors,
            "warnings": validation.warnings,
            "planning_output_artifacts": output_paths,
            "planning_validation_status": "SKIPPED",
            "planning_assist_status": "SKIPPED",
            "planning_assist_error": "Planning skipped due to analysis artifact load failure.",
            "planning_assist_warnings": validation.warnings,
        }

    loaded_profile = load_migration_profile(
        ai_hub_path=state.get("ai_hub_path", ""),
        profile_id=profile_id,
    )
    if not loaded_profile.ok:
        return {
            "planning_status": "FAIL",
            "current_unit": "planning",
            "errors": loaded_profile.errors,
            "blockers": list(loaded_profile.errors),
            "warnings": [],
            "planning_output_artifacts": output_paths,
            "planning_validation_status": "SKIPPED",
            "planning_assist_status": "SKIPPED",
            "planning_assist_error": "Planning skipped due to migration profile load failure.",
            "planning_assist_warnings": [],
        }

    compatibility = validate_profile_compatibility(loaded_artifacts, loaded_profile)
    if not compatibility.ok:
        return {
            "planning_status": "FAIL",
            "current_unit": "planning",
            "errors": compatibility.errors,
            "blockers": list(compatibility.errors),
            "warnings": compatibility.warnings,
            "planning_output_artifacts": output_paths,
            "planning_validation_status": "SKIPPED",
            "planning_assist_status": "SKIPPED",
            "planning_assist_error": "Planning skipped due to profile compatibility validation failure.",
            "planning_assist_warnings": compatibility.warnings,
        }

    units = build_migration_units(loaded_profile.profile)
    unit_ids = tuple(unit.id for unit in units)

    risk_result = classify_planning_risks(
        loaded_artifacts,
        compatibility.source_stack,
        profile_id=profile_id,
        migration_units=unit_ids,
    )
    risk_messages = [f"[{risk.severity}] {risk.code}: {risk.message}" for risk in risk_result.risks]
    blocker_messages = [
        f"{risk.code}: {risk.message}"
        for risk in risk_result.risks
        if risk.severity == "BLOCKER"
    ]
    risk_warning_messages = [
        f"{risk.code}: {risk.message}"
        for risk in risk_result.risks
        if risk.severity == "WARNING"
    ]
    deterministic_warnings = [*compatibility.warnings, *risk_warning_messages]

    profile_governance = _profile_governance(loaded_profile.profile)
    write_migration_plan(
        modernized_app_path=state.get("modernized_app_path", ""),
        payload=MigrationPlanPayload(
            run_id=state.get("run_id", ""),
            profile=profile_id,
            source_stack=compatibility.source_stack,
            target_stack=compatibility.target_stack,
            risks=tuple(risk_messages),
            blockers=tuple(blocker_messages),
            warnings=tuple(deterministic_warnings),
            units=units,
            strategy=profile_governance.get("strategy"),
            risk_level=profile_governance.get("risk_level"),
            production_allowed=profile_governance.get("production_allowed"),
            fallback_profile=profile_governance.get("fallback_profile"),
        ),
    )
    write_migration_units(
        modernized_app_path=state.get("modernized_app_path", ""),
        run_id=state.get("run_id", ""),
        units=units,
    )
    deterministic_approval_summary = (
        f"Planning generated {len(units)} migration units for profile "
        f"{profile_id}."
    )

    write_approval_request(
        modernized_app_path=state.get("modernized_app_path", ""),
        payload=ApprovalRequestPayload(
            run_id=state.get("run_id", ""),
            profile=profile_id,
            summary=deterministic_approval_summary,
            units=units,
            blockers=tuple(blocker_messages),
            warnings=tuple(deterministic_warnings),
        ),
    )
    write_plan_summary(
        modernized_app_path=state.get("modernized_app_path", ""),
        payload=PlanSummaryPayload(
            run_id=state.get("run_id", ""),
            profile=profile_id,
            source_stack=compatibility.source_stack,
            target_stack=compatibility.target_stack,
            risks=tuple(risk_messages),
            warnings=tuple(deterministic_warnings),
            units=units,
        ),
    )
    target_dependency_plan_path = write_target_dependency_plan(
        run_dir=Path(state.get("modernized_app_path", "")) / ".migration" / "runs" / state.get("run_id", ""),
        source_boot_version=compatibility.source_stack.spring_boot or "",
        target_boot_version=compatibility.target_stack.spring_boot or "",
        target_java_version=compatibility.target_stack.java or "",
        profile_id=profile_id,
        migration_unit_ids=unit_ids,
        openrewrite_recipes_expected=_expected_openrewrite_recipes(loaded_profile.profile, state),
    )
    validation_result = validate_planning_outputs(
        modernized_app_path=state.get("modernized_app_path", ""),
        run_id=state.get("run_id", ""),
    )
    unit_payload = [
        {
            "id": unit.id,
            "goal": unit.goal,
            "writes_source": unit.writes_source,
            "tools": list(unit.tools),
            "validation": list(unit.validation),
            "required": unit.required,
        }
        for unit in units
    ]

    try:
        config = load_planning_assist_config(ai_hub_path=state.get("ai_hub_path", ""), phase="planning")
    except TypeError:
        config = load_planning_assist_config()
    assist_result = PlanningAssistResult(status="SKIPPED")
    if not config.enabled:
        assist_result_status = "SKIPPED"
        assist_result_error = None
        assist_result_warnings = ["Planning assist disabled by config."]
    else:
        request = PlanningAssistRequest(
            run_id=state.get("run_id", ""),
            agent="planning_agent",
            phase="planning",
            model=config.model_override or config.default_model,
            prompt="Review planning output for advisory feedback only.",
            context={
                "profile": profile_id,
                "source_stack": compatibility.source_stack,
                "target_stack": compatibility.target_stack,
                "risks": risk_messages,
                "warnings": list(deterministic_warnings),
                "migration_units": unit_payload,
                "approval_summary": deterministic_approval_summary,
            },
            allowed_fields=["warnings", "approval_summary", "operator_notes", "risks"],
            forbidden_fields=[
                "unit_order",
                "tools",
                "blockers",
                "approval_required",
                "executable",
            ],
        )
        assist_result = CopilotPlanningAssistClient().review_plan(
            request=request, config=config
        )
        validation = validate_assist_output_for_merge(
            request=request,
            assist_result=assist_result,
        )
        assist_result = validation.sanitized_result
        assist_result_status = assist_result.status
        assist_result_error = assist_result.error
        assist_result_warnings = [*assist_result.warnings, *validation.warnings]
    merged_output = merge_advisory_assist_suggestions(
        deterministic_approval_summary=deterministic_approval_summary,
        deterministic_warnings=list(deterministic_warnings),
        assist_result=assist_result,
    )
    if assist_result.status == "USED":
        assist_result_warnings = [
            *assist_result_warnings,
            (
                "[WARNING] Ignored attempted structural changes if present: unit_order, "
                "tools, blockers, approval_required, executable."
            ),
        ]
    _safe_write_assist_artifact(
        CopilotAssistArtifactPayload(
            run_id=state.get("run_id", ""),
            status=assist_result.status,
            provider=config.provider,
            auth=config.auth_mode if config.enabled else "disabled",
            model=assist_result.resolved_model or config.model_override or config.default_model,
            requested_model=assist_result.requested_model or config.model_override or config.default_model,
            resolved_model=assist_result.resolved_model,
            model_source=assist_result.model_source,
            model_verified=assist_result.model_verified,
            inputs_summary={
                "profile": profile_id,
                "units_count": len(units),
                "risk_count": len(risk_messages),
                "warning_count": len(deterministic_warnings),
            },
            advisory_summary={
                "approval_summary_applied": merged_output.approval_summary
                != deterministic_approval_summary,
                "warning_count": len(merged_output.warnings),
                "operator_notes_count": len(merged_output.operator_notes),
                "risk_explanations_count": len(merged_output.risk_explanations),
            },
            warnings=assist_result_warnings,
            error=assist_result.error,
        )
    )

    planning_errors = list(validation_result.reasons) if validation_result.status != "PASS" else []
    planning_blockers = [*blocker_messages, *planning_errors]
    planning_status = "FAIL" if planning_blockers else "PASS"
    planning_artifact_refs = {
        **output_paths,
        "target_dependency_plan": str(target_dependency_plan_path),
    }
    review_updates: dict[str, object] = {}
    if planning_status == "PASS" and state.get("job_id"):
        from migration_factory.orchestrator.review_chain import (
            ReviewChainProductionError,
            produce_phase_review_chain,
        )

        try:
            review_updates = produce_phase_review_chain(
                state,
                phase="planning",
                stage_index=2,
                artifact_refs=planning_artifact_refs,
                deterministic_facts={
                    "profile": profile_id,
                    "source_stack": compatibility.source_stack,
                    "target_stack": compatibility.target_stack,
                    "risk_count": len(risk_messages),
                    "warning_count": len(deterministic_warnings),
                    "unit_count": len(units),
                    "units": unit_payload,
                },
                warnings=merged_output.warnings,
            )
        except ReviewChainProductionError as exc:
            planning_blockers.append(str(exc))
            planning_status = "FAIL"
    return {
        "planning_status": planning_status,
        "current_unit": "planning",
        "errors": planning_blockers,
        "blockers": planning_blockers,
        "warnings": merged_output.warnings,
        "planning_assist_status": assist_result_status,
        "planning_assist_error": (
            "Planning output validation failed."
            if validation_result.status != "PASS"
            else assist_result_error
        ),
        "planning_output_artifacts": output_paths,
        "planning_validation_status": validation_result.status,
        "risks": risk_messages,
        "migration_units": unit_payload,
        "planning_approval_summary": merged_output.approval_summary,
        "planning_operator_notes": merged_output.operator_notes,
        "planning_risk_explanations": merged_output.risk_explanations,
        "planning_assist_warnings": assist_result_warnings,
        "artifact_refs": {
            **dict(state.get("artifact_refs", {}) or {}),
            **planning_artifact_refs,
            **dict(review_updates.get("artifact_refs", {}) or {}),
        },
        **({"review_chain": review_updates["review_chain"]} if review_updates.get("review_chain") else {}),
    }


def _profile_governance(profile: dict) -> dict:
    governance = profile.get("governance")
    if not isinstance(governance, dict):
        governance = {}
    return {
        "strategy": profile.get("strategy") or governance.get("strategy"),
        "risk_level": profile.get("risk_level") or governance.get("risk_level"),
        "production_allowed": (
            profile.get("production_allowed")
            if "production_allowed" in profile
            else governance.get("production_allowed")
        ),
        "fallback_profile": profile.get("fallback_profile") or governance.get("fallback_profile"),
    }


def _expected_openrewrite_recipes(profile: dict, state: MigrationState) -> list[str]:
    openrewrite = profile.get("openrewrite")
    if not isinstance(openrewrite, dict):
        return []
    catalog_rel = str(openrewrite.get("catalog_path") or "").strip()
    if not catalog_rel:
        return []
    try:
        import yaml

        catalog_path = Path(state.get("ai_hub_path", "")) / catalog_rel
        payload = yaml.safe_load(catalog_path.read_text(encoding="utf-8")) or {}
    except Exception:
        return []
    recipes = payload.get("active_recipes") if isinstance(payload, dict) else None
    if not isinstance(recipes, list):
        return []
    return [str(recipe) for recipe in recipes]
