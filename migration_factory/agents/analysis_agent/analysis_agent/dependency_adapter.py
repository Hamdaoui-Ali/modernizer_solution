import json
import subprocess
from typing import Any, Dict, List, Optional

from migration_factory.maven import resolve_maven_executable


def _new_graph(raw_file: Optional[str] = None) -> Dict[str, Any]:
    graph = {
        "available": False,
        "raw_file": raw_file,
        "format": None,
        "warning": None,
        "root": None,
    }
    return graph


def _new_node(name: str, version: Optional[str] = None) -> Dict[str, Any]:
    return {"name": name, "version": version, "dependencies": []}


def _parse_maven_coord(coord: str) -> Dict[str, Optional[str]]:
    parts = coord.split(":")
    if len(parts) >= 4:
        return {"name": f"{parts[0]}:{parts[1]}", "version": parts[3]}
    if len(parts) >= 2:
        return {"name": f"{parts[0]}:{parts[1]}", "version": None}
    return {"name": coord.strip(), "version": None}


def _build_json_node(node: Dict[str, Any]) -> Dict[str, Any]:
    artifact = node.get("artifact") or "unknown"
    coord = _parse_maven_coord(artifact)
    out = _new_node(coord["name"], coord["version"])
    for child in node.get("children", []) or []:
        out["dependencies"].append(_build_json_node(child))
    return out


def _parse_json_tree(raw_text: str) -> Dict[str, Any]:
    payload = json.loads(raw_text)
    if not isinstance(payload, dict) or "artifact" not in payload:
        raise ValueError("Invalid Maven JSON tree payload")

    graph = _new_graph(raw_file="dependency-tree.raw.json")
    graph["available"] = True
    graph["format"] = "json"
    graph["root"] = _build_json_node(payload)
    return graph


def _line_depth(line: str) -> int:
    idx = line.find("+-")
    if idx == -1:
        idx = line.find("\\-")
    if idx == -1:
        return 0
    return idx // 3 + 1


def _parse_text_tree(raw_text: str) -> Dict[str, Any]:
    lines = [ln.strip() for ln in raw_text.splitlines() if ln.strip()]
    root_line: Optional[str] = None
    dep_lines: List[str] = []
    for ln in lines:
        normalized = ln.replace("\\ -", "\\-")
        if root_line is None and "+-" not in normalized and "\\-" not in normalized:
            root_line = ln
        if "---" in normalized or "+-" in normalized or "\\-" in normalized:
            dep_lines.append(normalized)

    if not dep_lines:
        graph = _new_graph(raw_file="dependency-tree.raw.txt")
        graph["format"] = "text"
        graph["warning"] = "Dependency tree output unavailable or unparsable"
        return graph

    if root_line:
        root_coord = root_line.split("---", 1)[1].strip() if "---" in root_line else root_line.replace("[INFO]", "").strip()
    else:
        first = dep_lines[0]
        root_coord = first.split("---", 1)[1].strip() if "---" in first else first.replace("[INFO]", "").strip()
    root_meta = _parse_maven_coord(root_coord)
    root = _new_node(root_meta["name"], root_meta["version"])

    for ln in dep_lines:
        if "---" in ln:
            segment = ln.split("---", 1)[1].strip()
        elif "+-" in ln:
            segment = ln.split("+-", 1)[1].strip()
        elif "\\-" in ln:
            segment = ln.split("\\-", 1)[1].strip()
        else:
            continue
        meta = _parse_maven_coord(segment)
        node = _new_node(meta["name"], meta["version"])
        root["dependencies"].append(node)

    graph = _new_graph(raw_file="dependency-tree.raw.txt")
    graph["available"] = True
    graph["format"] = "text"
    graph["root"] = root
    return graph


def _write_graph(context, graph: Dict[str, Any]) -> None:
    output_file = context.get_output_path("dependency_graph.json")
    with open(output_file, "w", encoding="utf-8") as handle:
        json.dump(graph, handle, indent=4)


def run_dependency_tree(context):
    maven_executable = resolve_maven_executable()
    json_cmd = [maven_executable, "dependency:tree", "-DoutputType=json"]
    text_cmd = [maven_executable, "dependency:tree", "-DoutputType=text"]

    try:
        result = subprocess.run(
            json_cmd,
            cwd=context.legacy_app_path,
            capture_output=True,
            text=True,
            check=True,
        )
        graph = _parse_json_tree(result.stdout)
        raw_json = context.get_output_path("dependency-tree.raw.json")
        with open(raw_json, "w", encoding="utf-8") as handle:
            handle.write(result.stdout)

        _write_graph(context, graph)
        return graph
    except Exception as json_error:
        try:
            result = subprocess.run(
                text_cmd,
                cwd=context.legacy_app_path,
                capture_output=True,
                text=True,
                check=True,
            )
            raw_txt = context.get_output_path("dependency-tree.raw.txt")
            with open(raw_txt, "w", encoding="utf-8") as handle:
                handle.write(result.stdout)

            graph = _parse_text_tree(result.stdout)
            if not graph["available"]:
                graph["warning"] = graph["warning"] or f"JSON parse failed: {json_error}"
            _write_graph(context, graph)
            return graph
        except Exception as text_error:
            graph = _new_graph()
            graph["warning"] = (
                f"Maven dependency tree unavailable. json_error={json_error}; text_error={text_error}"
            )
            _write_graph(context, graph)
            return graph
