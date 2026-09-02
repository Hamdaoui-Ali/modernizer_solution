import json

import pytest

from copilot_enricher import GuardrailValidator, enrich_with_ai, CopilotSDKWrapper


class DummyContext:
    def __init__(self, run_id, out_dir):
        self.run_id = run_id
        self._out_dir = out_dir

    def get_output_path(self, filename):
        return str(self._out_dir / filename)


def _base_report():
    return {
        "source_stack": {"java": "11", "spring_boot": "2.7.18"},
        "target_stack": {"java": "17", "spring_boot": "3.2.x"},
        "project_metadata": {"import_stats": {"javax_count": 1}},
        "ai_enrichment": {
            "status": "SKIPPED",
            "additional_risks": [],
            "recommendations": [],
        },
    }


def _enable_ai(monkeypatch):
    monkeypatch.setenv("AIMF_AI_ASSIST_ENABLED", "true")
    monkeypatch.setenv("AIMF_COPILOT_AUTH_MODE", "oauth_github_app")
    monkeypatch.setenv("AIMF_GITHUB_APP_OAUTH_TOKEN", "oauth-token")
    monkeypatch.setenv("COPILOT_ANALYSIS_MODEL", "gpt-5-mini")
    monkeypatch.setattr(CopilotSDKWrapper, "is_available", staticmethod(lambda: True))


def test_guardrail_blocks_stack_tampering():
    original_report = {
        "source_stack": {"java": "11", "spring_boot": "2.7.18"},
        "project_metadata": {"import_stats": {"javax_count": 50}},
    }
    tampered_report = {
        "source_stack": {"java": "17", "spring_boot": "2.7.18"},
        "project_metadata": {"import_stats": {"javax_count": 50}},
    }

    with pytest.raises(ValueError, match="attempted to modify source_stack"):
        GuardrailValidator.validate_no_tampering(original_report, tampered_report)


def test_guardrail_blocks_stats_tampering():
    original_report = {
        "source_stack": {"java": "11", "spring_boot": "2.7.18"},
        "project_metadata": {"import_stats": {"javax_count": 50}},
    }
    tampered_report = {
        "source_stack": {"java": "11", "spring_boot": "2.7.18"},
        "project_metadata": {"import_stats": {"javax_count": 0}},
    }

    with pytest.raises(ValueError, match="attempted to modify import_stats"):
        GuardrailValidator.validate_no_tampering(original_report, tampered_report)


def test_enrich_skipped_when_disabled(monkeypatch, tmp_path):
    monkeypatch.setenv("AIMF_AI_ASSIST_ENABLED", "false")
    ctx = DummyContext("run_1", tmp_path)

    result = enrich_with_ai(ctx, _base_report())

    assert result["ai_enrichment"]["status"] == "SKIPPED"
    artifact = json.loads((tmp_path / "copilot_assist.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "SKIPPED"


def test_enabled_assist_fails_open_when_adapter_unavailable(monkeypatch, tmp_path):
    _enable_ai(monkeypatch)

    ctx = DummyContext("run_2", tmp_path)
    result = enrich_with_ai(ctx, _base_report())

    assert result["ai_enrichment"]["status"] == "FAILED"
    artifact = json.loads((tmp_path / "copilot_assist.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "FAILED"
    assert artifact["auth_mode"] == "oauth_github_app"
    assert artifact["model"] == "gpt-5-mini"
    assert artifact["requested_model"] == "gpt-5-mini"
    assert artifact["resolved_model"] == "gpt-5-mini"
    assert artifact["model_source"] == "env_override"
    assert artifact["model_verified"] is False
    assert any("adapter_unavailable" in warning for warning in artifact["warnings"])


def test_invalid_analysis_model_override_fails_open(monkeypatch, tmp_path):
    _enable_ai(monkeypatch)
    monkeypatch.setenv("COPILOT_ANALYSIS_MODEL", "unknown-model")

    ctx = DummyContext("run_3", tmp_path)
    result = enrich_with_ai(ctx, _base_report())

    assert result["ai_enrichment"]["status"] == "FAILED"
    artifact = json.loads((tmp_path / "copilot_assist.json").read_text(encoding="utf-8"))
    assert artifact["status"] == "FAILED"
    assert artifact["resolved_model"] is None
    assert artifact["model_verified"] is False
    assert any("model_unavailable" in warning for warning in artifact["warnings"])


def test_forbidden_deterministic_mutation_ignored_with_warning(monkeypatch, tmp_path):
    advisory, warnings = GuardrailValidator.extract_advisory_fields(
        {
            "source_stack": {"java": "21"},
            "dependency_graph": {"changed": True},
            "recommendations": ["safe-rec"],
        }
    )

    assert advisory == {"recommendations": ["safe-rec"]}
    assert any("deterministic mutation attempt" in warning for warning in warnings)


def test_invalid_json_rejected(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="Invalid Copilot JSON output"):
        GuardrailValidator.extract_advisory_fields("{bad-json")


def test_invalid_confidence_rejected(monkeypatch, tmp_path):
    with pytest.raises(ValueError, match="Invalid confidence value"):
        GuardrailValidator.extract_advisory_fields({"confidence": 1.5})
