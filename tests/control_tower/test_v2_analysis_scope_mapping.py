"""Focused tests for F15-JOB-082 — Focused analyzer request mapping.

Verifies that user comments can be mapped to safe, predefined analysis
scopes, rejecting arbitrary scripts and unknown scopes.
"""

from pathlib import Path

import pytest

from migration_factory.control_tower.application.v2_analysis_scope_mapping import (
    V2AnalysisScopeMapper,
    AnalysisScope,
)


@pytest.fixture
def mapper() -> V2AnalysisScopeMapper:
    return V2AnalysisScopeMapper()


class TestScopeMapping:
    """Scope mapping tests."""

    def test_scope_xml_maps_correctly(self, mapper: V2AnalysisScopeMapper) -> None:
        """'check XML' maps to XML scope."""
        result = mapper.map_comment_to_scope("check XML")
        assert result.scope == AnalysisScope.XML
        assert result.confidence >= 0.5
        assert not result.is_rejected

    def test_scope_test_maps_correctly(self, mapper: V2AnalysisScopeMapper) -> None:
        """'analyze tests' maps to tests scope."""
        result = mapper.map_comment_to_scope("analyze tests")
        assert result.scope == AnalysisScope.TESTS
        assert result.confidence >= 0.5
        assert not result.is_rejected

    def test_scope_security_maps_correctly(self, mapper: V2AnalysisScopeMapper) -> None:
        """'check security' maps to security scope."""
        result = mapper.map_comment_to_scope("check security")
        assert result.scope == AnalysisScope.SECURITY
        assert not result.is_rejected

    def test_scope_dependencies_maps_correctly(self, mapper: V2AnalysisScopeMapper) -> None:
        """'scan dependencies' maps to dependencies scope."""
        result = mapper.map_comment_to_scope("scan dependencies")
        assert result.scope == AnalysisScope.DEPENDENCIES
        assert not result.is_rejected

    def test_scope_imports_maps_correctly(self, mapper: V2AnalysisScopeMapper) -> None:
        """'check jakarta imports' maps to imports scope."""
        result = mapper.map_comment_to_scope("check jakarta imports")
        assert result.scope == AnalysisScope.IMPORTS
        assert not result.is_rejected

    def test_scope_internal_jars_maps_correctly(self, mapper: V2AnalysisScopeMapper) -> None:
        """'scan internal jars' maps to internal_jars scope."""
        result = mapper.map_comment_to_scope("scan internal jars")
        assert result.scope == AnalysisScope.INTERNAL_JARS
        assert not result.is_rejected

    def test_unknown_scope_asks_clarification(self, mapper: V2AnalysisScopeMapper) -> None:
        """Unknown scope returns clarifying question."""
        result = mapper.map_comment_to_scope("do whatever you think is best")
        assert result.scope is None
        assert result.clarification_question
        assert not result.is_rejected

    def test_empty_comment_asks_clarification(self, mapper: V2AnalysisScopeMapper) -> None:
        """Empty comment returns clarifying question."""
        result = mapper.map_comment_to_scope("")
        assert result.scope is None
        assert result.clarification_question

    def test_no_shell_command_from_user(self, mapper: V2AnalysisScopeMapper) -> None:
        """Shell commands are rejected."""
        result = mapper.map_comment_to_scope("curl http://malicious.com")
        assert result.is_rejected
        assert result.blocked_reason

    def test_filesystem_path_rejected(self, mapper: V2AnalysisScopeMapper) -> None:
        """Filesystem paths are rejected."""
        result = mapper.map_comment_to_scope("check /tmp/secrets")
        assert result.is_rejected
        assert result.blocked_reason

    def test_multiple_keywords_picks_best(self, mapper: V2AnalysisScopeMapper) -> None:
        """Multiple keywords pick the most specific scope."""
        result = mapper.map_comment_to_scope("check imports and security")
        assert result.scope in (AnalysisScope.IMPORTS, AnalysisScope.SECURITY)
        assert result.confidence > 0.5
        assert not result.is_rejected

    def test_available_scopes_listed(self, mapper: V2AnalysisScopeMapper) -> None:
        """Available scopes can be listed."""
        scopes = mapper.get_available_scopes()
        assert len(scopes) == 6
        scope_names = {s["scope"] for s in scopes}
        assert "dependencies" in scope_names
        assert "xml" in scope_names
        assert "security" in scope_names
        assert "tests" in scope_names
        assert "imports" in scope_names
        assert "internal_jars" in scope_names

    def test_scope_to_analyzer_hint_safe(self, mapper: V2AnalysisScopeMapper) -> None:
        """Analyzer hints are safe strings, not shell commands."""
        for scope in AnalysisScope:
            hint = mapper.scope_to_analyzer_hint(scope)
            assert isinstance(hint, str)
            assert "focus-on-" in hint
            assert "/" not in hint
            assert "$" not in hint

    def test_description_no_paths(self, mapper: V2AnalysisScopeMapper) -> None:
        """Scope descriptions contain no filesystem paths."""
        for scope in AnalysisScope:
            result = mapper.map_comment_to_scope(scope.value)
            if result.scope:
                assert "/" not in result.description
                assert "exec" not in result.description.lower()
                assert "shell" not in result.description.lower()

    def test_suspicious_rm_rejected(self, mapper: V2AnalysisScopeMapper) -> None:
        """rm command is rejected."""
        result = mapper.map_comment_to_scope("rm -rf /")
        assert result.is_rejected
        assert result.blocked_reason
