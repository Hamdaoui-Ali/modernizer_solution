import json
import datetime

def assemble_report(context, maven_data, import_data):
    source_stack = maven_data.get("source_stack") or {}
    target_stack = maven_data.get("target_stack") or {}
    project_structure = maven_data.get("project_structure") or {}
    # Construction du rapport final selon le schéma AMF (Task #22, #231)
    report = {
        "schema_version": "1.0.0",
        "run_id": context.run_id,
        "agent": "analysis_agent",
        "status": "PASS",
        "timestamp": datetime.datetime.now().isoformat(),
        
        # Ces clés doivent correspondre exactement aux sorties du maven_scanner
        "source_stack": source_stack,
        "target_stack": target_stack,
        "warnings": maven_data.get("warnings", []),
        
        "project_metadata": {
            "modules": project_structure.get("modules", []),
            "import_stats": {
                "javax_count": import_data["javax_imports"],
                "jakarta_count": import_data["jakarta_imports"],
                "spring_count": import_data["spring_imports"]
            },
            "critical_files_to_migrate": import_data["files_with_javax"]
        },
        
        "rewrite": {
            "preview_artifact": "rewrite_preview.json",
            "plugin_plan_artifact": "rewrite_plugin_plan.json",
            "patch_artifact": "rewrite_dry_run.patch",
            "impact_artifact": "rewrite_impact_summary.json"
        },

        "ai_enrichment": {
            "status": "SKIPPED",
            "additional_risks": [],
            "recommendations": []
        },

        "artifact_refs": {
            "self": "analysis_report.json",
            "dependency_graph": "dependency_graph.json",
            "test_inventory": "test_inventory.json",
            "analysis_summary": "analysis_summary.md",
            "rewrite_preview": "rewrite_preview.json",
            "rewrite_plugin_plan": "rewrite_plugin_plan.json",
            "rewrite_dry_run": "rewrite_dry_run.patch",
            "rewrite_impact_summary": "rewrite_impact_summary.json"
        }
    }
    
    # Écriture de l'artéfact dans le dossier d'analyse (Task #16, #17)
    output_file = context.get_output_path("analysis_report.json")
    with open(output_file, 'w', encoding='utf-8') as f:
        json.dump(report, f, indent=4)
    
    return report
