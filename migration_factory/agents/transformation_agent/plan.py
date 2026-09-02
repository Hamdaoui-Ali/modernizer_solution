from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
import json


class MigrationPlanError(Exception):
    pass


@dataclass(frozen=True)
class MigrationUnit:
    id: str
    title: str | None
    transformations: list[dict[str, Any]] = field(default_factory=list)
    checks: list[dict[str, Any]] = field(default_factory=list)
    expected_files: list[str] = field(default_factory=list)
    raw: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MigrationPlan:
    schema_version: str
    migration_id: str
    migration_name: str | None
    target_path: Path
    migration_dir: Path
    ledger_file: Path
    units: list[MigrationUnit]
    raw: dict[str, Any]


def load_migration_plan(plan_path: str | Path, modernized_app_path: str | Path | None = None) -> MigrationPlan:
    path = Path(plan_path).expanduser().resolve()
    if not path.is_file():
        raise MigrationPlanError(f"Migration plan does not exist: {path}")

    raw = _read_yaml(path)
    if not isinstance(raw, dict):
        raise MigrationPlanError("Migration plan root must be a mapping")

    migration = _mapping(raw.get("migration"), "migration")
    workspaces = _mapping(raw.get("workspaces"), "workspaces")
    target_workspace = _mapping(workspaces.get("target"), "workspaces.target")

    target_path = _resolve_target_path(path, target_workspace, modernized_app_path)
    migration_dir = target_path / str(target_workspace.get("migration_dir", ".migration"))
    ledger_file = target_path / str(target_workspace.get("ledger_file", ".migration/ledger.json"))

    units = [_parse_unit(item) for item in _list(raw.get("migration_units"), "migration_units")]
    if not units:
        raise MigrationPlanError("Migration plan must contain at least one migration unit")

    return MigrationPlan(
        schema_version=str(raw.get("schema_version", "")),
        migration_id=str(migration.get("id") or "unknown-migration"),
        migration_name=migration.get("name"),
        target_path=target_path,
        migration_dir=migration_dir,
        ledger_file=ledger_file,
        units=units,
        raw=raw,
    )


def _read_yaml(path: Path) -> dict[str, Any]:
    try:
        import yaml  # type: ignore[import-untyped]
    except ImportError:
        loaded = _SimpleYamlParser(path.read_text(encoding="utf-8")).parse()
        if isinstance(loaded, dict):
            return loaded
        raise MigrationPlanError("Migration plan YAML must load to a mapping")

    with path.open("r", encoding="utf-8") as handle:
        loaded = yaml.safe_load(handle)
    if loaded is None:
        return {}
    if isinstance(loaded, dict):
        return loaded
    raise MigrationPlanError("Migration plan YAML must load to a mapping")


class _SimpleYamlParser:
    def __init__(self, text: str) -> None:
        self.lines = self._tokenize(text)

    def parse(self) -> Any:
        if not self.lines:
            return {}
        value, index = self._parse_block(0, self.lines[0][0])
        if index < len(self.lines):
            raise MigrationPlanError(f"Could not parse YAML near: {self.lines[index][1]}")
        return value

    def _parse_block(self, index: int, indent: int) -> tuple[Any, int]:
        if index >= len(self.lines):
            return {}, index
        if self.lines[index][1].startswith("- "):
            return self._parse_list(index, indent)
        return self._parse_mapping(index, indent)

    def _parse_mapping(self, index: int, indent: int) -> tuple[dict[str, Any], int]:
        mapping: dict[str, Any] = {}
        while index < len(self.lines):
            line_indent, text = self.lines[index]
            if line_indent < indent:
                break
            if line_indent > indent:
                break
            if text.startswith("- "):
                break

            key, raw_value = self._split_key_value(text)
            if raw_value in (">", "|"):
                value, index = self._parse_block_scalar(index + 1, line_indent, folded=raw_value == ">")
            elif raw_value == "":
                if index + 1 < len(self.lines) and self.lines[index + 1][0] > line_indent:
                    value, index = self._parse_block(index + 1, self.lines[index + 1][0])
                else:
                    value, index = {}, index + 1
            else:
                value = self._parse_scalar(raw_value)
                index += 1
            mapping[key] = value
        return mapping, index

    def _parse_list(self, index: int, indent: int) -> tuple[list[Any], int]:
        items: list[Any] = []
        while index < len(self.lines):
            line_indent, text = self.lines[index]
            if line_indent < indent:
                break
            if line_indent != indent or not text.startswith("- "):
                break

            content = text[2:].strip()
            if content == "":
                value, index = self._parse_block(index + 1, self.lines[index + 1][0])
                items.append(value)
                continue

            if self._looks_like_key_value(content):
                key, raw_value = self._split_key_value(content)
                item: dict[str, Any] = {}
                if raw_value in (">", "|"):
                    item[key], index = self._parse_block_scalar(index + 1, line_indent, folded=raw_value == ">")
                elif raw_value == "":
                    item[key], index = self._parse_block(index + 1, self.lines[index + 1][0])
                else:
                    item[key] = self._parse_scalar(raw_value)
                    index += 1

                if index < len(self.lines) and self.lines[index][0] > line_indent:
                    extra, index = self._parse_mapping(index, self.lines[index][0])
                    item.update(extra)
                items.append(item)
                continue

            items.append(self._parse_scalar(content))
            index += 1
        return items, index

    def _parse_block_scalar(self, index: int, parent_indent: int, *, folded: bool) -> tuple[str, int]:
        parts: list[str] = []
        while index < len(self.lines):
            line_indent, text = self.lines[index]
            if line_indent <= parent_indent:
                break
            parts.append(text)
            index += 1
        if folded:
            return " ".join(part.strip() for part in parts).strip(), index
        return "\n".join(parts), index

    def _tokenize(self, text: str) -> list[tuple[int, str]]:
        tokens: list[tuple[int, str]] = []
        for raw in text.splitlines():
            if not raw.strip() or raw.lstrip().startswith("#"):
                continue
            uncommented = self._strip_inline_comment(raw.rstrip())
            if not uncommented.strip():
                continue
            indent = len(uncommented) - len(uncommented.lstrip(" "))
            tokens.append((indent, uncommented.strip()))
        return tokens

    def _strip_inline_comment(self, line: str) -> str:
        in_single = False
        in_double = False
        for index, char in enumerate(line):
            if char == "'" and not in_double:
                in_single = not in_single
            elif char == '"' and not in_single:
                in_double = not in_double
            elif char == "#" and not in_single and not in_double:
                if index == 0 or line[index - 1].isspace():
                    return line[:index].rstrip()
        return line

    def _split_key_value(self, text: str) -> tuple[str, str]:
        if ":" not in text:
            raise MigrationPlanError(f"Expected key/value YAML line, got: {text}")
        key, value = text.split(":", 1)
        return key.strip(), value.strip()

    def _looks_like_key_value(self, text: str) -> bool:
        return ":" in text and not text.startswith(("http://", "https://"))

    def _parse_scalar(self, raw: str) -> Any:
        value = raw.strip()
        if value in ("null", "Null", "NULL", "~"):
            return None
        if value in ("true", "True", "TRUE"):
            return True
        if value in ("false", "False", "FALSE"):
            return False
        if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
            return value[1:-1]
        return value


def _resolve_target_path(
    plan_path: Path,
    target_workspace: dict[str, Any],
    modernized_app_path: str | Path | None,
) -> Path:
    if modernized_app_path is not None:
        return Path(modernized_app_path).expanduser().resolve()

    raw_path = target_workspace.get("path")
    if not raw_path:
        raise MigrationPlanError("workspaces.target.path is required when modernized_app_path is not provided")

    target_path = Path(str(raw_path)).expanduser()
    if target_path.is_absolute():
        return target_path.resolve()
    return (plan_path.parent / target_path).resolve()


def _parse_unit(raw_unit: Any) -> MigrationUnit:
    unit = _mapping(raw_unit, "migration_units[]")
    unit_id = unit.get("id")
    if not unit_id:
        raise MigrationPlanError("Each migration unit must define id")
    return MigrationUnit(
        id=str(unit_id),
        title=unit.get("title"),
        transformations=_list(unit.get("transformations", []), f"{unit_id}.transformations"),
        checks=_list(unit.get("checks", []), f"{unit_id}.checks"),
        expected_files=[str(item) for item in _list(unit.get("expected_files", []), f"{unit_id}.expected_files")],
        raw=unit,
    )


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if isinstance(value, dict):
        return value
    raise MigrationPlanError(f"{name} must be a mapping")


def _list(value: Any, name: str) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    raise MigrationPlanError(f"{name} must be a list")


def plan_to_json(plan: MigrationPlan) -> str:
    data = {
        "schema_version": plan.schema_version,
        "migration_id": plan.migration_id,
        "migration_name": plan.migration_name,
        "target_path": str(plan.target_path),
        "ledger_file": str(plan.ledger_file),
        "units": [{"id": unit.id, "title": unit.title} for unit in plan.units],
    }
    return json.dumps(data, indent=2)
