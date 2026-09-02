import os

def scan_java_imports(directory):
    counts = {
        "javax_imports": 0,
        "jakarta_imports": 0,
        "spring_imports": 0,
        "files_with_javax": []
    }

    for root, _, files in os.walk(directory):
        for file in files:
            if file.endswith(".java"):
                file_path = os.path.join(root, file)
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        has_javax = False
                        for line in f:
                            if line.strip().startswith("import "):
                                if "javax." in line:
                                    counts["javax_imports"] += 1
                                    has_javax = True
                                elif "jakarta." in line:
                                    counts["jakarta_imports"] += 1
                                elif "org.springframework." in line:
                                    counts["spring_imports"] += 1
                        
                        if has_javax:
                            counts["files_with_javax"].append(file_path)
                except Exception:
                    continue

    return counts