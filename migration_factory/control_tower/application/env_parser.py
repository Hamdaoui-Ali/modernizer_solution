"""V2 Local env block parser — typed parse of PowerShell env snippets.

This module parses pasted PowerShell env blocks into typed local setup
fields WITHOUT executing them. The parser extracts only allowlisted keys,
maps known flags to typed options, and returns ignored/blocked key sets.

Design:
- No execution: pure parsing + string extraction only.
- Allowlist: only explicitly listed keys are extracted.
- Blocklist: Azure/OpenAI secrets, deployment IDs are caught and reported.
- Ignored: PYTHONPATH and other non-allowlisted keys are returned as ignored.
- PowerShell env syntax: $env:KEY = "value" or $KEY = "value".
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any


# ── Allowlisted keys ─────────────────────────────────────────────────

# Env vars that are extracted from $env:KEY = "value" lines
ALLOWLISTED_ENV_KEYS: frozenset[str] = frozenset({
    "JAVA11_HOME",
    "JAVA17_HOME",
    "JAVA21_HOME",
    "MAVEN_CMD",
    "AI_MIGRATION_PROOF_LEVEL",
    "AI_MIGRATION_SKIP_ENDPOINT_SMOKE",
})

# Non-env PowerShell variables ($KEY = "value") that are extracted
ALLOWLISTED_VAR_KEYS: frozenset[str] = frozenset({
    "AI_HUB",
    "legacy",
    "outputParent",
    "runName",
    "stageContinuationPolicy",
})

# Azure/OpenAI key prefixes that should be blocked
BLOCKED_KEY_PREFIXES: tuple[str, ...] = (
    "AZURE_OPENAI_",
    "AZURE_",
    "OPENAI_",
    "AZURE_FOUNDRY_",
)

# Env keys that are explicitly ignored (soft ignored)
IGNORED_KEYS: frozenset[str] = frozenset({
    "PYTHONPATH",
    "PATH",
    "HOME",
    "USERPROFILE",
    "APPDATA",
    "LOCALAPPDATA",
    "TEMP",
    "TMP",
})

# Known AI_MIGRATION_* flags and their types
AI_MIGRATION_FLAGS: dict[str, type] = {
    "AI_MIGRATION_PROOF_LEVEL": str,
    "AI_MIGRATION_SKIP_ENDPOINT_SMOKE": bool,
}

FLAG_VALUES: dict[str, frozenset[str]] = {
    "AI_MIGRATION_PROOF_LEVEL": frozenset({
        "analyzed",
        "build_test_verified",
        "runtime_verified",
    }),
}


# ── Models ───────────────────────────────────────────────────────────


@dataclass(frozen=True)
class ParsedJavaHomes:
    java11: str = ""
    java17: str = ""
    java21: str = ""


@dataclass(frozen=True)
class ParsedMigrationFlags:
    proof_level: str = ""
    skip_endpoint_smoke: bool | None = None


@dataclass(frozen=True)
class EnvParseResult:
    """Result of parsing a PowerShell env block.

    All parsed values are raw strings extracted from the env block.
    No path validation, existence checks, or value coercion is done.
    """
    run_name: str = ""
    legacy_app_path: str = ""
    output_parent_path: str = ""
    ai_hub_path: str = ""
    java_homes: ParsedJavaHomes = field(default_factory=ParsedJavaHomes)
    maven_cmd: str = ""
    migration_flags: ParsedMigrationFlags = field(default_factory=ParsedMigrationFlags)
    stage_continuation_policy: str = ""
    ignored_keys: tuple[str, ...] = ()
    blocked_keys: tuple[str, ...] = ()


# ── Regex patterns ──────────────────────────────────────────────────

# Matches: $env:KEY = "value" or $env:KEY = 'value'
_ENV_ASSIGN_RE = re.compile(
    r"""\$env:([A-Z][A-Z0-9_]+)\s*=\s*["']([^"']*)["']""",
    re.IGNORECASE,
)

# Matches: $KEY = "value" or $KEY = 'value' (PowerShell variables)
_VAR_ASSIGN_RE = re.compile(
    r"""\$([A-Za-z][A-Za-z0-9_]*)\s*=\s*["']([^"']*)["']""",
)

# Matches: KEY=value (bare env style)
_BARE_ENV_RE = re.compile(
    r"""^([A-Z][A-Z0-9_]{1,})\s*=\s*["']?([^"'\s]+)["']?\s*$""",
    re.MULTILINE,
)


# ── Parser ──────────────────────────────────────────────────────────


def parse_env_block(block: str) -> EnvParseResult:
    """Parse a PowerShell env block into typed local setup fields.

    The parser is pure (no I/O, no execution). It extracts only
    allowlisted keys, returns ignored/blocked key sets, and maps
    known flags to typed options.
    """
    # Collect assignments from all patterns
    env_assignments: dict[str, str] = {}
    var_assignments: dict[str, str] = {}
    bare_assignments: dict[str, str] = {}

    for match in _ENV_ASSIGN_RE.finditer(block):
        key = match.group(1).upper()
        value = match.group(2)
        env_assignments[key] = value

    for match in _VAR_ASSIGN_RE.finditer(block):
        key = match.group(1)
        value = match.group(2)
        var_assignments[key] = value

    for match in _BARE_ENV_RE.finditer(block):
        key = match.group(1).upper()
        value = match.group(2)
        bare_assignments[key] = value

    # Merge: env assignments take precedence, then bare env
    all_keys: set[str] = set()
    all_keys.update(env_assignments.keys())
    all_keys.update(bare_assignments.keys())

    # Track ignored and blocked keys
    ignored: list[str] = []
    blocked: list[str] = []

    for key in sorted(all_keys):
        if _is_blocked(key):
            blocked.append(key)
        elif key in IGNORED_KEYS:
            ignored.append(key)
        elif key not in ALLOWLISTED_ENV_KEYS:
            ignored.append(key)

    # Extract allowlisted env values
    java_homes = ParsedJavaHomes(
        java11=env_assignments.get("JAVA11_HOME", ""),
        java17=env_assignments.get("JAVA17_HOME", ""),
        java21=env_assignments.get("JAVA21_HOME", ""),
    )
    maven_cmd = env_assignments.get("MAVEN_CMD", "")

    # Extract migration flags
    proof_level = env_assignments.get("AI_MIGRATION_PROOF_LEVEL", "")
    skip_raw = env_assignments.get("AI_MIGRATION_SKIP_ENDPOINT_SMOKE", "")
    skip_endpoint_smoke: bool | None = None
    if skip_raw.lower() in ("true", "1", "yes"):
        skip_endpoint_smoke = True
    elif skip_raw.lower() in ("false", "0", "no"):
        skip_endpoint_smoke = False

    flags = ParsedMigrationFlags(
        proof_level=proof_level if proof_level.lower()
        in FLAG_VALUES.get("AI_MIGRATION_PROOF_LEVEL", frozenset()) else "",
        skip_endpoint_smoke=skip_endpoint_smoke,
    )

    # Extract allowlisted PowerShell variables
    ai_hub_path = var_assignments.get("AI_HUB", "")
    legacy_app_path = var_assignments.get("legacy", "")
    output_parent_path = var_assignments.get("outputParent", "")
    run_name = var_assignments.get("runName", "")
    stage_continuation_policy_raw = var_assignments.get("stageContinuationPolicy", "")

    stage_continuation_policy = ""
    if stage_continuation_policy_raw:
        from migration_factory.control_tower.schemas.run_configuration import StageContinuationPolicy
        try:
            StageContinuationPolicy(stage_continuation_policy_raw.strip().strip('"').strip("'"))
            stage_continuation_policy = stage_continuation_policy_raw.strip().strip('"').strip("'")
        except ValueError:
            pass

    return EnvParseResult(
        run_name=run_name,
        legacy_app_path=legacy_app_path,
        output_parent_path=output_parent_path,
        ai_hub_path=ai_hub_path,
        java_homes=java_homes,
        maven_cmd=maven_cmd,
        migration_flags=flags,
        stage_continuation_policy=stage_continuation_policy,
        ignored_keys=tuple(sorted(set(ignored))),
        blocked_keys=tuple(sorted(set(blocked))),
    )


def parse_result_to_dict(result: EnvParseResult) -> dict[str, Any]:
    """Convert an EnvParseResult to a JSON-safe dict."""
    return {
        "parsed": {
            "run_name": result.run_name,
            "legacy_app_path": result.legacy_app_path,
            "output_parent_path": result.output_parent_path,
            "ai_hub_path": result.ai_hub_path,
            "java_homes": {
                "java11": result.java_homes.java11,
                "java17": result.java_homes.java17,
                "java21": result.java_homes.java21,
            },
            "maven_cmd": result.maven_cmd,
            "migration_flags": {
                "proof_level": result.migration_flags.proof_level,
                "skip_endpoint_smoke": result.migration_flags.skip_endpoint_smoke,
            },
            "stage_continuation_policy": result.stage_continuation_policy,
        },
        "ignored_keys": list(result.ignored_keys),
        "blocked_keys": list(result.blocked_keys),
    }


# ── Internal helpers ────────────────────────────────────────────────


def _is_blocked(key: str) -> bool:
    """Check if an env var key name is blocked."""
    upper = key.upper()
    for prefix in BLOCKED_KEY_PREFIXES:
        if upper.startswith(prefix):
            return True
    return False
