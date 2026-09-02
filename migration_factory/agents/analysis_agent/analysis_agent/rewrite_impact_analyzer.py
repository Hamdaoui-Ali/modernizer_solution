import re


def _major_version(value):
    match = re.match(r"^\D*(\d+)", str(value or ""))
    return int(match.group(1)) if match else None


def _facts_indicate_major_migration(analysis_facts, migration_signals):
    facts = analysis_facts or {}
    source_stack = facts.get("source_stack") or {}
    target_stack = facts.get("target_stack") or {}
    javax_count = int(facts.get("javax_count") or 0)

    source_boot = _major_version(source_stack.get("spring_boot"))
    target_boot = _major_version(target_stack.get("spring_boot"))
    source_java = _major_version(source_stack.get("java"))
    target_java = _major_version(target_stack.get("java"))

    boot_gap = source_boot == 2 and target_boot in {3, 4}
    boot4_gap = source_boot == 2 and target_boot == 4
    java_gap = (source_java, target_java) in {(11, 17), (8, 21), (11, 21), (17, 21)}
    javax_gap = javax_count > 0

    if boot_gap:
        migration_signals["api_or_boot_upgrade"] = True
        if target_boot == 3:
            migration_signals["boot_2_to_3_gap"] = True
        if boot4_gap:
            migration_signals["boot_2_to_4_gap"] = True
            migration_signals["boot4_target"] = True
    if java_gap:
        if target_java == 17:
            migration_signals["java_11_to_17_gap"] = True
        if target_java == 21:
            migration_signals["java_21_target"] = True
            if source_java == 8:
                migration_signals["java_8_to_21_gap"] = True
    if javax_gap:
        migration_signals["javax_removed"] = True
        migration_signals["javax_present"] = True

    return boot_gap or java_gap or javax_gap


def analyze_rewrite_patch(patch_text, analysis_facts=None):
    files = set()
    java_files = 0
    pom_files = 0
    config_files = 0
    test_files = 0
    high_risk = []
    migration_signals = {
        "api_or_boot_upgrade": False,
        "javax_removed": False,
        "boot_2_to_3_gap": False,
        "java_11_to_17_gap": False,
        "javax_present": False,
        "boot_2_to_4_gap": False,
        "boot4_target": False,
        "java_8_to_21_gap": False,
        "java_21_target": False,
        "security_config_touched": False,
        "datasource_config_touched": False,
    }
    added = 0
    removed = 0

    current_file = None
    for line in patch_text.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) >= 4:
                current_file = parts[3].removeprefix("b/")
                files.add(current_file)

                if current_file.endswith(".java"):
                    java_files += 1
                if current_file.endswith("pom.xml") or current_file == "pom.xml":
                    pom_files += 1
                if any(current_file.endswith(ext) for ext in (".yml", ".yaml", ".properties", ".xml", ".conf")):
                    config_files += 1
                if re.search(r"(^|/)src/test/|Test\.java$", current_file):
                    test_files += 1
                if current_file.endswith("pom.xml") or "src/main/java" in current_file:
                    high_risk.append(current_file)
                lower_file = current_file.lower()
                if "security" in lower_file:
                    migration_signals["security_config_touched"] = True
                if "datasource" in lower_file or "application." in lower_file:
                    migration_signals["datasource_config_touched"] = True

        if line.startswith("+++") or line.startswith("---"):
            continue
        if line.startswith("+"):
            added += 1
            if "jakarta." in line or "javax." in line or "spring.boot3" in line.lower():
                migration_signals["api_or_boot_upgrade"] = True
        elif line.startswith("-"):
            removed += 1
            if "javax." in line:
                migration_signals["javax_removed"] = True

    has_fact_migration = not files and _facts_indicate_major_migration(
        analysis_facts, migration_signals
    )

    if has_fact_migration:
        level = "HIGH"
    elif not files:
        level = "UNKNOWN"
    elif pom_files > 0 or len(high_risk) > 3 or (added + removed) > 250:
        level = "HIGH"
    elif (added + removed) > 50 or java_files > 5:
        level = "MEDIUM"
    else:
        level = "LOW"

    return {
        "overall_impact": level,
        "changed_files": sorted(files),
        "changed_file_count": len(files),
        "patch_lines_added": added,
        "patch_lines_removed": removed,
        "java_files_changed": java_files,
        "pom_files_changed": pom_files,
        "config_files_changed": config_files,
        "test_files_changed": test_files,
        "high_risk_files": sorted(set(high_risk)),
        "migration_signals": migration_signals,
    }
