from migration_factory.agents.planning_agent.assist_config import load_planning_assist_config


def test_load_planning_assist_config_defaults_when_env_missing(monkeypatch) -> None:
    monkeypatch.delenv("MF_PLANNING_ASSIST_ENABLED", raising=False)
    monkeypatch.delenv("MF_PLANNING_ASSIST_PROVIDER", raising=False)
    monkeypatch.delenv("MF_PLANNING_ASSIST_MODE", raising=False)
    monkeypatch.delenv("MF_PLANNING_ASSIST_MODEL", raising=False)

    config = load_planning_assist_config()

    assert config.enabled is False
    assert config.provider == "github_copilot"
    assert config.mode == "assist_only"
    assert config.direct_write is False
    assert config.model_override is None
    assert config.default_model == "gpt-5-mini"
    assert "gpt-5-mini" in config.allowed_models


def test_load_planning_assist_config_from_ai_hub(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("MF_PLANNING_ASSIST_ENABLED", raising=False)
    hub = tmp_path / "hub"
    agents = hub / "agents"
    agents.mkdir(parents=True)
    (agents / "copilot-assist.yaml").write_text(
        """
enabled: false
provider: github_copilot
mode: assist_only
direct_write: false
auth:
  default_mode: token
  modes: [github_signed_in_user, oauth_github_app, token]
model:
  default: gpt-5-mini
  allowed: [gpt-5-mini, gpt-4o]
  phase_overrides:
    analysis: gpt-5-mini
    planning: gpt-5-mini
allowed_phases: [analysis_review, planning_review]
forbidden_actions: [source_writes, blocker_changes, executable_changes, approval_changes, tool_changes, unit_changes]
""".strip(),
        encoding="utf-8",
    )

    config = load_planning_assist_config(ai_hub_path=hub)

    assert config.enabled is False
    assert config.auth_mode == "token"
    assert config.default_model == "gpt-5-mini"
    assert config.phase_model_overrides == {"analysis": "gpt-5-mini", "planning": "gpt-5-mini"}


def test_load_planning_assist_config_env_overrides_enabled_auth_and_model(tmp_path, monkeypatch) -> None:
    hub = tmp_path / "hub"
    (hub / "agents").mkdir(parents=True)
    (hub / "agents" / "copilot-assist.yaml").write_text(
        "enabled: false\nauth:\n  default_mode: github_signed_in_user\nmodel:\n  default: gpt-4.1\n  allowed: [gpt-4.1, gpt-4o]\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("MF_PLANNING_ASSIST_ENABLED", "true")
    monkeypatch.setenv("MF_PLANNING_ASSIST_AUTH_MODE", "token")
    monkeypatch.setenv("MF_PLANNING_ASSIST_MODEL", "gpt-4o")

    config = load_planning_assist_config(ai_hub_path=hub)

    assert config.enabled is True
    assert config.auth_mode == "token"
    assert config.model_override == "gpt-4o"


def test_load_planning_assist_config_model_override(monkeypatch) -> None:
    monkeypatch.setenv("MF_PLANNING_ASSIST_ENABLED", "true")
    monkeypatch.setenv("MF_PLANNING_ASSIST_MODEL", "gpt-4.1")

    config = load_planning_assist_config()

    assert config.enabled is True
    assert config.model_override == "gpt-4.1"
