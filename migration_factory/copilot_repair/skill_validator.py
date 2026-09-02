from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml


AGENT_PATH = Path(".github/agents/ai-migration-repair.agent.md")
SKILL_PATHS = (
    Path(".github/skills/ai-migration-factory/SKILL.md"),
    Path(".github/skills/openrewrite-diff-safety/SKILL.md"),
    Path(".github/skills/spring-boot-3-migration/SKILL.md"),
    Path(".github/skills/spring-security-6-migration/SKILL.md"),
    Path(".github/skills/h2-runtime-smoke/SKILL.md"),
    Path(".github/skills/dependency-repair/SKILL.md"),
    Path(".github/skills/internal-jar-javax-scan/SKILL.md"),
)
BROAD_TOOL_TOKENS = {"*", "write", "shell", "url", "read", "apply_patch", "create", "edit", "bash", "powershell"}
UNSAFE_TEXT_PATTERNS = (
    re.compile(r"\bmutate\b.*\bsource\b", re.IGNORECASE),
    re.compile(r"\bmodify\b.*\bsource\b", re.IGNORECASE),
    re.compile(r"\bwrite\b.*\bsandbox\b", re.IGNORECASE),
    re.compile(r"\bdeploy\b", re.IGNORECASE),
    re.compile(r"\bcreate\b.*\bPR\b", re.IGNORECASE),
    re.compile(r"\bpull request\b", re.IGNORECASE),
    re.compile(r"\bapprove\b.*\brun\b", re.IGNORECASE),
    re.compile(r"\bexpose\b.*\bsecret", re.IGNORECASE),
    re.compile(r"\bweaken\b.*\bsecurity\b", re.IGNORECASE),
    re.compile(r"\bdisable\b.*\bsecurity\b", re.IGNORECASE),
)


def validate_agent_and_skills(repo_root: str | Path) -> dict[str, Any]:
    root = Path(repo_root)
    errors: list[str] = []
    warnings: list[str] = []
    agent_status = _validate_markdown_contract(root / AGENT_PATH, is_agent=True, errors=errors)
    skill_statuses = [
        _validate_markdown_contract(root / skill_path, is_agent=False, errors=errors)
        for skill_path in SKILL_PATHS
    ]
    if any(status == "MISSING" for status in skill_statuses):
        skills_status = "MISSING"
    elif any(status == "INVALID" for status in skill_statuses):
        skills_status = "INVALID"
    else:
        skills_status = "FOUND"
    return {
        "agent_status": agent_status,
        "skills_status": skills_status,
        "warnings": warnings,
        "errors": errors,
    }


def validate_skill_file(path: str | Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    status = _validate_markdown_contract(Path(path), is_agent=False, errors=errors)
    return status == "FOUND", errors


def validate_agent_file(path: str | Path) -> tuple[bool, list[str]]:
    errors: list[str] = []
    status = _validate_markdown_contract(Path(path), is_agent=True, errors=errors)
    return status == "FOUND", errors


def _validate_markdown_contract(path: Path, *, is_agent: bool, errors: list[str]) -> str:
    if not path.is_file():
        errors.append(f"missing {'agent' if is_agent else 'skill'} file: {path.as_posix()}")
        return "MISSING"
    text = path.read_text(encoding="utf-8")
    frontmatter = _frontmatter(text)
    label = "agent" if is_agent else "skill"
    if frontmatter is None:
        errors.append(f"{label} missing YAML frontmatter: {path.as_posix()}")
        return "INVALID"
    if not str(frontmatter.get("name") or "").strip():
        errors.append(f"{label} frontmatter missing name: {path.as_posix()}")
    if not str(frontmatter.get("description") or "").strip():
        errors.append(f"{label} frontmatter missing description: {path.as_posix()}")
    _validate_allowed_tools(frontmatter, path, errors)
    _validate_unsafe_body(text, path, errors)
    return "INVALID" if errors and any(path.as_posix() in error for error in errors) else "FOUND"


def _frontmatter(text: str) -> dict[str, Any] | None:
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---", 4)
    if end < 0:
        return None
    try:
        payload = yaml.safe_load(text[4:end]) or {}
    except yaml.YAMLError:
        return None
    return payload if isinstance(payload, dict) else None


def _validate_allowed_tools(frontmatter: dict[str, Any], path: Path, errors: list[str]) -> None:
    value = frontmatter.get("allowed-tools") or frontmatter.get("tools")
    if value is None:
        return
    items: list[str]
    if isinstance(value, str):
        items = [item.strip() for item in value.split(",")]
    elif isinstance(value, list):
        items = [str(item).strip() for item in value]
    else:
        errors.append(f"invalid allowed-tools type: {path.as_posix()}")
        return
    lowered = {item.lower() for item in items if item}
    broad = sorted(lowered & BROAD_TOOL_TOKENS)
    if broad:
        errors.append(f"broad tools are forbidden in {path.as_posix()}: {', '.join(broad)}")


def _validate_unsafe_body(text: str, path: Path, errors: list[str]) -> None:
    for line in text.splitlines():
        lowered = line.lower()
        guardrail = any(phrase in lowered for phrase in ("do not", "must not", "never", "out of scope", "forbidden", "cannot"))
        for pattern in UNSAFE_TEXT_PATTERNS:
            if pattern.search(line) and not guardrail:
                errors.append(f"unsafe instruction in {path.as_posix()}: {pattern.pattern}")
