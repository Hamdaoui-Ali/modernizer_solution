import json
from pathlib import Path

import dependency_adapter


class DummyContext:
    def __init__(self, base_dir: Path):
        self.base_dir = base_dir
        self.legacy_app_path = str(base_dir / "legacy")
        (base_dir / "legacy").mkdir(parents=True, exist_ok=True)

    def get_output_path(self, filename: str) -> str:
        out = self.base_dir / filename
        out.parent.mkdir(parents=True, exist_ok=True)
        return str(out)


class ProcResult:
    def __init__(self, stdout: str):
        self.stdout = stdout


def test_run_dependency_tree_prefers_json_and_builds_structured_graph(tmp_path, monkeypatch):
    payload = {
        "artifact": "com.acme:demo:jar:1.0.0",
        "children": [
            {"artifact": "org.slf4j:slf4j-api:jar:2.0.13", "children": []},
            {
                "artifact": "junit:junit:jar:4.13.2:test",
                "children": [{"artifact": "org.hamcrest:hamcrest-core:jar:1.3", "children": []}],
            },
        ],
    }

    def fake_run(cmd, **kwargs):
        assert "-DoutputType=json" in cmd
        return ProcResult(json.dumps(payload))

    monkeypatch.setattr(dependency_adapter.subprocess, "run", fake_run)
    ctx = DummyContext(tmp_path)

    graph = dependency_adapter.run_dependency_tree(ctx)

    assert graph["available"] is True
    assert graph["format"] == "json"
    assert graph["raw_file"] == "dependency-tree.raw.json"
    assert graph["root"]["name"] == "com.acme:demo"
    assert len(graph["root"]["dependencies"]) == 2
    assert (tmp_path / "dependency-tree.raw.json").exists()

    saved = json.loads((tmp_path / "dependency_graph.json").read_text(encoding="utf-8"))
    assert saved["root"]["dependencies"][1]["dependencies"][0]["name"] == "org.hamcrest:hamcrest-core"


def test_run_dependency_tree_falls_back_to_text_and_parses_children(tmp_path, monkeypatch):
    text_tree = """[INFO] com.acme:demo:jar:1.0.0
[INFO] +- org.slf4j:slf4j-api:jar:2.0.13:compile
[INFO] \\ - ch.qos.logback:logback-classic:jar:1.5.6:compile
"""

    calls = {"count": 0}

    def fake_run(cmd, **kwargs):
        calls["count"] += 1
        if "-DoutputType=json" in cmd:
            raise RuntimeError("json unsupported")
        return ProcResult(text_tree)

    monkeypatch.setattr(dependency_adapter.subprocess, "run", fake_run)
    ctx = DummyContext(tmp_path)

    graph = dependency_adapter.run_dependency_tree(ctx)

    assert calls["count"] == 2
    assert graph["available"] is True
    assert graph["format"] == "text"
    assert graph["raw_file"] == "dependency-tree.raw.txt"
    assert graph["root"]["name"] == "com.acme:demo"
    dep_names = {dep["name"] for dep in graph["root"]["dependencies"]}
    assert "org.slf4j:slf4j-api" in dep_names
    assert (tmp_path / "dependency-tree.raw.txt").exists()


def test_run_dependency_tree_emits_unavailable_graph_on_maven_failure(tmp_path, monkeypatch):
    def fake_run(cmd, **kwargs):
        raise RuntimeError("mvn missing")

    monkeypatch.setattr(dependency_adapter.subprocess, "run", fake_run)
    ctx = DummyContext(tmp_path)

    graph = dependency_adapter.run_dependency_tree(ctx)

    assert graph["available"] is False
    assert graph["root"] is None
    assert "Maven dependency tree unavailable" in graph["warning"]

    saved = json.loads((tmp_path / "dependency_graph.json").read_text(encoding="utf-8"))
    assert saved["available"] is False
    assert "warning" in saved
