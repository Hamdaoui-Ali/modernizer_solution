import os
import xml.etree.ElementTree as ET
import json

def parse_surefire_reports(directory):
    reports_path = os.path.join(directory, "target", "surefire-reports")
    summary = {
        "available": False,
        "passed": 0,
        "failed": 0,
        "errors": 0,
        "skipped": 0
    }

    if os.path.exists(reports_path):
        summary["available"] = True
        for file in os.listdir(reports_path):
            if file.endswith(".xml") and file.startswith("TEST-"):
                try:
                    tree = ET.parse(os.path.join(reports_path, file))
                    root = tree.getroot()
                    summary["passed"] += int(root.get("tests", 0)) - int(root.get("failures", 0)) - int(root.get("errors", 0)) - int(root.get("skipped", 0))
                    summary["failed"] += int(root.get("failures", 0))
                    summary["errors"] += int(root.get("errors", 0))
                    summary["skipped"] += int(root.get("skipped", 0))
                except Exception:
                    continue
    
    return summary