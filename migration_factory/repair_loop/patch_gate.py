from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any

from migration_factory.repair_loop.rule_registry import evaluate_rule


BLOCKED_PARTS = {".git", ".migration", "target", "build", "node_modules"}
BLOCKED_FILE_NAMES = {
    ".env",
    ".env.local",
    ".env.production",
    "docker-compose.yml",
    "docker-compose.yaml",
    "Dockerfile",
    "Jenkinsfile",
}
BLOCKED_PREFIXES = (".github/workflows/", "deploy/", "deployment/", "k8s/", "helm/", "charts/")
SECRET_TOKENS = ("password", "secret", "token", "apikey", "api_key", "privatekey", "private_key", "keystore")
SECURITY_TOKENS = (
    "SecurityFilterChain",
    "HttpSecurity",
    "authorizeRequests",
    "authorizeHttpRequests",
    "antMatchers",
    "mvcMatchers",
    "requestMatchers",
    "permitAll",
    "denyAll",
    "authenticated",
    "hasRole",
    "hasAuthority",
    "OAuth2",
    "JWT",
    "Jwt",
    "csrf",
    "cors",
    "filter",
    "keystore",
    "SAML",
    "@PreAuthorize",
    "@PostAuthorize",
)
FORBIDDEN_SECURITY_PATTERNS = (
    re.compile(r"\+.*\.permitAll\s*\(", re.IGNORECASE),
    re.compile(r"-.*\.authenticated\s*\(", re.IGNORECASE),
    re.compile(r"-.*\.has(?:Role|Authority)\s*\(", re.IGNORECASE),
    re.compile(r"\+.*csrf\s*\([^)]*disable", re.IGNORECASE),
    re.compile(r"\+.*cors\s*\([^)]*disable", re.IGNORECASE),
    re.compile(r"\+.*(?:jwt|oauth2|resourceserver|auth).*disable", re.IGNORECASE),
)
SQL_SERVER_CLAIMS = ("sql server validated", "production db validated", "endpoint validated", "endpoint smoke validated")


@dataclass(frozen=True)
class PatchGateResult:
    status: str
    reason: str
    rule_id: str = ""
    risk: str = "BLOCKED"
    touched_paths: tuple[str, ...] = ()
    human_review_required: bool = False


def validate_technical_patch_application(
    *,
    unified_diff: str,
    sandbox_path: str | Path,
) -> PatchGateResult:
    """Apply-boundary checks only; no reviewer/risk/rule/policy semantics."""
    diff = str(unified_diff or "")
    if not is_unified_diff(diff):
        return PatchGateResult("INVALID_PATCH", "patch is not a valid unified diff")
    paths, path_errors = extract_touched_paths(diff)
    if path_errors:
        return PatchGateResult("INVALID_PATCH", "; ".join(path_errors), touched_paths=tuple(paths))
    sandbox = Path(sandbox_path).resolve()
    errors: list[str] = []
    for rel in paths:
        normalized = rel.replace("\\", "/")
        pure = PurePosixPath(normalized)
        win = PureWindowsPath(rel)
        if normalized.startswith("/") or normalized.startswith("//") or win.is_absolute() or re.match(r"^[A-Za-z]:", rel):
            errors.append(f"absolute patch path rejected: {rel}")
            continue
        if ".." in pure.parts:
            errors.append(f"path traversal rejected: {rel}")
            continue
        candidate = (sandbox / pure).resolve()
        if not candidate.is_relative_to(sandbox):
            errors.append(f"patch path escapes sandbox: {rel}")
        if _has_symlink_parent(candidate, sandbox):
            errors.append(f"patch path traverses a symlink: {rel}")
    if errors:
        return PatchGateResult("INVALID_PATCH", "; ".join(errors), touched_paths=tuple(paths))
    return PatchGateResult("ALLOWED", "technical diff integrity and sandbox containment passed", touched_paths=tuple(paths))


def normalize_unified_diff_for_sandbox(
    unified_diff: str,
    *,
    sandbox_path: str | Path,
) -> str:
    """Normalize an accidental leading ``sandbox/`` repo path exactly once."""
    paths, _ = extract_touched_paths(unified_diff)
    if not paths or not all(path.startswith("sandbox/") for path in paths):
        return unified_diff
    sandbox = Path(sandbox_path).resolve()
    if (sandbox / "sandbox").exists():
        return unified_diff
    for path in paths:
        stripped = sandbox / path.removeprefix("sandbox/")
        if not stripped.exists() and not stripped.parent.exists():
            return unified_diff

    normalized_lines: list[str] = []
    for line in unified_diff.splitlines(keepends=True):
        if line.startswith("diff --git ") or line.startswith("--- ") or line.startswith("+++ "):
            line = line.replace("a/sandbox/", "a/", 1).replace("b/sandbox/", "b/", 1)
        normalized_lines.append(line)
    return "".join(normalized_lines)


def evaluate_patch_proposal(
    *,
    proposal: dict[str, Any],
    sandbox_path: str | Path,
    run_dir: str | Path,
    legacy_path: str | Path,
    failure_classification: dict[str, Any] | None = None,
    h2_required: bool = False,
) -> PatchGateResult:
    rule_id = str(proposal.get("deterministic_rule_id") or "")
    risk = str(proposal.get("risk") or "").upper()
    requires_human_review = bool(proposal.get("requires_human_review", False))
    diff = str(proposal.get("unified_diff") or "")

    if not rule_id:
        return PatchGateResult("INVALID_PATCH", "patch proposal is missing deterministic_rule_id")
    if risk != "LOW":
        return PatchGateResult("HUMAN_REVIEW_REQUIRED", f"patch risk is not LOW: {risk}", rule_id, risk, human_review_required=True)
    if requires_human_review:
        return PatchGateResult("HUMAN_REVIEW_REQUIRED", "patch proposal requires human review", rule_id, risk, human_review_required=True)
    if not is_unified_diff(diff):
        return PatchGateResult("INVALID_PATCH", "patch proposal is not a unified diff", rule_id, risk)
    if _claims_out_of_scope(proposal):
        return PatchGateResult("BLOCKED", "patch proposal claims out-of-scope validation", rule_id, risk)

    paths, path_errors = extract_touched_paths(diff)
    if path_errors:
        return PatchGateResult("INVALID_PATCH", "; ".join(path_errors), rule_id, risk)
    validation_errors = validate_patch_paths(
        paths,
        sandbox_path=sandbox_path,
        run_dir=run_dir,
        legacy_path=legacy_path,
    )
    if validation_errors:
        return PatchGateResult("INVALID_PATCH", "; ".join(validation_errors), rule_id, risk, tuple(paths))

    security_reason = security_patch_reason(paths, diff)
    if security_reason:
        return PatchGateResult("HUMAN_REVIEW_REQUIRED", security_reason, rule_id, risk, tuple(paths), True)

    rule_decision = evaluate_rule(
        rule_id=rule_id,
        sandbox_path=sandbox_path,
        touched_paths=paths,
        unified_diff=diff,
        failure_classification=failure_classification,
        h2_required=h2_required,
    )
    if not rule_decision.allowed:
        status = "HUMAN_REVIEW_REQUIRED" if rule_decision.human_review_required else "BLOCKED"
        return PatchGateResult(status, rule_decision.reason, rule_id, risk, tuple(paths), rule_decision.human_review_required)
    return PatchGateResult("ALLOWED", rule_decision.reason, rule_id, risk, tuple(paths))


def is_unified_diff(diff: str) -> bool:
    text = diff.strip()
    if not text:
        return False
    if "GIT binary patch" in text or "Binary files " in text:
        return False
    return "diff --git " in text and "\n--- " in text and "\n+++ " in text and "\n@@" in text


def extract_touched_paths(diff: str) -> tuple[list[str], list[str]]:
    paths: list[str] = []
    errors: list[str] = []
    for line in diff.splitlines():
        if line.startswith("diff --git "):
            parts = line.split()
            if len(parts) < 4:
                errors.append("malformed diff --git header")
                continue
            for raw in parts[2:4]:
                path = _strip_diff_prefix(raw)
                if path != "/dev/null":
                    paths.append(path)
        elif line.startswith("--- ") or line.startswith("+++ "):
            raw = line[4:].split("\t", 1)[0].strip()
            path = _strip_diff_prefix(raw)
            if path != "/dev/null":
                paths.append(path)
    deduped: list[str] = []
    for path in paths:
        if path not in deduped:
            deduped.append(path)
    if not deduped:
        errors.append("unified diff contains no touched paths")
    return deduped, errors


def validate_patch_paths(
    paths: list[str],
    *,
    sandbox_path: str | Path,
    run_dir: str | Path,
    legacy_path: str | Path,
) -> list[str]:
    sandbox = Path(sandbox_path).resolve()
    run_root = Path(run_dir).resolve()
    legacy = Path(legacy_path).resolve()
    errors: list[str] = []
    for rel in paths:
        errors.extend(_relative_path_errors(rel))
        if errors and any(rel in error for error in errors):
            continue
        candidate = (sandbox / PurePosixPath(rel)).resolve()
        if not candidate.is_relative_to(sandbox):
            errors.append(f"patch path escapes sandbox: {rel}")
        if candidate == legacy or candidate.is_relative_to(legacy):
            errors.append(f"patch path touches legacy source: {rel}")
        if candidate == run_root:
            errors.append(f"patch path touches run root: {rel}")
        if _has_symlink_parent(candidate, sandbox):
            errors.append(f"patch path traverses a symlink: {rel}")
    return errors


def security_patch_reason(paths: list[str], diff: str) -> str:
    security_path = any(_looks_security_path(path) for path in paths)
    security_content = any(token.lower() in diff.lower() for token in SECURITY_TOKENS)
    if security_path or security_content:
        return "Spring Security or authentication-sensitive patch requires human review"
    for pattern in FORBIDDEN_SECURITY_PATTERNS:
        if pattern.search(diff):
            return "patch attempts to weaken Spring Security"
    return ""


def _relative_path_errors(path: str) -> list[str]:
    errors: list[str] = []
    normalized = path.replace("\\", "/")
    pure = PurePosixPath(normalized)
    win = PureWindowsPath(path)
    lowered = normalized.lower()
    if normalized.startswith("/") or win.is_absolute() or re.match(r"^[a-zA-Z]:", path) or normalized.startswith("//"):
        errors.append(f"absolute patch path rejected: {path}")
    if ".." in pure.parts:
        errors.append(f"path traversal rejected: {path}")
    if any(part in BLOCKED_PARTS for part in pure.parts):
        errors.append(f"blocked generated/internal path rejected: {path}")
    if pure.name in BLOCKED_FILE_NAMES:
        errors.append(f"blocked deployment/env file rejected: {path}")
    if any(lowered.startswith(prefix) for prefix in BLOCKED_PREFIXES):
        errors.append(f"blocked deployment/release path rejected: {path}")
    if any(token in lowered for token in SECRET_TOKENS):
        errors.append(f"secret-like path rejected: {path}")
    return errors


def _strip_diff_prefix(raw: str) -> str:
    path = raw.strip().strip('"')
    if path.startswith("a/") or path.startswith("b/"):
        return path[2:]
    return path


def _looks_security_path(path: str) -> bool:
    lowered = path.lower()
    return "security" in lowered or "auth" in lowered or "jwt" in lowered or "saml" in lowered


def _has_symlink_parent(path: Path, sandbox: Path) -> bool:
    current = sandbox
    try:
        rel_parts = path.relative_to(sandbox).parts
    except ValueError:
        return True
    for part in rel_parts:
        current = current / part
        if current.exists() and current.is_symlink():
            return True
    return False


def _claims_out_of_scope(proposal: dict[str, Any]) -> bool:
    text = " ".join(
        str(value)
        for value in (
            proposal.get("description", ""),
            proposal.get("expected_validation", []),
            proposal.get("limitations", []),
        )
    ).lower()
    return any(claim in text for claim in SQL_SERVER_CLAIMS)
