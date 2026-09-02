from migration_factory.agents.planning_agent.copilot_custom_agent import (
    CUSTOM_AGENT_NAME,
    CUSTOM_AGENT_TOOLS,
    FORBIDDEN_ACTIONS_TEXT,
    get_copilot_custom_agent_config,
)


def test_custom_agent_name_is_aimf_planning_assist() -> None:
    assert CUSTOM_AGENT_NAME == "aimf-planning-assist"


def test_custom_agent_tools_scope_is_view_only() -> None:
    assert CUSTOM_AGENT_TOOLS == ["view"]


def test_custom_agent_forbidden_actions_text_contains_required_restrictions() -> None:
    expected = (
        "Forbidden actions: changing unit order, tools, blockers, approval requirement, "
        "executable, or source files."
    )
    assert FORBIDDEN_ACTIONS_TEXT == expected


def test_custom_agent_config_exposes_wrapper_fields() -> None:
    config = get_copilot_custom_agent_config()

    assert config.name == "aimf-planning-assist"
    assert config.tools == ["view"]
    assert FORBIDDEN_ACTIONS_TEXT in config.prompt
