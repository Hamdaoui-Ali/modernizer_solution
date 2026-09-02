from __future__ import annotations

import json
from pathlib import Path

from migration_factory.dependency_policy import (
    apply_policy_patches_if_enabled,
    build_dependency_copilot_request,
    invoke_dependency_copilot_advisory,
    scan_dependency_policy,
    validate_dependency_copilot_response,
    write_dependency_policy_artifacts,
    write_target_dependency_plan,
)
from migration_factory.dependency_policy.artifacts import build_target_dependency_plan
from migration_factory.final_report.writer import generate_final_migration_report


def test_target_dependency_plan_generated_for_boot35(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"

    path = write_target_dependency_plan(
        run_dir=run_dir,
        source_boot_version="2.7",
        target_boot_version="3.5.14",
        target_java_version="17",
        openrewrite_recipes_expected=["org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"],
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert path.name == "target_dependency_plan.json"
    assert payload["target_jakarta_required"] is True
    assert "DEP-TOMCAT-BOOT3-001" in payload["policy_rule_ids"]
    assert payload["auto_apply_copilot_allowed"] is False


def test_target_dependency_plan_not_jakarta_required_for_java21_runtime_validation_route(tmp_path: Path) -> None:
    run_dir = tmp_path / "run"

    path = write_target_dependency_plan(
        run_dir=run_dir,
        source_boot_version="3.5.14",
        target_boot_version="3.5.14",
        target_java_version="21",
        profile_id="springboot-3.5-java17-to-java21",
        migration_unit_ids=["baseline", "java-21-runtime-validation"],
        openrewrite_recipes_expected=["org.openrewrite.java.migrate.UpgradeToJava21"],
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["target_jakarta_required"] is False
    assert payload["openrewrite_recipes_expected"] == ["org.openrewrite.java.migrate.UpgradeToJava21"]


def test_scanner_detects_tomcat9_override_as_v2_runtime_warning_after_build_pass(tmp_path: Path) -> None:
    app = _app_with_pom(tmp_path, properties={"tomcat.version": "9.0.102"})
    report = scan_dependency_policy(
        sandbox_path=app,
        target_plan=build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17"),
        build_passed=True,
    )

    risk = _risk(report.to_dict(), "DEP-TOMCAT-BOOT3-001")
    assert risk["severity"] == "WARNING"
    assert risk["blocks_v1_build_test"] is False
    assert risk["blocks_v2_runtime"] is True
    assert report.status == "PASS_WITH_WARNINGS"


def test_scanner_detects_old_zalando_problem_spring_web(tmp_path: Path) -> None:
    app = _app_with_pom(
        tmp_path,
        properties={"org-zalando.version": "0.24.0"},
        dependencies=[
            ("org.zalando", "problem-spring-web", "${org-zalando.version}"),
        ],
    )

    report = scan_dependency_policy(
        sandbox_path=app,
        target_plan=build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17"),
        build_passed=True,
    )

    risk = _risk(report.to_dict(), "DEP-ZALANDO-BOOT3-001")
    assert risk["category"] == "OLD_ZALANDO"
    assert "0.24.0" in risk["evidence"]
    assert risk["deterministic_fix_available"] is False


def test_scanner_separates_javax_source_from_logback_logger_names(tmp_path: Path) -> None:
    app = _app_with_pom(tmp_path)
    source = app / "src" / "main" / "java" / "Example.java"
    source.parent.mkdir(parents=True)
    source.write_text("import javax.servlet.http.HttpServletRequest;\nclass Example {}\n", encoding="utf-8")
    logback = app / "src" / "test" / "resources" / "logback-test.xml"
    logback.parent.mkdir(parents=True)
    logback.write_text('<logger name="javax.servlet" level="INFO"/>\n', encoding="utf-8")

    report = scan_dependency_policy(
        sandbox_path=app,
        target_plan=build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17"),
    )
    payload = report.to_dict()

    assert any(r["severity"] == "ERROR" and r["file"].endswith("Example.java") for r in payload["risks"])
    assert any(r["severity"] == "INFO" and r["file"].endswith("logback-test.xml") for r in payload["risks"])
    assert report.blocked_for_build_test is True


def test_scanner_accepts_jakarta_persistence_imports(tmp_path: Path) -> None:
    app = _app_with_pom(tmp_path)
    source = app / "src" / "main" / "java" / "Entity.java"
    source.parent.mkdir(parents=True)
    source.write_text("import jakarta.persistence.Entity;\n@Entity class Thing {}\n", encoding="utf-8")

    report = scan_dependency_policy(
        sandbox_path=app,
        target_plan=build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17"),
    )

    assert not any(risk.rule_id == "DEP-JAVAX-DEPS-001" and risk.severity == "ERROR" for risk in report.risks)
    assert report.source_vs_dependency_jakarta_findings["jakarta_imports_ok"]


def test_scanner_flags_explicit_tomcat_embed_versions(tmp_path: Path) -> None:
    app = _app_with_pom(
        tmp_path,
        dependencies=[("org.apache.tomcat.embed", "tomcat-embed-core", "9.0.102")],
    )

    report = scan_dependency_policy(
        sandbox_path=app,
        target_plan=build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17"),
    )

    risk = _risk(report.to_dict(), "DEP-TOMCAT-BOOT3-002")
    assert risk["deterministic_fix_available"] is True


def test_report_outputs_machine_json_and_markdown(tmp_path: Path) -> None:
    app = _app_with_pom(tmp_path / "app", properties={"tomcat.version": "9.0.102"})
    report = scan_dependency_policy(
        sandbox_path=app,
        target_plan=build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17"),
    )

    refs = write_dependency_policy_artifacts(run_dir=tmp_path / "run", report=report)

    assert json.loads(refs["dependency_policy_report"].read_text(encoding="utf-8"))["risks"]
    assert "Dependency Policy Report" in refs["dependency_policy_summary"].read_text(encoding="utf-8")


def test_no_policy_patch_applied_by_default(tmp_path: Path) -> None:
    app = _app_with_pom(tmp_path / "app", properties={"tomcat.version": "9.0.102"})
    plan = build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17")
    report = scan_dependency_policy(sandbox_path=app, target_plan=plan)

    result = apply_policy_patches_if_enabled(sandbox_path=app, run_dir=tmp_path / "run", report=report, target_plan=plan, env={})

    assert result["policy_patch_applied"] is False
    assert "<tomcat.version>9.0.102</tomcat.version>" in (app / "pom.xml").read_text(encoding="utf-8")


def test_policy_patch_removes_tomcat_version_only_when_enabled(tmp_path: Path) -> None:
    app = _app_with_pom(tmp_path / "app", properties={"tomcat.version": "9.0.102"})
    plan = build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17")
    report = scan_dependency_policy(sandbox_path=app, target_plan=plan)

    result = apply_policy_patches_if_enabled(
        sandbox_path=app,
        run_dir=tmp_path / "run",
        report=report,
        target_plan=plan,
        env={"AI_MIGRATION_APPLY_DEPENDENCY_POLICY_FIXES": "true"},
    )

    assert result["policy_patch_applied"] is True
    assert "tomcat.version" not in (app / "pom.xml").read_text(encoding="utf-8")


def test_copilot_dependency_advisory_request_includes_pom_policy_report_and_plan(tmp_path: Path) -> None:
    app = _app_with_pom(tmp_path / "app", properties={"tomcat.version": "9.0.102"})
    plan = build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17")
    report = scan_dependency_policy(sandbox_path=app, target_plan=plan)

    request = build_dependency_copilot_request(
        run_dir=tmp_path / "run",
        sandbox_path=app,
        target_plan=plan,
        policy_report=report,
    )

    assert "pom.xml" not in request
    assert "tomcat.version" in request["pom_xml"]
    assert request["dependency_policy_report"]["risks"]
    assert request["target_dependency_plan"]["auto_apply_copilot_allowed"] is False


def test_copilot_dependency_response_is_proposal_only() -> None:
    valid, errors = validate_dependency_copilot_response(
        {
            "schema_version": "1.0.0",
            "summary": "Use compatible dependency.",
            "dependency_findings": [],
            "proposed_changes": [
                {
                    "rule_id": "DEP-ZALANDO-BOOT3-001",
                    "file": "pom.xml",
                    "change_type": "dependency_version",
                    "before": "0.24.0",
                    "after": "proposal",
                    "reason": "Jakarta compatibility",
                    "safe_to_auto_apply": False,
                }
            ],
            "risks": [],
            "confidence": "LOW",
            "limitations": [],
            "no_auto_apply_ack": True,
        }
    )

    assert valid is True
    assert errors == []


def test_invalid_copilot_advisory_generates_deterministic_fallback(tmp_path: Path) -> None:
    app = _app_with_pom(tmp_path / "app", properties={"org-zalando.version": "0.24.0"}, dependencies=[("org.zalando", "problem-spring-web", "${org-zalando.version}")])
    plan = build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17")
    report = scan_dependency_policy(sandbox_path=app, target_plan=plan)

    result = invoke_dependency_copilot_advisory(
        run_dir=tmp_path / "run",
        sandbox_path=app,
        target_plan=plan,
        policy_report=report,
        invoker=lambda request: {"summary": "missing required fields"},
    )

    assert result["status"] == "FALLBACK"
    response = json.loads(Path(result["artifact_refs"]["dependency_copilot_response"]).read_text(encoding="utf-8"))
    assert response["no_auto_apply_ack"] is True
    assert response["proposed_changes"] == []


def test_final_report_includes_dependency_policy_refs(tmp_path: Path) -> None:
    run_dir = tmp_path / "modernized" / ".migration" / "runs" / "run-1"
    state = _final_report_state(tmp_path, run_dir)
    policy_report = {
        "status": "PASS_WITH_WARNINGS",
        "risks": [
            {
                "rule_id": "DEP-TOMCAT-BOOT3-001",
                "severity": "WARNING",
                "blocks_v1_build_test": False,
                "blocks_v2_runtime": True,
            }
        ],
    }
    dep_report = run_dir / "assessment" / "dependency_policy_report.json"
    dep_report.write_text(json.dumps(policy_report) + "\n", encoding="utf-8")
    target_plan = run_dir / "planning" / "target_dependency_plan.json"
    target_plan.write_text("{}\n", encoding="utf-8")
    state["artifact_refs"]["dependency_policy_report"] = str(dep_report)
    state["artifact_refs"]["target_dependency_plan"] = str(target_plan)
    state["dependency_policy_status"] = "PASS_WITH_WARNINGS"
    state["dependency_policy_risks_count"] = 1

    result = generate_final_migration_report(state)
    payload = json.loads(Path(result.artifact_refs["final_migration_report"]).read_text(encoding="utf-8"))

    assert payload["dependency_policy_status"] == "PASS_WITH_WARNINGS"
    assert payload["dependency_policy_report_ref"] == str(dep_report)
    assert payload["unresolved_v2_dependency_risks"][0]["rule_id"] == "DEP-TOMCAT-BOOT3-001"


def test_boot35_final_sandbox_can_pass_tests_with_dependency_warnings(tmp_path: Path) -> None:
    app = _app_with_pom(tmp_path / "app", properties={"tomcat.version": "9.0.102"})
    report = scan_dependency_policy(
        sandbox_path=app,
        target_plan=build_target_dependency_plan(target_boot_version="3.5.14", target_java_version="17"),
        build_passed=True,
    )

    assert report.status == "PASS_WITH_WARNINGS"
    assert report.blocked_for_build_test is False
    assert report.blocked_for_runtime is True


def test_target_dependency_plan_still_requires_jakarta_for_boot2_to_boot3(tmp_path: Path) -> None:
    path = write_target_dependency_plan(
        run_dir=tmp_path / "run",
        source_boot_version="2.7.18",
        target_boot_version="3.5.14",
        target_java_version="17",
        openrewrite_recipes_expected=["org.openrewrite.java.spring.boot3.UpgradeSpringBoot_3_5"],
    )

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["target_jakarta_required"] is True


def _risk(payload: dict, rule_id: str) -> dict:
    for risk in payload["risks"]:
        if risk["rule_id"] == rule_id:
            return risk
    raise AssertionError(f"missing risk {rule_id}")


def _app_with_pom(
    root: Path,
    *,
    properties: dict[str, str] | None = None,
    dependencies: list[tuple[str, str, str]] | None = None,
) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    props = "\n".join(f"    <{key}>{value}</{key}>" for key, value in (properties or {}).items())
    deps = "\n".join(
        f"""
    <dependency>
      <groupId>{group}</groupId>
      <artifactId>{artifact}</artifactId>
      <version>{version}</version>
    </dependency>""".rstrip()
        for group, artifact, version in (dependencies or [])
    )
    (root / "pom.xml").write_text(
        f"""
<project>
  <modelVersion>4.0.0</modelVersion>
  <properties>
{props}
  </properties>
  <dependencies>
{deps}
  </dependencies>
</project>
""".lstrip(),
        encoding="utf-8",
    )
    return root


def _final_report_state(tmp_path: Path, run_dir: Path) -> dict:
    legacy = tmp_path / "legacy"
    modernized = tmp_path / "modernized"
    sandbox = run_dir / "workspaces" / "sandbox"
    for rel in ("approval", "planning", "assessment", "transformation", "test/post_transform", "logs"):
        (run_dir / rel).mkdir(parents=True, exist_ok=True)
    (sandbox / ".migration").mkdir(parents=True, exist_ok=True)
    legacy.mkdir(exist_ok=True)
    modernized.mkdir(exist_ok=True)
    paths = {
        "approval_decision": run_dir / "approval" / "approval_decision.json",
        "approved_plan_lock": run_dir / "approval" / "approved_plan_lock.json",
        "transformation_execution_plan": run_dir / "transformation" / "transformation_execution_plan.yaml",
        "migration_ledger": sandbox / ".migration" / "ledger.json",
        "orchestration_summary": run_dir / "orchestration" / "orchestration_summary.json",
        "post_transform_test_report": run_dir / "test" / "post_transform" / "test_report.json",
    }
    paths["orchestration_summary"].parent.mkdir(parents=True, exist_ok=True)
    paths["approval_decision"].write_text('{"decision":"approved"}\n', encoding="utf-8")
    paths["approved_plan_lock"].write_text("{}\n", encoding="utf-8")
    paths["transformation_execution_plan"].write_text("recipes: []\n", encoding="utf-8")
    paths["migration_ledger"].write_text("{}\n", encoding="utf-8")
    paths["orchestration_summary"].write_text('{"artifact_refs":{}}\n', encoding="utf-8")
    paths["post_transform_test_report"].write_text(
        '{"test_status":"TEST_PASSED","totals":{"tests":1,"passed":1,"failures":0,"errors":0}}\n',
        encoding="utf-8",
    )
    (run_dir / "planning" / "migration_plan.yaml").write_text(
        'target_stack:\n  java: "17"\n  spring_boot: "3.5.14"\n',
        encoding="utf-8",
    )
    (run_dir / "assessment" / "assessment_report.json").write_text("{}\n", encoding="utf-8")
    return {
        "run_dir": str(run_dir),
        "run_id": "run-1",
        "artifact_refs": {key: str(path) for key, path in paths.items()},
        "approval_status": "COMPLETED",
        "approved_by": "tester",
        "transform_status": "TRANSFORM_APPLIED_IN_SANDBOX",
        "build_status": "BUILD_PASSED_IN_SANDBOX",
        "test_status": "TEST_PASSED",
        "test_totals": {"tests": 1, "passed": 1, "failures": 0, "errors": 0},
        "sandbox_path": str(sandbox),
    }
