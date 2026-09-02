"""F15 Focused Analyzer Request Mapping — map user comments to safe analysis scopes.

Defines allowed scopes (dependencies, imports, XML, tests, security, internal jars)
that user comments can be mapped to. Rejects arbitrary scripts or unknown scopes.
Unknown scopes trigger a clarifying question instead of a fallback action.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any
from uuid import uuid4

from migration_factory.control_tower.domain.checksums import utc_now_text


# ── Allowed scopes ────────────────────────────────────────────────────


class AnalysisScope(str, Enum):
    """Canonical, backend-owned analysis scopes.

    These are the ONLY scopes that user comments can map to.
    No arbitrary commands, scripts, or filesystem targets.
    """

    DEPENDENCIES = "dependencies"
    IMPORTS = "imports"
    XML = "xml"
    TESTS = "tests"
    SECURITY = "security"
    INTERNAL_JARS = "internal_jars"


# Human-readable descriptions with safe command hints (no shell commands)
_ALLOWED_SCOPE_DESCRIPTIONS: dict[AnalysisScope, str] = {
    AnalysisScope.DEPENDENCIES: (
        "Scan project dependencies for compatibility issues, "
        "deprecated APIs, and required version updates."
    ),
    AnalysisScope.IMPORTS: (
        "Analyze import statements for Jakarta EE migration "
        "requirements and package changes."
    ),
    AnalysisScope.XML: (
        "Review XML configuration files (pom.xml, application.xml, "
        "web.xml) for migration-relevant changes."
    ),
    AnalysisScope.TESTS: (
        "Analyze test code for API breakages and required "
        "test framework updates."
    ),
    AnalysisScope.SECURITY: (
        "Check security configurations, authentication providers, "
        "and authorization rules for migration impact."
    ),
    AnalysisScope.INTERNAL_JARS: (
        "Scan internal library dependencies for compatibility "
        "with the target Java/profile version."
    ),
}


# ── Keyword-to-scope mapping ──────────────────────────────────────────

# Natural language keywords mapped to safe scopes.
# Chatbot interprets user phrases; backend maps to these scopes.
_KEYWORD_SCOPE_MAP: dict[str, AnalysisScope] = {
    # Dependencies
    "dependency": AnalysisScope.DEPENDENCIES,
    "dependencies": AnalysisScope.DEPENDENCIES,
    "dep": AnalysisScope.DEPENDENCIES,
    "lib": AnalysisScope.DEPENDENCIES,
    "library": AnalysisScope.DEPENDENCIES,
    "libraries": AnalysisScope.DEPENDENCIES,
    "jar": AnalysisScope.DEPENDENCIES,
    "maven": AnalysisScope.DEPENDENCIES,
    "gradle": AnalysisScope.DEPENDENCIES,
    "pom": AnalysisScope.DEPENDENCIES,
    # Imports
    "import": AnalysisScope.IMPORTS,
    "imports": AnalysisScope.IMPORTS,
    "package": AnalysisScope.IMPORTS,
    "packages": AnalysisScope.IMPORTS,
    "jakarta": AnalysisScope.IMPORTS,
    "javax": AnalysisScope.IMPORTS,
    # XML
    "xml": AnalysisScope.XML,
    "config": AnalysisScope.XML,
    "configuration": AnalysisScope.XML,
    "application.xml": AnalysisScope.XML,
    "web.xml": AnalysisScope.XML,
    # Tests
    "test": AnalysisScope.TESTS,
    "tests": AnalysisScope.TESTS,
    "unit": AnalysisScope.TESTS,
    "junit": AnalysisScope.TESTS,
    "testing": AnalysisScope.TESTS,
    # Security
    "security": AnalysisScope.SECURITY,
    "auth": AnalysisScope.SECURITY,
    "authentication": AnalysisScope.SECURITY,
    "oauth": AnalysisScope.SECURITY,
    "permission": AnalysisScope.SECURITY,
    # Internal jars
    "internal": AnalysisScope.INTERNAL_JARS,
    "internal jar": AnalysisScope.INTERNAL_JARS,
    "proprietary": AnalysisScope.INTERNAL_JARS,
    "private": AnalysisScope.INTERNAL_JARS,
    "in-house": AnalysisScope.INTERNAL_JARS,
}


# ── Result types ──────────────────────────────────────────────────────


@dataclass(frozen=True)
class ScopeMappingResult:
    """Result of mapping a user comment to an analysis scope.

    Success:
      - scope is set
      - command_hint is a backend-owned description (not a shell command)
      - confidence is set (0.0-1.0)

    Unknown:
      - scope is None
      - clarification_question is set
      - matched_keywords is a list of recognized keywords (if any)

    Rejected:
      - blocked_reason is set explaining why (e.g. attempted script injection)
      - is_rejected is True
    """

    scope: AnalysisScope | None
    confidence: float  # 0.0 to 1.0
    description: str  # Safe description (not a shell command)
    matched_keywords: tuple[str, ...] = ()
    clarification_question: str = ""
    is_rejected: bool = False
    blocked_reason: str = ""
    mapping_id: str = ""


# ── Suspicious patterns that should be rejected ───────────────────────

_SUSPICIOUS_PATTERNS: list[str] = [
    # Shell commands
    "curl ", "wget ", "nc ", "bash ", "sh ", "exec ", "eval ",
    "$(", "`", "| ", "> ", "< ", "&&", "||",
    "; ", "rm ", "dd ", "chmod ", "sudo ",
    # Script/file references
    ".py", ".sh", ".bat", ".exe", ".js",
    "/etc/", "/usr/", "/bin/", "/opt/",
    # Arbitrary scope keywords
    "all files", "everything", "full scan", "deep scan",
]


# ── Service ───────────────────────────────────────────────────────────


class V2AnalysisScopeMapper:
    """Map user comments to safe, backend-owned analysis scopes.

    Chatbot is flexible in understanding user intent, but the backend
    only accepts predefined scopes. Unknown scopes trigger a clarifying
    question. Suspicious patterns (shell commands, file paths) are
    rejected outright.
    """

    def __init__(self) -> None:
        self._keyword_map = _KEYWORD_SCOPE_MAP
        self._suspicious = _SUSPICIOUS_PATTERNS

    def map_comment_to_scope(
        self,
        user_comment: str,
    ) -> ScopeMappingResult:
        """Map a user comment (from reanalysis request) to a safe scope.

        Steps:
        1. Check for suspicious/rejected patterns.
        2. Match user comments against keyword-to-scope map.
        3. If no clear match, return a clarifying question.

        Returns:
            ScopeMappingResult with the matched scope or clarification.
        """
        # 1. Check for suspicious patterns
        rejection = self._check_rejection(user_comment)
        if rejection:
            return rejection

        # 2. Normalize and extract keywords
        text = user_comment.lower().strip()
        if not text:
            return ScopeMappingResult(
                scope=None,
                confidence=0.0,
                description="No analysis scope specified.",
                clarification_question=self._build_clarification([]),
                mapping_id=uuid4().hex[:12],
            )

        # 3. Match against keywords
        matched_keywords: list[str] = []
        matched_scopes: dict[AnalysisScope, int] = {}

        for keyword, scope in self._keyword_map.items():
            if keyword.lower() in text:
                matched_keywords.append(keyword)
                matched_scopes[scope] = matched_scopes.get(scope, 0) + 1

        if not matched_scopes:
            return ScopeMappingResult(
                scope=None,
                confidence=0.0,
                description="Could not determine analysis scope from your comment.",
                clarification_question=self._build_clarification([]),
                mapping_id=uuid4().hex[:12],
            )

        # 4. Determine best matching scope
        best_scope = max(matched_scopes, key=matched_scopes.get)
        match_count = matched_scopes[best_scope]
        total_keywords = len(matched_keywords)

        # Confidence based on proportion of matched keywords
        confidence = min(0.5 + (match_count / max(total_keywords, 1)) * 0.5, 0.95)

        description = _ALLOWED_SCOPE_DESCRIPTIONS.get(
            best_scope,
            f"Focused analysis on {best_scope.value}."
        )

        return ScopeMappingResult(
            scope=best_scope,
            confidence=confidence,
            description=description,
            matched_keywords=tuple(set(matched_keywords)),
            mapping_id=uuid4().hex[:12],
        )

    def get_available_scopes(self) -> list[dict[str, str]]:
        """Return list of available scopes with descriptions.

        For assistant context — never includes shell commands or paths.
        """
        return [
            {
                "scope": s.value,
                "description": _ALLOWED_SCOPE_DESCRIPTIONS[s],
            }
            for s in AnalysisScope
        ]

    def scope_to_analyzer_hint(self, scope: AnalysisScope) -> str:
        """Convert a scope to a safe analyzer hint string.

        Returns a human-readable hint that the backend analyzer can
        use to focus its analysis. No shell commands, no paths,
        no arbitrary code.
        """
        hints: dict[AnalysisScope, str] = {
            AnalysisScope.DEPENDENCIES: "focus-on-dependencies",
            AnalysisScope.IMPORTS: "focus-on-imports",
            AnalysisScope.XML: "focus-on-xml-config",
            AnalysisScope.TESTS: "focus-on-tests",
            AnalysisScope.SECURITY: "focus-on-security",
            AnalysisScope.INTERNAL_JARS: "focus-on-internal-jars",
        }
        return hints.get(scope, "general-analysis")

    def _check_rejection(
        self,
        user_comment: str,
    ) -> ScopeMappingResult | None:
        """Check if the user comment contains suspicious patterns.

        Returns a rejected ScopeMappingResult if suspicious content
        is detected, None otherwise.
        """
        text = user_comment.lower()

        for pattern in self._suspicious:
            if pattern.lower() in text:
                return ScopeMappingResult(
                    scope=None,
                    confidence=0.0,
                    description="Comment was rejected as it contains potentially unsafe patterns.",
                    is_rejected=True,
                    blocked_reason=(
                        f"Comment contains suspicious pattern: '{pattern[:50]}'. "
                        f"Only predefined analysis scopes are allowed."
                    ),
                    mapping_id=uuid4().hex[:12],
                )

        # Check for absolute paths
        if "/tmp/" in text or "/var/" in text or "/home/" in text:
            return ScopeMappingResult(
                scope=None,
                confidence=0.0,
                description="Comment contains filesystem references and was rejected.",
                is_rejected=True,
                blocked_reason=(
                    "Filesystem paths are not allowed in analysis scope requests."
                ),
                mapping_id=uuid4().hex[:12],
            )

        return None

    def _build_clarification(
        self,
        matched_keywords: list[str],
    ) -> str:
        """Build a clarifying question when no single scope matches."""
        available = self.get_available_scopes()
        scope_list = "', '".join(s["scope"] for s in available)

        if matched_keywords:
            kw = "', '".join(matched_keywords[:3])
            return (
                f"I see keywords like '{kw}', but I'm not sure which "
                f"analysis scope to use. "
                f"Please choose one: '{scope_list}'."
            )

        return (
            f"Could you specify which area you'd like me to re-analyze? "
            f"Available scopes: '{scope_list}'."
        )
