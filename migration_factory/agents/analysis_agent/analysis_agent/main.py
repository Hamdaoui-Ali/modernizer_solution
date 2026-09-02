import argparse
from datetime import datetime, timezone
import inspect
import json
import sys
from dataclasses import dataclass
from typing import Dict, List

from config_scanner import save_config_inventory, scan_config_files
from context_manager import MigrationContext
from copilot_enricher import enrich_with_ai
from dependency_adapter import run_dependency_tree
from import_scanner import scan_java_imports
from maven_scanner import (
    build_source_profile_detection,
    load_profile_target_stack,
    scan_root_pom,
)
from openrewrite_adapter import run_openrewrite_dryrun
from readonly_verifier import snapshot_tree, write_read_only_verification
from report_assembler import assemble_report
from summary_generator import generate_summary
from surefire_parser import parse_surefire_reports
from test_scanner import save_test_inventory, scan_tests


@dataclass
class AnalysisResult:
    status: str
    artifact_paths: Dict[str, str]
    warnings: List[str]
    errors: List[str]
    assist_status: str
    rewrite_status: str


def run_analysis_agent(context: MigrationContext) -> AnalysisResult:
    warnings: List[str] = []
    errors: List[str] = []

    legacy_root = context.validate_read_path(context.legacy_app_path)
    legacy_pom = context.validate_read_path(f"{legacy_root}/pom.xml")
    modernized_root = context.validate_read_path(context.modernized_app_path)
    before_legacy = snapshot_tree(legacy_root)
    before_modernized = snapshot_tree(modernized_root)

    target_stack = load_profile_target_stack(context.ai_hub_path, context.profile)
    maven_results = _scan_root_pom_with_target(legacy_pom, target_stack)
    source_profile_detection = build_source_profile_detection(
        maven_results,
        job_id=context.run_id,
        created_at=_utc_now_text(),
        target_profile=_target_profile_from_context(context.profile, target_stack),
        evidence_checksum=_file_sha256_checksum(legacy_pom),
    )
    source_profile_detection_path = context.get_output_path("source_profile_detection.json")
    with open(source_profile_detection_path, "w", encoding="utf-8") as handle:
        json.dump(source_profile_detection.to_dict(), handle, indent=4)
    run_dependency_tree(context)

    import_results = scan_java_imports(legacy_root)
    analysis_facts = {
        "source_stack": maven_results.get("source_stack", {}),
        "target_stack": maven_results.get("target_stack", {}),
        "javax_count": import_results.get("javax_imports", 0),
    }

    config_inv = scan_config_files(legacy_root)
    save_config_inventory(context, config_inv)

    test_inventory = scan_tests(legacy_root)
    test_inventory["surefire_summary"] = parse_surefire_reports(legacy_root)
    save_test_inventory(context, test_inventory)

    rewrite_result = run_openrewrite_dryrun(context, analysis_facts=analysis_facts) or {}
    rewrite_status = rewrite_result.get("status", "SKIPPED")
    warnings.extend(maven_results.get("warnings", []))
    warnings.extend(rewrite_result.get("warnings", []))

    report_data = assemble_report(context, maven_results, import_results)
    final_report = enrich_with_ai(context, report_data)

    report_path = context.get_output_path("analysis_report.json")
    with open(report_path, "w", encoding="utf-8") as handle:
        json.dump(final_report, handle, indent=4)

    summary_path = generate_summary(context, maven_results, import_results)

    assist_status = "SKIPPED"
    if isinstance(final_report, dict):
        ai_enrichment = final_report.get("ai_enrichment") or {}
        assist_status = ai_enrichment.get("status", "SKIPPED")

    artifact_paths = {
        "analysis_report": report_path,
        "source_profile_detection": source_profile_detection_path,
        "dependency_graph": context.get_output_path("dependency_graph.json"),
        "test_inventory": context.get_output_path("test_inventory.json"),
        "analysis_summary": summary_path,
        "config_inventory": context.get_output_path("config_inventory.json"),
        "rewrite_preview": context.get_output_path("rewrite_preview.json"),
        "rewrite_patch": context.get_output_path("rewrite_dry_run.patch"),
        "rewrite_plugin_plan": context.get_output_path("rewrite_plugin_plan.json"),
        "rewrite_impact_summary": context.get_output_path("rewrite_impact_summary.json"),
        "copilot_assist": context.get_output_path("copilot_assist.json"),
        "read_only_verification": context.get_output_path("read_only_verification.json"),
    }

    read_only_verification = write_read_only_verification(context, before_legacy, before_modernized)
    if read_only_verification["source_modified"]:
        errors.append("Analysis modified source files; see read_only_verification.json")

    status = "COMPLETED" if not errors else "FAILED"
    return AnalysisResult(
        status=status,
        artifact_paths=artifact_paths,
        warnings=warnings,
        errors=errors,
        assist_status=assist_status,
        rewrite_status=rewrite_status,
    )


def _scan_root_pom_with_target(legacy_pom, target_stack):
    signature = inspect.signature(scan_root_pom)
    if "target_stack" in signature.parameters:
        return scan_root_pom(legacy_pom, target_stack=target_stack)
    return scan_root_pom(legacy_pom)


def _target_profile_from_context(profile_id, target_stack):
    profile_text = str(profile_id or "")
    profile_targets = {
        "springboot-2.7-to-3.5-java17": "springboot-3.5-java17",
        "springboot-3.5-java17-to-java21": "springboot-3.5-java21",
        "springboot-3.5-java21-to-4.0-java21": "springboot-4.0-java21",
    }
    if profile_text in profile_targets:
        return profile_targets[profile_text]

    target_boot = str((target_stack or {}).get("spring_boot", ""))
    target_java = str((target_stack or {}).get("java", ""))
    if target_boot.startswith("4.") and target_java.startswith("21"):
        return "springboot-4.0-java21"
    if target_boot.startswith("3.") and target_java.startswith("21"):
        return "springboot-3.5-java21"
    if target_boot.startswith("3.") and target_java.startswith("17"):
        return "springboot-3.5-java17"
    return None


def _file_sha256_checksum(path):
    import hashlib

    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return f"sha256:{digest.hexdigest()}"


def _utc_now_text():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def main():
    parser = argparse.ArgumentParser(description="AIMF Analysis Agent CLI")
    parser.add_argument("--run-id", required=True, help="ID unique de la migration")
    parser.add_argument("--legacy", required=True, help="Chemin vers l'application source")
    parser.add_argument("--modernized", required=True, help="Chemin vers le dossier de sortie")
    parser.add_argument("--ai-hub", help="Chemin vers le AI Hub contenant profiles/ et catalogs/")
    parser.add_argument("--profile", help="ID du profil AI Hub à charger")

    args = parser.parse_args()
    if bool(args.ai_hub) != bool(args.profile):
        parser.error("--ai-hub and --profile must be provided together")

    try:
        print(f"🚀 [AIMF] Démarrage de l'analyse - Run ID: {args.run_id}")
        ctx = MigrationContext(args.run_id, args.legacy, args.modernized, args.ai_hub, args.profile)
        result = run_analysis_agent(ctx)

        print("-" * 50)
        print("✅ ANALYSE TERMINÉE AVEC SUCCÈS")
        print(f"📂 Rapport JSON : {result.artifact_paths['analysis_report']}")
        print(f"📄 Résumé MD    : {result.artifact_paths['analysis_summary']}")
        print("-" * 50)

    except Exception as exc:
        print(f"❌ ERREUR CRITIQUE : {exc}")
        sys.exit(1)


if __name__ == "__main__":
    main()
