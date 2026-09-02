import json
from pathlib import Path


def _collect_test_files(root_directory):
    root_path = Path(root_directory)
    test_files = []
    module_names = set()
    test_roots = set()

    for base in [root_path, *(p.parent for p in root_path.glob("*/pom.xml"))]:
        for language in ("java", "kotlin"):
            test_root = base / "src" / "test" / language
            if test_root.exists():
                test_roots.add(test_root)
                if base != root_path:
                    module_names.add(base.relative_to(root_path).as_posix())

    for test_root in sorted(test_roots):
        for path in test_root.rglob("*"):
            if path.is_file() and (
                path.name.endswith("Test.java")
                or path.name.endswith("Tests.java")
                or path.name.endswith("Test.kt")
                or path.name.endswith("Tests.kt")
            ):
                test_files.append(path.relative_to(root_path).as_posix())

    return sorted(test_files), sorted(module_names)


def scan_tests(directory, modernized_directory=None):
    legacy_test_files, legacy_modules = _collect_test_files(directory)

    inventory = {
        "test_count": len(legacy_test_files),
        "test_files": legacy_test_files,
        "modules": legacy_modules,
        "surefire_reports_available": False
    }

    if modernized_directory:
        modernized_test_files, modernized_modules = _collect_test_files(modernized_directory)

        missing = sorted(set(legacy_test_files) - set(modernized_test_files))
        inventory.update(
            {
                "legacy_test_count": len(legacy_test_files),
                "modernized_test_count": len(modernized_test_files),
                "modernized_modules": modernized_modules,
                "missing_tests_count": len(missing),
                "missing_tests": missing,
            }
        )

    return inventory


def save_test_inventory(context, inventory):
    output_file = context.get_output_path("test_inventory.json")
    with open(output_file, 'w') as f:
        json.dump(inventory, f, indent=4)
    return output_file
