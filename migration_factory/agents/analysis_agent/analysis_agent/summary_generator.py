def generate_summary(context, maven_data, import_data):
    source_stack = maven_data.get("source_stack") or {}
    target_stack = maven_data.get("target_stack", {})
    project_structure = maven_data.get("project_structure") or {}
    warning_lines = "\n".join(
        f"* **Avertissement :** {warning}" for warning in maven_data.get("warnings", [])
    ) or "* Aucun avertissement de cible."

    summary = f"""# Rapport d'Analyse de Migration (AIMF)
**ID du Run :** {context.run_id}
**Date :** {maven_data.get('timestamp', 'N/A')}

## 1. Etat de la Stack (Stack & Gap)
* **Java Source :** {source_stack.get('java', 'unknown')} -> **Cible :** {target_stack.get('java', '17')} [cite: 342, 443]
* **Spring Boot :** {source_stack.get('spring_boot', 'unknown')} -> **Cible :** {target_stack.get('spring_boot', '3.5.14')} [cite: 342, 443]
* **Build Tool :** {source_stack.get('build_tool', 'unknown')}
* **Spring Framework Cible :** {target_stack.get('spring_framework', 'unknown')}

## 2. Structure du Projet
* **Nombre de modules detectes :** {project_structure.get('module_count', 0)} [cite: 444]
* **Modules :** {", ".join(project_structure.get('modules', []))}

## 3. Inventaire de Migration (Imports)
* **Imports `javax.*` (a migrer) :** {import_data['javax_imports']} [cite: 444]
* **Imports `jakarta.*` :** {import_data['jakarta_imports']}
* **Imports Spring :** {import_data['spring_imports']}

## 4. Recommandations de l'Agent de Planning
* [ ] Migrer les dependances du POM racine. [cite: 445]
* [ ] Remplacer les imports `javax` par `jakarta` dans {len(import_data['files_with_javax'])} fichiers. [cite: 445]

## 5. Avertissements de cible
{warning_lines}
"""

    output_file = context.get_output_path("analysis_summary.md")
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(summary)

    return output_file
