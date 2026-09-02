"""F0 closure: prove no copilot imports exist in control_tower."""

from __future__ import annotations

from pathlib import Path

_FORBIDDEN_IMPORTS = (
    "migration_factory.copilot_assist",
    "migration_factory.copilot_repair",
    "migration_factory.agents.copilot_doc_agent",
    "migration_factory.final_report.copilot",
)


def _collect_matching_lines(root: Path) -> list[str]:
    matches: list[str] = []
    for py_file in sorted(root.rglob("*.py")):
        try:
            text = py_file.read_text(encoding="utf-8")
        except Exception:
            continue
        for lineno, line in enumerate(text.splitlines(), start=1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for forbidden in _FORBIDDEN_IMPORTS:
                if forbidden in stripped:
                    matches.append(f"{py_file.relative_to(root.parent)}:{lineno}: {stripped}")
    return matches


def test_control_tower_has_zero_copilot_imports() -> None:
    root = Path(__file__).resolve().parent.parent.parent / "migration_factory" / "control_tower"
    assert root.is_dir(), f"control_tower directory not found: {root}"
    matches = _collect_matching_lines(root)
    assert not matches, f"Copilot imports found in control_tower:\n" + "\n".join(matches)
